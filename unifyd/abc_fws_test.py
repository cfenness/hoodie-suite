"""Exercise abc_fws_scraper's new incremental-landing + resume path with the network and the
warehouse mocked. Proves the properties the rework is FOR, not just that it imports."""
import os, sys, json, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ABC_BATCH"] = "3"          # tiny batches so a 10-product catalog exercises flushing
os.environ["ABC_QTY"] = "0"

import abc_fws_scraper as abc

CATALOG = [(str(1000 + i), "https://abcfws.com/p/%d/" % (1000 + i)) for i in range(10)]
STORES = ["ABC #003 - OBT", "ABC #004 - LBV", "Online"]

# ---- mocks -------------------------------------------------------------------------------------
abc.harvest_ids = lambda log=print: (CATALOG, False)


def fake_fetch_product(sku, url, log=print):
    rows = [{"store": s, "instock": True, "price": 9.99, "qty": 5, "upc": "0" + sku,
             "name": "Product " + sku, "brand": "Brand " + sku[-1]} for s in STORES]
    return sku, rows, True


abc.fetch_product = fake_fetch_product

LANDED = []          # (part, n_cells)
BUCKET = {}


class FakeObserve:
    @staticmethod
    def record(source, rows, date=None, log=print, part=None):
        LANDED.append((part, len(rows)))


class FakeWarehouse:
    @staticmethod
    def get_bytes(k):
        return BUCKET.get(k)

    @staticmethod
    def put_bytes(k, v):
        BUCKET[k] = v
        return k


sys.modules["observe"] = FakeObserve
sys.modules["warehouse"] = FakeWarehouse

out = "/tmp/abc_test_out"
os.makedirs(out, exist_ok=True)
for f in os.listdir(out):
    os.remove(os.path.join(out, f))

print("=== RUN 1: full crawl, batches of 3 products ===")
ds, runs, mv = abc.pull(crawl_all=True, out=out, log=lambda *a: None)
print("landed batches:", LANDED)
parts = [p for p, _ in LANDED]
assert len(LANDED) >= 3, "expected multiple batches, got %r" % LANDED
assert len(set(parts)) == len(parts), "DUPLICATE part names -> double counting: %r" % parts
total_cells = sum(n for _, n in LANDED)
assert total_cells == 10 * 3, "expected 30 cells landed, got %d" % total_cells
assert runs[0]["status"] == "success", "full pass should be success, got %r (%s)" % (
    runs[0]["status"], runs[0].get("warnings"))
assert "_collect/snapshots/abc-fws.json" in BUCKET, "complete pass must save the shared snapshot"
snap_cells = json.loads(BUCKET["_collect/snapshots/abc-fws.json"])["cells"]
assert len(snap_cells) == 30, "snapshot should hold 30 cells, got %d" % len(snap_cells)
one = next(iter(snap_cells.values()))
assert one["name"] and one["brand"], "name/brand missing from landed cell: %r" % one
print("PASS run 1: %d batches, %d cells, unique parts, snapshot saved" % (len(LANDED), total_cells))

print()
print("=== RUN 2: resume — checkpoint says all 10 done, so nothing should be re-fetched ===")
LANDED.clear()
fetches = []
abc.fetch_product = lambda sku, url, log=print: (fetches.append(sku), fake_fetch_product(sku, url))[1]
ds2, runs2, mv2 = abc.pull(crawl_all=True, out=out, log=lambda *a: None)
print("re-fetched products:", len(fetches), "| status:", runs2[0]["status"])
assert len(fetches) == 0, "resume failed — re-fetched %d products already done" % len(fetches)
assert runs2[0]["status"] == "success", \
    "a fully-resumed (already complete) run must not report %r" % runs2[0]["status"]
print("PASS run 2: resume skipped all %d already-captured products" % len(CATALOG))

print()
print("=== RUN 3: PARTIAL pass must NOT overwrite the shared snapshot ===")
BUCKET.pop("_collect/resume/%s.json" % __import__("time").strftime("%Y-%m-%d"), None)
for k in list(BUCKET):
    if k.startswith("_collect/resume/"):
        del BUCKET[k]
before_snap = BUCKET["_collect/snapshots/abc-fws.json"]
LANDED.clear()


def flaky(sku, url, log=print):
    if int(sku) >= 1005:
        raise RuntimeError("simulated block mid-crawl")
    return fake_fetch_product(sku, url, log)


abc.fetch_product = flaky
ds3, runs3, mv3 = abc.pull(crawl_all=True, out=out, log=lambda *a: None)
print("status:", runs3[0]["status"])
print("warnings:", runs3[0].get("warnings"))
assert runs3[0]["status"] == "degraded", "partial pass must be degraded, got %r" % runs3[0]["status"]
assert any("INCOMPLETE" in w for w in runs3[0].get("warnings") or []), \
    "partial pass must warn about remaining products: %r" % runs3[0].get("warnings")
assert BUCKET["_collect/snapshots/abc-fws.json"] == before_snap, \
    "PARTIAL pass overwrote the shared snapshot — would fake an assortment collapse"
assert sum(n for _, n in LANDED) == 5 * 3, "partial run should still LAND what it fetched, got %r" % LANDED
print("PASS run 3: partial pass landed %d cells, reported degraded, snapshot preserved"
      % sum(n for _, n in LANDED))
print()
print("=== RUN 4: LIVE-CATALOG DRIFT — the site changes during a multi-hour crawl ===")
for k in list(BUCKET):
    if k.startswith("_collect/resume/"):
        del BUCKET[k]
LANDED.clear()
abc.fetch_product = fake_fetch_product

# The catalog GROWS mid-crawl: re-harvest returns the original 10 plus 2 new products, minus 1 dropped.
GROWN = CATALOG[1:] + [("2001", "https://abcfws.com/p/2001/"), ("2002", "https://abcfws.com/p/2002/")]
calls = {"n": 0}


def drifting_harvest(log=print):
    calls["n"] += 1
    return (CATALOG, False) if calls["n"] == 1 else (GROWN, False)


abc.harvest_ids = drifting_harvest
ds4, runs4, mv4 = abc.pull(crawl_all=True, out=out, log=lambda *a: None)
d = runs4[0].get("drift") or {}
print("drift:", d)
assert d.get("catalog_start") == 10, d
assert d.get("catalog_end") == 11, d
assert d.get("added_during_crawl") == 2, "should see the 2 products added mid-crawl: %r" % d
assert d.get("removed_during_crawl") == 1, "should see the 1 product dropped mid-crawl: %r" % d
assert d.get("missing_vs_live") == 2, "the 2 new products are uncaptured vs the LIVE catalog: %r" % d
# SOME IS A FAILURE. We crawled every product we were handed, but the LIVE catalog has 2 we do not
# hold — a catalog with holes is a defective product, so this must NOT report clean. The cause is
# still spelled out (drift, not a crawl miss) because that decides what to do about it.
assert runs4[0]["status"] != "success", \
    "an incomplete catalog must not report success: %r" % runs4[0]["status"]
assert any("INCOMPLETE" in w for w in runs4[0].get("warnings") or []), \
    "the shortfall must be stated: %r" % runs4[0].get("warnings")
assert any("published during the crawl" in w for w in runs4[0].get("warnings") or []), \
    "the CAUSE (drift, not a crawl miss) must survive: %r" % runs4[0].get("warnings")
print("PASS run 4: incomplete vs the live catalog is a failure, with the cause preserved")

print()
print("ALL ASSERTIONS PASSED")
