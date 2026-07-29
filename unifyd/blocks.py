#!/usr/bin/env python3
"""blocks.py — say WHY a fetch failed, per source and per method.

WHAT THIS FIXES. Every failure collapsed into one counter, `unreachable`, which lumped together a
throttled shell, a closed store, a timeout, and an undecodable id. With one number covering four causes,
four different models of the UberEats limit got fitted in a single day and three were wrong — per-IP
concurrency, aggregate request rate, and a "25% dead-store background" that turned out to be mild
throttling. Each was disproved only by the next run, because nothing recorded which failure was which.

The adaptation loop can only be as fast as this classifier. A router that escalates without it is
guessing, and a controller fed a blended signal regulates against the wrong thing — which is happening
live: the pacer treats a permanently-closed store exactly like a block, so it backs off from a condition
no amount of slowing down can improve.

THE DISTINCTION THAT MATTERS MOST is `not_found` vs `soft_block`. Both look like "no data":

  * not_found  — the store is gone. Permanent, cheap, and NOT our fault. Never a throttle signal, and a
                 candidate for the dead-store skip list.
  * soft_block — a 200 with the payload hollowed out. This is the expensive one: it looks like success
                 to anything that only checks the status code, and it is exactly how a throttled sweep
                 marked 242,503 stores covered while landing data for 10,040.

Classification is deliberately conservative: anything unrecognised is `unknown`, never `ok`. An
unclassified failure should show up as a gap in our understanding, not get quietly absorbed into a
healthy bucket.
"""
import re
import threading

# Ordered by how strongly they should influence a rate controller.
OK = "ok"                       # real payload
EMPTY = "empty"                 # answered, structure present, genuinely nothing in it — still COVERED
NOT_FOUND = "not_found"         # store gone/delisted — permanent, not a block
SOFT_BLOCK = "soft_block"       # 200 with the payload stripped — the dangerous one
HTTP_BLOCK = "http_block"       # 403/401 — an explicit refusal
RATE_LIMITED = "rate_limited"   # 429 / Retry-After — they are telling us the rate
CAPTCHA = "captcha"             # interstitial challenge
SERVER = "server"               # 5xx — their problem, usually transient
TIMEOUT = "timeout"
NETWORK = "network"             # DNS/TLS/connection reset
UNKNOWN = "unknown"

CLASSES = (OK, EMPTY, NOT_FOUND, SOFT_BLOCK, HTTP_BLOCK, RATE_LIMITED, CAPTCHA, SERVER, TIMEOUT,
           NETWORK, UNKNOWN)

# Classes that mean "we are being pushed back" — the ONLY ones a rate controller should react to.
# not_found and empty are properties of the target's catalogue, not of our request rate: backing off
# because stores are closed is how a healthy run throttles itself to a standstill.
THROTTLE_SIGNALS = frozenset((SOFT_BLOCK, HTTP_BLOCK, RATE_LIMITED, CAPTCHA))
# Classes worth retrying later. A block is retryable (conditions change); a missing store is not.
RETRYABLE = frozenset((SOFT_BLOCK, HTTP_BLOCK, RATE_LIMITED, CAPTCHA, SERVER, TIMEOUT, NETWORK, UNKNOWN))

_CAPTCHA_PAT = re.compile(
    r"px-captcha|perimeterx|_px[A-Za-z]*Challenge|datadome|cf-chl|challenge-platform|"
    r"recaptcha|hcaptcha|are you a human|verify you are human", re.I)
_BLOCK_PAT = re.compile(r"access denied|forbidden|blocked|unusual traffic|bot detect", re.I)


