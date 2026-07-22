"""browser_warm.py — self-hosted headless-browser session: warm anti-bot cookies AND fetch through the browser.

The point: get OFF Bright Data AND off the manual "paste a warmed cookie" step, in one tool. BD sold two things —
a real browser (to run the PerimeterX / Incapsula / Akamai / Forter JS challenge) and residential IPs (so the
request isn't blocked for coming from a datacenter). Here the browser is our own Chromium and the residential IP
is the Mac's own connection, so both are covered with zero BD spend and zero human step.

Two ways to use the warmed session, pick by how the site binds its token:
  • cookie_header()  — hand the trusted cookies + the SAME user-agent to a requests.Session. Works when the
    token is NOT bound to the browser's TLS fingerprint (Incapsula reese84, most Akamai).
  • fetch() / fetch_json() — run the fetch INSIDE the page, so it inherits the real TLS fingerprint + the
    auto-refreshing cookie. Robust for PerimeterX (_px3 is fingerprint-bound; a requests.Session has a different
    fingerprint and gets re-challenged in ~90s). This is the method that cracked Walmart with no BD.

Persistent profile per domain (user_data_dir) so the trusted cookie + fingerprint SURVIVE across daily runs →
the site increasingly trusts the returning profile and challenges less. Requires `playwright` + its chromium
(`pip install playwright && playwright install chromium`). Headless "new" by default; BROWSER_HEADFUL=1 shows a
window (sometimes needed for the toughest fingerprinting). NOT a silver bullet for every anti-bot — the hardest
(Forter/DoorDash, some Akamai) may still need a residential *browser* on the Mac; test per target with probe().
"""
import json
import os
import re

