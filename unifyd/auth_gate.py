"""auth_gate.py — Google OIDC ("Sign in with Google") gate for the all-in-one suite.

Protects the WHOLE origin (static pages + /api/*) behind a Google login, keyed to an
email allowlist — so even though Google's consent screen is account-agnostic, only the
addresses in ALLOWED_EMAILS actually get in.

Zero new dependencies: the code exchange uses `requests` (already a dep) and the id_token
is read with stdlib base64/json. We DON'T re-verify the JWT signature because the token is
fetched server-to-server directly from Google's token endpoint over TLS, authenticated with
our client secret — per OIDC §3.1.3.7 tokens obtained that way are trusted without local
signature checks. We still validate iss / aud / nonce / email_verified / exp / allowlist.

Config (all via env / Fly secrets):
    GOOGLE_CLIENT_ID       OAuth 2.0 Client ID      (required to enable)
    GOOGLE_CLIENT_SECRET   OAuth 2.0 Client secret  (required to enable)
    SESSION_SECRET         Flask cookie-signing key (required to enable)
    ALLOWED_EMAILS         comma-separated allowlist (required to let anyone in)
    OAUTH_REDIRECT_URI     optional explicit callback URL; else derived from the request
                           (honors X-Forwarded-Proto/Host, so it's https behind Fly's edge)

When the three required secrets are absent the gate is OFF (open) — local dev and the
pre-secrets deploy keep working; /api/health is always public so the platform probe passes.
"""
import base64, json, os, time
from urllib.parse import urlencode

from flask import request, redirect, session, jsonify, abort

GOOGLE_AUTH  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_ISS  = {"accounts.google.com", "https://accounts.google.com"}

# Paths reachable WITHOUT a session. Everything else is gated.
_PUBLIC = {"/api/health", "/auth/login", "/auth/callback", "/auth/logout",
           "/auth/me", "/api/auth/mobile", "/favicon.ico", "/robots.txt"}

# Mobile bearer tokens: RN has no cookie jar, so native apps authenticate by exchanging a
# Google ID token (POST /api/auth/mobile) for one of OUR signed tokens, sent as a Bearer.
MOBILE_MAX_AGE = 30 * 24 * 3600   # 30 days


def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(os.environ.get("SESSION_SECRET", ""), salt="hoodie-mobile")


def mint_mobile_token(email):
    return _serializer().dumps({"email": email})


def verify_mobile_token(token):
    """Return the email for a valid, unexpired, allowlisted mobile token, else None."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MOBILE_MAX_AGE)
    except Exception:
        return None
    email = (data.get("email") or "").lower()
    allowed = _allowed_emails()
    if allowed and email not in allowed:
        return None
    return email or None


def _mobile_auds():
    """Allowed Google audiences for the mobile exchange — the web client id plus any
    platform client ids in GOOGLE_MOBILE_CLIENT_IDS (comma-separated)."""
    ids = {os.environ.get("GOOGLE_CLIENT_ID", "").strip()}
    for a in os.environ.get("GOOGLE_MOBILE_CLIENT_IDS", "").split(","):
        if a.strip():
            ids.add(a.strip())
    return {i for i in ids if i}


def _cfg():
    return (
        os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        os.environ.get("SESSION_SECRET", "").strip(),
    )


def enabled():
    cid, secret, sess = _cfg()
    return bool(cid and secret and sess)


# ── Access model ────────────────────────────────────────────────────────────────────────────────────
# Two tiers: ADMINS (manage the allowlist + the admin console) and ALLOWED users (get in). The effective
# allowlist is the UNION of three sources, so the admin UI can add users without a redeploy AND a bad edit
# can never lock everyone out or open the gate:
#   1. ALLOWED_EMAILS env  — the immutable "bootstrap" list (set via `flyctl secrets`).
#   2. a runtime provider   — the UI-managed list server.py registers (durable admin_allowlist.json).
#   3. the admins           — always allowed, so an admin can never lock themselves out.
_DEFAULT_ADMIN = "chris.fennessey1@gmail.com"     # fail-safe owner: admin even if ADMIN_EMAILS is unset
_allow_provider = None


def _admin_emails():
    """The admin set (manage users + admin console). ADMIN_EMAILS env override; defaults to the owner so
    admin access can't be accidentally removed."""
    raw = os.environ.get("ADMIN_EMAILS", "").strip()
    if not raw:
        return {_DEFAULT_ADMIN}
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(email):
    return bool(email) and str(email).strip().lower() in _admin_emails()


