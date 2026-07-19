#!/usr/bin/env python3
"""resi.py — ONE provider-agnostic residential-proxy resolver for every scraper.

Set a single env var and any pool (IPRoyal, Bright Data, Webshare, …) works across both
the stdlib fetchers and the patchright/playwright browsers. Switching pools = changing
one line; scraper code never changes.

Config, checked in priority order:
  1. RESI_PROXY        — a full URL:  http://user:pass@host:port   (or user:pass@host:port)
  2. RESI_PROXY_USER + RESI_PROXY_PASS + RESI_PROXY_HOST + RESI_PROXY_PORT   (parts; the
     password is URL-encoded for you, so IPRoyal's `_country-us_session-x` params are safe)
  3. Fallback: the existing Bright Data vars (BRIGHTDATA_PROXY_USER/PASS/HOST/PORT or
     BRIGHTDATA_PROXY) so nothing that works today breaks.

IPRoyal example (residential, PAYG, no business):
    RESI_PROXY_HOST=geo.iproyal.com
    RESI_PROXY_PORT=12321
    RESI_PROXY_USER=<your-username>
    RESI_PROXY_PASS=<your-password>_country-us          # append _session-<id>_lifetime-30m for sticky

Verify it (prints the exit IP + geo THROUGH the proxy — should be a US residential IP that
is NOT your home IP):
    python resi.py
"""
import os
import ssl
import urllib.parse
import urllib.request


# providers whose HTTPS is a clean CONNECT tunnel (real cert) → verify normally. Bright Data's
# Unlocker MITMs HTTPS to solve challenges, so its cert won't chain to a public CA → skip verify.
def _is_unlocker(host):
    return "brd.superproxy" in (host or "") or "brightdata" in (host or "")


def parts():
    """Resolve (user, pass, host, port) from the env, or (None,…) if no proxy is configured."""
    # 1. full URL
    full = os.environ.get("RESI_PROXY", "").strip()
    if full:
        if "://" not in full:
            full = "http://" + full
        u = urllib.parse.urlsplit(full)
        return (urllib.parse.unquote(u.username or ""), urllib.parse.unquote(u.password or ""),
                u.hostname or "", str(u.port or ""))
    # 2. parts (resi), else 3. bright data parts / combined
    user = (os.environ.get("RESI_PROXY_USER") or os.environ.get("BRIGHTDATA_PROXY_USER") or "").strip()
    pw = os.environ.get("RESI_PROXY_PASS") or os.environ.get("BRIGHTDATA_PROXY_PASS") or ""
    if user and pw:
        host = (os.environ.get("RESI_PROXY_HOST") or os.environ.get("BRIGHTDATA_PROXY_HOST")
                or "brd.superproxy.io").strip()
        port = (os.environ.get("RESI_PROXY_PORT") or os.environ.get("BRIGHTDATA_PROXY_PORT")
                or "33335").strip()
        return (user, pw, host, port)
    combined = os.environ.get("BRIGHTDATA_PROXY", "").strip()
    if combined:
        if "://" not in combined:
            combined = "http://" + combined
        u = urllib.parse.urlsplit(combined)
        return (urllib.parse.unquote(u.username or ""), urllib.parse.unquote(u.password or ""),
                u.hostname or "", str(u.port or ""))
    return (None, None, None, None)


def enabled():
    return bool(parts()[0])


def url():
    """Full proxy URL `http://user:pass@host:port` (password URL-encoded), or None if unconfigured."""
    user, pw, host, port = parts()
    if not user:
        return None
    cred = urllib.parse.quote(user, safe="") + ":" + urllib.parse.quote(pw, safe="")
    return "http://%s@%s:%s" % (cred, host, port)


def proxies():
    """requests-style {'http':…, 'https':…} dict, or None. Pass straight to requests(proxies=…)."""
    p = url()
    return {"http": p, "https": p} if p else None


def browser():
    """patchright/playwright proxy dict {'server','username','password'}, or None. The server is the
    bare host:port (creds go in separate fields — playwright rejects creds embedded in server)."""
    user, pw, host, port = parts()
    if not user:
        return None
    return {"server": "http://%s:%s" % (host, port), "username": user, "password": pw}


