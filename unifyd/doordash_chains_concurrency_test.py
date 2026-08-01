#!/usr/bin/env python3
"""doordash_chains_concurrency_test.py — full-universe sweep, no curated chain list, nothing re-scraped.

doordash_chains.py used to bucket doordash_stores against a hardcoded ~15-chain list and only ever
attempt those ~24,919 stores out of the real 767,716-row sitemap universe — a self-imposed scope limit,
not a real constraint (the sitemap carries no chain/vertical field; a non-retail store just costs one
wasted fetch). That list is gone. This file proves the replacement:

  1. The full universe is read with NO name/chain filtering.
  2. "Already covered" is the UNION of the new unified table AND every legacy per-chain table from
     before this change — a store already landed under the old design must NOT look uncovered and get
     redone. This is the single most important property here: silently re-scraping already-landed work
     is exactly the "start over" failure mode this exists to prevent.
  3. shard/nshard partitions the remaining stores deterministically with NO overlap, so N machines can
     run concurrently against disjoint slices (the actual throughput lever at this scale — see
     CLAUDE.md's "too slow => shard across machines, never truncate").
  4. Each store lands with ITS OWN real sitemap name as source/attribution, not a curated bucket key.

doordash_full.run is monkeypatched to a controllable fake so these tests run with no real network call
or warehouse credentials, same discipline as the rest of this suite.

    python3 unifyd/doordash_chains_concurrency_test.py
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
    for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
        os.environ.pop(v, None)
    import tempfile
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="ddchains_full_")

    import doordash_chains as ddc

    print("no curated chain list survives")
    check("no TARGET_CHAINS dict", not hasattr(ddc, "TARGET_CHAINS"), "still present")
    check("no _match_chain heuristic", not hasattr(ddc, "_match_chain"), "still present")

    print("\nall_stores() reads the FULL universe, no name/chain filtering")
    STORES = {"1": "cvs pharmacy portland", "2": "joes liquor denver", "3": "some random taco place",
              "4": "circle k tulsa", "5": "mom and pop wine shop"}
    warehouse.write_accumulate("doordash_stores",
                               [{"store_id": k, "name": v} for k, v in STORES.items()],
                               key="store_id", fields=["store_id", "name"])
    names = ddc.all_stores(log=lambda *a: None)
    check("every store in the sitemap is returned, including non-chain / unrecognized names",
          set(names) == set(STORES), names)

    print("\n_landed_stores() unions the new unified table AND every legacy per-chain table — "
          "a store scraped under the OLD curated design must not look uncovered")
    warehouse.write_accumulate("doordash_products_full",
                               [{"store": "1", "product_id": "p1"}], key=lambda r: (r["store"], r["product_id"]),
                               fields=["store", "product_id"])
    warehouse.write_accumulate("circlek_products_full",
                               [{"store": "4", "product_id": "p1"}], key=lambda r: (r["store"], r["product_id"]),
                               fields=["store", "product_id"])
    covered = ddc._landed_stores(log=lambda *a: None)
    check("store landed in the NEW unified table is covered", "1" in covered, covered)
    check("store landed in a LEGACY per-chain table (circlek) is ALSO covered — not silently redone",
          "4" in covered, covered)
    check("stores never landed anywhere are NOT covered", "2" not in covered and "5" not in covered, covered)

    print("\nrun() only picks stores not covered anywhere, and passes their REAL names through")
    calls = []

    def fake_dd_run(chain, stores=None, store_names=None, log=print, on_store=None):
        calls.append((chain, list(stores), dict(store_names or {})))
        if on_store:
            on_store(len(stores), len(stores))
        return "run-1", len(stores) * 3

    real_dd_run = ddc.doordash_full.run
    ddc.doordash_full.run = fake_dd_run
    try:
        rec = ddc.run(log=lambda *a: None)
        check("only the 3 uncovered stores were picked (2, 3, 5) — 1 and 4 already covered",
              set(calls[0][1]) == {"2", "3", "5"}, calls)
        check("chain passed to doordash_full.run is the fixed 'doordash' namespace, not a curated key",
              calls[0][0] == "doordash", calls)
        check("each picked store's REAL sitemap name is threaded through as store_names",
              calls[0][2] == {"2": STORES["2"], "3": STORES["3"], "5": STORES["5"]}, calls[0][2])
        check("run record reports the true universe size (5), not a curated subset",
              rec["universe_total"] == 5, rec)
    finally:
        ddc.doordash_full.run = real_dd_run

    print("\nshard/nshard partitions the remaining stores with NO overlap and NO gaps")
    many_stores = {str(i): "store %d" % i for i in range(50)}
    warehouse.write_accumulate("doordash_stores",
                               [{"store_id": k, "name": v} for k, v in many_stores.items()],
                               key="store_id", fields=["store_id", "name"])
    seen_by_shard = {}
    picks = []

    def fake_dd_run2(chain, stores=None, store_names=None, log=print, on_store=None):
        picks.extend(stores)
        return "run", 0

    ddc.doordash_full.run = fake_dd_run2
    try:
        for shard in range(4):
            picks.clear()
            ddc.run(batch=1000, shard=shard, nshard=4, log=lambda *a: None)
            seen_by_shard[shard] = set(picks)
        all_picked = set().union(*seen_by_shard.values())
        check("every shard's picks are disjoint from every other shard's",
              all(seen_by_shard[a].isdisjoint(seen_by_shard[b])
                  for a in seen_by_shard for b in seen_by_shard if a != b), seen_by_shard)
        check("the 4 shards together cover the full remaining universe (no gaps)",
              all_picked >= set(many_stores) - {"1", "2", "3", "4", "5"}, len(all_picked))
    finally:
        ddc.doordash_full.run = real_dd_run

    print("\nperiodic consolidation fires DURING a large batch, not just at the end — a batch against "
          "the full 767k-store universe can run for hours, and the canonical table must not look "
          "empty (as if nothing landed) the whole time real data is safely in the parts table")
    consolidate_calls = []
    real_consolidate = ddc.doordash_full.consolidate
    ddc.doordash_full.consolidate = lambda chain, log=print: consolidate_calls.append(chain) or 0

    def fake_dd_run3(chain, stores=None, store_names=None, log=print, on_store=None):
        for i, _s in enumerate(stores, start=1):
            if on_store:
                on_store(i, len(stores))
        return "run", 0

    ddc.doordash_full.run = fake_dd_run3
    os.environ["DDFULL_CONSOLIDATE_EVERY"] = "10"
    import tempfile as _tempfile
    warehouse._LOCAL_DIR = _tempfile.mkdtemp(prefix="ddchains_consolidate_")   # fresh, isolated universe
    try:
        many_stores2 = {str(i): "store %d" % i for i in range(250, 285)}   # 35 uncovered stores, nothing else
        warehouse.write_accumulate("doordash_stores",
                                   [{"store_id": k, "name": v} for k, v in many_stores2.items()],
                                   key="store_id", fields=["store_id", "name"])
        ddc.run(batch=1000, log=lambda *a: None)
        check("consolidate fired periodically DURING the batch (every 10 of 35 stores => 3 times), "
              "not just once at the very end", len(consolidate_calls) == 3, consolidate_calls)
        check("each periodic consolidate targeted the unified 'doordash' table",
              all(c == "doordash" for c in consolidate_calls), consolidate_calls)
    finally:
        os.environ.pop("DDFULL_CONSOLIDATE_EVERY", None)
        ddc.doordash_full.run = real_dd_run
        ddc.doordash_full.consolidate = real_consolidate

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