def classify(status=None, body=None, exc=None, has_payload=None, has_items=None):
    """Name the failure. `has_payload` is the caller's structural check (e.g. ue_catalog.has_catalog):
    did the response carry the shape we asked for? `has_items` distinguishes a real-but-empty result."""
    if exc is not None:
        e = ("%s %s" % (type(exc).__name__, exc)).lower()
        if "timeout" in e or "timed out" in e:
            return TIMEOUT
        if any(t in e for t in ("connection", "resolve", "dns", "ssl", "tls", "reset", "closed")):
            return NETWORK
        return UNKNOWN

    text = body if isinstance(body, str) else ""
    if text and _CAPTCHA_PAT.search(text[:4000]):
        return CAPTCHA

    if status is not None:
        s = int(status)
        if s == 429:
            return RATE_LIMITED
        if s in (401, 403):
            # A challenge often arrives dressed as a 403 — check the body before calling it a refusal.
            return CAPTCHA if (text and _CAPTCHA_PAT.search(text[:4000])) else HTTP_BLOCK
        if s in (404, 410):
            return NOT_FOUND
        if s >= 500:
            return SERVER
        if s != 200 and s >= 400:
            return HTTP_BLOCK

    # 200-shaped from here down.
    if has_payload is False:
        # A 200 with no structure. If the body names a block, say so; otherwise it is a SOFT block —
        # the failure mode that looks like success to a status-code check.
        if text and _BLOCK_PAT.search(text[:4000]):
            return HTTP_BLOCK
        return SOFT_BLOCK
    if has_payload is True:
        return EMPTY if has_items is False else OK
    return UNKNOWN                       # never fall through to OK


def is_throttle(cls):
    """Should a rate controller react to this? Only real pushback — never a closed store."""
    return cls in THROTTLE_SIGNALS


class Tally:
    """Per-(method, class) counts for one run. Method is the ladder rung a request went out on —
    direct / isp / browser / resi — so success rates are attributable to a METHOD, which is what an
    escalation router needs to decide anything."""

    __slots__ = ("_c", "_e", "_lock")

    def __init__(self):
        self._c = {}
        self._e = {}                      # (exit_ip, class) — attribution to an INDIVIDUAL identity
        self._lock = threading.Lock()

    def record(self, cls, method="direct", exit=None):
        """`exit` is the exit IP this request went out on. Per-METHOD tells you which rung of the ladder
        is healthy; per-EXIT tells you which identities are spent — the difference between "the ISP pool
        is failing" and "these six IPs are burned and the rest are fine". Without it, a pool half made of
        exhausted identities reads as a uniformly degraded pool, and the fix looks like "buy a different
        product" rather than "retire six addresses"."""
        with self._lock:
            k = (method, cls)
            self._c[k] = self._c.get(k, 0) + 1
            if exit:
                ke = (exit, cls)
                self._e[ke] = self._e.get(ke, 0) + 1
        return cls

    def by_exit(self):
        """Per-IP success rate: {exit: {'good': n, 'total': n, 'pct': f}}. This is what retires a hot
        identity, and what makes a burned-vs-fresh comparison readable."""
        with self._lock:
            out = {}
            for (ip, cls), n in self._e.items():
                d = out.setdefault(ip, {"good": 0, "total": 0})
                d["total"] += n
                if cls in (OK, EMPTY):
                    d["good"] += n
            for ip, d in out.items():
                d["pct"] = round(100.0 * d["good"] / max(1, d["total"]), 1)
            return out

    def counts(self):
        with self._lock:
            return dict(self._c)

    def flat(self):
        """Journal-friendly: {'isp.soft_block': 12, ...} plus a per-method success rate."""
        out = {}
        by_method = {}
        with self._lock:
            for (method, cls), n in self._c.items():
                out["%s.%s" % (method, cls)] = n
                m = by_method.setdefault(method, {"good": 0, "total": 0})
                m["total"] += n
                if cls in (OK, EMPTY):
                    m["good"] += n
        for method, m in by_method.items():
            out["%s.success_pct" % method] = round(100.0 * m["good"] / max(1, m["total"]), 1)
        return out

    def summary(self):
        c = self.counts()
        tot = sum(c.values()) or 1
        parts = sorted(((n, "%s/%s" % (mth, cls)) for (mth, cls), n in c.items()), reverse=True)
        return " ".join("%s=%d(%.0f%%)" % (name, n, 100.0 * n / tot) for n, name in parts[:6])


_GLOBAL = {"tally": None}


def install():
    _GLOBAL["tally"] = Tally()
    return _GLOBAL["tally"]


def get():
    return _GLOBAL["tally"]
