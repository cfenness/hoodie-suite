#!/usr/bin/env python3
"""bucketize.py — move an accumulating catalog to the BUCKETED layout, on Fly, verified.

WHY THIS EXISTS. `write_accumulate` merges by rewriting the WHOLE table: it reads every existing row,
drops the ones being replaced, appends the new batch. That is fine for a small catalog and fatal for a
growing one — each flush costs more than the last, so a crawl dies later and later as it succeeds more.
UberEats hit this: `ubereats_products` carries a full `raw_json` per row, and five to eight shards per
fleet were OOM-killed mid-merge while the crawl itself was working fine.

The bucketed (v2) layout is the fix the warehouse already documents: the merge happens per md5-prefix
bucket in DuckDB, so peak memory is bounded by ONE bucket instead of the table, no matter how large the
table grows. `migrate_to_bucketed` verifies row count AND distinct-key count against the original before
writing the manifest, and leaves the original single file untouched as the rollback copy.

WHY A MODULE AND NOT A SHELL ONE-LINER. Two standing rules. Nothing runs on anyone's Mac — this touches
production warehouse data, so it executes on Fly like every other job. And anything used to make the
system work belongs IN the app, not in a scrollback buffer: the next accumulating table to outgrow a
whole-table merge should run this, not rediscover it.

    python3 bucketize.py                      # report which catalogs are bucketed and which aren't
    python3 bucketize.py ubereats_products    # migrate one (idempotent — a bucketed table is a no-op)

NEVER RUN MID-SCRAPE: a concurrent writer during the split would land rows into the old layout that the
manifest does not know about. The bench shows live runs; check it is idle first. This refuses to start
if the source's journal shows an active run.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import warehouse  # noqa: E402

# Tables that ACCUMULATE (write_accumulate) and therefore pay a whole-table merge per write. key_cols
# must be the string identity columns the merge de-duplicates on — the same identity write_accumulate
# uses, or the migration would silently change what "already have this row" means.
# hex_len 2 = 256 buckets: sized for where these tables are GOING (502k stores x ~100 items is tens of
# millions of rows), not where they are today.
CATALOGS = {
    "ubereats_products": (["store_uuid", "item_uuid"], 2),
    "postmates_products": (["store_uuid", "item_uuid"], 2),
    "abc_catalog": (["sku"], 1),
    # Added fixing the 2026-07-29 OOM triage: geo (mem=16384) and binnys (default mem) both died in
    # write_accumulate's v1 read-modify-write on tables at 1.5-1.8M rows — the exact shape this module
    # exists for. Both keys are the identity write_accumulate already merges on at every call site
    # (verified — changing key_cols here would silently redefine "already have this row").
    "src_outlets": (["source", "store_id"], 2),      # key used by 6 write_accumulate call sites (geo_all's
                                                      # stages, refresh_fast, ue_sitemap) — all consistent
    "binnys_products": (["sku", "store"], 2),
}

# The source(s) that write each catalog, so an active run can be detected before touching the layout.
# A table may have more than one writer (src_outlets: fast-geo/geocode/aggregator-geo run inside "geo",
# plus refresh_fast/ue_sitemap/naop/build-outlets write it directly) — a list checks all of them.
WRITER = {"ubereats_products": "ubereats", "postmates_products": "postmates", "abc_catalog": "abc-fws",
          "src_outlets": ["geo", "fast-geo", "geocode", "naop", "build-outlets"],
          "binnys_products": "binnys"}


def _live(table):
    """True if any source that writes `table` has a run in flight. A migration racing a writer is the
    one way this operation can lose data, so it is checked rather than assumed."""
    sids = WRITER.get(table)
    if not sids:
        return False
    sids = [sids] if isinstance(sids, str) else sids
    try:
        import run_journal
        for sid in sids:
            for d in run_journal.recent(limit=40, source=sid):
                if d.get("status") in ("queued", "starting", "running"):
                    return "%s:%s" % (sid, d.get("run_id"))
    except Exception:
        pass                              # journal unreadable → fall through to the caller's judgement
    return False


def status(log=print):
    log("%-22s %12s  %-9s %s" % ("table", "rows", "layout", "key"))
    for t, (keys, _hex) in sorted(CATALOGS.items()):
        try:
            n = warehouse.row_count(t)
        except Exception:
            n = -1
        lay = "bucketed" if warehouse.read_manifest(t) else "single"
        log("%-22s %12s  %-9s %s" % (t, ("?" if n < 0 else "{:,}".format(n)), lay, ",".join(keys)))


def migrate(table, log=print):
    if table not in CATALOGS:
        log("bucketize: %r is not a declared accumulating catalog (%s)" % (table, ", ".join(sorted(CATALOGS))))
        return 2
    if warehouse.read_manifest(table):
        log("bucketize: %s is already bucketed — nothing to do" % table)
        return 0
    run = _live(table)
    if run:
        log("bucketize: REFUSING — %s has an active run (%s). A writer during the split would land rows "
            "the manifest never sees. Wait for it to finish." % (table, run))
        return 3
    keys, hexlen = CATALOGS[table]
    before = warehouse.row_count(table)
    log("bucketize: %s — %s rows, key=%s, %d buckets" % (table, "{:,}".format(before), "+".join(keys), 16 ** hexlen))
    log("bucketize: migrating (row count AND distinct-key count are verified before the manifest is written;")
    log("           the original single file is kept untouched as the rollback copy)")
    man = warehouse.migrate_to_bucketed(table, keys, hex_len=hexlen)
    after = warehouse.row_count(table)
    parts = len((man or {}).get("parts") or {})
    log("bucketize: DONE — %s rows across %d bucket parts (was %s)" % ("{:,}".format(after), parts, "{:,}".format(before)))
    if after != before:
        log("bucketize: WARNING — row count moved (%s → %s). Roll back by dropping the manifest."
            % ("{:,}".format(before), "{:,}".format(after)))
        return 1
    return 0


def main(argv):
    if not argv:
        status()
        return 0
    rc = 0
    for t in argv:
        rc = migrate(t) or rc
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
