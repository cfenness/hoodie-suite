#!/usr/bin/env python3
"""binnys_lean_test.py — binnys_scraper.pull must NOT land raw_json in binnys_products.

Found while migrating binnys_products to the bucketed layout (the #710 OOM fix): the table's real
schema carries raw_json — the whole Algolia hit, up to 6KB, on EVERY (sku, store) row — the exact
"payload as a mutable catalog column" mistake raw_capture.py exists to fix, previously seen on
ubereats_products and (separately, same PR) offprem_products. #710 bucketized this table without
noticing; the migration attempt was still producing tens of thousands of tiny partition files hours in,
because DuckDB's own fat-row heuristic throttles concurrency hard when rows are this wide. Bucketing
alone was treating the symptom.

This fixes it at the source, same pattern as off_premise.py: raw_json goes to raw_capture.record()
(append-only, never re-read on a merge), and only CATALOG_FIELDS (PRODUCT_FIELDS minus raw_json) lands
in the accumulate/full-rebuild writes. Nothing is lost — proven below.

Fully stubbed/offline: no network, no real warehouse.

    python3 unifyd/binnys_lean_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binnys_scraper as bn

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


HIT = {
    "objectID": "SKU-42", "productName": "Old Vine Zinfandel 750ml", "productBrandName": "Test Cellars",
    "productType": "Wine", "proof": None,
    "storesPriceAndInventory": [{"storeCode": "003", "purchaseAvailability": 12,
                                 "isOnSale": False, "prices": {"regularPrice": 19.99}}],
}


def main():
    print("binnys_scraper.pull — raw_json leaves the catalog write, lands in raw_capture instead")

    calls = {"accumulate": [], "raw_capture": []}

    real_fetch = bn.fetch_records
    real_write_accumulate = bn.warehouse.write_accumulate
    real_record = bn.raw_capture.record
    real_run_record = bn.run_record

    def fake_fetch_records(sample=300, crawl_all=False, log=print):
        return [dict(HIT)], False   # crawl_all=False -> takes the accumulate branch, not full-rebuild

    def fake_write_accumulate(table, rows, key, fields=None):
        calls["accumulate"].append((table, [dict(r) for r in rows]))
        return {"rows": len(rows), "uri": table}

    def fake_record(source, day, part, rows, log=print):
        calls["raw_capture"].append((source, [dict(r) for r in rows]))
        return len(rows)

    bn.fetch_records = fake_fetch_records
    bn.warehouse.write_accumulate = fake_write_accumulate
    bn.raw_capture.record = fake_record
    bn.run_record = lambda *a, **k: {"movement": {}, "status": "success", "warnings": []}
    bn.observe.record = lambda *a, **k: None

    try:
        datasets, runs, movement = bn.pull(sample=10, crawl_all=False, out="/tmp", state_dir="/tmp")
    finally:
        bn.fetch_records = real_fetch
        bn.warehouse.write_accumulate = real_write_accumulate
        bn.raw_capture.record = real_record
        bn.run_record = real_run_record

    check("pull() still landed a real dataset summary (unaffected by the lean-write change)",
          datasets.get("binnys_store_cells", {}).get("total") == 1, datasets)
    check("write_accumulate was called for binnys_products", len(calls["accumulate"]) == 1, calls["accumulate"])

    if calls["accumulate"]:
        table, written_rows = calls["accumulate"][0]
        check("landed table is binnys_products", table == "binnys_products", table)
        check("no raw_json key reaches write_accumulate", all("raw_json" not in r for r in written_rows),
              [r for r in written_rows if "raw_json" in r])
        check("the row otherwise keeps its real fields", written_rows and written_rows[0].get("sku") == "SKU-42",
              written_rows)
        check("qty/price survive the lean projection",
              written_rows and written_rows[0].get("qty") == 12 and written_rows[0].get("price") == 19.99,
              written_rows)

    check("raw_capture.record was called exactly once", len(calls["raw_capture"]) == 1, calls["raw_capture"])
    if calls["raw_capture"]:
        source, captured = calls["raw_capture"][0]
        check("captured under the binnys source tag", source == "binnys", source)
        check("the payload itself made it into the capture (nothing lost)",
              captured and captured[0].get("raw_json"), captured)
        check("capture carries sku|store as entity_id (recoverable, not anonymous)",
              captured and captured[0].get("entity_id") == "SKU-42|003", captured)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
