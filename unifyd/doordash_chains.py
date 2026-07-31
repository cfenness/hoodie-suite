#!/usr/bin/env python3
"""doordash_chains.py — chain-attribution driver for doordash_full.py, at real national scale.

doordash_full.py's own chain catalog (doordash.py's CHAINS dict) has only 6 hand-seeded TEST store
ids total — nowhere near national. The store universe that DOES exist at scale, `doordash_stores`
(harvested $0 from DoorDash's sitemaps by doordash_sitemap.py, ~587k rows), carries no reliable
`chain` column — different writers land different column sets, and the bulk (sitemap-sourced) rows
have none. The only attribution mechanism available is a NAME-SUBSTRING heuristic — the exact one
doordash_naop.py's `_RETAIL_CHAINS`/`_is_retail` already use to EXCLUDE retail chains from its
on-premise sweep. This module inverts that: match FOR a curated list of major retail chains, then
drive doordash_full.run() with the real matched store-id list instead of the 6-store placeholder.

RESUMABLE BATCHES, not a one-shot capped run (learned the hard way — an artificial small cap on a
"full"/master-data pull that never gets revisited is exactly how a pipeline silently drifts hundreds
of thousands of records short of true coverage while everyone assumes it's complete). Each run, per
chain: read how many stores are ALREADY landed in `<chain>_products_full` (doordash_full.run now
`write_accumulate`s — see its own fix — so this is a real merge, not a table a later batch could
wipe), subtract those from the full matched universe, take up to DDFULL_BATCH_PER_CHAIN of what's
LEFT. Repeated triggers converge every matched chain to full coverage; nothing is silently capped
forever, and matched/covered/remaining is printed AND landed every run so a coverage gap is always
a visible, queryable fact — never discovered by surprise later. $0: doordash_full.py/doordash.py
already fetch through the flat-rate ISP pool (Bright Data retired for DoorDash 2026-07-24).

    python doordash_chains.py                                  # one batch, every matched chain
    python doordash_chains.py --chains cvs,totalwine --batch 50 # a smaller manual slice
"""
import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import doordash_full

# Chains process CONCURRENTLY, not sequentially — measured live 2026-07-30: with 12 chains run
# one-at-a-time (each internally parallel across DDFULL_WORKERS stores), the SMALLEST chain
# (abcfinewine, 123 stores) alone took 11.9 hours and was still not done — the full 24,919-store
# national universe projected to ~119 days at that rate. Sequential chains cap total concurrency at
# DDFULL_WORKERS regardless of how many chains are queued; running chains themselves concurrently
# multiplies that. Concurrency is free on the flat-rate ISP pool (same reasoning doordash_full.py's
# own per-store concurrency already uses) — the constraint was never cost, it was this loop.
CHAIN_CONCURRENCY = int(os.environ.get("DDFULL_CHAIN_CONCURRENCY", "4"))

# chain key (becomes the `<key>_products_full` table doordash_full.run writes) -> lowercase name
# substrings to match against doordash_stores.name. Reuses doordash_naop.py's _RETAIL_CHAINS list
# (the exact set DoorDash's own store sitemap surfaces as major retail banners) rather than
# inventing a second one — same substrings, opposite purpose (match FOR, not exclude).
TARGET_CHAINS = {
    "totalwine":   ["total wine"],
    "cvs":         ["cvs"],
    "walgreens":   ["walgreens"],
    "circlek":     ["circle k"],
    "walmart":     ["walmart"],
    "target":      ["target"],
    "kroger":      ["kroger"],
    "safeway":     ["safeway"],
    "albertsons":  ["albertsons"],
    "publix":      ["publix"],
    "meijer":      ["meijer"],
    "costco":      ["costco", "sam s club"],
    "bevmo":       ["bevmo"],
    "abcfinewine": ["abc fine wine"],
    "binnys":      ["binny"],
}

RUN_FIELDS = ["run_id", "ts", "chains_attempted", "stores_landed_this_run", "items_landed_this_run",
              "matched_total", "covered_total", "remaining_total", "per_chain", "duration_s"]


def _match_chain(name):
    n = (name or "").lower()
    for key, needles in TARGET_CHAINS.items():
        if any(needle in n for needle in needles):
            return key
    return None


def bucket_stores(log=print):
    """doordash_stores -> {chain_key: [store_id, ...]}, via the name-substring heuristic. This is
    the TOTAL national universe per chain — not batch-limited; batching happens per-chain below
    against what's already landed. NO LIMIT here: doordash_stores has grown past 600k (767,716 as of
    2026-07-30) and a capped read here silently drops whatever chain-matched stores sit past the cap
    every single run, forever — exactly the "we fixed it and it never got revisited" undercount this
    module's own docstring warns about for the batching below. Two string columns across ~1M rows is
    trivial for DuckDB; there is no cost reason to cap it."""
    try:
        universe = warehouse.query("doordash_stores",
                                   "SELECT store_id, name FROM t WHERE store_id IS NOT NULL")
    except Exception as e:
        log("[doordash_chains] doordash_stores unreadable: %s" % str(e)[:140])
        return {}
    buckets = {}
    for r in universe:
        key = _match_chain(r.get("name"))
        if key:
            buckets.setdefault(key, []).append(str(r["store_id"]))
    log("[doordash_chains] universe=%d matched=%d across %d chains"
        % (len(universe), sum(len(v) for v in buckets.values()), len(buckets)))
    return buckets


def _landed_stores(chain):
    """Store ids already covered for this chain, read from its own <chain>_products_full table
    (accumulate-merged by doordash_full.run — see that fix). Empty/unreadable = nothing landed yet,
    never treated as an error (a chain's first run always starts from zero)."""
    try:
        rows = warehouse.query(chain + "_products_full", "SELECT DISTINCT store FROM t")
        return {str(r["store"]) for r in rows}
    except Exception:
        return set()


