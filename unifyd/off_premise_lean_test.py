#!/usr/bin/env python3
"""off_premise_lean_test.py — off_premise.run_census must NOT land raw_json in the accumulating
catalogs it writes, and must not lose it either.

Fixing the 2026-07-29 offprem-census OOM: `write_accumulate` merges by rewriting the whole table, and
every product row here carried a raw_json payload (6-12KB, from the platform's own product JSON/JSON-LD)
straight into that merge — the exact "payload as a mutable catalog column" mistake raw_capture.py exists
to fix, previously observed on ubereats_products. The fix moves raw_json to raw_capture.record() (append-
only, never re-read on a merge) and writes a lean projection to the per-market and master catalogs.

This proves both halves: the catalogs really do go lean, and nothing is silently dropped — every payload
that would have landed in the catalog instead lands in raw_payloads via raw_capture, verifiably.

Fully stubbed/offline: no network, no real warehouse (fakes capture what would have been written).

    python3 unifyd/off_premise_lean_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import off_premise as off

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


class _FakeRun:
    def progress(self, *a, **k): pass
    def finish(self, *a, **k): pass


def main():
    print("off_premise.run_census — raw_json leaves the accumulate path, lands in raw_capture instead")

    calls = {"accumulate": [], "raw_capture": []}

    real_query = off.warehouse.query
    real_write_accumulate = off.warehouse.write_accumulate
    real_record = off.raw_capture.record
    real_start = off.runlog.start if hasattr(off, "runlog") else None
    real_observe_record = off.observe.record

    def fake_query(census, sql):
        return [{"account": "Test Wine & Spirits", "website": "https://testwine.example.com",
                 "platform": "Shopify"}]

    def fake_write_accumulate(table, rows, key, fields=None):
        calls["accumulate"].append((table, [dict(r) for r in rows]))
        return {"rows": len(rows), "uri": table}

    def fake_record(source, day, part, rows, log=print):
        calls["raw_capture"].append((source, [dict(r) for r in rows]))
        return len(rows)

    def fake_shopify_catalog(base, key, log=print):
        return [{"name": "Old Vine Zinfandel 750ml", "brand": "Test Cellars", "price": 19.99,
                 "sku": "TC-ZIN-750", "upc": "012345678905", "size_ml": 750,
                 "raw_json": '{"handle":"old-vine-zin","variants":[{"id":1}]}' * 40}]  # ~2KB, real-shaped

    import runlog
    off.warehouse.query = fake_query
    off.warehouse.write_accumulate = fake_write_accumulate
    off.raw_capture.record = fake_record
    off.observe.record = lambda *a, **k: None
    off.shopify_catalog = fake_shopify_catalog
    runlog.start = lambda *a, **k: _FakeRun()

    try:
        rows = off.run_census(market="testmarket", platforms=("Shopify",),
                               census="testmarket_offprem_census", out="testmarket_offprem_products")
    finally:
        off.warehouse.query = real_query
        off.warehouse.write_accumulate = real_write_accumulate
        off.raw_capture.record = real_record
        off.observe.record = real_observe_record
        runlog.start = real_start

    check("the function still returns the full rows (raw_json intact) for any downstream caller",
          rows and rows[0].get("raw_json"), "return value should be unaffected")
    check("write_accumulate was called for both the per-market and master tables",
          len(calls["accumulate"]) == 2, calls["accumulate"])

    tables_written = [t for t, _ in calls["accumulate"]]
    check("per-market table was written", "testmarket_offprem_products" in tables_written, tables_written)
    check("master offprem_products was written", "offprem_products" in tables_written, tables_written)

    for table, written_rows in calls["accumulate"]:
        check("no raw_json key reaches write_accumulate for %s" % table,
              all("raw_json" not in r for r in written_rows),
              [r for r in written_rows if "raw_json" in r])
        check("the row otherwise keeps its real fields for %s" % table,
              written_rows and written_rows[0].get("sku") == "TC-ZIN-750", written_rows)

    check("raw_capture.record was called exactly once (not once per write_accumulate call)",
          len(calls["raw_capture"]) == 1, calls["raw_capture"])
    if calls["raw_capture"]:
        source, captured = calls["raw_capture"][0]
        check("captured under the off-premise source tag", source == "off-premise", source)
        check("the payload itself made it into the capture (nothing lost)",
              captured and captured[0].get("raw_json", "").startswith('{"handle"'),
              captured)
        check("capture carries the sku as entity_id (recoverable, not anonymous)",
              captured and captured[0].get("entity_id") == "TC-ZIN-750", captured)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
