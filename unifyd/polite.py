"""polite.py — the shared "don't get banned" HTTP layer for every scraper.

Ban-avoidance is mostly discipline, not tricks. This centralizes the disciplined behavior so no scraper
has to re-implement it, and so we behave consistently across sources:

  1. PER-HOST RATE LIMITING + JITTER — a minimum gap between requests to a host, randomized so we don't
     emit a robotic fixed-interval pattern.
  2. EXPONENTIAL BACKOFF on 429 / 503 / 500 / 502 (and 403) — honor Retry-After when present.
  3. CIRCUIT BREAKER — the single most important control: once a host has blocked us N times in a row,
     STOP hitting it and cool down. A soft-block you keep hammering becomes a hard IP/account ban; backing
     off immediately is what keeps a bad afternoon from becoming a permanent ban.
  4. REALISTIC, ROTATING HEADERS — a small pool of real browser UAs + normal Accept-* headers.
  5. OPTIONAL PROXY — route a host through Bright Data (rotating residential IPs) so an IP block is
     ineffective. Off by default; opt in per host via use_proxy=True (needs BRIGHTDATA_* env).

Usage:
    import polite
    body = polite.get(url, min_interval=8, jitter=4, breaker_after=4)   # bytes, or raises Blocked
    # honor a source's own crawl-delay by setting min_interval to it.

Design notes: thread-safe (per-host lock), so the concurrent scrapers (ABC/Spec's) share one rate limiter
per host across their worker threads. A tripped breaker raises Blocked — callers should let the run END
(degraded) rather than swallow it, so we never grind into a ban.
"""
import gzip
import os
import random
import ssl
import threading
import zlib
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

# a few real, current desktop UAs — rotated per request
_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# retryable "you're going too fast / temporarily blocked" statuses
_RETRY_CODES = {429, 500, 502, 503, 504}
_BLOCK_CODES = {403, 429, 503}     # count toward the circuit breaker


class Blocked(Exception):
    """Raised when a host is blocking us and we've decided to STOP (breaker tripped / retries exhausted)."""


class _Host:
    __slots__ = ("last", "fails", "open_until", "lock")

    def __init__(self):
        self.last = 0.0          # last request time
        self.fails = 0           # consecutive block/throttle count
        self.open_until = 0.0    # circuit-breaker cool-down expiry
        self.lock = threading.Lock()


_HOSTS = {}
_HOSTS_LOCK = threading.Lock()


def _host_state(host):
    with _HOSTS_LOCK:
        if host not in _HOSTS:
            _HOSTS[host] = _Host()
        return _HOSTS[host]


def _headers(extra=None):
    h = {"User-Agent": random.choice(_UAS),
         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
         "Accept-Language": "en-US,en;q=0.9",
         "Accept-Encoding": "gzip, deflate",
         "Connection": "keep-alive"}
    if extra:
        h.update(extra)
    return h


def _proxy_opener():
    """A urllib opener routed through the Bright Data residential/unlocker proxy (rotating IPs + anti-bot).
    The Unlocker MITMs HTTPS to solve challenges, so its cert won't chain to a public CA — we skip cert
    verification on the proxied path (standard for the BD Unlocker proxy interface)."""
    user = os.environ.get("BRIGHTDATA_PROXY_USER") or os.environ.get("BRD_PROXY_USER")
    pw = os.environ.get("BRIGHTDATA_PROXY_PASS") or os.environ.get("BRD_PROXY_PASS")
    hostport = os.environ.get("BRIGHTDATA_PROXY", "brd.superproxy.io:22225")
    if not (user and pw):
        return None
    p = "http://%s:%s@%s" % (user, pw, hostport)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}),
                                       urllib.request.HTTPSHandler(context=ctx))


def get(url, *, min_interval=5.0, jitter=3.0, timeout=30, headers=None, data=None, method=None,
        max_retries=4, breaker_after=5, breaker_cooldown=600, use_proxy=False, host=None,
        return_headers=False):
    """Polite GET/POST returning the response body (bytes), or (body, headers_dict) when return_headers.
    Raises Blocked when the host is blocking us and we've decided to stop. `min_interval` is the minimum
    seconds between requests to this host (set it to the source's crawl-delay); requests are spaced by
    min_interval + random jitter."""
    host = host or urlparse(url).netloc
    st = _host_state(host)

    now = time.time()
    if now < st.open_until:
        raise Blocked("circuit breaker OPEN for %s — cooling down %.0fs more"
                      % (host, st.open_until - now))

    opener = _proxy_opener() if use_proxy else None

    for attempt in range(max_retries + 1):
        # pace: hold the per-host lock only to reserve the slot, then sleep outside it
        with st.lock:
            gap = st.last + min_interval + random.uniform(0, jitter) - time.time()
            st.last = time.time() + max(0.0, gap)
        if gap > 0:
            time.sleep(gap)

        req = urllib.request.Request(url, data=data, headers=_headers(headers), method=method)
        try:
            resp = opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)
            body = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                body = gzip.decompress(body)
            elif enc == "deflate":
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    body = zlib.decompress(body, -zlib.MAX_WBITS)   # raw deflate
            with st.lock:
                st.fails = 0
            return (body, dict(resp.headers)) if return_headers else body
        except urllib.error.HTTPError as e:
            if e.code in _BLOCK_CODES or e.code in _RETRY_CODES:
                with st.lock:
                    st.fails += 1
                    tripped = st.fails >= breaker_after
                    if tripped:
                        st.open_until = time.time() + breaker_cooldown
                if tripped:
                    raise Blocked("%s blocked us (%d) %d× in a row — TRIPPING breaker, backing off %ds"
                                  % (host, e.code, st.fails, breaker_cooldown))
                backoff = min(90.0, 2.0 ** attempt + random.uniform(0, jitter * 2))
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and str(ra).isdigit():
                    backoff = max(backoff, float(ra))
                time.sleep(backoff)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt >= max_retries:
                raise
            time.sleep(min(60.0, 2.0 ** attempt) + random.uniform(0, jitter))
    raise Blocked("%s: exhausted %d retries" % (host, max_retries))


def status():
    """Snapshot of per-host health — for a monitor/alert (fails + whether the breaker is open)."""
    now = time.time()
    return {h: {"consecutive_fails": s.fails, "breaker_open": now < s.open_until,
                "cooldown_s": max(0, int(s.open_until - now))} for h, s in _HOSTS.items()}
