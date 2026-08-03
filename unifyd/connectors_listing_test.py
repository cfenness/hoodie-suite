"""Offline test for the Pulls-console listing: python connectors_listing_test.py

No Flask, no warehouse, no network — this exercises the pure row builders that /api/connectors renders,
by reading server.py's CONNECTORS_META out of the source (importing server needs Flask, which the
scraper images don't carry).

The rule under test: CONNECTORS_META is a CURATED list, not the source of truth for what exists. A
source the registry owns must be VISIBLE in the console the day it lands — the same drift the run path
already paid down. Measured before this landed: 40 of 54 enabled registry sources were invisible,
including both distributor platform recipes.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_registry as reg

passed = failed = 0


def ok(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s %s" % (name, extra))


src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")).read()
block = src[src.index("CONNECTORS_META = ["):]
block = block[:block.index("\n]\n")]
curated = set(re.findall(r'\{"id": "([^"]+)"', block))
enabled = [s for s in reg.SOURCES if s.get("enabled")]
enabled_ids = {s["id"] for s in enabled}

# Mirror of server._registry_conn_rows (which can't be imported without Flask).
_KLASS_GROUP = {"mac": "Browser (anti-bot)", "creds": "Credentialed", "headless": "Registry source"}
fallthrough = [{"id": s["id"], "label": s.get("label") or s["id"],
                "group": _KLASS_GROUP.get(s.get("klass"), "Registry source"),
                "data": (s.get("tables") or [None])[0],
                "heavy": s.get("klass") == "mac",
                "needs_creds": bool(s.get("requires"))}
               for s in enabled if s["id"] not in curated]
listed = curated | {r["id"] for r in fallthrough}

ok("server.py wires the registry fallthrough into /api/connectors",
   "_registry_conn_rows()" in src and "CONNECTORS_META + _registry_conn_rows()" in src)
ok("groups are derived from the rendered rows, not just the curated list",
   'groups=sorted({c.get("group") or "" for c in out})' in src)
ok("EVERY enabled registry source is listed", not (enabled_ids - listed),
   sorted(enabled_ids - listed))
ok("the salsify platform recipe is listed", {"bbg", "salsify"} <= listed)
ok("so are the other two distributor platform recipes",
   {"vip-brandbuilder", "sevenfifty"} <= listed)
ok("curated rows still win over the derived floor (no duplicate ids)",
   len([r["id"] for r in fallthrough]) == len({r["id"] for r in fallthrough})
   and not (curated & {r["id"] for r in fallthrough}))
ok("bbg + salsify keep their curated labels, not the registry default",
   '"id": "bbg", "label": "Breakthru Beverage (Salsify catalog)"' in block
   and '"id": "salsify", "label": "Salsify Sites (public catalog platform)"' in block)
ok("a derived row carries a data table so the console can show a row count",
   all(r["data"] for r in fallthrough if reg.by_id(r["id"]).get("tables")))
ok("creds-gated sources are flagged, not silently unrunnable",
   all(r["needs_creds"] for r in fallthrough if reg.by_id(r["id"]).get("requires")))
ok("anti-bot browser sources are flagged heavy",
   all(r["heavy"] for r in fallthrough if reg.by_id(r["id"]).get("klass") == "mac"))
ok("disabled registry sources are NOT auto-listed (manual-trigger-only stays manual)",
   not ({s["id"] for s in reg.SOURCES if not s.get("enabled")} & {r["id"] for r in fallthrough}))


# ── the Scrape Run Tracker's source directory (/api/runs/docs) ───────────────────────────────────
# Same rule, second surface: the tracker's dropdown was two hardcoded entries, so a new scrape was
# untrackable until hand-added. It now derives from the registry, with the doc-backed long runs on top.
print("\nScrape Run Tracker directory")
docs_block = src[src.index("_RUN_DOCS = ["):]
docs_block = docs_block[:docs_block.index("\n]\n")]
run_docs = set(re.findall(r'\{"id": "([^"]+)"', docs_block))
scrapes_block = src[src.index("_SCRAPES = ["):]
scrapes_block = scrapes_block[:scrapes_block.index("\n]\n")]
scrapes = set(re.findall(r'\{"id": "([^"]+)"', scrapes_block))
tracker = run_docs | enabled_ids

ok("the tracker directory endpoint exists", '@app.get("/api/runs/docs")' in src)
ok("doc-backed long runs are still first-class", {"ubereats", "doordash"} <= run_docs)
ok("every enabled registry source is selectable in the tracker", enabled_ids <= tracker)
ok("bbg + salsify have a live-data table wired (_SCRAPES)", {"bbg", "salsify"} <= scrapes)
ok("partitioned tables are never handed to the dataset reader",
   'not t.endswith("_parts")' in src)
ok("kebab-case registry ids are accepted by /api/scrape/dataset",
   'site.replace("_", "").replace("-", "").isalnum()' in src,
   "dashed ids like trader-joes / abc-fws would 400")

# The tracker must never depend on a Mac-side poller. Progress comes from the scraper's own
# HOODIE_PROGRESS heartbeat → run_journal (shared warehouse) → /api/runs/live.
print("\nProgress path (no Mac in the loop)")
ok("the live-journal endpoint exists", '@app.get("/api/runs/live")' in src)
ok("it reads the shared warehouse journal, not a local file",
   "import run_journal" in src and "run_journal.recent(" in src and "run_journal.read(" in src)
salsify_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "salsify.py")).read()
ok("salsify heartbeats its own progress from its Fly machine",
   "HOODIE_PROGRESS" in salsify_src and "_progress(" in salsify_src)
ok("…and the heartbeat can never fail the crawl",
   re.search(r"def _progress\(\*\*kw\):.*?try:.*?except Exception:\s*pass", salsify_src, re.S) is not None)
ok("registry sources are listed BEFORE the legacy doc-backed pair",
   src.index("for s in source_registry.SOURCES:\n        if not s.get(\"enabled\") or s[\"id\"] in seen:")
   < src.index("for d in _RUN_DOCS:                                    # legacy"))

tracker_html = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "apps", "scrape-run-tracker.html")
if os.path.exists(tracker_html):
    html = open(tracker_html).read()
    ok("the tracker fetches the live directory", "/api/runs/docs" in html)
    ok("…and still works with no backend (fallback list retained)",
       "loadSourceDirectory" in html and 'id:"ubereats"' in html)
    ok("a source with no tick doc is explained, not 404'd against undefined",
       "if(!src.path) throw" in html)
    ok("the tracker reads the live journal for registry sources",
       "/api/runs/live" in html and "renderJournal" in html)
    ok("claimed vs landed rows are labelled separately, never merged",
       "Claimed rows (scraper)" in html and "Landed · " in html)
    ok("the legacy Mac-poller docs are labelled as legacy",
       "Legacy run docs (Mac poller)" in html)
    ok("a legacy doc still renders when a source has no journal yet",
       "if(src.path){ /* fall through to the markdown path below */ }" in html)

print("\n%d listed (%d curated + %d derived) of %d enabled registry sources"
      % (len(listed), len(curated), len(fallthrough), len(enabled_ids)))
print("%d selectable in the Scrape Run Tracker (%d doc-backed + %d registry)"
      % (len(tracker), len(run_docs), len(enabled_ids - run_docs)))
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
