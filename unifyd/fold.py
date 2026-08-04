"""fold.py — stage-1 parts → stage-2 aggregate, incrementally and set-based.

Step 3 of docs/PIPELINE-DESIGN.md. Replaces `ue_catalog.consolidate`'s in-memory dict, which read
the ENTIRE parts history into Python on every run, pruned nothing, and therefore cost more every
day regardless of how little new data arrived. At the stated target (502k stores x ~100 items) that
is the wrong shape, not a tuning problem.

WHAT THIS DOES DIFFERENTLY

  * INCREMENTAL (C3). A watermark records which part FILES have been folded. Each run reads only
    the new ones, so cost is proportional to new parts, not to history. The watermark also turns
    "how much is waiting" into a number you can display instead of guess, and makes folded parts
    prunable.
  * SET-BASED. The dedupe happens in DuckDB over the new parts, not in a Python dict. Python only
    ever holds the deduped NEW rows.
  * REUSES THE SCALE-CORRECT MERGE. The deduped rows go through `warehouse.write_accumulate`, which
    for a bucketed (v2) table is `_accumulate_bucketed`: group by md5 bucket, rewrite only touched
    buckets via a DuckDB anti-join, manifest last as the atomic swap. That primitive already existed
    and the old fold bypassed it.

TWO FINDINGS THAT CHANGED THE DESIGN — both verified in the source, both worth knowing before
editing anything here.

1. THERE IS NO TIMESTAMP TO ORDER BY. `<site>_products_parts` has no `observed_at` / `date` column
   (see table_spec). So the canonical "latest observation per key wins" cannot be expressed as
   `ORDER BY observed_at DESC` — there is nothing to order by. The old fold's
   `for r in rows: latest[key] = r` therefore did not mean "latest": it meant "whichever row the
   unordered union scan happened to return last". The winner was arbitrary.

   The only recency signal that exists for every row is the PART FILE NAME, which encodes the day
   (`2026-07-30_s02_b0004`). ISO dates sort lexically, so ordering by filename is a real, stable
   recency order that works retroactively over all existing parts. That is what this module uses,
   via DuckDB's `filename=true`. Adding a real `observed_at` to the parts schema is the better
   long-term fix and is tracked as a follow-up — it requires changing the writers, and a column
   that is NULL for all history would not help this fold today.

2. ROW-LEVEL "LATEST WINS" IS LOSSY IN BOTH DIRECTIONS, so this fold merges FIELD BY FIELD.
   Two different writers append to the same parts table for the same (store_uuid, item_uuid):
     - the catalog sweep (`ue_catalog._land`) writes price / list_price / promo / in_stock /
       stock_label / section context, and no UPC;
     - the enrich pass (`ue_enrich._flush`) writes UPC / GTIN / brand / size / ABV from the item
       detail endpoint, seeding `name` as "" and carrying no price.
   Whole-row replacement therefore DISCARDS one writer's contribution whichever way it resolves:
   catalog wins -> the UPC is lost; enrich wins -> the price and name are lost. Because the old
   ordering was arbitrary, the live aggregate is an unpredictable mix of both.

   So the merge is `arg_max(col, part) FILTER (WHERE col IS NOT NULL)` per column: for each key,
   every column independently takes its most recent NON-EMPTY value. A later row that is silent
   about a field no longer erases a value an earlier row supplied. For STRING columns "" counts as
   absent (the enrich stub literally sets `name = ""`), which is why the per-column predicate is
   driven by the declared type in `table_spec` rather than assumed.

WHAT THIS MODULE DOES NOT DO. It does not delete parts. Archiving folded parts is safe only once a
fold has been verified in production, and deleting inputs is the one step that cannot be undone;
`pending()` already makes the backlog visible without it.
"""
import time

import table_spec
import warehouse

WATERMARK_KIND = "fold"


