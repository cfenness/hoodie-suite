#!/usr/bin/env python3
"""doordash_efficiency_test.py — the per-store cost cuts must actually fire, not just exist as constants.

Grounded in a real cost breakdown (2026-07-31): a single store's ~2min scrape is ~166 HTTP fetches
(alcohol tree walk + 16-term search union + non-alc tree walk), each followed by a blanket time.sleep(0.4)
— 66+s of pure sleep, more than half the observed per-store cost — plus a 60s-per-attempt fetch timeout
(x4 retries = up to 240s) against 3000+ observed timeouts. At 2,500-5,000 concurrent accounts needed to
hit daily-viable throughput via concurrency alone, cutting the PER-STORE cost multiplies through every
concurrency level instead of just adding workers. Three cuts, each verified here:

  1. POLITE_SLEEP_S replaces the hardcoded 0.4s (still paces every request, just not at 4x actual cost).
  2. FETCH_TIMEOUT_S replaces the hardcoded 60s-per-attempt (fails dead requests 3x faster).
  3. The 16-fetch term-search union is SKIPPED once the tree walk already found a real catalog — its own
     purpose is catching small/shallow catalogs, so it's pure waste once the tree walk proves the catalog
     is already large.

No real network call in any of these — dd._session/_unlock/search_store are monkeypatched, same
discipline as doordash_full_persist_test.py and doordash_telemetry_test.py.

    python3 unifyd/doordash_efficiency_test.py
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    import doordash as dd
    import doordash_full as ddf

    print("constants replace the old hardcoded values")
    check("FETCH_TIMEOUT_S default is 20s, not the old hardcoded 60s",
          dd.FETCH_TIMEOUT_S == 20.0, dd.FETCH_TIMEOUT_S)
    check("POLITE_SLEEP_S default is 0.15s, not the old hardcoded 0.4s",
          ddf.POLITE_SLEEP_S == 0.15, ddf.POLITE_SLEEP_S)
    check("TERM_SEARCH_MIN_ITEMS has a real default", ddf.TERM_SEARCH_MIN_ITEMS == 40, ddf.TERM_SEARCH_MIN_ITEMS)

    print("\nboth _session() and _fetch()'s round-robin path wire in FETCH_TIMEOUT_S, not a stray 60")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "doordash.py")).read()
    check("_session()'s cr.Session uses FETCH_TIMEOUT_S", "timeout=FETCH_TIMEOUT_S" in src, "missing")
    check("no hardcoded timeout=60 survives in doordash.py", "timeout=60" not in src, "still present")

    print("\nenv vars override the constants (so a live run can be tuned without a redeploy)")
    os.environ["DDFULL_FETCH_TIMEOUT_S"] = "12"
    os.environ["DDFULL_POLITE_SLEEP_S"] = "0.05"
    os.environ["DDFULL_TERM_SEARCH_MIN_ITEMS"] = "5"
    try:
        importlib.reload(dd)
        importlib.reload(ddf)
        check("DDFULL_FETCH_TIMEOUT_S overrides FETCH_TIMEOUT_S", dd.FETCH_TIMEOUT_S == 12.0, dd.FETCH_TIMEOUT_S)
        check("DDFULL_POLITE_SLEEP_S overrides POLITE_SLEEP_S", ddf.POLITE_SLEEP_S == 0.05, ddf.POLITE_SLEEP_S)
        check("DDFULL_TERM_SEARCH_MIN_ITEMS overrides TERM_SEARCH_MIN_ITEMS",
              ddf.TERM_SEARCH_MIN_ITEMS == 5, ddf.TERM_SEARCH_MIN_ITEMS)
    finally:
        for v in ("DDFULL_FETCH_TIMEOUT_S", "DDFULL_POLITE_SLEEP_S", "DDFULL_TERM_SEARCH_MIN_ITEMS"):
            os.environ.pop(v, None)
        importlib.reload(dd)
        importlib.reload(ddf)

    print("\nfull_catalog() actually sleeps POLITE_SLEEP_S, not a hardcoded value")
    sleeps = []
    real_sleep = ddf.time.sleep
    ddf.time.sleep = lambda s: sleeps.append(s)

    real_session = dd._session
    real_unlock = dd._unlock
    real_search = dd.search_store
    dd._session = lambda store: "fake-session"
    dd._unlock = lambda url, key=None, session=None, log=None: "<html></html>"   # no cat paths, no latitude
    dd.search_store = lambda store, term, key=None, session=None, log=None: []

    # a catalog SMALL enough that the term-search union still runs (tree walk alone won't hit the
    # default 40-item threshold on an empty canned page — 0 items found).
    try:
        items, outlet = ddf.full_catalog("999", None, log=lambda *a: None)
        check("term-search union ran for a small/shallow catalog (0 items from the tree walk)",
              len(sleeps) == 1 + len(dd.ALCOHOL_TERMS) + 1, (len(sleeps), sleeps))
        check("every recorded sleep used POLITE_SLEEP_S, not the old 0.4s",
              all(s == ddf.POLITE_SLEEP_S for s in sleeps) and ddf.POLITE_SLEEP_S != 0.4, sleeps)

        print("\nterm-search union is SKIPPED once the tree walk already found a real catalog")
        sleeps.clear()
        real_parse_items = dd._parse_items
        canned = [{"name": "Item %d" % i, "price": "$9.99", "image_url": ""}
                  for i in range(ddf.TERM_SEARCH_MIN_ITEMS)]
        calls = {"n": 0}

        def fake_parse_items(blob):
            calls["n"] += 1
            return canned if calls["n"] == 1 else []   # only the tree-walk root page returns items

        dd._parse_items = fake_parse_items
        search_calls = []
        dd.search_store = lambda store, term, key=None, session=None, log=None: (
            search_calls.append(term) or [])
        try:
            items2, outlet2 = ddf.full_catalog("999", None, log=lambda *a: None)
            check("tree walk found >= TERM_SEARCH_MIN_ITEMS items",
                  len(items2) >= ddf.TERM_SEARCH_MIN_ITEMS, len(items2))
            check("search_store was NEVER called — the union was skipped as pure waste",
                  search_calls == [], search_calls)
            check("only the tree-walk + non-alc-walk sleeps fired (2), not +16 for a skipped union",
                  len(sleeps) == 2, sleeps)
        finally:
            dd._parse_items = real_parse_items
    finally:
        ddf.time.sleep = real_sleep
        dd._session = real_session
        dd._unlock = real_unlock
        dd.search_store = real_search

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
