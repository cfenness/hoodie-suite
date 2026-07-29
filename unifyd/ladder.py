#!/usr/bin/env python3
"""ladder.py — when a rung closes, take the next one. Automatically, and remember it.

THE FAILURE THIS EXISTS FOR, in full. On 2026-07-29 UberEats stopped accepting the cold curl_cffi path.
The system had every sense organ and no reflex: `blocks` correctly named the failure (CAPTCHA, 82% then
100%), `pace` correctly backed off, `sessions` correctly rotated — and none of it could do the one thing
that would have helped, which is TRY SOMETHING ELSE. A human spent twelve hours fitting four models to a
blended signal while a known-good browser path sat unused in the same repository.

The lesson is not "we needed better tuning". Every constant we tuned was wrong within a day, because a
rate limit, a session quota and a fingerprint policy all belong to someone else and all move. The only
durable answer is a system that treats its current access method as disposable, notices within minutes
when one stops working, moves to the next, and WRITES DOWN what it learned so the next occurrence costs
nothing.

FOUR PROPERTIES, each earned from a specific failure today:

  ESCALATE ON CLASSIFIED BLOCK, NOT ON FAILURE. `not_found` and `timeout` must never promote a source to
  an expensive rung — that is how a dead-store background talked the pacer into a standstill. Only
  soft_block / http_block / rate_limited / captcha count, the same signal set `sessions` uses.

  PERSIST THE CHOICE. A rung discovered at 3am is worthless if the next process starts from scratch.
  Choices land in the warehouse, so cracking a target once keeps it cracked — the asset that compounds.

  DECAY BACK DOWN. A source promoted to a browser during an outage must retest the cheap rung later, or
  one bad afternoon permanently doubles the cost of that source. Expensive rungs are a loan, not a home.

  NEVER AUTO-ESCALATE INTO SPEND. The ladder stops at the last free rung unless FETCH_POLICY explicitly
  allows more. A system that can promote itself into a metered path will eventually do so at 3am, and
  the first anyone hears of it is the invoice.

    rung = ladder.current("ubereats")          # what to use right now
    ladder.report("ubereats", rung, cls)       # feed the classifier's verdict back
"""
import os
import time

# Cheapest and fastest first. Cost is not only money: a browser is ~10-50x slower than a raw fetch and
# RAM-bound per instance, so climbing the ladder costs throughput long before it costs dollars.
DIRECT = "direct"          # plain HTTP, honest UA
MOBILE = "mobile"          # mobile UA + headers — often a different, laxer policy
IMPERSONATE = "impersonate"  # curl_cffi TLS impersonation (what UberEats just closed)
BROWSER = "browser"        # real Chromium, our own fingerprint work — fixed cost, slow
PAID = "paid"              # metered residential/unlocker — NEVER reached automatically

RUNGS = (DIRECT, MOBILE, IMPERSONATE, BROWSER, PAID)
FREE_RUNGS = (DIRECT, MOBILE, IMPERSONATE, BROWSER)

TABLE = "ladder_state"
FIELDS = ["day", "source", "rung", "ok", "blocked", "chosen_at", "reason"]

# How much evidence before acting. Small enough to react inside a run, large enough that a handful of
# unlucky responses cannot promote a source to a browser.
WINDOW = int(os.environ.get("LADDER_WINDOW", "40"))
BLOCK_FRAC = float(os.environ.get("LADDER_BLOCK_FRAC", "0.5"))
# How long before a promoted source retests the rung below it.
DECAY_S = float(os.environ.get("LADDER_DECAY_S", str(6 * 3600)))
# How many TLS costumes to try before conceding the rung. Bounded so a permanently closed path cannot
# spin through profiles forever instead of escalating.
MAX_COSTUMES = int(os.environ.get("LADDER_MAX_COSTUMES", "3"))


def allowed_rungs():
    """Free rungs always; the metered rung only when explicitly opted in."""
    try:
        import resi
        if resi.paygo_allowed():
            return RUNGS
    except Exception:
        pass
    return FREE_RUNGS


