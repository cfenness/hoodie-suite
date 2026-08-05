"""The store universe must be read from the PER-SITE sitemap table.

This is the ratchet for a bug that ran for months in silence. `universe()` read a hardcoded
`ubereats_sitemap` and then filtered `source = <site>`, so for postmates it matched nothing —
`ubereats_sitemap` only ever contains ubereats rows. Measured live: `universe("postmates")` returned
0 against 269,007 rows sitting in `postmates_sitemap`.

The failure was invisible from every angle that gets looked at:
  * the sweep exited in ~10 seconds and reported `incomplete`, not `failed`
  * `delta: 0` on six consecutive runs, which reads as "nothing changed today"
  * `postmates_products` had 3,190 rows from the ARCHIVED zone crawler, so the table was not empty
  * the registry said enabled, daily, 8 shards

So the test asserts the table NAME, not just that a read happened. Pure stdlib; the warehouse is
mocked, which is the only way to catch a wrong table name without a live warehouse.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# Two sitemaps, each holding only its own site's rows — the real shape.
BOOK = {
    "ubereats_sitemap": [{"store_uuid": "ue%d" % i, "store_name": "UE %d" % i, "source": "ubereats"}
                         for i in range(5)],
    "postmates_sitemap": [{"store_uuid": "pm%d" % i, "store_name": "PM %d" % i, "source": "postmates"}
                          for i in range(3)],
}
READS = []


class FakeWarehouse:
    @staticmethod
    def query(name, sql=None, params=None):
        READS.append(name)
        site = (params or [None])[0]
        return [r for r in BOOK.get(name, []) if r["source"] == site]

    @staticmethod
    def query_parts(name, sql=None, params=None):
        return []

    @staticmethod
    def row_count(name):
        return len(BOOK.get(name, []))


sys.modules["warehouse"] = FakeWarehouse
for stub in ("observe", "ubereats", "getstore", "resi", "pace", "ladder", "raw_capture",
             "identity_router", "idset", "blocks", "extract_qa", "value_rules"):
    sys.modules.setdefault(stub, type(sys)(stub))

import ue_catalog  # noqa: E402

print("the universe comes from the PER-SITE table:")
READS.clear()
ue = ue_catalog.universe("ubereats", log=lambda *a: None)
check(len(ue) == 5, "ubereats reads its 5 stores (%d)" % len(ue))
check(READS == ["ubereats_sitemap"], "...from `ubereats_sitemap` (read %s)" % READS)

READS.clear()
pm = ue_catalog.universe("postmates", log=lambda *a: None)
check(READS == ["postmates_sitemap"],
      "postmates reads `postmates_sitemap`, NOT ubereats_sitemap (read %s) — the bug was a "
      "hardcoded table name, so the name is what has to be asserted" % READS)
check(len(pm) == 3, "...and gets its 3 stores rather than 0 (%d)" % len(pm))
check({u for u, _ in pm} == {"pm0", "pm1", "pm2"}, "and they are the postmates stores")

print("\nno cross-contamination:")
check(not ({u for u, _ in pm} & {u for u, _ in ue}),
      "the two universes are disjoint — a site never sees the other's stores")

print("\nan empty universe is a FAILURE, not a quiet run:")
BOOK["postmates_sitemap"] = []
out = ue_catalog.run("postmates", shard=0, nshard=1, log=lambda *a: None)
check(out.get("status") == "failed",
      "a run with no stores reports `failed` (got %r) — it reported `incomplete` for months, which "
      "the health digest does not treat as a break" % out.get("status"))
check("postmates_sitemap" in (out.get("error") or ""),
      "and the error names the table it actually read (%r)" % (out.get("error") or "")[:80])

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