def _session_url(session):
    """Like url() but pinned to a specific rotating session id, so each retry gets a DIFFERENT exit IP
    (IPRoyal: `_session-<id>` on pass; BD: `-session-<id>` on user). Used by opener(session=…)."""
    user, pw, host, port = parts()
    if not user:
        return None
    tag = "".join(c for c in str(session) if c.isalnum())[:24]
    if "iproyal" in (host or ""):
        pw = pw.split("_session-")[0] + "_session-%s_lifetime-5m" % tag
    elif _is_unlocker(host):
        user = user.split("-session-")[0] + "-session-" + tag
    cred = urllib.parse.quote(user, safe="") + ":" + urllib.parse.quote(pw, safe="")
    return "http://%s@%s:%s" % (cred, host, port)


def geo_session_url(session, state=None, city=None, lifetime="30m"):
    """A proxy URL pinned to a US geo (IPRoyal `_state-<state>` / `_city-<city>` on the password) AND a sticky
    session id — so a browser routed through it exits from the SAME region as the delivery zone it's crawling.
    UberEats' feed is location-based and returns EMPTY when the exit IP's geo conflicts with the pl= zone, so
    geo-matching the IP is what makes the proxy work for coverage (and lets per-region workers run in parallel).
    `state`/`city` are lowercased and space→dash (e.g. 'Illinois'→'illinois', 'New York'→'new-york')."""
    user, pw, host, port = parts()
    if not user:
        return None
    base = pw.split("_country")[0].split("_state")[0].split("_city")[0].split("_session")[0]
    tag = "".join(c for c in str(session) if c.isalnum())[:24]
    geo = "_country-us"
    if state:
        geo += "_state-" + str(state).strip().lower().replace(" ", "-")
    if city:
        geo += "_city-" + str(city).strip().lower().replace(" ", "-")
    if "iproyal" in (host or ""):
        pw = base + geo + "_session-%s_lifetime-%s" % (tag, lifetime)
    cred = urllib.parse.quote(user, safe="") + ":" + urllib.parse.quote(pw, safe="")
    return "http://%s@%s:%s" % (cred, host, port)


def opener(verify=None, session=None):
    """A urllib opener routed through the proxy. `verify` defaults to False for BD Unlocker hosts
    (their MITM cert won't chain) and True otherwise (IPRoyal/Webshare are clean CONNECT tunnels).
    `session` pins a rotating-session id so callers can force a fresh exit IP per retry."""
    p = _session_url(session) if session is not None else url()
    if not p:
        return None
    _, _, host, _ = parts()
    if verify is None:
        verify = not _is_unlocker(host)
    handlers = [urllib.request.ProxyHandler({"http": p, "https": p})]
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def sticky(tag, lifetime="30m"):
    """Return a COPY of the proxy env pinned to one IP for `tag` (so a cookie-warmed session keeps the
    same exit IP). Provider-aware: IPRoyal appends `_session-<tag>_lifetime-…` to the password; Bright
    Data appends `-session-<tag>` to the username. Use: os.environ.update(resi.sticky('7now-tx')) before
    launching the browser/opener. No-op (returns {}) if no proxy configured."""
    user, pw, host, port = parts()
    if not user:
        return {}
    safe = "".join(c for c in str(tag) if c.isalnum())[:24]
    if "iproyal" in (host or ""):
        pw = pw.split("_session-")[0] + "_session-%s_lifetime-%s" % (safe, lifetime)
    elif _is_unlocker(host):
        user = user.split("-session-")[0] + "-session-" + safe
    return {"RESI_PROXY_USER": user, "RESI_PROXY_PASS": pw,
            "RESI_PROXY_HOST": host, "RESI_PROXY_PORT": port, "RESI_PROXY": ""}


# ── ISP / static-residential pool ────────────────────────────────────────────────────────────────────────
# ISP proxies bill FLAT per IP (unlimited bandwidth) instead of per-GB — the cost-effective path for the
# bandwidth-heavy catalog/geofill fetches (any US IP works; no geo-match needed there). A pool of fixed IPs also
# gives free parallelism + flag distribution: pin each worker to a different IP so no single IP gets burned.
# Config: ISP_PROXIES = one endpoint per line (or comma/semicolon-separated). Accepts any of:
#     http://user:pass@host:port   |   user:pass@host:port   |   host:port:user:pass   |   host:port
# Paste the list straight from the IPRoyal ISP dashboard. Falls back to nothing (isp_enabled()=False) if unset.
_ISP_RR = [0]


