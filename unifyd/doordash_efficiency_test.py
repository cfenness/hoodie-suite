#!/usr/bin/env python3
"""doordash_efficiency_test.py — cut idle time and true dead ends, never cut real capture.

Grounded in a real cost breakdown (2026-07-31): a single store's ~2min scrape is ~166 HTTP fetches
(alcohol tree walk + 16-term search union + non-alc tree walk), each followed by a blanket time.sleep(0.4)
— 66+s of pure sleep, more than half the observed per-store cost. That sleep is pure idle wait with no
completeness impact, so it's safe to cut (POLITE_SLEEP_S, tested below).

Two OTHER cuts were tried and reverted because they risked completeness: skipping the term-search union
once the tree walk found >= 40 items (a real risk — more real items could still exist for a store that
DOES carry alcohol), and cutting the per-attempt timeout from 60s to 20s (risks giving up on a
legitimately-slow-but-real response). Both reverted; this file locks in that revert as a regression.

A THIRD, narrower cut replaced them once doordash_chains.py stopped curating a ~15-chain list and
started attempting the full 767k-store DoorDash sitemap: most of that universe is restaurants with no
alcohol category at all, and the ROOT alcohol-category fetch already proves that in one request — every
one of those stores was still paying 16 term-search fetches + a full non-alc walk to reconfirm what the
first fetch already showed. That's the ONLY case skipped now: pages <= 1 and zero items from the tree
walk (true zero signal). The critical property this file protects: ANY signal at all — even a single
category or a single item — still gets the full, unconditional treatment. This is a fundamentally
different, much safer heuristic than the reverted "found enough already" one; it never trades away real
coverage, it only skips work that would find nothing.

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

    print("the completeness-neutral cut: POLITE_SLEEP_S replaces the hardcoded 0.4s")
    check("POLITE_SLEEP_S default is 0.15s, not the old hardcoded 0.4s",
          ddf.POLITE_SLEEP_S == 0.15, ddf.POLITE_SLEEP_S)

    print("\nregression: the fetch timeout must stay at the ORIGINAL 60s — a shorter one was reverted "
          "because it risked giving up on legitimately-slow-but-real responses")
    check("FETCH_TIMEOUT_S default is still 60s", dd.FETCH_TIMEOUT_S == 60.0, dd.FETCH_TIMEOUT_S)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "doordash.py")).read()
    check("_session()'s cr.Session uses FETCH_TIMEOUT_S (not a stray literal)",
          "timeout=FETCH_TIMEOUT_S" in src, "missing")

    print("\nregression: no 'found enough already' skip survives — that one was a real completeness risk")
    ddf_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "doordash_full.py")).read()
    check("no TERM_SEARCH_MIN_ITEMS constant (the reverted 'found enough' threshold)",
          "TERM_SEARCH_MIN_ITEMS" not in ddf_src, "still present")

    print("\nenv var overrides the one tunable knob (so a live run can be paced without a redeploy)")
    os.environ["DDFULL_POLITE_SLEEP_S"] = "0.05"
    try:
        importlib.reload(ddf)
        check("DDFULL_POLITE_SLEEP_S overrides POLITE_SLEEP_S", ddf.POLITE_SLEEP_S == 0.05, ddf.POLITE_SLEEP_S)
    finally:
        os.environ.pop("DDFULL_POLITE_SLEEP_S", None)
        importlib.reload(ddf)

    real_session = dd._session
    real_unlock = dd._unlock
    real_search = dd.search_store
    real_parse_items = dd._parse_items
    real_sleep = ddf.time.sleep
    dd._session = lambda store: "fake-session"

    def run_with_root_html(root_html, item_batches):
        """item_batches[i] = items _parse_items returns on the i-th call (root page is call 0)."""
        sleeps = []
        ddf.time.sleep = lambda s: sleeps.append(s)
        calls = {"n": 0}

        def fake_parse_items(blob):
            i = calls["n"]; calls["n"] += 1
            return item_batches[i] if i < len(item_batches) else []

        dd._unlock = lambda url, key=None, session=None, log=None: root_html
        dd._parse_items = fake_parse_items
        search_calls = []
        dd.search_store = lambda store, term, key=None, session=None, log=None: (
            search_calls.append(term) or [])
        try:
            items, outlet = ddf.full_catalog("999", None, log=lambda *a: None)
            return items, search_calls, sleeps
        finally:
            dd._unlock = real_unlock
            dd._parse_items = real_parse_items
            dd.search_store = real_search
            ddf.time.sleep = real_sleep

    try:
        print("\ntrue zero signal (no categories, no items from the tree-walk root) — "
              "skips term-search AND non-alc walk, the only case that does")
        items, search_calls, sleeps = run_with_root_html("<html></html>", [[]])
        check("zero items found", items == [], items)
        check("search_store was NEVER called — nothing to reconfirm from a store with no alcohol section",
              search_calls == [], search_calls)
        check("only the tree-walk root's own sleep fired (1), not +16 for a search union or the non-alc walk",
              sleeps == [ddf.POLITE_SLEEP_S], sleeps)

        print("\nANY signal at all — even ONE item — still gets the FULL, unconditional treatment "
              "(the property that must never regress)")
        one_item = [{"name": "Lone Beer", "price": "$5.99", "image_url": ""}]
        items2, search_calls2, sleeps2 = run_with_root_html("<html></html>", [one_item])
        check("the one real item survived", len(items2) == 1, items2)
        check("search_store WAS called for every term despite finding just one item — not skipped",
              search_calls2 == list(dd.ALCOHOL_TERMS), search_calls2)
        check("sleeps fired for tree walk (1) + term-search (16) + non-alc walk (1) = 18",
              len(sleeps2) == 1 + len(dd.ALCOHOL_TERMS) + 1, (len(sleeps2), sleeps2))

        print("\na LARGE catalog (60 items) also still gets the full treatment — "
              "regression test for the reverted 'found enough already' skip")
        canned = [{"name": "Item %d" % i, "price": "$9.99", "image_url": ""} for i in range(60)]
        items3, search_calls3, sleeps3 = run_with_root_html("<html></html>", [canned])
        check("all 60 items survived", len(items3) >= 60, len(items3))
        check("search_store WAS called for every term despite the large tree-walk catalog",
              search_calls3 == list(dd.ALCOHOL_TERMS), search_calls3)
        check("every recorded sleep used POLITE_SLEEP_S, not the old 0.4s",
              all(s == ddf.POLITE_SLEEP_S for s in sleeps3) and ddf.POLITE_SLEEP_S != 0.4, sleeps3)
    finally:
        dd._session = real_session
        dd._unlock = real_unlock
        dd.search_store = real_search
        dd._parse_items = real_parse_items
        ddf.time.sleep = real_sleep

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
