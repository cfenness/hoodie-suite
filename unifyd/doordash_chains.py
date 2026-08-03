#!/usr/bin/env python3
"""doordash_chains.py — full national DoorDash retail sweep. NO curated chain list.

Previously this bucketed `doordash_stores` against a hardcoded ~15-name list (CVS, Walgreens, Circle K,
...) and only ever attempted those ~24,919 stores out of the 767,716-row sitemap universe. That was a
self-imposed scope limit, not a real constraint — DoorDash's own sitemap carries no chain/vertical field
at all (restaurant and retail store ids look identical), so the curated list was pure premature
optimization to avoid wasting a fetch on non-retail stores. Removed: this now attempts every store in
`doordash_stores`. A store with no alcohol category costs exactly ONE wasted fetch before
`doordash_full.full_catalog()`'s tree walk empties out and returns — the existing per-store cost
structure already makes this cheap; the curated list was solving a problem that barely existed.

Lands into ONE unified table (`doordash_products_full` / `doordash_outlets_full`) instead of one table
per chain — the per-row `source` field carries the REAL store name from the sitemap (best-effort, not a
curated match), so downstream consumers still get an attribution without any hardcoded list gating
coverage.

RESUMABLE BATCHES, not a one-shot capped run — read what's already landed (union of the new unified
table AND every legacy `<chain>_products_full` table from before this change, so nothing already
scraped gets silently redone), subtract from the full universe, take up to `batch` of what's left.
Repeated triggers converge toward full coverage.

    python doordash_chains.py                  # one batch of the full universe
    python doordash_chains.py --batch 500       # a smaller manual slice
    python doordash_chains.py --shard 0 --nshard 6   # 1/6 of the universe, for running N machines at once
"""
import argparse
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import doordash_full

RUN_FIELDS = ["run_id", "ts", "universe_total", "covered_total", "remaining_total",
              "stores_landed_this_run", "items_landed_this_run", "duration_s"]

# Every table a store could already be landed in from BEFORE this change (the curated-chain era) — a
# store found in any of these must not be re-treated as "new" just because the unified table is empty.
_LEGACY_CHAIN_TABLES = ["abcfinewine", "albertsons", "binnys", "circlek", "costco", "cvs", "kroger",
                        "meijer", "safeway", "target", "walgreens", "walmart", "bevmo", "publix",
                        "totalwine"]


def all_stores(log=print, metro=None):
    """The FULL doordash_stores universe — store_id -> name. NO filtering by name/chain. NO LIMIT:
    two string columns across ~1M rows is trivial for DuckDB.

    `metro`, when given, is a set of (city_lower, STATE) pairs from city_centroid.metro_cities() —
    a geographic scope (an anchor city + radius, e.g. "Orlando, FL" pulls in Windermere/Winter Garden
    too), not a literal city-name allowlist. doordash_stores carries no lat/lng of its own, so this
    filters by matching its own city/state text against that reference-side scope, normalized the
    same way city_centroid's lookups are (city_centroid._norm) so a raw DoorDash city string like
    "Winter Garden" matches the same key the metro scope was built from."""
    try:
        cols = "store_id, name, city, state" if metro else "store_id, name"
        rows = warehouse.query("doordash_stores", "SELECT %s FROM t WHERE store_id IS NOT NULL" % cols)
    except Exception as e:
        log("[doordash_chains] doordash_stores unreadable: %s" % str(e)[:140])
        return {}
    if metro:
        import city_centroid as cc
        rows = [r for r in rows if (cc._norm(r.get("city") or ""), str(r.get("state") or "").strip().upper()) in metro]
    names = {str(r["store_id"]): (r.get("name") or "") for r in rows}
    log("[doordash_chains] universe=%d stores (%s)"
        % (len(names), "metro-scoped, %d city/state pairs" % len(metro) if metro else "full sitemap, no chain filter"))
    return names


