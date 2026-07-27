"""build_item_identity.py — produce the served `item_identity` IN-SYSTEM (warehouse), nothing local.

The matching-convergence cutover (MATCHING-CONVERGENCE.md) proved the served recall lift is
UPC-DETERMINISTIC: canon scored R=1.000 vs unifyd item_key's 0.269 on the UPC gold precisely because
canon anchors identity on the UPC. That collapse — "same UPC ⇒ same item" — needs no local Postgres and no
canon cascade; it is a group-by-UPC the warehouse does directly. So this build computes `item_identity`
(the table the serving overlay `canon_identity.py` joins) entirely in DuckDB, as a `source_registry` build
that runs in the system on cadence.

IDENTITY = the numeric UPC. `canon_item_id = CAST(<digits-only UPC> AS BIGINT)` — deterministic, stable
across runs, unique per UPC, and it collapses leading-zero variants ("012…"/"12…" → same bigint) for free
(the [[upc-resolution-engine]] zero-strip, built in). Every UPC-bearing source SKU across the FULL mapped
universe (`_stage_product`) unioned with `retail_observations` maps to that id; two sources on one UPC →
one identity (the lift). UPC-less SKUs are absent — they keep unifyd's `item_key` via the COALESCE overlay.

Cross-UPC merges (pack variants), no-UPC and fuzzy identity are the HARD TAIL the gold doesn't yet measure;
that is hoodie-canon's cascade, to run as a cloud engine later. This build owns `item_identity` for the
deterministic core today (single writer).

Schema = the frozen item_identity contract (same columns `ingest_canon_identity.py` landed), so the overlay
and `master_quality.score_canon` consume it unchanged.

    python build_item_identity.py         # (in-app) recompute item_identity from the warehouse
"""

import argparse
import sys

import warehouse

TABLE = "item_identity"

# digits-only UPC (mirror of the master_quality / bridge UPC normalization); `name` is DuckDB-reserved.
_UPC = "regexp_replace(CAST(upc AS VARCHAR), '[^0-9]', '', 'g')"
# _obs_src normalization (mirror of build_product_master.build_source_xwalk._obs_src) so item_identity.source
# matches the served source name.
_SRC = "replace(replace(replace(_source, '_products', ''), '_catalog', ''), '_', '-')"

FIELDS = [
    "source", "product_id", "canon_item_id",
    "brand", "product_name", "category", "size_ml", "size_raw",
    "upcs", "identity_key", "status", "merged_into", "match_tier", "method_version",
]

# _stage_product — the full mapped catalog universe (carries brand/category/size). One row per (source SKU).
_STAGE_SQL = (
    "SELECT %s AS source, CAST(_source_id AS VARCHAR) AS product_id, "
    "       max(%s) AS upc, max(brand) AS brand, max(product_name) AS product_name, "
    "       max(category) AS category, max(size_ml) AS size_ml, max(container) AS size_raw "
    "FROM t "
    "WHERE _source_id IS NOT NULL AND _source_id <> '' "
    "  AND upc IS NOT NULL AND length(%s) >= 11 "
    "GROUP BY 1, 2" % (_SRC, _UPC, _UPC)
)
# retail_observations — the retail time-series (many rows per SKU; no category/size columns).
_RETAIL_SQL = (
    "SELECT source, CAST(product_id AS VARCHAR) AS product_id, "
    "       max(%s) AS upc, max(brand) AS brand, max(\"name\") AS product_name, "
    "       NULL AS category, NULL AS size_ml, NULL AS size_raw "
    "FROM t "
    "WHERE product_id IS NOT NULL AND CAST(product_id AS VARCHAR) <> '' "
    "  AND upc IS NOT NULL AND length(%s) >= 11 "
    "GROUP BY 1, 2" % (_UPC, _UPC)
)


def _pull(read, table, sql, log):
    try:
        rows = read(table, sql)
        log("  [item_identity] %s: %d UPC'd SKUs" % (table, len(rows)))
        return rows
    except Exception as e:
        log("  [item_identity] %s unavailable (skipped): %s" % (table, str(e)[:60]))
        return []


def _record(r):
    """One source SKU → an item_identity row keyed by its UPC (canon_item_id = the UPC as bigint). The
    stored UPC is the CANONICAL form ``str(cid)`` so leading-zero variants share one id AND one upc string."""
    cid = int(r["upc"])                  # digits-only, length>=11 guaranteed by the WHERE; leading zeros collapse
    canon_upc = str(cid)
    size_ml = r.get("size_ml")
    return {
        "source": r["source"], "product_id": r["product_id"], "canon_item_id": cid,
        "brand": r.get("brand") or "", "product_name": r.get("product_name") or "",
        "category": r.get("category") or "", "size_ml": float(size_ml) if size_ml is not None else None,
        "size_raw": r.get("size_raw") or "", "upcs": [canon_upc],
        "identity_key": "upc:%s" % canon_upc, "status": "active", "merged_into": None,
        "match_tier": 0, "method_version": "warehouse:upc",
    }


def build(log=print):
    """Recompute `item_identity` from the warehouse and (re)write it. `_stage_product` wins a (source,
    product_id) collision (the richer mapped-master row); `retail_observations` fills unmapped sources.
    Returns {records, items, sources}."""
    seen = {}   # (source, product_id) -> record; first writer wins (stage before retail)
    for read, table, sql in ((warehouse.query, "_stage_product", _STAGE_SQL),
                             (warehouse.query_parts, "retail_observations", _RETAIL_SQL)):
        for r in _pull(read, table, sql, log):
            key = (r["source"], r["product_id"])
            if key not in seen:
                seen[key] = _record(r)
    rows = list(seen.values())
    warehouse.write_parquet(TABLE, rows, FIELDS)
    out = {"records": len(rows), "items": len({r["canon_item_id"] for r in rows}),
           "sources": len({r["source"] for r in rows})}
    log("[build_item_identity] %d source SKUs → %d UPC identities across %d sources (in-warehouse, no local)"
        % (out["records"], out["items"], out["sources"]))
    return out


def main():
    argparse.ArgumentParser(description="Compute the served item_identity in-warehouse (UPC-deterministic).").parse_args()
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
