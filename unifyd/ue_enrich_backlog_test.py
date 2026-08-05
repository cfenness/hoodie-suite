#!/usr/bin/env python3
"""ue_enrich_backlog_test.py — the enrich work-list must carry section context, and must shard in SQL.

THE BREAK THIS LOCKS DOWN. `ue_enrich` had landed ZERO rows since it was created — 8 shards a day
against ~549k queued items, no output, no run records. Proven live 2026-08-04 on the same item, the
same session, the same machine:

    WITH real section ctx   HTTP 200 {"status":"success", ...}   -> real item detail
    EMPTY ctx               HTTP 200 {"status":"failure","message":"invalid_uuid","code":"404"}

The chain: the stage-2 aggregate `<site>_products` has no section/subsection columns (they were
added to the write schema after that table was first written, and the merge carries the old column
set forward). `backlog()` detected that and degraded to '' on the documented belief that
"getMenuItemV1 accepts empty section ids" — no longer true. Every item 404'd into a bare `continue`
and the run still exited successfully.

So the work-list now reads the PARTS, which carry section/subsection on 100% of rows, and the
aggregate's schema can no longer break enrichment at all.

These are pure string/shape checks — no warehouse, no DuckDB, no network.

    python3 unifyd/ue_enrich_backlog_test.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ue_enrich  # noqa: E402

RAN, FAILED = [], []


def check(label, ok, detail=""):
    RAN.append(label)
    if not ok:
        FAILED.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("\n     " + detail) if detail and not ok else ""))


def main():
    print("ue_enrich work-list")
    sql = ue_enrich.backlog_sql(0, 1)

    # --- the actual bug: section context must be selected, never blanked ------------------------
    check("selects section context (the field whose absence 404'd every call)",
          "AS section" in sql and "AS subsection" in sql)
    check("takes the most recent NON-EMPTY section per key",
          'arg_max(section,    __part) FILTER (WHERE NULLIF(section,\'\')    IS NOT NULL)' in sql)
    check("never hardcodes an empty section (the old degraded path)",
          "'' AS section" not in sql and "'' AS subsection" not in sql)

    # --- one row per (store,item), and only genuinely unresolved items --------------------------
    check("one row per (store_uuid, item_uuid)", "GROUP BY store_uuid, item_uuid" in sql)
    check("excludes items ANY part already resolved (HAVING, not WHERE)",
          "HAVING count(*) FILTER" in sql and "NULLIF(upc,'')" in sql and "NULLIF(gtin,'')" in sql)

    # --- sharding happens in SQL, so a shard materialises only its own slice ---------------------
    s0, s3 = ue_enrich.backlog_sql(0, 8), ue_enrich.backlog_sql(3, 8)
    check("shard filter is pushed into SQL", "hash(item_uuid) % 8 = 0" in s0)
    check("each shard asks for a different slice", "hash(item_uuid) % 8 = 3" in s3 and s0 != s3)
    check("unsharded runs carry no shard predicate", "hash(item_uuid)" not in ue_enrich.backlog_sql(0, 1))

    # --- it must read the PARTS, not the aggregate ----------------------------------------------
    src = open(os.path.join(HERE, "ue_enrich.py"), encoding="utf-8").read()
    body = src.split("def backlog(site", 1)[1].split("def run(", 1)[0]
    check("backlog reads <site>_products_parts, not the aggregate",
          '"%s_products_parts" % site' in body and '"%s_products" % site' not in body)
    check("no longer gates on has_column (the aggregate's schema can't break enrich now)",
          "has_column" not in body)

    # --- an empty work-list must not be reported as a drained backlog ---------------------------
    runbody = src.split("def run(site", 1)[1]
    check("empty work-list distinguishes 'drained' from 'no parts at all'",
          "NO PARTS to derive a work-list from" in runbody and '"degraded"' in runbody)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
