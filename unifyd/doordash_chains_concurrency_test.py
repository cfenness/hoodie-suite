#!/usr/bin/env python3
"""doordash_chains_concurrency_test.py — chains must run CONCURRENTLY, not one at a time.

Grounded in a real incident: 12 chains run sequentially meant the smallest chain (123 stores) alone
took 11.9 hours and was still not done — the full 24,919-store national universe projected to ~119
days at that rate. Sequential chains cap total concurrency at DDFULL_WORKERS regardless of how many
chains are queued; this fix runs up to DDFULL_CHAIN_CONCURRENCY chains at once instead.

doordash_full.run is monkeypatched to a controllable fake (sleep + return) so this test proves the
CONCURRENCY property directly (wall-clock time, not just that the code doesn't crash) without any
real network call or warehouse write.

    python3 unifyd/doordash_chains_concurrency_test.py
"""
import os
import sys
import time

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
    # LOCAL mode — no real warehouse/network touched by runlog's scrape_runs write or write_accumulate.
    for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
        os.environ.pop(v, None)
    import tempfile
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="ddchains_conc_")

    import doordash_chains as ddc

    # --- fake the plan directly (skip bucket_stores' real doordash_stores read) ----------------------
    # each "chain" takes this long — sequential would be ~4x this + fixed overhead, concurrent ~1x it.
    # Long enough that runlog/warehouse's own fixed setup cost can't masquerade as "still concurrent".
    FAKE_PLAN_DELAY = 1.0

    def fake_bucket_stores(log=print):
        return {"a": ["1"], "b": ["2"], "c": ["3"], "d": ["4"]}

    def fake_landed_stores(chain):
        return set()

    calls = []

    def fake_doordash_full_run(chain, stores=None, log=print, on_store=None):
        t0 = time.time()
        calls.append((chain, t0))
        time.sleep(FAKE_PLAN_DELAY)
        if on_store:
            on_store(len(stores), len(stores))
        return "run-%s" % chain, len(stores) * 10   # pretend 10 items/store

    real_bucket_stores = ddc.bucket_stores
    real_landed_stores = ddc._landed_stores
    real_dd_run = ddc.doordash_full.run
    ddc.bucket_stores = fake_bucket_stores
    ddc._landed_stores = fake_landed_stores
    ddc.doordash_full.run = fake_doordash_full_run
    try:
        print("4 chains run CONCURRENTLY, not sequentially")
        t0 = time.time()
        rec = ddc.run(log=lambda *a: None)
        elapsed = time.time() - t0
        check("all 4 chains landed (10 items each x 1 store)", rec["items_landed_this_run"] == 40, rec)
        check("all 4 chains' stores counted", rec["stores_landed_this_run"] == 4, rec)
        check("wall-clock time is close to ONE chain's delay, not 4x (concurrent, not sequential): %.2fs"
              % elapsed, elapsed < FAKE_PLAN_DELAY * 2.0, elapsed)

        # the 4 calls should have started within a small window of each other (concurrent), not
        # staggered by ~FAKE_PLAN_DELAY each (sequential)
        starts = sorted(t for _c, t in calls)
        spread = starts[-1] - starts[0]
        check("all 4 chains started within a fraction of the per-chain delay (concurrent start): %.3fs"
              % spread, spread < FAKE_PLAN_DELAY * 0.5, spread)

        print("\nCHAIN_CONCURRENCY actually bounds how many run at once")
        import threading
        in_flight = [0]
        max_in_flight = [0]
        lock = threading.Lock()

        def fake_run_tracking(chain, stores=None, log=print, on_store=None):
            with lock:
                in_flight[0] += 1
                max_in_flight[0] = max(max_in_flight[0], in_flight[0])
            time.sleep(0.15)
            with lock:
                in_flight[0] -= 1
            return "run-%s" % chain, 1

        def fake_bucket_stores_8(log=print):
            return {str(i): [str(i)] for i in range(8)}

        ddc.doordash_full.run = fake_run_tracking
        ddc.bucket_stores = fake_bucket_stores_8
        real_concurrency = ddc.CHAIN_CONCURRENCY
        ddc.CHAIN_CONCURRENCY = 3
        try:
            ddc.run(log=lambda *a: None)
        finally:
            ddc.CHAIN_CONCURRENCY = real_concurrency
        check("never more than CHAIN_CONCURRENCY (3) chains ran at once (saw %d)"
              % max_in_flight[0], max_in_flight[0] <= 3, max_in_flight[0])
        check("concurrency was actually exercised, not accidentally serial (saw %d > 1)"
              % max_in_flight[0], max_in_flight[0] > 1, max_in_flight[0])

        print("\none chain's failure does not block or crash the others")
        ddc.bucket_stores = fake_bucket_stores

        def fake_run_one_fails(chain, stores=None, log=print, on_store=None):
            if chain == "b":
                raise TimeoutError("simulated: chain b blew up")
            if on_store:
                on_store(len(stores), len(stores))
            return "run-%s" % chain, 5

        ddc.doordash_full.run = fake_run_one_fails
        rec2 = ddc.run(log=lambda *a: None)
        check("the 3 good chains still landed despite one failing",
              rec2["items_landed_this_run"] == 15, rec2)
        check("the failed chain is recorded with an error, not silently dropped",
              "error" in eval(rec2["per_chain"])["b"], rec2["per_chain"])
    finally:
        ddc.bucket_stores = real_bucket_stores
        ddc._landed_stores = real_landed_stores
        ddc.doordash_full.run = real_dd_run

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
