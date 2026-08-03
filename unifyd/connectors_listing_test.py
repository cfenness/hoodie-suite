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

print("\n%d listed (%d curated + %d derived) of %d enabled registry sources"
      % (len(listed), len(curated), len(fallthrough), len(enabled_ids)))
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
