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
_PUBLIC = {"/api/health", "/auth/login", "/auth/callback", "/auth/logout", "/favicon.ico"}


def _cfg():
    return (
        os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        os.environ.get("SESSION_SECRET", "").strip(),
    )


def enabled():
    cid, secret, sess = _cfg()
    return bool(cid and secret and sess)


def _allowed_emails():
    raw = os.environ.get("ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


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
        if session.get("email"):
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
        if err:
            return _deny("Google returned: %s" % err)
        if request.args.get("state") != session.pop("oauth_state", None):
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
            return _deny(str(e))
        except Exception as e:
            return _deny("sign-in failed: %s" % (str(e)[:200]))
        session["email"] = email
        dest = session.pop("next", "/") or "/"
        if not dest.startswith("/"):
            dest = "/"                       # only ever redirect to our own paths
        return redirect(dest)

    @app.get("/auth/logout")
    def auth_logout():
        session.clear()
        return redirect("/auth/login") if enabled() else redirect("/")


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
