#!/usr/bin/env python3
"""dispatch_concurrency_test.py — a source must never run twice against the same pool at once.

Grounded in a real incident: 4 duplicate manual `ubereats` machines ran concurrently against the same
12-hour-rested proxy pool on 2026-07-30 (source: user, not the scheduler — a manual trigger bypasses
dispatch_ephemeral's own running_sources() check entirely, since that check only guards the scheduler's
own tick). conflicting_machine() is the fix: it's called from run_ephemeral.py itself, so every launch
path (scheduled, manual, or a stray direct `flyctl machine run`) is checked at the one point they all
funnel through before doing real work.

These tests exercise the sharded-vs-unsharded conflict rule without touching the Fly API: `_machines()`
is monkeypatched to return a synthetic machine list.

    python3 unifyd/dispatch_concurrency_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def _machine(mid, source, shard=None, state="started"):
    md = {"role": "ephemeral-pull", "source": source}
    if shard is not None:
        md["shard"] = shard
    return {"id": mid, "state": state, "config": {"metadata": md}}


def main():
    print("dispatch_ephemeral.conflicting_machine")
    import dispatch_ephemeral as D

    real_machines = D._machines

    def with_fleet(fleet):
        D._machines = lambda: fleet

    try:
        # --- the actual incident: an unsharded manual run collides with an already-running one -----
        with_fleet([_machine("m1", "ubereats")])
        check("a second unsharded launch of an already-running source is refused",
              D.conflicting_machine("ubereats") == "m1")

        # --- no conflict: different source, or nothing running -------------------------------------
        with_fleet([_machine("m1", "doordash-full")])
        check("a different source never conflicts", D.conflicting_machine("ubereats") is None)
        with_fleet([])
        check("nothing running -> no conflict", D.conflicting_machine("ubereats") is None)

        # --- a finished/destroyed machine doesn't block a new launch --------------------------------
        with_fleet([_machine("m1", "ubereats", state="destroyed")])
        check("a destroyed machine doesn't block a relaunch",
              D.conflicting_machine("ubereats") is None)

        # --- self-exclusion: a machine must never see itself as a conflict --------------------------
        with_fleet([_machine("m1", "ubereats")])
        check("a machine excludes itself from the conflict check",
              D.conflicting_machine("ubereats", self_id="m1") is None)

        # --- legitimate fleet: sibling shards at DIFFERENT indices coexist --------------------------
        with_fleet([_machine("m0", "ubereats", shard="0/8"), _machine("m2", "ubereats", shard="2/8")])
        check("a new shard at a distinct index does not conflict with its siblings",
              D.conflicting_machine("ubereats", shard="1/8") is None)

        # --- but the SAME shard index twice is a real duplicate -------------------------------------
        with_fleet([_machine("m0", "ubereats", shard="0/8")])
        check("relaunching the exact same shard index is refused",
              D.conflicting_machine("ubereats", shard="0/8") == "m0")

        # --- an unsharded launch conflicts with ANY running shard of the same source ----------------
        with_fleet([_machine("m3", "ubereats", shard="3/8")])
        check("an unsharded launch conflicts with an active sharded fleet (would re-cover the universe)",
              D.conflicting_machine("ubereats") == "m3")

        # --- a sharded launch conflicts with an already-running UNSHARDED run of the same source ----
        with_fleet([_machine("m1", "ubereats")])
        check("a sharded launch conflicts with an active unsharded run of the same source",
              D.conflicting_machine("ubereats", shard="0/8") == "m1")
    finally:
        D._machines = real_machines

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
