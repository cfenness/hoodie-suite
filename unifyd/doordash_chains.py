#!/usr/bin/env python3
"""doordash_chains.py — chain-attribution driver for doordash_full.py, at real (bounded) scale.

doordash_full.py's own chain catalog (doordash.py's CHAINS dict) has only 6 hand-seeded TEST store
ids total — nowhere near national. The store universe that DOES exist at scale, `doordash_stores`
(harvested $0 from DoorDash's sitemaps by doordash_sitemap.py, ~587k rows), carries no reliable
`chain` column — different writers land different column sets, and the bulk (sitemap-sourced) rows
have none. The only attribution mechanism available is a NAME-SUBSTRING heuristic — the exact one
doordash_naop.py's `_RETAIL_CHAINS`/`_is_retail` already use to EXCLUDE retail chains from its
on-premise sweep. This module inverts that: match FOR a curated list of major retail chains, then
drive doordash_full.run() with the real matched store-id list instead of the 6-store placeholder.

Deliberately ONE bounded run, not an incremental daily crawl (per the "no multi-day runs" rule):
each chain is capped at DDFULL_STORES_PER_CHAIN stores (default 15) so total request volume — the
category-tree walk is ~15-20+ requests per store — stays inside the registry entry's timeout. $0:
doordash_full.py/doordash.py already fetch through the flat-rate ISP pool (Bright Data retired for
DoorDash 2026-07-24), never a metered proxy.

    python doordash_chains.py                 # sweep every TARGET_CHAINS match, capped per chain
    python doordash_chains.py --chains cvs,totalwine --stores-per-chain 5   # a smaller manual test
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import doordash_full

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

RUN_FIELDS = ["run_id", "ts", "chains_attempted", "chains_landed", "stores_attempted",
              "items_landed", "per_chain", "duration_s"]


def _match_chain(name):
    n = (name or "").lower()
    for key, needles in TARGET_CHAINS.items():
        if any(needle in n for needle in needles):
            return key
    return None


def bucket_stores(log=print):
    """doordash_stores -> {chain_key: [store_id, ...]}, via the name-substring heuristic."""
    try:
        universe = warehouse.query("doordash_stores",
                                   "SELECT store_id, name FROM t WHERE store_id IS NOT NULL LIMIT 300000")
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


def run(chains=None, stores_per_chain=None, log=print):
    """ONE bounded sweep: bucket doordash_stores by chain, cap each chain's store list, drive
    doordash_full.run() per matched chain. Lands a summary row to doordash_full_runs (the registry
    entry's verify-landing table) via write_accumulate — single-file, so run_one's row-count check
    sees a real delta (write_partition dirs are invisible to it)."""
    t0 = time.time()
    cap = stores_per_chain or int(os.environ.get("DDFULL_STORES_PER_CHAIN", "15"))
    buckets = bucket_stores(log=log)
    if chains:
        buckets = {k: v for k, v in buckets.items() if k in set(chains)}
    per_chain, total_items, landed = {}, 0, 0
    for key, store_ids in sorted(buckets.items()):
        picked = store_ids[:cap]
        log("[doordash_chains] %s: %d matched, running %d" % (key, len(store_ids), len(picked)))
        try:
            _run_id, n_items = doordash_full.run(key, stores=picked, log=log)
        except Exception as e:
            log("[doordash_chains] %s FAILED: %s" % (key, str(e)[:160]))
            per_chain[key] = {"stores": len(picked), "items": 0, "error": str(e)[:160]}
            continue
        per_chain[key] = {"stores": len(picked), "items": n_items or 0}
        total_items += n_items or 0
        if n_items:
            landed += 1
    run_id = "ddchains-" + time.strftime("%Y%m%d-%H%M%S")
    rec = dict(run_id=run_id, ts=int(t0), chains_attempted=len(buckets), chains_landed=landed,
              stores_attempted=sum(v["stores"] for v in per_chain.values()),
              items_landed=total_items, per_chain=str(per_chain), duration_s=round(time.time() - t0, 1))
    warehouse.write_accumulate("doordash_full_runs", [rec], key="run_id", fields=RUN_FIELDS)
    log("[doordash_chains] DONE — %d/%d chains landed, %d items, %.0fs"
        % (landed, len(buckets), total_items, rec["duration_s"]))
    return rec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chains", default="", help="comma-separated chain keys (default: all matched)")
    ap.add_argument("--stores-per-chain", type=int, default=None)
    a = ap.parse_args()
    run(chains=[c.strip() for c in a.chains.split(",") if c.strip()] or None,
        stores_per_chain=a.stores_per_chain)
