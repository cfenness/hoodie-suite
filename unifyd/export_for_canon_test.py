"""Tests for export_for_canon (S4 source-coverage widening): python export_for_canon_test.py
Proves the export unions _stage_product (mapped catalogs) with retail_observations, normalizes source
names like build_source_xwalk, keeps only valid-UPC SKUs, dedups per (source, product_id) preferring the
mapped-master row, and survives a missing table. Local mode, throwaway dir, no network."""
import json
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TMP = tempfile.mkdtemp(prefix="export_canon_")
warehouse._LOCAL_DIR = TMP
import export_for_canon as ex

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


def run():
    p = os.path.join(TMP, "out.jsonl")
    res = ex.export(p, log=lambda *a: None)
    recs = [json.loads(x) for x in open(p)] if os.path.exists(p) else []
    return res, {(r["source"], r["product_id"]): r for r in recs}


# ── _stage_product: mapped catalogs across MANY sources; source names get normalized ─────────────
warehouse.write_parquet("_stage_product", [
    dict(_source="abc_catalog", _source_id="A1", upc="012345678905", brand="Tito's", product_name="Handmade"),
    dict(_source="binnys_products", _source_id="B1", upc="099887766554", brand="GG", product_name="Grey Goose"),
    dict(_source="total_wine_products", _source_id="T1", upc="111222333444", brand="Jim", product_name="JB"),
    dict(_source="abc_catalog", _source_id="N1", upc="", brand="x", product_name="no upc"),   # UPC-less → skipped
], fields=["_source", "_source_id", "upc", "brand", "product_name"])

# ── retail_observations: time-series (many rows per SKU) incl. a source NOT in _stage_product ─────
warehouse.write_partition("retail_observations", "seed", [
    dict(date="2026-07-10", source="kroger", product_id="K1", upc="555666777888", brand="Bulleit", name="Bourbon"),
    dict(date="2026-07-11", source="kroger", product_id="K1", upc="555666777888", brand="Bulleit", name="Bourbon"),  # dup date
    dict(date="2026-07-10", source="abc", product_id="A1", upc="000000000000", brand="OTHER", name="collision"),   # collides w/ stage abc/A1
    dict(date="2026-07-10", source="specs", product_id="S1", upc="", brand="x", name="no upc"),   # UPC-less → skipped
], fields=["date", "source", "product_id", "upc", "brand", "name"])

res, by = run()

ok("source names normalized (abc_catalog→abc)", ("abc", "A1") in by)
ok("binnys_products→binnys", ("binnys", "B1") in by)
ok("total_wine_products→total-wine", ("total-wine", "T1") in by)
ok("retail source unioned in (kroger)", ("kroger", "K1") in by)
ok("UPC-less rows skipped", not any(r["product_id"] in ("N1", "S1") for r in by.values()))
ok("retail time-series + collision dedup to distinct SKUs", res["products"] == 4)  # abc/A1, binnys/B1, total-wine/T1, kroger/K1
ok("sources counted across the union", res["sources"] == 4)        # abc, binnys, total-wine, kroger
# collision: _stage_product wins (mapped-master row), so abc/A1 keeps Tito's not the retail 'collision'
ok("_stage_product wins a (source,product_id) collision", by[("abc", "A1")]["brand"] == "Tito's")
ok("kept UPCs are the valid ones", by[("kroger", "K1")]["upc"] == "555666777888")

# ── a missing _stage_product must not abort — retail-only still exports ───────────────────────────
shutil.rmtree(os.path.join(TMP, "_stage_product.parquet"), ignore_errors=True)
os.remove(os.path.join(TMP, "_stage_product.parquet")) if os.path.exists(os.path.join(TMP, "_stage_product.parquet")) else None
res2, by2 = run()
ok("missing _stage_product → retail-only export survives", ("kroger", "K1") in by2 and ("abc", "A1") in by2)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
