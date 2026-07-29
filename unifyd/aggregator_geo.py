#!/usr/bin/env python3
"""aggregator_geo.py — PRECISE geo for UberEats/Postmates outlets, whose only geo source is the store page.

UberEats/Postmates land from the sitemap with just name+slug — no city, no address, no coords — so the fast
city-centroid layer and the Census address geocoder can't touch them; the store PAGE is the only geo source.
This fetches it $0 (curl_cffi Safari + the ISP pool) and pulls PRECISE lat/lng (+ address) straight off the
schema.org block, stamping geo_precision='exact'. A fetched-but-empty page is stamped 'agg_miss' so it isn't
re-fetched forever — the pass drains the ~790k no-address UberEats/Postmates pool run over run. Bounded
(AGG_GEO_LIMIT) + concurrent; a large crawl that chips away. (DoorDash ships city+state, so it's mapped
instantly by the city-centroid fast layer instead — see city_centroid.py.)
"""
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import refresh_fast
import outlet_ident


def _fetch(url, tries=3):
    """$0 fetch — curl_cffi Safari-17 through the ISP pool (rotating exit)."""
    import resi
    try:
        from curl_cffi import requests as cr
    except Exception:
        return ""
    for a in range(tries):
        u = resi.isp_url() if resi.isp_enabled() else None
        px = {"http": u, "https": u} if u else None
        try:
            r = cr.get(url, impersonate="safari17_0", proxies=px, timeout=45)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
        except Exception:
            pass
        time.sleep(1 + a)
    return ""


def _ue_slug_map(sites=("ubereats", "postmates")):
    """{store_uuid -> slug} from the *_sitemap tables — the UberEats/Postmates store URL needs the slug."""
    out = {}
    for site in sites:
        try:
            for r in warehouse.query("%s_sitemap" % site, "SELECT store_uuid, slug FROM t"):
                if r.get("store_uuid"):
                    out[str(r["store_uuid"])] = r.get("slug") or ""
        except Exception:
            pass
    return out


STAGE = "agg_geo_stage"


def _stage(rows, shard_i, bucket):
    """Land one shard's bucket as its OWN parquet part. Concurrency-safe by construction: each writer
    owns a distinct path, so there is no read-modify-write for two machines to interleave."""
    import time as _t
    stamp = int(_t.time())
    recs = [dict(r, staged_at=stamp) for r in rows]
    warehouse.write_partition(STAGE, "s%02d_b%04d" % (shard_i, bucket), recs,
                              fields=refresh_fast.FLD + ["staged_at"])


def merge_stage(log=print):
    """Fold every staged part into src_outlets in ONE write_accumulate. Run serialized (geo_all does).

    Last-write-wins by `staged_at`, not by whichever part DuckDB happens to read last. Parts persist
    after a merge (there is no delete in the warehouse API), so a re-run would otherwise be able to
    re-apply an OLD staged row over a newer exact geo — a silent regression that would look like the
    geocoder losing accuracy for no reason.
    """
    try:
        rows = warehouse.query_parts(STAGE, "SELECT * FROM t")
    except Exception as e:                                       # noqa: BLE001
        log("[agg-geo] stage unreadable: %s" % str(e)[:100])
        return 0
    if not rows:
        log("[agg-geo] stage empty — nothing to merge")
        return 0
    best = {}
    for r in rows:
        k = (r.get("source"), r.get("store_id"))
        cur = best.get(k)
        if cur is None or (r.get("staged_at") or 0) >= (cur.get("staged_at") or 0):
            best[k] = r
    out = [{k: r.get(k) for k in refresh_fast.FLD} for r in best.values()]
    warehouse.write_accumulate("src_outlets", out, key=lambda r: (r["source"], r["store_id"]),
                               fields=refresh_fast.FLD)
    log("[agg-geo] merged %d staged rows (%d parts-worth, deduped from %d) -> src_outlets"
        % (len(out), len(best), len(rows)))
    return len(out)


