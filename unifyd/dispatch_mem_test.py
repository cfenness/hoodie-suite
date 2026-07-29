#!/usr/bin/env python3
"""dispatch_mem_test.py — ephemeral machines must be sized from the registry, not from luck.

Grounded in a real OOM. `aggregator-geo` declares mem=16384. It was hand-spawned without a mem hint,
landed on the generic 4096 default, and the kernel SIGKILLed it: `status=failed delta=0`, no error the
caller could see, and a backlog that looked like it "just didn't drain". The dispatcher itself passes
`s.get("mem")` at every call site — so the scheduled path was fine and the bug only appeared on a path
that forgot. That is precisely the kind of gap a default should not paper over.

These tests exercise the sizing decision without touching the Fly API: `spawn()` is monkeypatched at
the `_api` boundary so the machine config it WOULD have created is captured and asserted on.

    python3 unifyd/dispatch_mem_test.py
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


def main():
    print("dispatch_ephemeral.spawn sizing")
    import dispatch_ephemeral as D

    captured = {}

    def fake_api(method, path, body=None, timeout=60):
        captured["config"] = body["config"]
        return {"id": "m_test"}

    real_api = D._api
    D._api = fake_api
    try:
        def guest(sid, **kw):
            captured.clear()
            D.spawn(sid, "img:test", kw.pop("klass", "headless"), **kw)
            return captured["config"]["guest"]

        # --- THE REGRESSION: no hint, registry declares 16384 -----------------------------------
        g = guest("aggregator-geo")
        check("no hint still gets the registry's declared mem", g["memory_mb"] == 16384, g)
        check("cpus scale to unlock that mem (Fly caps 2GB/cpu)", g["cpus"] >= 8, g)

        # --- an explicit hint still wins (a deliberate smaller run is legitimate) ----------------
        g = guest("aggregator-geo", mem_hint=4096)
        check("an explicit hint overrides the registry", g["memory_mb"] == 4096, g)

        # --- sources with no declared mem keep the klass defaults --------------------------------
        g = guest("does-not-exist-anywhere")
        check("unknown source falls back to the headless default", g["memory_mb"] == 4096, g)
        g = guest("does-not-exist-anywhere", klass="mac")
        check("headful falls back to 8192 for Chrome", g["memory_mb"] == 8192, g)

        # --- cpu scaling is a function of mem, and bounded ---------------------------------------
        g = guest("does-not-exist-anywhere", mem_hint=2048)
        check("small mem still gets the 4-cpu floor", g["cpus"] == 4, g)
        g = guest("does-not-exist-anywhere", mem_hint=65536)
        check("cpus are capped at 8", g["cpus"] == 8, g)

        # --- the registry lookup must never be able to block a spawn -----------------------------
        import source_registry
        real_sources = source_registry.SOURCES
        try:
            del source_registry.SOURCES        # simulate a broken/renamed registry
            g = guest("aggregator-geo")
            check("a broken registry degrades to the default, does not raise",
                  g["memory_mb"] == 4096, g)
        finally:
            source_registry.SOURCES = real_sources

        # --- and the declared value is real, not invented by this test ---------------------------
        declared = next((int(s["mem"]) for s in source_registry.SOURCES
                         if s.get("id") == "aggregator-geo" and s.get("mem")), None)
        check("aggregator-geo really does declare 16384 in the registry", declared == 16384, declared)
    finally:
        D._api = real_api

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
