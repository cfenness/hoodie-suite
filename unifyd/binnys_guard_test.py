"""Guard test: a full crawl may only REPLACE the catalog when it is actually complete."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binnys_scraper as b

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)

# _safe_to_replace against a landed table of 29,787
b.warehouse = types.SimpleNamespace(row_count=lambda t: 29787)
ok("a complete crawl of the same size may replace", b._safe_to_replace(29787, log=lambda *_: None))
ok("a slightly smaller crawl may replace (within the floor)", b._safe_to_replace(28000, log=lambda *_: None))
ok("a TRUNCATED crawl may NOT replace (500 of 29,787)", not b._safe_to_replace(500, log=lambda *_: None))
ok("a half-crawl may NOT replace", not b._safe_to_replace(14000, log=lambda *_: None))

b.warehouse = types.SimpleNamespace(row_count=lambda t: (_ for _ in ()).throw(RuntimeError("tigris blip")))
ok("if the prior count can't be read, do NOT replace", not b._safe_to_replace(29787, log=lambda *_: None))

b.warehouse = types.SimpleNamespace(row_count=lambda t: 0)
ok("an empty landed table may be replaced (first ever crawl)", b._safe_to_replace(29787, log=lambda *_: None))

# fetch_records completeness flag
calls = {"n": 0}
def fake_query(page, n):
    calls["n"] += 1
    if page == 0: return {"hits": [{"i": 0}], "nbPages": 5}
    if page == 3: raise RuntimeError("page 3 dropped")
    return {"hits": [{"i": page}]}
b.query = fake_query
recs, complete = b.fetch_records(crawl_all=True, log=lambda *_: None)
ok("a dropped page marks the crawl INCOMPLETE", complete is False)
ok("...and returns only what it got (%d)" % len(recs), len(recs) == 3)

b.query = lambda page, n: {"hits": [{"i": page}], "nbPages": 3}
recs, complete = b.fetch_records(crawl_all=True, log=lambda *_: None)
ok("a clean crawl is COMPLETE", complete is True)
recs, complete = b.fetch_records(sample=10, crawl_all=False, log=lambda *_: None)
ok("a sample run is never 'complete' (never authorised to replace)", complete is False)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