def enrich_geo(limit=None, workers=None, log=print):
    # doordash is handled by the city-centroid fast layer (it ships city+state) — not here. ubereats/postmates
    # ship only name+slug, so we hit getStoreV1 DIRECTLY (getstore.py): a light JSON POST, cold, that returns the
    # store's exact geo + full street address. Far faster + more reliable than the store HTML page, so we can run
    # heavy concurrency (the ISP pool rotates exits → polite per-IP) and approach ~1h for the whole ~790k.
    # geo_precision (VARCHAR) is the marker — NOT addr_valid, a BOOL (writing a string into it voided the write).
    limit = limit if limit is not None else int(os.environ.get("AGG_GEO_LIMIT", "800000"))
    workers = workers or int(os.environ.get("AGG_GEO_WORKERS", "64"))
    gp = ("AND (geo_precision IS NULL OR geo_precision NOT IN ('exact', 'agg_miss')) "
          if warehouse.has_column("src_outlets", "geo_precision") else "")
    # SHARDING — 6 machines, one universe, no coordination.
    # Measured: ~450 rows/min at 64 workers, so a ~770k backlog is ~28h of fetching. That does not fit
    # one nightly window, and the answer is not a bigger cap (see the no-caps rule) but more machines.
    # The dispatcher spawns `shards: N` from the registry and hands each machine UE_SHARD=i/N; the split
    # is a stable hash, so shards cannot overlap or leave a gap and never need to talk to each other.
    #
    # A DIFFERENT SALT from the bucket split below. Both are `hash(...) % n` over the same key, so
    # reusing the expression would correlate them — a shard could draw its rows overwhelmingly from a
    # few buckets and leave the rest empty, silently doing a fraction of the work it reported.
    shard_sql, shard_lbl, shard_i = "", "1/1", 0
    try:
        _sh = os.environ.get("UE_SHARD", "").strip()
        if _sh and "/" in _sh:
            _i, _n = (int(x) for x in _sh.split("/", 1))
            if _n > 1 and 0 <= _i < _n:
                shard_sql = ("AND (hash(CAST(source AS VARCHAR) || '|' || CAST(store_id AS VARCHAR) "
                             "|| '#shard') %% %d) = %d " % (_n, _i))
                shard_lbl, shard_i = "%d/%d" % (_i + 1, _n), _i
    except Exception:                                            # noqa: BLE001 — a bad shard spec must
        shard_sql, shard_lbl, shard_i = "", "1/1", 0                         # run the WHOLE universe, never none
    gp = gp + shard_sql
    # MEMORY MUST SCALE WITH THE CHUNK, NOT THE BACKLOG.
    #
    # This used to be one `SELECT * ... LIMIT 800000`, materialising up to 800k full 27-column rows as
    # Python dicts before a single fetch happened. On a 4GB ephemeral (duckdb capped at ~1.9GB of it)
    # that stalls or dies before doing any work — which is exactly what was observed: two runs that
    # produced no output past "starting aggregator-geo" and landed nothing, with no error to explain
    # it. Chunking the WRITE was necessary but not sufficient; the READ was the wall.
    #
    # Hash buckets rather than LIMIT/OFFSET: OFFSET re-scans the whole table for every page, and there
    # is no stable sort key to page on. hash(source|store_id) % n is deterministic, evenly spread, and
    # each bucket is an independent query holding only its own slice.
    chunk = int(os.environ.get("AGG_GEO_CHUNK", "40000"))
    try:
        backlog = warehouse.query(
            "src_outlets",
            "SELECT count(*) n FROM t WHERE lat IS NULL AND source IN ('ubereats', 'postmates') "
            + gp)[0]["n"]
    except Exception as e:                                       # noqa: BLE001
        log("[agg-geo] could not size the backlog: %s" % str(e)[:100])
        return 0
    if not backlog:
        log("[agg-geo] no un-geocoded aggregator outlets")
        return 0
    target = min(int(backlog), int(limit))
    nb = max(1, -(-int(backlog) // chunk))                       # ceil — buckets over the WHOLE backlog
    log("[agg-geo] shard %s — backlog %d; %d buckets of ~%d (target this run: %d)"
        % (shard_lbl, backlog, nb, chunk, target))
    import getstore
    out, cnt, lock = [], [0], threading.Lock()
    cur = [0]                                                    # rows in the bucket being worked

    def _work(r):
        sid, src = str(r["store_id"]), r["source"]
        d = dict(r)
        d["geo_precision"] = "agg_miss"                          # tried marker (overwritten to 'exact' on a hit)
        try:
            e = getstore.store_geo(sid, session="ag%d" % (cnt[0] % 400), site=src)
            if e.get("lat") is not None:
                d["lat"], d["lng"] = e["lat"], e["lng"]           # getStoreV1 location = exact rooftop geo
                d["geo_precision"] = "exact"
            if e.get("street"):                                   # …plus the full street address
                d["address"] = e["street"]
                d["city"] = e.get("city") or d.get("city") or ""
                d["state"] = e.get("state") or d.get("state") or ""
                d["zip"] = e.get("zip") or d.get("zip") or ""
        except Exception:
            pass
        with lock:
            out.append({k: d.get(k) for k in refresh_fast.FLD})
            cnt[0] += 1
            if cnt[0] % 100 == 0:
                log("  [agg-geo] %d/%d in bucket" % (cnt[0], cur[0]))

    # CHECKPOINT IN CHUNKS — do not hold 800k rows of work hostage to a single write at the end.
    #
    # This used to run the whole batch through the pool and write ONCE, after. The `geo` registry
    # entry has timeout=10800 (3h); a full pass over the ~770k aggregator backlog does not finish in
    # that window, so the process was killed before the write and EVERY fetched row was discarded —
    # every day, at full network cost, landing nothing. Measured effect: ubereats sat at 28,861 of
    # 529,646 geocoded (5.5%) with a 500,790-row backlog that never moved.
    #
    # Writing per chunk makes progress durable: a timeout now costs at most one chunk, and successive
    # daily runs actually advance because the rows written are no longer re-selected (lat IS NULL).
    # Chunk size trades write amplification (write_accumulate rewrites the whole table) against how
    # much a kill throws away — 40k is ~20 writes for the full backlog.
    got_addr = got_geo = landed = 0
    for b in range(nb):
        if landed >= target:
            break
        try:
            part = warehouse.query(
                "src_outlets",
                "SELECT * FROM t WHERE lat IS NULL AND source IN ('ubereats', 'postmates') " + gp +
                "AND (hash(CAST(source AS VARCHAR) || '|' || CAST(store_id AS VARCHAR)) %% %d) = %d"
                % (nb, b))
        except Exception as e:                                   # noqa: BLE001
            log("  [agg-geo] bucket %d/%d read failed: %s" % (b + 1, nb, str(e)[:90]))
            continue
        if not part:
            continue
        cur[0], cnt[0] = len(part), 0
        del out[:]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_work, part))
        if not out:
            continue
        # STAGE, DO NOT MERGE. write_accumulate rewrites the WHOLE src_outlets table (read -> merge ->
        # write), so N shards doing it concurrently clobber each other and the last writer silently
        # drops the rest — the hazard geo_all exists to serialize away, and the reason sharding was
        # declared but left off. write_partition lands ONE file per (shard, bucket) instead: disjoint
        # paths, no read-modify-write, so shards cannot collide by construction. merge_stage() folds
        # the whole stage into src_outlets in a single serialized pass afterwards.
        _stage(out, shard_i, b)
        got_addr += sum(1 for o in out if o.get("address"))
        got_geo += sum(1 for o in out if o.get("lat") is not None)
        landed += len(out)
        log("  [agg-geo] bucket %d/%d — %d landed so far (+%d geo, +%d address) — durable"
            % (b + 1, nb, landed, got_geo, got_addr))
    log("[agg-geo] +%d address (→ geocode pass), +%d direct geo of %d fetched -> src_outlets"
        % (got_addr, got_geo, landed))
    # The remaining backlog is stated every run. A pass that drains 40k of 500k must not read the
    # same as one that finished — that ambiguity is why this went unnoticed for so long.
    # A SHARD MUST NEVER MERGE. If each of N shards folded the stage into src_outlets itself, we would
    # have re-created the exact concurrent read-modify-write this staging exists to prevent. Only an
    # unsharded run merges its own work; the fleet's stage is merged once, serialized, by geo_all.
    if shard_lbl == "1/1":
        merge_stage(log=log)
    else:
        log("[agg-geo] shard %s staged its work; geo_all merges the fleet's stage serially" % shard_lbl)

    left = int(backlog) - landed
    if left > 0:
        log("[agg-geo] %d aggregator outlets still un-geocoded; run again to continue draining" % left)
    return landed


def run(log=print):
    return enrich_geo(log=log)


if __name__ == "__main__":
    print("enriched %d aggregator outlets" % enrich_geo())