def _landed_stores(log=print):
    """Store ids already covered — the UNION of the new unified table and every legacy per-chain
    table. Without the legacy union, a store already scraped under the old curated-chain design would
    look 'uncovered' in the new unified table and get redone from scratch, which is exactly the
    'starting over' this must not do."""
    covered = set()
    try:
        rows = warehouse.query("doordash_products_full", "SELECT DISTINCT store FROM t")
        covered.update(str(r["store"]) for r in rows)
    except Exception:
        pass
    for chain in _LEGACY_CHAIN_TABLES:
        try:
            rows = warehouse.query(chain + "_products_full", "SELECT DISTINCT store FROM t")
            covered.update(str(r["store"]) for r in rows)
        except Exception:
            continue
    return covered


def _shard_of(store_id, nshard):
    return int(hashlib.md5(store_id.encode()).hexdigest(), 16) % nshard


def run(batch=None, shard=None, nshard=None, log=print, metro=None):
    """ONE resumable batch against the FULL national universe, or a metro-scoped subset of it. shard/
    nshard (both required together) partition the store-id space deterministically so N machines can
    each take a disjoint slice and run concurrently without double-scraping the same stores — see
    CLAUDE.md's "too slow => shard across machines, never truncate" rule.

    `metro`, when given, is a set of (city_lower, STATE) pairs from city_centroid.metro_cities() —
    restricts the universe to a geographic scope (anchor cities + radius) instead of every
    doordash_stores row nationally. Passed straight through to all_stores()."""
    t0 = time.time()
    import blocks
    blocks.install()
    # SELF-TUNING AIMD RATE PACER — the same one ue_catalog.py's getstore.py already uses (pace.py),
    # applied to DoorDash for the first time. DoorDash previously had NO adaptive rate control at all:
    # concurrency was gated only by DDFULL_WORKERS and a flat sleep, no feedback loop, no fleet-wide
    # budget — exactly the "worker count is a proxy that breaks whenever the work per worker changes"
    # failure mode pace.py's own docstring documents from UberEats' history (120 workers over 20 IPs
    # collapsed at ~59 req/s aggregate — the ceiling was request RATE, not IP count or worker count).
    # DoorDash's OWN safe rate has never been measured; DD_FLEET_RATE starts conservative (below where
    # we'd guess trouble starts) and additive-increase finds the real ceiling empirically instead of
    # us guessing a worker count again. shard/nshard (when running multiple machines concurrently)
    # split this same budget the way UberEats' shards already do.
    # DD_FLEET_MAX_RATE (default 500, well above anything observed) overrides Pacer's own rate*4
    # default — measured live 2026-08-02: two separate runs (starting at 20 and 40 req/s) each
    # converged EXACTLY at 4x their starting rate with zero backoffs the whole time, meaning that
    # ceiling came from this multiplier, not from DoorDash pushing back. Without an explicit cap,
    # every restart just plateaus at whatever 4x the guess happened to be instead of ever finding
    # where the target itself actually trips.
    try:
        import pace
        _rate = pace.shard_rate(nshard or 1, fleet_rate=float(os.environ.get("DD_FLEET_RATE", "20")))
        _max_rate = float(os.environ.get("DD_FLEET_MAX_RATE", "500"))
        pace.install(_rate, max_rate=_max_rate)
        log("[doordash_chains] paced at %.2f req/s (fleet %.0f/s across %d shard(s), cap %.0f/s) — AIMD from here"
            % (_rate, float(os.environ.get("DD_FLEET_RATE", "20")), nshard or 1, _max_rate))
    except Exception as e:
        log("[doordash_chains] pacing unavailable (%s) — running unpaced" % str(e)[:60])
    cap = batch or int(os.environ.get("DDFULL_BATCH", "5000"))

    names = all_stores(log=log, metro=metro)
    universe_total = len(names)
    covered = _landed_stores(log=log)
    covered_total = sum(1 for s in names if s in covered)

    todo = [s for s in names if s not in covered]
    if shard is not None and nshard:
        todo = [s for s in todo if _shard_of(s, nshard) == shard]
        log("[doordash_chains] shard %d/%d: %d stores in this shard's slice of the remaining %d"
            % (shard, nshard, len(todo), universe_total - covered_total))
    picked = todo[:cap]
    log("[doordash_chains] universe=%d covered=%d remaining=%d taking=%d"
        % (universe_total, covered_total, len(todo), len(picked)))

    store_names = {s: names[s] for s in picked}

    # Periodic consolidation — the canonical doordash_products_full table otherwise only gets its
    # end-of-run fold once the WHOLE picked batch finishes (doordash_full.run's own convenience fold).
    # At full-universe scale (hundreds of thousands of stores, most of them a 1-2 fetch restaurant
    # reject and a handful genuinely large retail catalogs), a batch can run for hours, during which
    # the canonical table would look completely empty despite real, already-landed data sitting safely
    # in the per-store parts table — exactly the "is this actually landing anything" question a batch
    # this large will otherwise raise every time. Folding every CONSOLIDATE_EVERY completions makes
    # already-scraped data visible without waiting for the batch to end.
    CONSOLIDATE_EVERY = int(os.environ.get("DDFULL_CONSOLIDATE_EVERY", "100"))

    import runlog
    with runlog.track("doordash-full", total=len(picked)) as r:
        def _tick(i, n):
            r.progress(i, n)
            if i and i % CONSOLIDATE_EVERY == 0:
                try:
                    doordash_full.consolidate("doordash", log=log)
                except Exception as e:
                    log("[doordash_chains] periodic consolidate failed: %s" % str(e)[:150])

        try:
            _run_id, n_items = doordash_full.run("doordash", stores=picked, store_names=store_names,
                                                 log=log, on_store=_tick)
            stores_this_run, items_this_run = len(picked), (n_items or 0)
        except Exception as e:
            log("[doordash_chains] run FAILED: %s" % str(e)[:200])
            stores_this_run = items_this_run = 0

    remaining_total = universe_total - covered_total - stores_this_run
    run_id = "ddchains-" + time.strftime("%Y%m%d-%H%M%S")
    rec = dict(run_id=run_id, ts=int(t0), universe_total=universe_total,
              covered_total=covered_total + stores_this_run, remaining_total=max(0, remaining_total),
              stores_landed_this_run=stores_this_run, items_landed_this_run=items_this_run,
              duration_s=round(time.time() - t0, 1))
    warehouse.write_accumulate("doordash_full_runs", [rec], key="run_id", fields=RUN_FIELDS)
    log("[doordash_chains] DONE — %d stores / %d items this run · %s: %d/%d covered, %d remaining · %.0fs"
        % (stores_this_run, items_this_run, "metro scope" if metro else "national",
           rec["covered_total"], universe_total, rec["remaining_total"], rec["duration_s"]))
    return rec


def _parse_metro(spec, radius_mi):
    """'--metro' CLI value: ';'-separated "City,ST" anchors -> city_centroid.metro_cities() scope."""
    import city_centroid as cc
    anchors = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        city, _, state = part.rpartition(",")
        anchors.append((city.strip(), state.strip()))
    scope = cc.metro_cities(anchors, radius_mi=radius_mi)
    if not scope:
        raise SystemExit("--metro matched no known cities — check the city,ST spelling: %r" % spec)
    return scope


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=None, help="max NEW stores this run")
    ap.add_argument("--shard", type=int, default=None)
    ap.add_argument("--nshard", type=int, default=None)
    ap.add_argument("--metro", default="",
                    help="';'-separated 'City,ST' anchors, e.g. 'Orlando,FL;Miami,FL' — restricts the "
                         "universe to a radius around each anchor (suburbs included) instead of national")
    ap.add_argument("--metro-radius-mi", type=float, default=30)
    a = ap.parse_args()
    metro = _parse_metro(a.metro, a.metro_radius_mi) if a.metro else None
    run(batch=a.batch, shard=a.shard, nshard=a.nshard, metro=metro)