class SourceLadder:
    """One source's position on the ladder, plus the evidence for it."""

    __slots__ = ("source", "rung", "_ok", "_bad", "_since", "_lock", "history", "_costumes")

    def __init__(self, source, rung=None):
        import threading
        self.source = source
        self.rung = rung or DIRECT
        self._ok = self._bad = 0
        self._since = time.time()
        self.history = []
        self._costumes = 0
        self._lock = threading.Lock()

    def report(self, cls):
        """Feed one classified outcome. Returns the rung to use next — usually the current one."""
        try:
            import blocks
            blocked = blocks.is_throttle(cls)
        except Exception:
            blocked = False
        with self._lock:
            if blocked:
                self._bad += 1
            else:
                self._ok += 1
            n = self._ok + self._bad
            if n < WINDOW:
                return self.rung
            frac = self._bad / float(n)
            self._ok = self._bad = 0
            if frac >= BLOCK_FRAC:
                # TRY A NEW COSTUME BEFORE PAYING FOR A BROWSER. Today proved the cheapest possible
                # fix — one TLS profile string — was the entire answer, after a day spent modelling
                # rate limits and IP reputation. A profile rotation costs one string and nothing else;
                # the browser rung costs 10-50x throughput and a Chromium per process. Exhaust the free
                # move first, and only escalate if changing costume does not help either.
                if self.rung == IMPERSONATE and self._costumes < MAX_COSTUMES:
                    self._costumes += 1
                    try:
                        import getstore
                        getstore.rotate_profile(self.source)
                        self.history.append((self.rung, "%s(costume %d)" % (self.rung, self._costumes),
                                             round(frac, 2), int(time.time())))
                        return self.rung
                    except Exception:
                        pass
                nxt = self._next_rung()
                if nxt != self.rung:
                    self.history.append((self.rung, nxt, round(frac, 2), int(time.time())))
                    self.rung, self._since = nxt, time.time()
            elif frac < 0.05 and (time.time() - self._since) > DECAY_S:
                # Healthy for a long stretch on an expensive rung — try to come back down. An outage
                # should not permanently raise a source's cost.
                prev = self._prev_rung()
                if prev != self.rung:
                    self.history.append((self.rung, prev, round(frac, 2), int(time.time())))
                    self.rung, self._since = prev, time.time()
            return self.rung

    def _next_rung(self):
        allow = allowed_rungs()
        i = RUNGS.index(self.rung)
        for r in RUNGS[i + 1:]:
            if r in allow:
                return r
        return self.rung                  # already at the top of what we are permitted to use

    def _prev_rung(self):
        i = RUNGS.index(self.rung)
        return RUNGS[i - 1] if i > 0 else self.rung

    def stats(self):
        with self._lock:
            return {"source": self.source, "rung": self.rung,
                    "held_s": int(time.time() - self._since),
                    "moves": [{"from": a, "to": b, "block_frac": f, "at": t} for a, b, f, t in self.history]}


_L = {}


def current(source, default=None):
    """The rung to use now. Reads a persisted choice on first touch so a restart does not re-learn."""
    if source in _L:
        return _L[source].rung
    rung = default or _load(source) or _declared(source) or IMPERSONATE
    _L[source] = SourceLadder(source, rung)
    return _L[source].rung


def report(source, cls):
    if source not in _L:
        current(source)
    return _L[source].report(cls)


def _declared(source):
    """A registry-declared starting rung — the per-domain playbook entry."""
    try:
        import source_registry
        for s in getattr(source_registry, "SOURCES", []):
            if s.get("id") == source:
                return s.get("rung")
    except Exception:
        pass
    return None


def _load(source):
    """The last rung this source was known to work on. Cracking a target once should keep it cracked."""
    try:
        import warehouse
        rows = warehouse.query_parts(
            TABLE, "SELECT rung FROM t WHERE source = '%s' ORDER BY chosen_at DESC LIMIT 1"
            % source.replace("'", ""))
        return (rows or [{}])[0].get("rung")
    except Exception:
        return None


def persist(source, reason="", log=print):
    """Write the current choice so the next process starts where this one finished."""
    sl = _L.get(source)
    if not sl:
        return 0
    try:
        import warehouse
        warehouse.write_partition(
            TABLE, "%s_%s" % (time.strftime("%Y-%m-%d"), source),
            [{"day": time.strftime("%Y-%m-%d"), "source": source, "rung": sl.rung,
              "ok": sl._ok, "blocked": sl._bad, "chosen_at": int(time.time()), "reason": reason[:200]}],
            fields=FIELDS)
        return 1
    except Exception as e:
        log("  [ladder] not persisted: %s" % str(e)[:90])
        return 0


def reset():
    _L.clear()