# ---------------------------------------------------------------------------------------------
# Pure logic — no warehouse, no DuckDB, no network. Everything here is unit-testable offline.
# ---------------------------------------------------------------------------------------------
def plan(all_parts, consumed):
    """The part files still to fold, oldest first.

    `all_parts` is every part file currently present; `consumed` is the watermark's list. Set
    difference, then sorted — because the sort IS the recency order (see finding 1).
    """
    todo = sorted(set(all_parts) - set(consumed or []))
    return todo


def _present(col, dtype):
    """SQL predicate for 'this row actually supplied a value for this column'.

    For STRING columns the empty string means absent, not 'set to empty' — `ue_enrich` seeds
    `name=""` on its stub rows, and treating that as a value would let an enrich row blank out a
    real product name.
    """
    q = '"%s"' % col
    if dtype == table_spec.STRING:
        return "NULLIF(%s, '') IS NOT NULL" % q
    return "%s IS NOT NULL" % q


def coalesce_sql(spec, files_sql):
    """The set-based fold: one row per key, each column independently taking its most recent
    non-empty value, ordered by the part file that supplied it.

    `files_sql` is a rendered DuckDB file list. Kept as a parameter so this function stays pure and
    testable without a warehouse or a connection.
    """
    keys = list(spec.key_cols)
    key_sel = ", ".join('"%s"' % k for k in keys)
    cols = [c for c in spec.fields if c not in keys]
    picks = [
        'arg_max("%s", "__part") FILTER (WHERE %s) AS "%s"' % (c, _present(c, spec.dtypes[c]), c)
        for c in cols
    ]
    return (
        "SELECT %s, %s "
        "FROM read_parquet([%s], union_by_name=true, filename='__part') "
        "GROUP BY %s"
    ) % (key_sel, ", ".join(picks), files_sql, key_sel)


def watermark_after(consumed, folded, now=None):
    """The watermark doc to persist after folding `folded`.

    Kept pure so the bookkeeping is testable without storage. `consumed` is deduped and sorted so
    the doc is stable and diffable rather than growing in arbitrary order.
    """
    merged = sorted(set(consumed or []) | set(folded or []))
    return {"consumed": merged, "count": len(merged),
            "last_folded": sorted(folded or [])[-1] if folded else None,
            "updated_at": int(now if now is not None else time.time())}


# ---------------------------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------------------------
def read_watermark(table):
    return warehouse.read_doc(WATERMARK_KIND, table, default={"consumed": [], "count": 0})


def pending(table):
    """{'pending': n, 'consumed': n, 'parts': [...]} — the backlog, as a NUMBER.

    This is what makes "how much is waiting" answerable on a screen (§6) and what lets a fold run
    because unconsolidated parts exist (C4) rather than because an upstream reported `ok`.
    """
    parts_table = table + "_parts"
    all_parts = _part_names(parts_table)
    wm = read_watermark(table)
    todo = plan(all_parts, wm.get("consumed"))
    return {"table": table, "pending": len(todo), "consumed": len(wm.get("consumed") or []),
            "parts": todo}


def _part_names(parts_table):
    """Every part file for a time-series table, fully qualified (bucket-included when remote)."""
    return sorted(warehouse._partition_files(parts_table) or [])


def _part_sql(path):
    """Render a partition-file path the way DuckDB wants it — mirroring `query_parts`.

    NOT `warehouse._part_sql_path`. That helper takes a PREFIX-RELATIVE path and prepends
    bucket+prefix; `_partition_files` already returns FULLY-QUALIFIED paths (bucket included
    remotely, absolute locally). Composing the two doubles the prefix, and the resulting
    `s3://bucket/warehouse/bucket/warehouse/...` 404s. Caught by the first dry run against real
    storage, which is exactly what a dry run is for — no unit test with fake paths would have.
    """
    return ("s3://%s" % path) if warehouse.remote() else path


