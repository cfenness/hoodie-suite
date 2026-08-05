#!/usr/bin/env python3
"""ue_enrich.py — drain the backlog of un-resolved UberEats items, off the critical path.

WHY THIS IS A SEPARATE JOB. The store sweep is ONE request per store: 502,212 requests, ~30 minutes
across the fleet. Enrichment is one request per NEW item, and at a measured ~82 items/store that turns
the same job into ~41.7M requests — and it ran SERIALLY inside each store's worker thread, ~18.5s per
store, which matched the observed fleet rate exactly. The pull we were measuring was a 30-minute job
wearing a 46-hour coat.

They separate cleanly because they answer different questions:
  * price / stock / promo are VOLATILE, and arrive free with the catalog call the sweep already makes.
  * UPC / GTIN / brand / size / ABV are STATIC per item — fetch once, ever.

So the sweep stays fast and complete on a daily clock, and this drains the static-attribute backlog
continuously. Day one is a real backfill; after that only genuinely-new items cost anything, because
a resolved item is never re-fetched.

CONTRACT (the same rules as the sweep, learned the hard way today):
  * append-only PARTS, never write_accumulate — concurrent shards merging a catalog lose rows.
  * checkpointed, so a killed shard resumes instead of restarting.
  * the work-list is a QUERY, not a cap: everything still unresolved, sharded by a stable hash.
  * reports its own denominator so completeness is graded against the JOB, not a watermark.

    python3 ue_enrich.py --shard 0/8
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import warehouse                      # noqa: E402
import ue_catalog                     # noqa: E402
import ubereats                       # noqa: E402


def backlog_sql(shard=0, nshard=1):
    """The work-list query, as a string. Pure, so it is testable without a warehouse or DuckDB.

    SHARD IN SQL, NOT IN PYTHON. The old code materialised the ENTIRE backlog in every shard and
    then discarded 7/8 of it — tolerable at 549k rows from the aggregate, not at parts scale (the
    same over-materialisation that OOM-killed the first fold). DuckDB's hash() is stable and
    partitions the space disjointly, which is all sharding needs; it deliberately does NOT match
    ue_catalog._shard_of's md5 assignment, because these are different work-lists and nothing
    requires them to agree.

    One row per (store, item): the most recent non-empty section context, and ONLY items that no
    part has ever resolved. HAVING rather than WHERE, so a key resolved by ANY part — postmates'
    inline enrich, or a previous enrich run — drops out entirely instead of being re-fetched forever.
    """
    where_shard = ("AND hash(item_uuid) %% %d = %d" % (nshard, shard)) if nshard > 1 else ""
    return (
        "SELECT store_uuid, item_uuid, "
        "  arg_max(store_name, __part) FILTER (WHERE NULLIF(store_name,'') IS NOT NULL) AS store_name, "
        "  COALESCE(arg_max(section,    __part) FILTER (WHERE NULLIF(section,'')    IS NOT NULL), '') AS section, "
        "  COALESCE(arg_max(subsection, __part) FILTER (WHERE NULLIF(subsection,'') IS NOT NULL), '') AS subsection "
        "FROM t "
        "WHERE item_uuid IS NOT NULL AND store_uuid IS NOT NULL %s "
        "GROUP BY store_uuid, item_uuid "
        "HAVING count(*) FILTER (WHERE NULLIF(upc,'') IS NOT NULL "
        "                           OR NULLIF(gtin,'') IS NOT NULL) = 0" % where_shard)


def backlog(site="ubereats", shard=0, nshard=1, log=print):
    """Items still missing an identifier, WITH the section context getMenuItemV1 requires.

    READS THE PARTS, NOT THE AGGREGATE — and that is the whole point.

    This job had landed ZERO rows since it was created. Proven live on 2026-08-04, same item, same
    session, same machine:

        WITH real section ctx   HTTP 200 {"status":"success", ...}   -> real item detail
        EMPTY ctx               HTTP 200 {"status":"failure","message":"invalid_uuid","code":"404"}

    The chain: `<site>_products` (the stage-2 aggregate) has no section/subsection columns — they
    were added to the write schema after that table was first written, and `write_accumulate`
    carries the old column set forward forever. The old code detected that and degraded to '' on the
    documented belief that "getMenuItemV1 accepts empty section ids". That belief is now false, so
    every item 404'd, every failure hit a bare `continue`, and the run still exited successfully:
    8 shards/day against ~549k queued items, zero output, zero run records.

    The PARTS carry section/subsection on 100% of rows (measured: 40,629/40,629 across the newest
    six). So the work-list comes from there. This also means enrichment no longer depends on the
    aggregate's schema at all — the failure above can't recur through a column the fold happens not
    to carry.

    Still derived, never capped: if the list is large that is the honest size of the job, and the
    answer is more shards.
    """
    parts = "%s_products_parts" % site
    sql = backlog_sql(shard, nshard)
    # `warehouse.query_parts` builds its view without DuckDB's filename column, and the recency
    # order here IS the part filename (the parts carry no timestamp — see fold.py). So read the file
    # list through fold's helpers rather than growing a second parts-reading path.
    import fold
    files = fold._part_names(parts)
    if not files:
        log("[enrich] no parts for %s yet — nothing to enrich" % parts)
        return []
    files_sql = ", ".join("'%s'" % fold._part_sql(f).replace("'", "") for f in files)
    con = warehouse.connect()
    cur = con.execute(sql.replace(
        "FROM t ",
        "FROM read_parquet([%s], union_by_name=true, filename='__part') " % files_sql))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    with_ctx = sum(1 for r in rows if (r.get("section") or ""))
    log("[enrich] %s unresolved items in shard %d/%d (the completeness denominator); "
        "%s carry section context"
        % (f"{len(rows):,}", shard, nshard, f"{with_ctx:,}"))
    if rows and not with_ctx:
        # Every call would 404. Say so instead of burning a shard's worth of requests to learn it.
        log("[enrich] WARNING: no item carries section context — getMenuItemV1 will reject every "
            "request (invalid_uuid). Check that the parts still write section/subsection.")
    return rows


def run(site="ubereats", shard=0, nshard=1, workers=None, log=print):
    workers = workers or ue_catalog.auto_workers(nshard, log=log)
    day = time.strftime("%Y-%m-%d")
    work = backlog(site, shard, nshard, log=log)
    if not work:
        # AN EMPTY WORK-LIST IS NOT PROOF THE BACKLOG IS DRAINED. It is equally what you get from a
        # parts table that has not landed yet, so distinguish the two instead of claiming success —
        # the whole reason this job's silence went unnoticed for weeks is that it never said which
        # of the two had happened.
        import fold
        has_parts = bool(fold._part_names("%s_products_parts" % site))
        return {"status": "ok" if has_parts else "degraded",
                "site": site, "shard": "%d/%d" % (shard, nshard),
                "items_total": 0, "items_done": 0, "remaining": 0,
                "note": ("no unresolved items — backlog is drained" if has_parts else
                         "NO PARTS to derive a work-list from — this is not a drained backlog")}

    by_store = {}
    for r in work:
        by_store.setdefault(r["store_uuid"], []).append(r)
    log("[enrich] %s items across %s stores (%d workers)"
        % (f"{len(work):,}", f"{len(by_store):,}", workers))

    lock = threading.Lock()
    pending, n_done, n_hit, n_miss, batch = [], 0, 0, 0, [0]
    t0 = time.time()

    def _flush(force=False):
        if not pending or (not force and len(pending) < 2000):
            return
        batch[0] += 1
        warehouse.write_partition(
            "%s_products_parts" % site, "%s_enr_s%02d_b%04d" % (day, shard, batch[0]),
            list(pending), fields=[k for k in ue_catalog.PRODUCT_FIELDS if k != "raw_json"])
        pending.clear()

    def _one(su_items):
        nonlocal n_done, n_hit, n_miss
        su, items = su_items
        # ONE session, MANY items per store — the request context (section/subsection) already lives in
        # the catalog row, so no store re-fetch is needed to enrich its items.
        idx = {r["item_uuid"]: {"section": r.get("section") or "",
                                "subsection": r.get("subsection") or ""} for r in items}
        stub = [{"item_uuid": r["item_uuid"], "name": ""} for r in items]
        try:
            out = ue_catalog.enrich_items(su, (items[0].get("store_name") or ""), stub, idx,
                                          site=site, known=None)
        except Exception:
            out = []
        got = []
        for rec in (out or []):
            if rec.get("upc") or rec.get("gtins"):
                rec["store_uuid"] = su
                rec["store_name"] = items[0].get("store_name") or ""
                got.append(rec)
        with lock:
            n_done += len(items)
            n_hit += len(got)
            n_miss += len(items) - len(got)
            if got:
                pending.extend(got)
                # The payload is an EVENT — append it, never merge it. Same rule as the sweep.
                try:
                    import raw_capture
                    raw_capture.record(site, day, "enr_s%02d" % shard,
                                       [{"kind": "item", "entity_id": g.get("item_uuid"),
                                         "parent_id": su, "raw_json": g.get("raw_json")} for g in got],
                                       log=lambda *_: None)
                except Exception:
                    pass
            _flush()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, list(by_store.items())))
    _flush(force=True)

    dur = round(time.time() - t0, 1)
    rec = {"status": "ok" if n_done >= len(work) else "incomplete",
           "site": site, "shard": "%d/%d" % (shard, nshard),
           "items_total": len(work), "items_done": n_done, "remaining": max(0, len(work) - n_done),
           "resolved": n_hit, "unresolved": n_miss,
           "items_per_sec": round(n_done / max(1.0, dur), 2), "duration_s": dur}
    log("HOODIE_RESULT " + json.dumps(rec))
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", default="ubereats")
    ap.add_argument("--shard", default=os.environ.get("UE_SHARD", "0/1"))
    ap.add_argument("--workers", type=int, default=None)
    a = ap.parse_args(argv)
    i, _, n = a.shard.partition("/")
    return run(a.site, int(i or 0), int(n or 1), workers=a.workers)


if __name__ == "__main__":
    r = main()
    sys.exit(0 if (r or {}).get("status") in ("ok", "current") else 1)
