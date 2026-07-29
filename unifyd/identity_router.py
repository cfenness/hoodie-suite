#!/usr/bin/env python3
"""identity_router.py — pick the (exit, costume) for the NEXT session from what's healthy RIGHT NOW.

WHAT THIS FIXES. Measured 2026-07-29 (COLLECT-HANDOFF.md): exit health and TLS-costume health are both
volatile on an hour timescale, in either direction, for a cause not yet identified — and separately,
piling many concurrent sessions onto ONE exit burns it out in *minutes* (10 workers on 1 IP: 20% usable
collapsing to 0% within 3 minutes; the same 10 workers spread over 5 IPs: 67% usable). A round-robin
counter and a single process-wide costume — what `_session()` used before this — cannot react to either
fact: it keeps sending fresh sessions to an exit that just proved bad, and it never notices a currently-
clean costume sitting unused. A cached "good identity" list is provably stale within the hour it would
take to build one; this exists to stop caching that decision at all and make it fresh on every pick.

THE ARCHITECTURE, and why each half is separate. Two different risks showed up in the data and they
have different shapes:

  CONCENTRATION (fast, ours to control) — many simultaneous sessions on one IP is itself a bad signal,
  independent of TLS costume: `_hot()` counts recent per-EXIT activity (not per-pair) in a short window
  and pick() avoids adding load to an exit that already has plenty. This is deliberately about the bare
  IP, not the (exit, costume) pair, because the concentration experiment showed the SAME exit degrade
  under load regardless of which costume was riding it.

  HEALTH (slow, ours to route around) — a (exit, costume) pair's recent success rate, smoothed so an
  unsampled pair isn't mistaken for a bad one, plus an exploration bonus so a pair that hasn't been
  tried in a while gets re-probed rather than permanently written off. Health can recover (the fire-map
  survivors flipped in both directions within an hour), so nothing here is a permanent blacklist.

WHY NO ACQUIRE/RELEASE. An earlier draft tracked true in-flight concurrency with an incrementing counter
that a caller had to decrement on session retire. That leaks the moment a thread dies without releasing
— exactly the kind of bookkeeping-outranks-evidence bug this project's other controllers were built to
avoid. Recency-windowed timestamps self-heal: a dead thread just stops adding timestamps and the window
empties on its own, no explicit cleanup required.

ONLY A CLASSIFIED THROTTLE QUARANTINES A PAIR, never `not_found`/`timeout`/`unknown` — the same rule
`ladder`/`sessions`/`pace` already enforce, for the same reason: reacting to background noise is how a
healthy fleet talks itself into a standstill.

NEVER CONSULTED FOR A PINNED THREAD. `getstore._session()` only asks the router in its normal
round-robin branch; a caller that pinned an exit for measurement (`getstore.pin_exit`) bypasses this
entirely, by construction — a controlled experiment must not have its identity silently swapped out
from under it.

    px, costume = identity_router.pick(pool, costumes)   # before priming a new session
    identity_router.record(exit_ip, costume, cls)        # after every classified outcome
"""
import math
import threading
import time

RECENT_MAXLEN = 20            # outcomes remembered per (exit, costume) pair
HOT_WINDOW_S = 15.0            # how recent counts as "currently busy" for one exit
HOT_MAX = 2                    # requests-in-window before a fresh pick avoids adding to this exit
PRIOR_GOOD, PRIOR_TOTAL = 1.0, 2.0   # Beta-smoothing prior: an untried pair scores neutral, not 0 or 1
EXPLORE_C = 0.6                # exploration weight — 0 disables exploration, pure exploitation
QUARANTINE_STREAK = 3          # consecutive throttle outcomes before a pair is quarantined
QUARANTINE_BASE_S = 60.0
QUARANTINE_MAX_S = 900.0       # capped, same reasoning as pace.py's rate floor: don't calibrate into an outage


class _Pair(object):
    __slots__ = ("recent", "streak", "quarantined_until", "quarantine_s", "picks")

    def __init__(self):
        self.recent = []              # list of (ts, good: bool), capped to RECENT_MAXLEN
        self.streak = 0                # consecutive throttle outcomes, resets on any non-throttle
        self.quarantined_until = 0.0
        self.quarantine_s = QUARANTINE_BASE_S
        self.picks = 0


