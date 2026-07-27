"""Tests for self_heal (SCRAPING-PLATFORM.md P2 — spread self-heal): python self_heal_test.py
Verifies the AI parse re-derivation is now SOURCE-AGNOSTIC: tabular (column index) + keyed/JSON (source
key) mappings, both validated (only needed fields; a keyed answer must exist in the record), cached,
disabled cleanly when off, and the TTB back-compat wrapper still works. The LLM call is stubbed — no key,
no network."""
import os
import sys

os.environ["AGENT_SELF_HEAL"] = "1"     # enable BEFORE import (SELF_HEAL is read at import)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import self_heal as sh

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


ok("enabled() reflects the flag", sh.enabled() is True)

# ── stub the LLM round-trip; record calls to prove caching ────────────────────────────────────────
calls = {"n": 0}
_stub_reply = {}


def _fake_ask(prompt, want_int):
    calls["n"] += 1
    return dict(_stub_reply)


sh._ask = _fake_ask
sh._cache.clear()

# ── TABULAR: keep only needed fields; drop extras ─────────────────────────────────────────────────
_stub_reply = {"brand": 5, "junk": 2}      # 'junk' not needed → dropped; 'origin' not answered → absent
m = sh.heal_columns(["a", "b", "c"], ["x", "y", "z"], needed=["brand", "origin"], context="Some Site")
ok("tabular: keeps needed, drops extras", m == {"brand": 5})
ok("tabular: cached (2nd call, no 2nd LLM hit)",
   sh.heal_columns(["a", "b", "c"], ["x", "y", "z"], needed=["brand", "origin"]) == {"brand": 5} and calls["n"] == 1)

# ── KEYED/JSON: answer must be a real key in the sample ───────────────────────────────────────────
sh._cache.clear(); calls["n"] = 0
rec = {"brandName": "Tito's", "titleText": "Vodka", "priceCents": 1999}
_stub_reply = {"brand": "brandName", "name": "titleText", "missing": "nope_not_a_key"}
f = sh.heal_fields(rec, needed=["brand", "name", "missing"], context="Shopify products.json")
ok("keyed: maps needed fields to real keys", f == {"brand": "brandName", "name": "titleText"})
ok("keyed: drops a hallucinated key not in the record", "missing" not in f)

# ── TTB back-compat: propose_mapping still routes through heal_columns ─────────────────────────────
sh._cache.clear(); calls["n"] = 0
_stub_reply = {"brand": 3, "origin": 4}
tm = sh.propose_mapping(["h1", "h2", "h3", "h4", "h5"], ["s1", "s2", "s3", "s4", "s5"], needed=["brand", "origin"])
ok("TTB propose_mapping still works", tm == {"brand": 3, "origin": 4})

# ── disabled: no flag → no LLM, returns {} ────────────────────────────────────────────────────────
sh.SELF_HEAL = False; sh._cache.clear(); calls["n"] = 0
ok("disabled: heal_columns → {}", sh.heal_columns(["a"], ["x"], ["brand"]) == {})
ok("disabled: heal_fields → {}", sh.heal_fields({"k": "v"}, ["brand"]) == {})
ok("disabled: no LLM call made", calls["n"] == 0)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