def _parse_isp_entry(s):
    s = s.strip()
    if not s:
        return None
    if "://" in s:
        return s
    if "@" in s:                                   # user:pass@host:port
        return "http://" + s
    parts_ = s.split(":")
    if len(parts_) == 4:                           # host:port:user:pass (IPRoyal export)
        h, po, u, pw = parts_
        return "http://%s:%s@%s:%s" % (urllib.parse.quote(u, safe=""), urllib.parse.quote(pw, safe=""), h, po)
    if len(parts_) == 2:                           # host:port (creds already in URL / open)
        return "http://%s" % s
    return None


def isp_pool():
    """List of normalized proxy URLs from ISP_PROXIES (empty if unset). Each is a static, unlimited-bandwidth IP."""
    raw = os.environ.get("ISP_PROXIES", "")
    if not raw:
        return []
    entries = [e for chunk in raw.replace(";", "\n").replace(",", "\n").splitlines() for e in [chunk.strip()] if e]
    return [p for p in (_parse_isp_entry(e) for e in entries) if p]


def isp_enabled():
    return bool(isp_pool())


def isp_url(key=None):
    """Pick one proxy URL from the ISP pool. With `key` → deterministic (a warmed cookie sticks to one IP);
    without → round-robin across the pool (spreads load + distributes any flagging). None if pool empty."""
    pool = isp_pool()
    if not pool:
        return None
    if key is not None:
        h = 0
        for c in str(key):
            h = (h * 131 + ord(c)) & 0xFFFFFFFF
        return pool[h % len(pool)]
    i = _ISP_RR[0] % len(pool)
    _ISP_RR[0] += 1
    return pool[i]


def isp_proxies(key=None):
    """requests-style {'http':…,'https':…} for one ISP IP, or None. Pass to requests/curl_cffi(proxies=…)."""
    p = isp_url(key)
    return {"http": p, "https": p} if p else None


def best_url(key=None):
    """The preferred proxy for bandwidth-heavy fetches: an ISP IP (flat-rate, unlimited) if a pool is set,
    else the rotating residential session URL. Lets scrapers say `resi.best_url(tag)` and get the cheap path."""
    return isp_url(key) if isp_enabled() else _session_url(key if key is not None else "rot")


def exit_ip(timeout=25):
    """Fetch the exit IP + geo THROUGH the proxy (via ipapi.co) — the verification probe. Returns a dict
    or raises. Direct (no proxy) if unconfigured, so you can compare against your home IP."""
    import json
    op = opener()
    o = op.open if op else urllib.request.urlopen
    body = o(urllib.request.Request("https://ipapi.co/json/",
                                    headers={"User-Agent": "curl/8"}), timeout=timeout).read()
    return json.loads(body.decode("utf-8", "replace"))


def _load_env_file():
    """Load warehouse.env (same file kroger_api._load_creds reads) so `python resi.py` sees RESI_PROXY_*
    without exporting anything. Only sets keys not already in the environment."""
    for p in [os.environ.get("WH_ENV_FILE", ""),
              os.path.expanduser("~/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/warehouse.env"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "warehouse.env")]:
        if p and os.path.exists(p):
            for line in open(p, encoding="utf-8", errors="replace"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and v and not os.environ.get(k):
                        os.environ[k] = v
            break


if __name__ == "__main__":
    _load_env_file()
    print("proxy configured:", enabled(), "| host:", parts()[2] or "—")
    if enabled():
        try:
            home = None
            try:
                import json
                home = json.loads(urllib.request.urlopen("https://ipapi.co/json/", timeout=20)
                                  .read().decode())["ip"]
            except Exception:
                pass
            info = exit_ip()
            print("  exit IP :", info.get("ip"), "(%s, %s %s)" % (
                info.get("org", "?"), info.get("city", "?"), info.get("region", "?")))
            print("  home IP :", home or "(couldn't fetch)")
            print("  VERDICT :", "✅ routing through proxy — different IP"
                  if home and info.get("ip") != home else
                  "⚠️  exit IP == home IP — proxy NOT engaged" if home else
                  "exit IP fetched via proxy (couldn't compare to home)")
        except Exception as e:
            print("  ERROR through proxy:", str(e)[:200])
    else:
        print("  set RESI_PROXY (or RESI_PROXY_USER/PASS/HOST/PORT) and re-run.")
