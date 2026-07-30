#!/usr/bin/env python3
"""fleet_watchdog.py — Layer 1 of COLLECT-HANDOFF.md §9: a deterministic, $0 cross-shard collision
detector. NOT the LLM triage layer §9b/9c describes — that stays a design, unbuilt, and this module has
no dependency on it ever existing. This is the half that's cheap, certain, and useful on its own: it
reads what every shard is ALREADY saying about itself (the `HOODIE_PROGRESS` heartbeat emitted by
`ue_catalog._beat`) and flags the one thing no single shard can see about itself — that its current hot
exits overlap with another shard's.

THE BUG THIS WATCHES FOR. Measured 2026-07-29 (§1e): `identity_router`'s concentration-avoidance is
per-PROCESS. Running N independent shard processes against the SAME pool means N routers, each blind to
the other N-1's picks — a 95-100% single-shard control collapsed to near-0% as the real 8-shard fleet,
because shards silently piled onto the same exits with no way to know it. §9a's `getstore.set_shard`
fixes this structurally (disjoint partitions, no shared state needed) and should make the collision this
tool looks for impossible in normal operation — this exists as the belt to that fix's suspenders: proof
the partition is actually holding (e.g. `UE_PARTITION_POOL=0`, a bad shard/nshard mismatch between two
processes, or some future caller of `identity_router` that isn't sharded at all).

WHY CROSS-SECTIONAL, NOT TREND-BASED. The collision was originally found by comparing a control run's
usable% against the fleet run's usable% side by side — a trend comparison, the exact shape that produced
three retracted or re-run findings the same day (the `exit_offset=0` artifact, `costume_probe`'s
concurrency-vs-trajectory mislabeling, the global-vs-per-costume compare that "dissolved" because the two
costumes never held still long enough). Ambient hour-scale drift confounds any before/after read. This
tool never compares a number to its own past — it asks a cross-sectional question at one instant: do two
shards' hot-exit sets touch RIGHT NOW? That's true or false independent of drift, so it can't be fooled by
the same confound that burned the trend-based reads.

WHAT IT DOES NOT DO. Read-only, by design. It never re-partitions, pauses a shard, or touches the ladder
— a wrong alarm here costs a log line, not a scrape. A bounded-action layer consuming this tool's output
is exactly what §9b/9c scope, and exactly what is NOT built here.

    python3 fleet_watchdog.py /tmp/ue_shard_*.log             # one-shot check, exit 1 if collision found
    python3 fleet_watchdog.py --watch 15 /tmp/ue_shard_*.log   # re-check every 15s until Ctrl-C
"""
import glob
import json
import sys
import time

PROGRESS_PREFIX = "HOODIE_PROGRESS "


def latest_heartbeat(path):
    """The most recent HOODIE_PROGRESS line in a shard's log file, parsed. None if the file has none yet
    or can't be read — a shard that hasn't beaten yet is not an error, just nothing to report. Reads the
    whole file rather than seeking from the end: shard logs are heartbeat-cadence (every ~200 stores or
    60s, see `ue_catalog._beat`), not high-frequency, so this stays cheap without needing tail-from-offset
    bookkeeping."""
    last = None
    try:
        with open(path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith(PROGRESS_PREFIX):
                    try:
                        last = json.loads(line[len(PROGRESS_PREFIX):])
                    except Exception:
                        continue                  # a truncated/mid-write line; keep the last good one
    except Exception:
        return None
    return last


def scan(paths):
    """One pass over every shard's log. Returns {"shards": {path: heartbeat-or-None}, "collisions": [...]}
    where a collision is {"exit": ip, "shards": [path, ...]} — two or more shards whose LATEST heartbeat
    both name this exit in `hot_exits`, at THIS scan. Never compared against a prior scan — see the
    module docstring for why that's deliberate."""
    shards = {p: latest_heartbeat(p) for p in paths}
    by_exit = {}
    for p, hb in shards.items():
        if not hb:
            continue
        for ip in (hb.get("hot_exits") or []):
            by_exit.setdefault(ip, []).append(p)
    collisions = [{"exit": ip, "shards": ps} for ip, ps in sorted(by_exit.items()) if len(ps) > 1]
    return {"shards": shards, "collisions": collisions}


def report(result, log=print):
    n_alive = sum(1 for hb in result["shards"].values() if hb)
    log("[fleet_watchdog] %d/%d shards reporting" % (n_alive, len(result["shards"])))
    if result["collisions"]:
        for c in result["collisions"]:
            log("[fleet_watchdog] !! COLLISION exit=%s shared by %d shards RIGHT NOW: %s"
                % (c["exit"], len(c["shards"]), ", ".join(c["shards"])))
    else:
        log("[fleet_watchdog] no cross-shard exit overlap this scan")
    return result


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Layer-1 deterministic cross-shard collision detector (COLLECT-HANDOFF.md §9b). "
                    "Read-only: reports, never acts.")
    ap.add_argument("logs", nargs="+", help="shard log file paths or shell globs")
    ap.add_argument("--watch", type=float, default=0,
                    help="re-scan every N seconds instead of once (Ctrl-C to stop)")
    a = ap.parse_args(argv)
    # A pattern with glob metacharacters that matches NOTHING must contribute zero paths, not itself as
    # a literal filename — `glob.glob(pat) or [pat]` looked right but silently turned an unmatched
    # "/tmp/no-such-*.log" into a fake path that never opens, masking the "nothing matched" case entirely.
    # A literal path with no metacharacters is kept even if it doesn't exist yet (a shard log written
    # after this scan starts) — that's a legitimate "not reporting yet", not a typo'd argument.
    def _expand(pat):
        return glob.glob(pat) if any(c in pat for c in "*?[") else [pat]
    paths = sorted({p for pat in a.logs for p in _expand(pat)})
    if not paths:
        print("[fleet_watchdog] no log files matched", file=sys.stderr)
        return 2
    if a.watch:
        while True:
            report(scan(paths))
            time.sleep(a.watch)
    result = report(scan(paths))
    return 1 if result["collisions"] else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