class Router(object):
    def __init__(self):
        self._pairs = {}               # (exit, costume) -> _Pair
        self._exit_ts = {}             # exit -> list of recent timestamps (any costume)
        self._lock = threading.Lock()
        self._total_picks = 0

    def reset(self):
        with self._lock:
            self._pairs.clear()
            self._exit_ts.clear()
            self._total_picks = 0

    def record(self, exit_ip, costume, cls, now=None):
        """Feed one classified outcome. `cls` is a `blocks` class name."""
        if not exit_ip:
            return
        now = time.time() if now is None else now
        import blocks
        good = cls in (blocks.OK, blocks.EMPTY)
        throttled = blocks.is_throttle(cls)
        with self._lock:
            ts = self._exit_ts.setdefault(exit_ip, [])
            ts.append(now)
            if len(ts) > 4 * HOT_MAX + 8:          # bounded; hot() only ever looks at a short window
                del ts[: len(ts) // 2]

            p = self._pairs.setdefault((exit_ip, costume), _Pair())
            p.recent.append((now, good))
            if len(p.recent) > RECENT_MAXLEN:
                p.recent.pop(0)
            if throttled:
                p.streak += 1
                if p.streak >= QUARANTINE_STREAK:
                    p.quarantined_until = now + p.quarantine_s
                    p.quarantine_s = min(QUARANTINE_MAX_S, p.quarantine_s * 2)
                    p.streak = 0
            elif good:
                p.streak = 0
                p.quarantine_s = QUARANTINE_BASE_S    # a real success earns back the fast cooldown

    def _hot(self, exit_ip, now):
        ts = self._exit_ts.get(exit_ip)
        if not ts:
            return 0
        return sum(1 for t in ts if now - t <= HOT_WINDOW_S)

    def _score(self, key, now):
        # UCB-style: exploit the smoothed success rate, explore proportional to 1/sqrt(evidence+1).
        # The exploration term is keyed on EVIDENCE (len(recent)), not on how many times pick() chose
        # this pair — record() fires once per REQUEST and pick() once per SESSION (which can carry many
        # requests), so `picks` under-counts how much is actually known about a pair. An early version
        # used `picks` here and a pair with a dozen recorded captchas but zero picks scored HIGHER than
        # a pair with no data at all — the bonus was rewarding "never routed to" instead of "no evidence".
        p = self._pairs.get(key)
        n = len(p.recent) if p is not None else 0
        good = sum(1 for _t, g in p.recent if g) if p is not None else 0
        smoothed = (good + PRIOR_GOOD) / (n + PRIOR_TOTAL)
        bonus = EXPLORE_C * math.sqrt(math.log(self._total_picks + 2) / (n + 1))
        return smoothed + bonus, n

    def pick(self, pool, costumes, now=None):
        """Choose (exit_entry, costume) for a new session prime. `pool` is a list of raw proxy entries
        (same shape as `resi.isp_pool()`); `costumes` a list of candidate TLS profile names. Returns
        (None, None) if either is empty. Never returns nothing when data exists — a fully-quarantined
        or fully-hot pool still returns its LEAST-bad option rather than deadlocking a fleet."""
        if not pool or not costumes:
            return None, None
        now = time.time() if now is None else now
        with self._lock:
            entries = {(e.split("@")[-1].split(":")[0]): e for e in pool}
            candidates = [(ip, c) for ip in entries for c in costumes]

            def usable(key):
                ip, _c = key
                p = self._pairs.get(key)
                if p is not None and p.quarantined_until > now:
                    return False
                return self._hot(ip, now) < HOT_MAX

            pool_ok = [k for k in candidates if usable(k)]
            search = pool_ok if pool_ok else candidates   # never deadlock: degrade to "least bad"

            best_key, best_score = None, None
            for key in search:
                score, _n = self._score(key, now)
                if best_score is None or score > best_score:
                    best_key, best_score = key, score
                elif score == best_score and best_key is not None:
                    # Deterministic tie-break: prefer the exit touched least recently overall, then
                    # stable dict order — no randomness, so this stays reproducible under test.
                    if self._hot(key[0], now) < self._hot(best_key[0], now):
                        best_key = key
            ip, costume = best_key
            self._pairs.setdefault(best_key, _Pair()).picks += 1
            self._total_picks += 1
            return entries[ip], costume

    def stats(self, top=8):
        with self._lock:
            now = time.time()
            rows = []
            for (ip, costume), p in self._pairs.items():
                n = len(p.recent)
                good = sum(1 for _t, g in p.recent if g)
                rows.append({"exit": ip, "costume": costume, "n": n,
                            "pct": round(100.0 * good / n, 1) if n else None,
                            "quarantined": p.quarantined_until > now, "picks": p.picks})
            rows.sort(key=lambda r: (r["pct"] is None, r["pct"] if r["pct"] is not None else 0))
            return {"pairs_tracked": len(self._pairs), "total_picks": self._total_picks,
                    "quarantined": sum(1 for r in rows if r["quarantined"]),
                    "worst": rows[:top], "best": rows[-top:][::-1] if rows else []}


_GLOBAL = {"router": Router()}


def get():
    return _GLOBAL["router"]


def record(exit_ip, costume, cls, now=None):
    get().record(exit_ip, costume, cls, now=now)


def pick(pool, costumes, now=None):
    return get().pick(pool, costumes, now=now)


def stats():
    return get().stats()


def reset():
    get().reset()
