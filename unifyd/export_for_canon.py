"""export_for_canon.py — export unifyd's FULL UPC-bearing product universe as JSONL for the canon bridge.

Broadens canon's SOURCE COVERAGE (S4): the identity overlay (canon_identity.py) only lifts sources canon
has ingested. The first ingest pulled ~90k UPC'd products from `retail_observations` (9 retail sources);
this pulls the WHOLE mapped universe from `_stage_product` — every source the master build shreds (retail
+ catalog + distributor) — UNIONed with `retail_observations`, so canon resolves identity across all of
them. UPC-less rows are skipped (canon's Tier-0 is UPC-anchored); those SKUs keep unifyd's `item_key` via
the COALESCE overlay, losing nothing.

Source names are normalized EXACTLY as `build_product_master.build_source_xwalk` normalizes them
(`abc_catalog`→`abc`, `binnys_products`→`binnys`, `total_wine_products`→`total-wine`), so canon's
`item_identity.source` matches the served source and the overlay joins cleanly.

The canon side then loads this via `hoodie-canon/src/acquire/bridge.py`, resolves through the cascade, and
`ingest_canon_identity.py` lands the widened `item_identity`.

    python export_for_canon.py /tmp/canon_ingest_products.jsonl
"""

import argparse
import json
import sys

import warehouse

# _obs_src normalization in SQL (mirror of build_source_xwalk._obs_src). `name` is a DuckDB reserved word.
_SRC = "replace(replace(replace(_source, '_products', ''), '_catalog', ''), '_', '-')"
_UPC = "regexp_replace(CAST(upc AS VARCHAR), '[^0-9]', '', 'g')"

# _stage_product: the full mapped catalog universe (_source / _source_id / upc / brand / product_name).
_STAGE_SQL = (
    "SELECT %s AS source, CAST(_source_id AS VARCHAR) AS product_id, "
    "       max(%s) AS upc, max(brand) AS brand, max(product_name) AS name "
    "FROM t "
    "WHERE _source_id IS NOT NULL AND _source_id <> '' "
    "  AND upc IS NOT NULL AND length(%s) >= 11 "
    "GROUP BY 1, 2" % (_SRC, _UPC, _UPC)
)
# retail_observations: the retail time-series (source / product_id / upc / brand / "name"), many rows per SKU.
_RETAIL_SQL = (
    "SELECT source, CAST(product_id AS VARCHAR) AS product_id, "
    "       max(%s) AS upc, max(brand) AS brand, max(\"name\") AS name "
    "FROM t "
    "WHERE product_id IS NOT NULL AND CAST(product_id AS VARCHAR) <> '' "
    "  AND upc IS NOT NULL AND length(%s) >= 11 "
    "GROUP BY 1, 2" % (_UPC, _UPC)
)


def _pull(read, table, sql, log):
    """Run one source query; return its rows or [] if the table is absent/unreadable (coverage is a union
    of whatever exists — a missing table must never abort the export)."""
    try:
        rows = read(table, sql)
        log("  [export] %s: %d UPC'd SKUs" % (table, len(rows)))
        return rows
    except Exception as e:
        log("  [export] %s unavailable (skipped): %s" % (table, str(e)[:60]))
        return []


def export(path, log=print):
    """Write the widened UPC-bearing universe as JSONL. `_stage_product` wins on a (source, product_id)
    collision (the cleaner mapped-master row); `retail_observations` fills sources the master hasn't mapped.
    Returns {products, sources}."""
    seen = {}   # (source, product_id) -> record; first writer wins (stage before retail)
    for read, table, sql in ((warehouse.query, "_stage_product", _STAGE_SQL),
                             (warehouse.query_parts, "retail_observations", _RETAIL_SQL)):
        for r in _pull(read, table, sql, log):
            key = (r["source"], r["product_id"])
            if key in seen:
                continue
            seen[key] = {"source": r["source"], "product_id": r["product_id"],
                         "upc": r["upc"], "brand": r["brand"] or "", "name": r["name"] or ""}
    with open(path, "w") as f:
        for rec in seen.values():
            f.write(json.dumps(rec) + "\n")
    out = {"products": len(seen), "sources": len({s for s, _ in seen})}
    log("[export_for_canon] %d UPC'd products across %d sources -> %s" % (out["products"], out["sources"], path))
    return out


def main():
    ap = argparse.ArgumentParser(description="Export unifyd's UPC-bearing product universe for the canon bridge.")
    ap.add_argument("path", nargs="?", default="/tmp/canon_ingest_products.jsonl")
    a = ap.parse_args()
    export(a.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
