"""ingest_canon_identity — land hoodie-canon's authoritative item_identity into the warehouse.

The unifyd side of the matching-convergence S3 seam (hoodie-suite/MATCHING-CONVERGENCE.md). Canon
computes identity offline and EXPORTS it as JSONL (`canon/src/index/export_identity.py`), one record per
source SKU carrying the authoritative ``canon_item_id`` (which supersedes unifyd's provisional
``xwalk_source_sku.item_key``). This reads that export and writes the ``item_identity`` warehouse table,
so the serving path (S4) can join `(source, product_id) -> canon_item_id` exactly where it joins
`xwalk_source_sku` today — making the cutover a DATA change, not a query rewrite.

FROZEN CONTRACT (mirrors the canon export; see that module's docstring for field semantics):
    source, product_id, canon_item_id, brand, product_name, category, size_ml, size_raw,
    upcs (list<str>), identity_key, status, merged_into, match_tier, method_version

Full-snapshot replace (canon exports the WHOLE identity universe each run — an authoritative rebuild,
not an accumulate), so ``write_parquet`` is right: its empty-guard refuses a 0-row clobber of a good
table (a truncated/failed export never wipes the served identity). Idempotent: re-running the same
export yields the same table.

    python ingest_canon_identity.py /tmp/canon_item_identity.jsonl
"""

import argparse
import json
import sys

import warehouse

TABLE = "item_identity"

# The stable Parquet schema — every record is projected onto exactly these columns (canon emits them
# all; a missing key lands NULL) so the schema never drifts between exports.
FIELDS = [
    "source", "product_id", "canon_item_id",
    "brand", "product_name", "category", "size_ml", "size_raw",
    "upcs", "identity_key", "status", "merged_into", "match_tier", "method_version",
]


def _rows(path):
    """Stream the canon JSONL export as dicts, coercing the id/tier ints and defaulting upcs to []."""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            r["upcs"] = list(r.get("upcs") or [])
            yield {k: r.get(k) for k in FIELDS}


def load(path, allow_empty=False):
    """Read the canon identity export at ``path`` and (re)write the ``item_identity`` table.
    Returns warehouse.write_parquet's {rows, uri}."""
    rows = list(_rows(path))
    res = warehouse.write_parquet(TABLE, rows, FIELDS, allow_empty=allow_empty)
    # a quick served-shape check: distinct canon items + the recall lift (sources sharing an identity)
    summary = warehouse.query(
        TABLE,
        "SELECT count(*) AS records, count(DISTINCT canon_item_id) AS items, "
        "count(DISTINCT source) AS sources FROM t",
    )[0]
    res.update(summary)
    return res


def main():
    ap = argparse.ArgumentParser(description="Ingest canon's item_identity export into the warehouse.")
    ap.add_argument("path", nargs="?", default="/tmp/canon_item_identity.jsonl")
    ap.add_argument("--allow-empty", action="store_true", help="permit a 0-row truncate (deliberate)")
    a = ap.parse_args()
    res = load(a.path, allow_empty=a.allow_empty)
    print("[ingest_canon_identity] wrote %(rows)d rows -> %(uri)s" % res)
    print("[ingest_canon_identity] %(records)d records | %(items)d canon items | %(sources)d sources"
          % res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