def set_allowlist_provider(fn):
    """server.py registers a 0-arg callable returning the UI-managed emails (durable state). A hook, so
    auth_gate stays free of any warehouse/state dependency."""
    global _allow_provider
    _allow_provider = fn


def _allowed_emails():
    emails = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}
    if _allow_provider:
        try:
            emails |= {str(e).strip().lower() for e in (_allow_provider() or []) if str(e).strip()}
        except Exception:
            pass                                   # store unreachable -> env + admins (fail safe, never lock out)
    emails |= _admin_emails()                       # admins are always allowed
    return emails


def _redirect_uri():
    explicit = os.environ.get("OAUTH_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return "%s://%s/auth/callback" % (proto, host)


def _decode_jwt_payload(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed id_token")
    seg = parts[1]
    seg += "=" * (-len(seg) % 4)               # restore base64url padding
    return json.loads(base64.urlsafe_b64decode(seg.encode()))


def verify_claims(claims, client_id, allowed, nonce, now=None):
    """Validate Google id_token claims. Returns the verified email, or raises ValueError."""
    now = int(now if now is not None else time.time())
    if claims.get("iss") not in _GOOGLE_ISS:
        raise ValueError("bad issuer")
    aud = claims.get("aud")
    if aud != client_id:
        raise ValueError("aud mismatch")
    if nonce and claims.get("nonce") != nonce:
        raise ValueError("nonce mismatch")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or now > int(exp) + 60:   # small skew grace
        raise ValueError("token expired")
    if not claims.get("email_verified"):
        raise ValueError("email not verified")
    email = (claims.get("email") or "").lower()
    if not email:
        raise ValueError("no email")
    if allowed and email not in allowed:
        raise ValueError("email not in allowlist: %s" % email)
    return email


def init(app):
    """Wire the gate + /auth/* routes onto the Flask app. No-op behaviors stay if unconfigured."""
    cid, secret, sess_secret = _cfg()
    if sess_secret:
        app.secret_key = sess_secret
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",     # Lax lets the cookie ride the top-level GET callback
    )

    @app.before_request
    def _gate():
        if not enabled():
            return                          # unconfigured -> open (local dev / pre-secrets)
        p = request.path
        if p in _PUBLIC:
            return
        print("AUTHDEBUG _gate: path=%r has_email=%r session_keys=%r cookie_present=%r ua=%r"
              % (p, bool(session.get("email")), list(session.keys()),
                 bool(request.cookies.get(app.config.get("SESSION_COOKIE_NAME", "session"))),
                 request.headers.get("User-Agent", "")[:60]),
              flush=True)
        if session.get("email"):
            return
        # mobile bearer token (native apps have no cookie)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and verify_mobile_token(auth[7:]):
            return
        # data ingestion: any script can POST to /api/ingest/* with the shared INGEST_TOKEN
        _ingest = os.environ.get("INGEST_TOKEN", "")
        if p.startswith("/api/ingest/") and _ingest and auth == "Bearer " + _ingest:
            return
        # non-browser agent access: server.py's own AGENT_TOKEN check never runs today because this
        # gate (registered first) already rejects the request before that later before_request fires.
        # Honor it HERE instead, for the whole /api/* surface — matching server.py's own comment that
        # AGENT_TOKEN "gates /api/* for non-browser callers."
        _agent = os.environ.get("AGENT_TOKEN", "")
        if p.startswith("/api/") and _agent and auth == "Bearer " + _agent:
            return
        if p.startswith("/api/"):
            return jsonify(ok=False, error="unauthorized"), 401
        # bounce browsers to Google, remembering where they were headed
        session["next"] = request.full_path if request.query_string else p
        return redirect("/auth/login")

    @app.get("/auth/login")
    def auth_login():
        if not enabled():
            abort(404)
        import secrets as _secrets
        state = _secrets.token_urlsafe(24)
        nonce = _secrets.token_urlsafe(24)
        session["oauth_state"] = state
        session["oauth_nonce"] = nonce
        params = {
            "client_id": cid,
            "redirect_uri": _redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "access_type": "online",
            "prompt": "select_account",
        }
        return redirect(GOOGLE_AUTH + "?" + urlencode(params))

    @app.get("/auth/callback")
    def auth_callback():
        if not enabled():
            abort(404)
        import requests
        err = request.args.get("error")
        print("AUTHDEBUG callback: incoming state=%r session_had_state=%r cookie_present=%r"
              % (request.args.get("state"), session.get("oauth_state"),
                 bool(request.cookies.get(app.config.get("SESSION_COOKIE_NAME", "session")))), flush=True)
        if err:
            print("AUTHDEBUG callback: google returned error=%r" % err, flush=True)
            return _deny("Google returned: %s" % err)
        if request.args.get("state") != session.pop("oauth_state", None):
            print("AUTHDEBUG callback: STATE MISMATCH", flush=True)
            return _deny("state mismatch — please try signing in again.")
        code = request.args.get("code")
        if not code:
            return _deny("no authorization code.")
        try:
            r = requests.post(GOOGLE_TOKEN, timeout=15, data={
                "code": code,
                "client_id": cid,
                "client_secret": secret,
                "redirect_uri": _redirect_uri(),
                "grant_type": "authorization_code",
            })
            r.raise_for_status()
            id_token = r.json().get("id_token")
            if not id_token:
                return _deny("no id_token in Google's response.")
            claims = _decode_jwt_payload(id_token)
            email = verify_claims(claims, cid, _allowed_emails(),
                                  session.pop("oauth_nonce", None))
        except ValueError as e:
            print("AUTHDEBUG callback: verify_claims/ValueError: %r" % str(e), flush=True)
            return _deny(str(e))
        except Exception as e:
            print("AUTHDEBUG callback: exception: %r" % str(e), flush=True)
            return _deny("sign-in failed: %s" % (str(e)[:200]))
        session["email"] = email
        dest = session.pop("next", "/") or "/"
        if not dest.startswith("/"):
            dest = "/"                       # only ever redirect to our own paths
        print("AUTHDEBUG callback: SUCCESS email=%r dest=%r" % (email, dest), flush=True)
        return redirect(dest)

    @app.get("/auth/logout")
    def auth_logout():
        session.clear()
        return redirect("/auth/login") if enabled() else redirect("/")

    @app.get("/auth/me")
    def auth_me():
        # Public: lets the UI decide whether to show a "Sign out" control + admin-only tiles. Reveals only
        # the already-signed-in account's own email + admin status (or null); no info leak when gate is off.
        em = session.get("email")
        return jsonify(gated=enabled(), email=em, is_admin=is_admin(em))

    @app.post("/api/auth/mobile")
    def auth_mobile():
        # Exchange a Google ID token (from the native app's Google sign-in) for one of our
        # signed bearer tokens. Verified via Google's tokeninfo (no JWKS needed), then
        # checked against the audience + email allowlist.
        if not enabled():
            abort(404)
        import requests
        idt = (request.get_json(force=True, silent=True) or {}).get("id_token")
        if not idt:
            return jsonify(ok=False, error="id_token required"), 400
        try:
            r = requests.get("https://oauth2.googleapis.com/tokeninfo",
                             params={"id_token": idt}, timeout=10)
            r.raise_for_status()
            claims = r.json()
        except Exception:
            return jsonify(ok=False, error="token verification failed"), 401
        auds = _mobile_auds()
        if auds and claims.get("aud") not in auds:
            return jsonify(ok=False, error="aud mismatch"), 401
        if str(claims.get("email_verified")).lower() != "true":
            return jsonify(ok=False, error="email not verified"), 401
        email = (claims.get("email") or "").lower()
        allowed = _allowed_emails()
        if allowed and email not in allowed:
            return jsonify(ok=False, error="not allowed"), 403
        return jsonify(ok=True, token=mint_mobile_token(email), email=email)


def _deny(msg):
    body = (
        "<!doctype html><meta charset=utf-8>"
        "<title>Access denied</title>"
        "<div style=\"font:15px/1.5 -apple-system,Segoe UI,sans-serif;max-width:34rem;"
        "margin:12vh auto;padding:0 1.25rem;color:#1a1a1a\">"
        "<h2 style=\"margin:0 0 .5rem\">Access denied</h2>"
        "<p style=\"color:#555\">%s</p>"
        "<p><a href=\"/auth/login\" style=\"color:#5b3df5;text-decoration:none;font-weight:600\">"
        "&larr; Try a different Google account</a></p></div>"
    ) % msg
    return body, 403
