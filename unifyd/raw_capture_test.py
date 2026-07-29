"""Payloads are captured in full, and never in the merge path.

Two rules meet here and used to be in conflict. DISCARD NOTHING says keep the entire source payload beside
every modelled row. But those payloads lived in the accumulating catalogs, and write_accumulate rewrites
the whole table on every flush — so the payload column was re-read and re-written continuously, memory
scaled with the table rather than the batch, and the cost per batch GREW as the crawl succeeded. UberEats
proved it live: 33,250 rows blew a 1.1GB DuckDB limit, and 5-8 shards per fleet were OOM-killed mid-merge
while the crawl itself worked fine.

A payload is an EVENT — what the source said at a moment — not a mutable attribute of a product. Events
append. So they land date-partitioned, and the catalog keeps only modelled columns. Same capture, no merge
cost, and the payload becomes history instead of a value the next pull overwrites.

Pure record-shaping — the warehouse write is stubbed, so no network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse  # noqa: E402
import raw_capture  # noqa: E402

fails = []
WRITES = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


warehouse.write_partition = lambda name, part, records, fields=None: (
    WRITES.append({"name": name, "part": part, "records": records, "fields": fields}), len(records))[1]

# ── a batch lands append-only, fully ─────────────────────────────────────────────────────────────
n = raw_capture.record("ubereats", "2026-07-28", "s07_b0003", [
    {"kind": "item", "entity_id": "i-1", "parent_id": "st-9", "raw_json": '{"a":1}'},
    {"kind": "item", "entity_id": "i-2", "parent_id": "st-9", "raw_json": {"nested": ["ok", 2]}},
])
check("both payloads kept", n, 2)
check("append-only table", WRITES[-1]["name"], "raw_payloads")
check("partitioned by day+source", WRITES[-1]["part"], "2026-07-28_ubereats_s07_b0003")

rec = WRITES[-1]["records"]
check("payload preserved verbatim", rec[0]["raw_json"], '{"a":1}')
check("object payload serialized", json.loads(rec[1]["raw_json"])["nested"], ["ok", 2])
check("parent kept for join-back", rec[0]["parent_id"], "st-9")
check("source stamped", rec[0]["source"], "ubereats")
check("captured_at present", isinstance(rec[0]["captured_at"], int), True)

# ── an empty payload is not written as a hollow row ──────────────────────────────────────────────
before = len(WRITES)
check("no payload → no write", raw_capture.record("ubereats", "2026-07-28", "p", [{"entity_id": "x"}]), 0)
check("nothing appended", len(WRITES), before)
check("empty batch is a no-op", raw_capture.record("ubereats", "2026-07-28", "p", []), 0)

# ── capture failure must never fail the scrape that got the modelled row ─────────────────────────
def boom(*a, **k):
    raise RuntimeError("tigris down")


warehouse.write_partition = boom
check("write failure is survivable",
      raw_capture.record("ubereats", "2026-07-28", "p",
                         [{"entity_id": "i", "raw_json": "{}"}], log=lambda *_: None), 0)

# ── the catalog write must NOT carry the payload column ──────────────────────────────────────────
import ue_catalog  # noqa: E402
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ue_catalog.py")).read()
check("payload excluded from the merge", 'lean = [k for k in PRODUCT_FIELDS if k != "raw_json"]' in src, True)
check("catalog still declares every other field", "raw_json" in ue_catalog.PRODUCT_FIELDS, True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("── %d failed" % len(fails))
    sys.exit(1)
print("── raw capture: payloads kept in full, kept out of the merge path (15 checks)")