def run(chains=None, batch=None, log=print):
    """ONE resumable batch: bucket doordash_stores by chain, take up to `batch` NOT-YET-LANDED
    stores per matched chain, drive doordash_full.run() for just that increment. Repeated triggers
    converge every chain to full national coverage — never a permanent small cap.

    Wrapped in runlog.track() — the SAME live-progress mechanism abc_catalog.py/specs_scraper.py/
    total_wine_full.py already use (writes to the shared scrape_runs table from inside this
    subprocess, so it's visible in /api/jobs regardless of when the parent's stdout capture
    happens to flush). Without this a "full" pull just shows 'running' with no signal for
    however many minutes it takes — exactly the kind of invisible-until-it's-done job that made
    the earlier cap silently unnoticeable.

    ONE tally for the WHOLE call (installed here, not per-chain inside doordash_full.run) — with
    chains now running concurrently, a fresh tally per chain would race (each chain's own start
    clobbering the others'). doordash.py's _fetch() records into whatever tally is installed and
    periodically heartbeats it (HOODIE_DD_PROGRESS), so a "why is this slow" question is answerable
    from the log stream instead of inferring it from log-line timestamps.

    CHAINS RUN CONCURRENTLY (up to DDFULL_CHAIN_CONCURRENCY at once, default 4) — see the module
    docstring for why: sequential chains capped total concurrency at DDFULL_WORKERS regardless of
    how many chains were queued. Progress is tracked per-chain (`chain_done`) and summed under a
    lock, since runlog.progress() needs one cumulative total but multiple chains now advance it at
    once from different threads."""
    t0 = time.time()
    import blocks
    blocks.install()
    cap = batch or int(os.environ.get("DDFULL_BATCH_PER_CHAIN", "200"))
    buckets = bucket_stores(log=log)
    if chains:
        buckets = {k: v for k, v in buckets.items() if k in set(chains)}

    # PLAN first (read-only): every chain's picked batch, so the total store count for THIS run is
    # known before work starts — runlog.track needs total up front for pct/ETA to mean anything.
    plan, matched_total, covered_total = {}, 0, 0
    for key, store_ids in sorted(buckets.items()):
        matched_total += len(store_ids)
        already = _landed_stores(key)
        covered_total += len(already)
        todo = [s for s in store_ids if s not in already]
        picked = todo[:cap]
        plan[key] = {"matched": len(store_ids), "covered": len(already), "picked": picked}
        log("[doordash_chains] %s: matched=%d covered=%d remaining=%d taking=%d"
            % (key, len(store_ids), len(already), len(todo), len(picked)))
    total_picked = sum(len(p["picked"]) for p in plan.values())

    import runlog
    per_chain = {}
    stores_this_run = items_this_run = 0
    active = [(key, p) for key, p in plan.items() if p["picked"]]
    for key, p in plan.items():
        if not p["picked"]:
            per_chain[key] = {"matched": p["matched"], "covered": p["covered"], "taken": 0, "items": 0}

    with runlog.track("doordash-full", total=total_picked) as r:
        progress_lock = threading.Lock()
        chain_done = {}    # key -> stores completed so far in THAT chain, summed for the cumulative total

        def make_tick(key):
            def _tick(i, n):
                with progress_lock:
                    chain_done[key] = i
                    r.progress(sum(chain_done.values()), total_picked)
            return _tick

        def _run_one(key, p):
            picked = p["picked"]
            try:
                _run_id, n_items = doordash_full.run(key, stores=picked, log=log, on_store=make_tick(key))
                return key, {"matched": p["matched"], "covered": p["covered"], "taken": len(picked),
                            "items": n_items or 0}, len(picked), (n_items or 0)
            except Exception as e:
                log("[doordash_chains] %s FAILED: %s" % (key, str(e)[:160]))
                return key, {"matched": p["matched"], "covered": p["covered"], "taken": len(picked),
                            "items": 0, "error": str(e)[:160]}, 0, 0

        with ThreadPoolExecutor(max_workers=max(1, CHAIN_CONCURRENCY)) as ex:
            futures = [ex.submit(_run_one, key, p) for key, p in active]
            for fut in as_completed(futures):
                key, result, taken, items = fut.result()
                per_chain[key] = result
                stores_this_run += taken
                items_this_run += items

    remaining_total = matched_total - covered_total - stores_this_run
    run_id = "ddchains-" + time.strftime("%Y%m%d-%H%M%S")
    rec = dict(run_id=run_id, ts=int(t0), chains_attempted=len(buckets),
              stores_landed_this_run=stores_this_run, items_landed_this_run=items_this_run,
              matched_total=matched_total, covered_total=covered_total + stores_this_run,
              remaining_total=max(0, remaining_total), per_chain=str(per_chain),
              duration_s=round(time.time() - t0, 1))
    warehouse.write_accumulate("doordash_full_runs", [rec], key="run_id", fields=RUN_FIELDS)
    log("[doordash_chains] DONE — %d stores / %d items this run · national: %d/%d covered, %d remaining · %.0fs"
        % (stores_this_run, items_this_run, rec["covered_total"], matched_total,
           rec["remaining_total"], rec["duration_s"]))
    return rec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chains", default="", help="comma-separated chain keys (default: all matched)")
    ap.add_argument("--batch", type=int, default=None, help="max NEW stores per chain this run")
    a = ap.parse_args()
    run(chains=[c.strip() for c in a.chains.split(",") if c.strip()] or None, batch=a.batch)
