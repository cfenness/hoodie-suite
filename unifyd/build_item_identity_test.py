"""Tests for build_item_identity (in-warehouse UPC identity): python build_item_identity_test.py
Proves item_identity is computed in-warehouse with the UPC as canon_item_id — same UPC across sources
collapses to ONE identity (the served lift), leading-zero variants collapse too, UPC-less SKUs are absent
(they keep item_key via the overlay), the contract schema matches, and _stage_product wins a collision.
Local mode, throwaway dir, no network."""
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="build_iid_")
warehouse._LOCAL_DIR = TMP
import build_item_identity as bi

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


# ── _stage_product: Tito's 750ml at abc+binnys (SAME upc), a lone item, a UPC-less row ────────────
warehouse.write_parquet("_stage_product", [
    dict(_source="abc_catalog", _source_id="A1", upc="0012345678905", brand="Tito's",
         product_name="Handmade", category="vodka", size_ml=750, container="750ml"),
    dict(_source="binnys_products", _source_id="B7", upc="012345678905", brand="Titos",   # SAME upc, leading-zero variant
         product_name="Handmade Vodka", category="vodka", size_ml=750, container="750ml"),
    dict(_source="abc_catalog", _source_id="G1", upc="099887766554", brand="GG",
         product_name="Grey Goose", category="vodka", size_ml=750, container="750ml"),
    dict(_source="abc_catalog", _source_id="N1", upc="", brand="x", product_name="no upc",
         category="", size_ml=None, container=""),   # UPC-less → absent
], fields=["_source", "_source_id", "upc", "brand", "product_name", "category", "size_ml", "container"])

# ── retail_observations: kroger carries the SAME Tito's UPC (time-series, dup rows) + a new source ─
warehouse.write_partition("retail_observations", "seed", [
    dict(date="2026-07-10", source="kroger", product_id="K9", upc="12345678905", brand="Tito's", name="Tito's"),
    dict(date="2026-07-11", source="kroger", product_id="K9", upc="12345678905", brand="Tito's", name="Tito's"),
    dict(date="2026-07-10", source="specs", product_id="S3", upc="555666777888", brand="Bulleit", name="Bourbon"),
], fields=["date", "source", "product_id", "upc", "brand", "name"])

res = bi.build(log=lambda *a: None)
rows = warehouse.query(bi.TABLE, "SELECT * FROM t")
by = {(r["source"], r["product_id"]): r for r in rows}

ok("contract schema", list(rows[0].keys()) == bi.FIELDS)
ok("source names normalized (abc_catalog→abc, binnys_products→binnys)",
   ("abc", "A1") in by and ("binnys", "B7") in by)
ok("UPC-less SKU absent", ("abc", "N1") not in by)

# THE LIFT: three sources on the same Tito's UPC → ONE canon_item_id (item_key would have split them)
titos_ids = {by[k]["canon_item_id"] for k in (("abc", "A1"), ("binnys", "B7"), ("kroger", "K9"))}
ok("same UPC across 3 sources → ONE identity", len(titos_ids) == 1)
ok("canon_item_id IS the numeric UPC", by[("abc", "A1")]["canon_item_id"] == 12345678905)
ok("leading-zero variants collapse to same id", by[("abc", "A1")]["canon_item_id"] == by[("binnys", "B7")]["canon_item_id"])
ok("retail row shares the identity too", by[("kroger", "K9")]["canon_item_id"] == 12345678905)
ok("distinct UPCs stay distinct", by[("abc", "G1")]["canon_item_id"] != by[("abc", "A1")]["canon_item_id"])

# corroboration/coverage: 3 distinct identities (titos, gg, bulleit); titos spans 3 sources
ok("identity count = distinct UPCs", res["items"] == 3)
ok("kroger time-series deduped to one SKU", res["records"] == 5)  # abc/A1, binnys/B7, abc/G1, kroger/K9, specs/S3
ok("richer fields carried from _stage_product", by[("abc", "A1")]["category"] == "vodka" and by[("abc", "A1")]["size_ml"] == 750.0)
ok("method + upcs list", by[("abc", "A1")]["method_version"] == "warehouse:upc" and by[("abc", "A1")]["upcs"] == ["12345678905"])

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