def run(table, limit=None, dry_run=False, log=print):
    """Fold new parts of `<table>_parts` into `<table>`. Returns a run record.

    AN EMPTY BACKLOG IS NOT A STALL, AND NEITHER IS A FAILURE (§6). The three outcomes are
    distinct in the return value and in the log, because collapsing them is exactly what let a
    broken source report benignly:
        status='current'  nothing new to fold — success, zero work
        status='ok'       folded n parts into m rows
        (raises)          could not read or write — loud, never a silent 0
    """
    spec = table_spec.spec_for(table)
    if spec is None:
        raise ValueError("fold: %s has no table_spec declaration — declare it before folding "
                         "(the key and the column types are what the fold merges on)" % table)
    if not spec.key_cols:
        raise ValueError("fold: %s declares no key_cols" % table)

    parts_table = table + "_parts"
    wm = read_watermark(table)
    todo = plan(_part_names(parts_table), wm.get("consumed"))
    if limit:
        todo = todo[:limit]
    if not todo:
        log("[fold] %s: nothing to fold (%d parts already consumed)" % (table, wm.get("count") or 0))
        return {"table": table, "status": "current", "parts": 0, "rows": 0,
                "consumed": wm.get("count") or 0}

    files_sql = ", ".join("'%s'" % _part_sql(f).replace("'", "") for f in todo)
    sql = coalesce_sql(spec, files_sql)
    con = warehouse.connect()
    cur = con.execute(sql)                      # ONE execution — .description and .fetchall() from
    cols = [d[0] for d in cur.description]      # the same cursor, or the whole fold runs twice
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    if not rows:
        # Parts existed but yielded nothing. That is NOT 'current' — it means the parts are
        # unreadable or empty, which is a real signal, so say so instead of reporting success.
        log("[fold] %s: %d new part(s) produced 0 rows — parts unreadable or empty" % (table, len(todo)))
        return {"table": table, "status": "degraded", "parts": len(todo), "rows": 0,
                "warning": "new parts folded to 0 rows"}

    if dry_run:
        # Everything above is read-only, so a dry run exercises the REAL query against the REAL
        # parts and reports what would land — without touching the aggregate or the watermark.
        log("[fold] %s: DRY RUN — %d new part(s) would fold to %s rows (nothing written)"
            % (table, len(todo), f"{len(rows):,}"))
        return {"table": table, "status": "dry-run", "parts": len(todo), "rows": len(rows),
                "consumed": wm.get("count") or 0}

    warehouse.write_accumulate(table, rows, key=tuple(spec.key_cols),
                               fields=list(spec.fields), coverage=False)
    warehouse.write_doc(WATERMARK_KIND, table, watermark_after(wm.get("consumed"), todo))
    log("[fold] %s: folded %d new part(s) -> %s rows merged" % (table, len(todo), f"{len(rows):,}"))
    return {"table": table, "status": "ok", "parts": len(todo), "rows": len(rows),
            "consumed": (wm.get("count") or 0) + len(todo)}


def main(argv=None):
    """CLI so the fold can be INSPECTED on Fly before anything is wired to it.

        python3 unifyd/fold.py --table ubereats_products --pending    # backlog only, read-only
        python3 unifyd/fold.py --table ubereats_products --dry-run    # real query, writes nothing
        python3 unifyd/fold.py --table ubereats_products              # fold
    """
    import argparse
    import json as _json
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True, help="the stage-2 aggregate, e.g. ubereats_products")
    ap.add_argument("--pending", action="store_true", help="report the backlog and exit")
    ap.add_argument("--dry-run", action="store_true", help="run the fold query, write nothing")
    ap.add_argument("--limit", type=int, default=None, help="fold at most N new parts")
    a = ap.parse_args(argv)
    if a.pending:
        p = pending(a.table)
        p.pop("parts", None)                       # the count is the signal; the list can be huge
        print(_json.dumps(p))
        return 0
    print(_json.dumps(run(a.table, limit=a.limit, dry_run=a.dry_run)))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
