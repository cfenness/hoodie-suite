#!/usr/bin/env python3
"""raw_capture.py — the append-only home for full source payloads.

THE RULE THIS SERVES: discard nothing. Every scraper keeps the entire raw payload beside the modelled
row, because the fields we don't model today are the ones we'll want next quarter, and re-scraping to
recover them costs more than storing them ever will.

THE MISTAKE IT FIXES: those payloads were being stored INSIDE the accumulating catalogs. `write_accumulate`
merges by rewriting the whole table — read every existing row, drop the replaced ones, append the batch —
so a fat `raw_json` column is re-read and re-written on every single flush, forever. Two consequences,
both observed live on UberEats:

  * memory scales with the TABLE, not the batch. 33,250 rows exceeded a 1.1GB DuckDB limit purely because
    each row carried a payload. Five to eight shards per fleet were OOM-killed mid-merge while the crawl
    itself was working — landing +28,991 rows in the same run that lost every shard.
  * it gets WORSE as the scrape succeeds. Each flush re-writes everything landed so far, so the cost per
    batch grows all crawl. A source is punished for collecting.

Bucketing bounds that. It does not remove it. The shape is what's wrong: a payload is an EVENT (this is
what the source said at this moment), not a mutable attribute of a product. Events append; they are never
merged. So they belong in a date-partitioned table, exactly like `retail_observations`.

    raw_capture.record("ubereats", day, "s00_b0001", rows)

Same capture, none of the merge cost, and the payloads become a queryable history — what a source said
LAST week is now recoverable instead of overwritten, which the merged column could never give us.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import warehouse  # noqa: E402

TABLE = "raw_payloads"
FIELDS = ["source", "day", "captured_at", "kind", "entity_id", "parent_id", "url", "raw_json"]


def record(source, day, part, rows, log=print):
    """Append one batch of raw payloads. `rows` are dicts with any of FIELDS; `raw_json` may be a string
    or an object (serialized here). Append-only: never merged, never re-read, so cost is O(batch) forever
    no matter how large the history grows. Best-effort — losing a payload must never fail a scrape that
    successfully captured the modelled row."""
    rows = [r for r in (rows or []) if r]
    if not rows:
        return 0
    now = int(time.time())
    out = []
    for r in rows:
        raw = r.get("raw_json")
        if raw is not None and not isinstance(raw, str):
            try:
                raw = json.dumps(raw, default=str)
            except Exception:
                raw = None
        if not raw:
            continue                      # nothing to keep — don't write an empty payload row
        out.append({"source": source, "day": day, "captured_at": r.get("captured_at") or now,
                    "kind": r.get("kind") or "item", "entity_id": str(r.get("entity_id") or ""),
                    "parent_id": str(r.get("parent_id") or ""), "url": r.get("url") or "",
                    "raw_json": raw})
    if not out:
        return 0
    try:
        warehouse.write_partition(TABLE, "%s_%s_%s" % (day, source, part), out, fields=FIELDS)
        return len(out)
    except Exception as e:
        log("  [raw] %s capture failed: %s" % (source, str(e)[:110]))
        return 0


def evacuate(table, source, key_col, parent_col=None, day=None, log=print):
    """ONE-TIME: move an existing fat `raw_json` column out of an accumulating catalog into raw_payloads.

    The forward fix stops NEW payloads landing in the catalog, but rows already written still carry theirs
    and still cost a full re-read on every merge until they happen to be replaced. This copies them into
    the append-only table first (so nothing is lost — the copy is verified before the source is cleared),
    then blanks the column in place.

    Run from the writer host with no scrape in flight, same constraint as bucketize.
    """
    day = day or time.strftime("%Y-%m-%d")
    # query(name, sql) exposes the table as the view `t` and returns a list of DICTS (not tuples).
    rows = warehouse.query(
        table,
        "SELECT %s AS k, %s AS p, raw_json FROM t WHERE raw_json IS NOT NULL AND raw_json <> ''"
        % (key_col, (parent_col or "''")))
    rows = list(rows or [])
    if not rows:
        log("evacuate: %s has no payloads to move" % table)
        return 0
    log("evacuate: moving %s payloads out of %s" % ("{:,}".format(len(rows)), table))
    moved = record(source, day, "evacuate",
                   [{"kind": "item", "entity_id": r.get("k"), "parent_id": r.get("p"),
                     "raw_json": r.get("raw_json")} for r in rows], log=log)
    if moved != len(rows):
        log("evacuate: ABORT — kept %d of %d payloads; leaving %s untouched" % (moved, len(rows), table))
        return 1
    log("evacuate: %s payloads safe in %s — clearing the column" % ("{:,}".format(moved), TABLE))
    # ONLY NOW is the catalog rewritten, and only after the copy above was verified row-for-row. Order
    # matters more than elegance here: today's bugs were repeatedly "bookkeeping destroyed the payload it
    # described", so the destructive step never runs until the preserved copy is confirmed landed.
    # EXCLUDE keeps the payloads from being read back into memory just to be thrown away — the whole
    # point is to stop touching that column.
    try:
        lean = warehouse.query(table, "SELECT * EXCLUDE (raw_json), '' AS raw_json FROM t")
    except Exception as e:
        log("evacuate: payloads are SAFE in %s but the lean read failed (%s) — catalog left as-is"
            % (TABLE, str(e)[:90]))
        return 1
    lean = list(lean or [])
    if len(lean) != warehouse.row_count(table):
        log("evacuate: lean read returned %d of %d rows — refusing to overwrite the catalog"
            % (len(lean), warehouse.row_count(table)))
        return 1
    warehouse.write_parquet(table, lean, fields=list(lean[0].keys()))
    log("evacuate: %s rewritten lean (%s rows); payloads live in %s"
        % (table, "{:,}".format(len(lean)), TABLE))
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__.strip().splitlines()[0])
        print("usage: raw_capture.py evacuate <table> <source> <key_col> [parent_col]")
        sys.exit(0)
    if a[0] == "evacuate":
        sys.exit(evacuate(a[1], a[2], a[3], a[4] if len(a) > 4 else None))
    print("unknown command %r" % a[0])
    sys.exit(2)
