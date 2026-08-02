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

# ── EARNED CONCURRENCY (the warm-up ramp) ────────────────────────────────────────────────────────
# HOT_MAX used to be a flat allowance every exit got for free, including one we had never sent a byte
# to and one that just came off quarantine. The concentration experiment above measured what a cold
# exit costs when you guess wrong (10 workers on 1 IP: 20% usable -> 0% in three minutes), so handing
# full concurrency to an unproven address is paying that price on purpose. Capacity is now EARNED:
# every exit starts at RAMP_MIN and buys its way up to HOT_MAX with recent successes. An exit leaving
# quarantine goes back to the floor and re-earns, because "it stopped being throttled" is not the same
# evidence as "it is working."
RAMP_MIN = 1                   # slots for a cold, unproven, or freshly-burned exit
RAMP_WINDOW_S = 600.0          # successes older than this stop counting toward earned capacity
RAMP_STEP = 3                  # recent successes needed to earn each slot above RAMP_MIN
RAMP_PENALTY_S = 120.0         # stay at the floor this long AFTER a quarantine expires

# ── REPUTATION PERSISTENCE ───────────────────────────────────────────────────────────────────────
# Health is volatile on an HOUR timescale in BOTH directions (see the module docstring) — so a
# persisted verdict is evidence with a shelf life, never a blacklist. Anything older than this is
# dropped on load rather than believed: re-learning that a recovered IP is fine costs a few requests,
# while benching a recovered IP on stale evidence costs the whole exit for as long as the file lives.
REPUTATION_TTL_S = 7200.0      # 2h — outcomes older than this are not loaded at all


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

    def _touch(self, exit_ip, now):
        """Caller already holds `self._lock`. Stamp one unit of activity on this exit — from either an
        outcome (`record`) OR a pick itself (see `pick`'s comment for why the second one is required)."""
        ts = self._exit_ts.setdefault(exit_ip, [])
        ts.append(now)
        if len(ts) > 4 * HOT_MAX + 8:              # bounded; hot() only ever looks at a short window
            del ts[: len(ts) // 2]

    def record(self, exit_ip, costume, cls, now=None):
        """Feed one classified outcome. `cls` is a `blocks` class name."""
        if not exit_ip:
            return
        now = time.time() if now is None else now
        import blocks
        good = cls in (blocks.OK, blocks.EMPTY)
        throttled = blocks.is_throttle(cls)
        with self._lock:
            self._touch(exit_ip, now)
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

    def _caps(self, now):
        """exit -> concurrent slots it has EARNED right now.

        Computed ONCE per pick() and handed to the usable() filter, not recomputed per candidate: a
        50-IP pool against 7 costumes is 350 candidates, and a per-candidate scan of every tracked
        pair would make picking quadratic in the size of the thing it exists to protect.

        An exit absent from the returned map has no history at all and is treated as cold (RAMP_MIN)
        by the caller's `.get(ip, RAMP_MIN)` — a new address is unproven, not trusted."""
        goods, burned = {}, set()
        for (ip, _c), p in self._pairs.items():
            # In quarantine, or inside the penalty shadow just after one — either way, back to the
            # floor. Coming off quarantine means "stopped failing", which is not "known good".
            # The `> 0` guard is not defensive noise: a never-quarantined pair carries 0.0, and
            # `0.0 > now - RAMP_PENALTY_S` is TRUE for the first RAMP_PENALTY_S seconds of any clock,
            # which branded every healthy exit as burned and pinned the whole pool to RAMP_MIN.
            if p.quarantined_until > 0 and p.quarantined_until > now - RAMP_PENALTY_S:
                burned.add(ip)
            g = goods.get(ip, 0)
            for t, ok in p.recent:
                if ok and now - t <= RAMP_WINDOW_S:
                    g += 1
            goods[ip] = g
        caps = {}
        for ip in set(goods) | burned:
            caps[ip] = RAMP_MIN if ip in burned else min(HOT_MAX, RAMP_MIN + goods.get(ip, 0) // RAMP_STEP)
        return caps

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
        or fully-hot pool still returns its LEAST-bad option rather than deadlocking a fleet.

        THE PICK ITSELF COUNTS AS ACTIVITY, immediately — not only once an outcome comes back. Measured
        2026-07-29: with an empty router, N threads racing to prime their FIRST session all see the same
        empty state and identical scores, so a deterministic tie-break sent every one of them to the same
        exit — 10 concurrent sessions on 1 IP, the exact concentration disaster this router exists to
        prevent, self-inflicted (one pair took 10 of 13 total picks in a 400-request run and died: 0%
        across 20 outcomes). `_hot()` previously only counted `record()` calls, which arrive only after a
        full request round-trip — a blind spot lasting exactly as long as it takes the FIRST of several
        racing threads to hear back, which is precisely when the collision happens. Stamping activity here
        closes it: this method holds `self._lock` for its whole body, so concurrent callers are serialized
        anyway, and the second caller in that serialized order now sees the first caller's pick already
        counted against that exit's hotness — before either has sent a single byte on the wire."""
        if not pool or not costumes:
            return None, None
        now = time.time() if now is None else now
        with self._lock:
            entries = {(e.split("@")[-1].split(":")[0]): e for e in pool}
            candidates = [(ip, c) for ip in entries for c in costumes]
            caps = self._caps(now)          # earned concurrency, computed once — see _caps()

            def usable(key):
                ip, _c = key
                p = self._pairs.get(key)
                if p is not None and p.quarantined_until > now:
                    return False
                return self._hot(ip, now) < caps.get(ip, RAMP_MIN)

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
            self._touch(ip, now)              # count immediately — see the docstring above
            self._pairs.setdefault(best_key, _Pair()).picks += 1
            self._total_picks += 1
            return entries[ip], costume

    def hot_exits(self, top=8, now=None):
        """Exits THIS process has touched within HOT_WINDOW_S, busiest first — the same window `_hot()`
        checks before every pick. Small and cheap by construction (bounded by however many exits are
        actually in play right now, not the whole pool), meant to ride along on a heartbeat.

        This is the piece a fleet-level aggregator needs and no single process has: two processes
        reporting the SAME exit here at the SAME moment is the cross-shard concentration collision
        (COLLECT-HANDOFF.md §1e) that neither one can see from inside its own `identity_router`, because
        each only ever sees its own picks."""
        now = time.time() if now is None else now
        with self._lock:
            rows = [(ip, sum(1 for t in ts if now - t <= HOT_WINDOW_S)) for ip, ts in self._exit_ts.items()]
        rows = [r for r in rows if r[1] > 0]
        rows.sort(key=lambda r: -r[1])
        return rows[:top]

    def snapshot(self, now=None):
        """Exportable reputation: the (exit, costume) health this process paid real requests to learn.

        `_exit_ts` is deliberately NOT exported. Hotness answers "how loaded is this exit RIGHT NOW",
        which is meaningless once the process holding those sockets is gone — persisting it would
        make a restarted fleet believe it was already busy and refuse to pick anything."""
        now = time.time() if now is None else now
        with self._lock:
            out = []
            for (ip, costume), p in self._pairs.items():
                recent = [[round(t, 1), bool(g)] for t, g in p.recent]
                if not recent and p.quarantined_until <= now:
                    continue                   # nothing worth carrying for this pair
                out.append({"exit": ip, "costume": costume, "recent": recent,
                            "quarantined_until": round(p.quarantined_until, 1),
                            "quarantine_s": p.quarantine_s})
            return {"at": round(now, 1), "pairs": out}

    def merge(self, snap, now=None, ttl_s=REPUTATION_TTL_S):
        """Fold a persisted snapshot back in. Returns how many pairs contributed anything.

        TWO RULES, both load-bearing:

        1. STALE OUTCOMES ARE DROPPED, NOT BELIEVED. Exit health flips in both directions within an
           hour, so an old verdict is not weak evidence — it is potentially inverted evidence. Loading
           a 6-hour-old "this IP is bad" would bench an address that has since recovered, for free,
           with no request ever proving it. Re-learning costs a few requests; a stale bench costs the
           whole exit for as long as the file survives.
        2. AN EXPIRED QUARANTINE IS NOT RE-IMPOSED. `quarantined_until` is an absolute timestamp, so a
           quarantine that ran out while nothing was running has simply ended. Only a still-future
           deadline is honored — otherwise a restart could serve a sentence twice.

        Merging is additive across shards: eight processes each learned something different about the
        same 50 addresses, and the union is the point. Outcomes are de-duplicated by (ts, good) so
        re-reading our own file after a crash-restart cannot inflate the evidence count."""
        now = time.time() if now is None else now
        pairs = (snap or {}).get("pairs") or []
        n_merged = 0
        with self._lock:
            for row in pairs:
                ip, costume = row.get("exit"), row.get("costume")
                if not ip:
                    continue
                fresh = [(float(t), bool(g)) for t, g in (row.get("recent") or [])
                         if now - float(t) <= ttl_s]
                q_until = float(row.get("quarantined_until") or 0.0)
                q_live = q_until > now
                if not fresh and not q_live:
                    continue                   # nothing survives the TTL — treat as no evidence
                p = self._pairs.setdefault((ip, costume), _Pair())
                seen = set(p.recent)
                for item in fresh:
                    if item not in seen:
                        p.recent.append(item)
                        seen.add(item)
                p.recent.sort(key=lambda r: r[0])
                if len(p.recent) > RECENT_MAXLEN:
                    del p.recent[: len(p.recent) - RECENT_MAXLEN]
                if q_live:
                    p.quarantined_until = max(p.quarantined_until, q_until)
                    p.quarantine_s = max(p.quarantine_s, float(row.get("quarantine_s") or QUARANTINE_BASE_S))
                n_merged += 1
        return n_merged

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


def hot_exits(top=8):
    return get().hot_exits(top=top)


def reset():
    get().reset()


# ── cross-run, cross-shard reputation ────────────────────────────────────────────────────────────
# Exit reputation was the most expensive thing this router produced and the only thing it threw away:
# every state here lived in memory and died with the process. Measured cost, 2026-08-02: one session
# restarted an 8-shard fleet five times, and on every restart all eight shards re-discovered the same
# burned addresses from scratch — spending real requests to re-learn what the previous process already
# knew. That is worse than waste. Re-probing an address the target has already flagged does not just
# cost a request, it deepens the flag; some of that day's "the whole pool is uniformly burned" was
# plausibly self-inflicted by exactly that loop.
#
# ONE FILE PER SHARD, NEVER A SHARED ONE. Eight writers merging into a single key is the read-modify-
# write lost update this codebase has already been bitten by (see ue_catalog._land: two shards
# accumulating at once took ubereats_products from 33,250 rows DOWN to 8,798). Each shard owns its own
# key and readers union the set — the same append-only shape that has never lost a row.
#
# READING EVERY SHARD'S FILE IS THE POINT, not a side effect: what shard 2 paid to learn about an
# address is equally true for shard 5, and eight processes independently re-buying the same fact is
# dead time in the most literal sense.
def _rep_key(site, shard, nshard):
    return "_collect/reputation/%s_%s-%s.json" % (site, shard, nshard)


def save(site, shard, nshard, log=None):
    """Persist THIS shard's reputation. Best-effort by construction — a warehouse hiccup must never be
    the reason a scrape stops, exactly as `_ck_save` treats checkpointing."""
    try:
        import json
        import warehouse
        snap = get().snapshot()
        if not snap.get("pairs"):
            return 0
        warehouse.put_bytes(_rep_key(site, shard, nshard), json.dumps(snap).encode())
        return len(snap["pairs"])
    except Exception as e:
        if log:
            log("[rep] save skipped: %s" % str(e)[:90])
        return 0


def load(site, nshard, log=None):
    """Merge every shard's persisted reputation into this process. Returns (files_read, pairs_merged).

    Absent files are the normal first-run case, not an error — a fleet that has never run has nothing
    to remember, and a shard that has not written yet simply contributes nothing."""
    files, merged = 0, 0
    try:
        import json
        import warehouse
        r = get()
        for i in range(max(1, int(nshard))):
            try:
                raw = warehouse.get_bytes(_rep_key(site, i, nshard))
            except Exception:
                raw = None
            if not raw:
                continue
            files += 1
            merged += r.merge(json.loads(raw))
    except Exception as e:
        if log:
            log("[rep] load skipped: %s" % str(e)[:90])
        return files, merged
    if log and files:
        log("[rep] reputation carried over: %d pair(s) from %d shard file(s) — not re-learning these"
            % (merged, files))
    return files, merged