# One realistic desktop UA, reused for BOTH the browser and any requests.Session it hands cookies to — an anti-bot
# that binds the cookie to the UA rejects a mismatch, so they MUST match.
UA = os.environ.get("BROWSER_UA",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36")
_PROFILE_ROOT = os.environ.get("BROWSER_PROFILE_DIR", os.path.expanduser("~/.hoodie_browser_profiles"))

# Minimal stealth: undo the tells a vanilla automated Chromium leaks. (No external stealth lib — these are the
# high-value patches; the persistent real-Chromium profile does most of the trust-building.)
_STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""


def _parse_proxy(p):
    """Normalize a proxy to Playwright's dict form. Accepts a dict, a 'http://user:pass@host:port' URL, or
    'host:port'. Returns None when unset. Provider-agnostic — this is NOT Bright Data-specific."""
    if not p:
        return None
    if isinstance(p, dict):
        return p
    m = re.match(r"^(?:(\w+)://)?(?:([^:@/]+):([^@/]+)@)?([^:/@]+:\d+)$", str(p).strip())
    if not m:
        return {"server": str(p)}
    scheme, user, pw, hostport = m.groups()
    out = {"server": "%s://%s" % (scheme or "http", hostport)}
    if user:
        out["username"] = user; out["password"] = pw
    return out


class Warmer:
    """A persistent Chromium session for one domain. Use as a context manager:

        with Warmer("walmart.com") as w:
            w.prime("https://www.walmart.com/")
            status, html = w.fetch("https://www.walmart.com/search?q=bourbon")
    """

    def __init__(self, domain, headful=None, ua=UA, channel=None, proxy=None, patchright=None):
        self.domain = domain
        self.ua = ua
        self.headful = (os.environ.get("BROWSER_HEADFUL", "0") == "1") if headful is None else headful
        # patchright = patched Playwright that removes the CDP `Runtime.enable` automation leak. Needed for the
        # tougher gates (Incapsula/7NOW serves a BLANK shell to plain Playwright; patchright boots the real app).
        self.patchright = (os.environ.get("BROWSER_PATCHRIGHT", "0") == "1") if patchright is None else patchright
        # channel="chrome" uses the REAL installed Google Chrome (legit fingerprint + real GPU) instead of
        # Playwright's bundled Chromium — the difference that clears reCAPTCHA-Enterprise scoring on tough sites.
        self.channel = channel or (os.environ.get("BROWSER_CHANNEL") or None)
        # CLOUD path: run headful under Xvfb (`xvfb-run python …`) on a Linux box with real google-chrome-stable.
        # `proxy` is a GENERIC hook (any provider — never BD-specific): a dict {server,username,password} or a
        # "http://user:pass@host:port" URL via BROWSER_PROXY, only needed if a datacenter IP gets scored down.
        self.proxy = _parse_proxy(proxy or os.environ.get("BROWSER_PROXY"))
        self._pw = None
        self._ctx = None

    def __enter__(self):
        if self.patchright:
            from patchright.sync_api import sync_playwright
        else:
            from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        suffix = ("_patchright" if self.patchright else "_chrome" if self.channel == "chrome" else "")
        suffix += os.environ.get("BROWSER_PROFILE_SUFFIX", "")   # distinct profile per parallel worker (sharded crawls)
        prof = os.path.join(_PROFILE_ROOT, self.domain.replace(".", "_") + suffix)
        os.makedirs(prof, exist_ok=True)
        if self.patchright:
            # patchright handles stealth itself — DON'T override UA/viewport/args or add init scripts (they undo it)
            kw = dict(channel=self.channel or "chrome", headless=(not self.headful), no_viewport=True)
            if self.proxy:
                kw["proxy"] = self.proxy
            self._ctx = self._pw.chromium.launch_persistent_context(prof, **kw)
        else:
            kw = dict(headless=(not self.headful), channel=self.channel, user_agent=self.ua,
                      viewport={"width": 1300, "height": 1400}, locale="en-US",
                      args=["--disable-blink-features=AutomationControlled", "--no-first-run"])
            if self.proxy:
                kw["proxy"] = self.proxy
            self._ctx = self._pw.chromium.launch_persistent_context(prof, **kw)
            self._ctx.add_init_script(_STEALTH)
        return self

    def human(self, moves=5):
        """Generate human-ish signals (mouse movement + gentle scroll) so behavioral bot-scoring (reCAPTCHA
        Enterprise) reads the session as a person. Index-seeded, deterministic (Math.random is unavailable)."""
        p = self._page()
        for i in range(moves):
            p.mouse.move(200 + (i * 173) % 900, 200 + (i * 271) % 700)
            p.wait_for_timeout(350)
        p.mouse.wheel(0, 2000); p.wait_for_timeout(1200)

    def click_through(self, href_substr, wait_ms=11000):
        """Human path past the store-level wall: from the current (feed) page, find the anchor whose href
        contains `href_substr`, scroll it into view, move to it, and issue a TRUSTED click (SPA nav). Deep-linking
        the store URL trips the security check; a real click from the feed does not. Returns True if clicked."""
        p = self._page()
        link = p.query_selector('a[href*="%s"]' % href_substr)
        if not link:
            return False
        link.scroll_into_view_if_needed(); p.wait_for_timeout(500)
        box = link.bounding_box()
        if box:
            p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2); p.wait_for_timeout(400)
        link.click()
        p.wait_for_timeout(wait_ms)
        return True

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _page(self):
        return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def prime(self, url, settle_ms=2500, challenge_gone=None, tries=20):
        """Load `url` so the anti-bot JS runs and mints the trusted cookie. If `challenge_gone` (a JS expression
        that returns true once the real page is up) is given, poll until it clears. Returns the page."""
        p = self._page()
        p.goto(url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(settle_ms)
        if challenge_gone:
            for _ in range(tries):
                try:
                    if p.evaluate(challenge_gone):
                        break
                except Exception:
                    pass
                p.wait_for_timeout(1000)
        return p

    def fetch(self, url, as_json=False, timeout_ms=45000):
        """In-page fetch — inherits the trusted session + real TLS fingerprint. Returns (status, text) or
        (status, obj) with as_json. The robust path for PerimeterX-style fingerprint-bound tokens."""
        p = self._page()
        p.set_default_timeout(timeout_ms)
        res = p.evaluate(
            """async (u) => { try { const r = await fetch(u, {credentials:'include'});
                 return {status:r.status, body: await r.text()}; }
               catch(e) { return {status:0, body: String(e)}; } }""", url)
        if as_json:
            try:
                return res["status"], json.loads(res["body"])
            except Exception:
                return res["status"], None
        return res["status"], res["body"]

    def fetch_json(self, url, timeout_ms=45000):
        return self.fetch(url, as_json=True, timeout_ms=timeout_ms)

    def post_json(self, url, body, headers=None, timeout_ms=45000):
        """In-page POST — inherits the trusted session + real TLS fingerprint (same as fetch, for POST BFFs like
        Uber's getMenuItemV1 that need a JSON body + CSRF header). `body` is a dict, `headers` a dict of extra
        request headers to merge. Returns (status, obj|None)."""
        p = self._page()
        p.set_default_timeout(timeout_ms)
        hdrs = {"content-type": "application/json", "x-csrf-token": "x"}
        hdrs.update(headers or {})
        res = p.evaluate(
            """async ({u, b, h}) => { try {
                 const r = await fetch(u, {method:'POST', credentials:'include', headers:h, body:JSON.stringify(b)});
                 return {status:r.status, body: await r.text()}; }
               catch(e) { return {status:0, body:String(e)}; } }""",
            {"u": url, "b": body, "h": hdrs})
        try:
            return res["status"], json.loads(res["body"])
        except Exception:
            return res["status"], None

    def cookie_header(self, name_filter=None):
        """Trusted cookies as a `Cookie:` header string, for handing to a requests.Session (pair with UA).
        `name_filter` = iterable of cookie names to keep (e.g. the PX/Incapsula tokens)."""
        out = []
        for c in self._ctx.cookies():
            if name_filter and c["name"] not in name_filter:
                continue
            out.append("%s=%s" % (c["name"], c["value"]))
        return "; ".join(out)

    def cookies(self):
        return self._ctx.cookies()


def warm_cookie(domain, url, name_filter=None, challenge_gone=None, settle_ms=3000, log=None, **kw):
    """One-call 'give me a FRESH cookie': open a warmed session for `domain`, load `url` so the anti-bot
    JS mints the trusted cookie, and return it as a `Cookie:` header string (optionally filtered to
    `name_filter`). This is the path for scrapers that REPLAY the cookie through requests/urllib — an
    Incapsula/Imperva-style session token that isn't bound to the TLS fingerprint (Kroger's atlas), as
    opposed to the fingerprint-bound PerimeterX case that must fetch in-page. `**kw` (channel, proxy,
    headful, patchright) pass through to Warmer. Returns '' if a browser isn't available or warming
    fails, so the caller can fall back to a configured cookie — warming is a strict upgrade, never a
    hard dependency."""
    try:
        with Warmer(domain, **kw) as w:
            w.prime(url, settle_ms=settle_ms, challenge_gone=challenge_gone)
            ck = w.cookie_header(name_filter)
        if log:
            log("[browser_warm] %s cookie warmed (%d chars)" % (domain, len(ck)) if ck
                else "[browser_warm] %s warmed but no cookies captured" % domain)
        return ck
    except Exception as e:
        if log:
            log("[browser_warm] %s warm failed (%s) — caller falls back to a configured cookie"
                % (domain, str(e)[:70]))
        return ""


def probe(domain, url, blocked_re=r"Robot or human|captcha|Access Denied|Incapsula|/_Incapsula_|Pardon the",
          challenge_gone=None):
    """Diagnostic: can this warmed browser reach a real page for `url`? Returns a dict {status, len, blocked}.
    Use to decide, per target, whether the headless warmer is enough or a headful/residential fallback is needed."""
    import re
    with Warmer(domain) as w:
        w.prime(url, challenge_gone=challenge_gone)
        status, body = w.fetch(url)
        return {"status": status, "len": len(body or ""),
                "blocked": bool(re.search(blocked_re, body or "", re.I))}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Probe the self-hosted browser warmer against a site.")
    ap.add_argument("domain")
    ap.add_argument("url")
    a = ap.parse_args()
    print(json.dumps(probe(a.domain, a.url), indent=2))
