#!/usr/bin/env python3
"""fold_test.py — the incremental fold's logic, tested with no DuckDB, no network, no warehouse.

The fold's correctness lives in three pure decisions, and all three are testable offline:
  * WHICH parts are new (the watermark set difference)
  * HOW rows merge (per-column, most-recent non-empty — not whole-row replacement)
  * WHAT the watermark becomes afterwards

Those are the parts that can be silently wrong. The DuckDB execution around them is a thin seam,
kept deliberately thin so this file can cover the logic without the dependency — the same pattern
the overlay pipeline uses (stages injected with their data source, so the whole thing tests with
no DuckDB and no network).

    python3 unifyd/fold_test.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fold          # noqa: E402
import table_spec    # noqa: E402

RAN, FAILED = [], []


def check(label, ok, detail=""):
    RAN.append(label)
    if not ok:
        FAILED.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("\n     " + detail) if detail and not ok else ""))


def main():
    print("fold logic")
    spec = table_spec.spec_for("ubereats_products")

    # --- 1. watermark set difference ------------------------------------------------------------
    allp = ["2026-07-30_s00_b0001", "2026-07-30_s01_b0001", "2026-07-31_s00_b0001"]
    check("plan() returns only unconsumed parts",
          fold.plan(allp, ["2026-07-30_s00_b0001"]) ==
          ["2026-07-30_s01_b0001", "2026-07-31_s00_b0001"])
    check("plan() is empty when everything is consumed", fold.plan(allp, allp) == [])
    check("plan() treats a missing watermark as 'nothing consumed'", fold.plan(allp, None) == allp)
    check("plan() is sorted — the sort IS the recency order",
          fold.plan(["b", "a", "c"], []) == ["a", "b", "c"])
    # A part that vanished from storage must not resurrect as pending.
    check("plan() ignores consumed parts no longer present",
          fold.plan(["x"], ["x", "gone"]) == [])

    # --- 2. the merge is PER COLUMN, not per row ------------------------------------------------
    sql = fold.coalesce_sql(spec, "'f1.parquet'")
    check("fold groups by the declared key",
          'GROUP BY "store_uuid", "item_uuid"' in sql, sql[-90:])
    check("every non-key column is coalesced independently (arg_max per column)",
          all('arg_max("%s", "__part")' % c in sql
              for c in ("price", "upc", "name", "in_stock")),
          "missing per-column arg_max")
    check("no whole-row replacement — key columns are not arg_max'd",
          'arg_max("store_uuid"' not in sql and 'arg_max("item_uuid"' not in sql)

    # THE regression this protects: enrich seeds name="" and carries no price; the catalog carries
    # price and no UPC. If "" counted as a value, an enrich row would blank a real product name.
    check("STRING columns treat '' as ABSENT (enrich stubs must not blank a name)",
          """NULLIF("name", '') IS NOT NULL""" in sql)
    check("non-string columns test NULL only (0 and false are real values)",
          '"price" IS NOT NULL' in sql and """NULLIF("price", '')""" not in sql)
    check("boolean column is not treated as a string",
          '"in_stock" IS NOT NULL' in sql and """NULLIF("in_stock", '')""" not in sql)

    # Ordering comes from the part filename, because the table has no timestamp column at all.
    check("ordering signal is the part file (no observed_at exists on this table)",
          "filename='__part'" in sql
          and not any(f in spec.fields for f in ("observed_at", "date")),
          "spec fields: %s" % spec.fields)

    # --- 3. watermark bookkeeping ---------------------------------------------------------------
    wm = fold.watermark_after(["a"], ["b", "c"], now=1234)
    check("watermark unions consumed + newly folded", wm["consumed"] == ["a", "b", "c"])
    check("watermark count matches its list", wm["count"] == 3)
    check("watermark records the newest part folded", wm["last_folded"] == "c")
    check("watermark stamps the run", wm["updated_at"] == 1234)
    check("re-folding the same part does not duplicate it",
          fold.watermark_after(["a"], ["a"], now=1)["consumed"] == ["a"])
    check("watermark is sorted (stable + diffable)",
          fold.watermark_after(["z"], ["a"], now=1)["consumed"] == ["a", "z"])

    # --- 4. an undeclared table cannot be folded ------------------------------------------------
    # The fold merges on the declared key and types; guessing them is how the old fold hardcoded
    # (store_uuid, item_uuid) for every table.
    try:
        fold.run("some_table_with_no_spec")
        ok = False
    except ValueError as e:
        ok = "table_spec" in str(e)
    except Exception:
        ok = False
    check("folding an undeclared table raises, rather than guessing a key", ok)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
