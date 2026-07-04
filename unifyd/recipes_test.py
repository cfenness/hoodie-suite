"""Tests for the self-reinforcing recipe store. Run: python recipes_test.py"""
import recipes as R

_pass = _fail = 0
def ok(cond, name):
    global _pass, _fail
    if cond: _pass += 1
    else: _fail += 1; print("  FAIL:", name)

def rows(n, **fill):
    """n identical row dicts; fill kwargs set per-field values (default 'x')."""
    return {"rows": [dict({"name": "x", "price": "9"}, **{k: v for k, v in fill.items()}) for _ in range(n)],
            "engine": "api-json"}

def sparse(n, present):
    """n rows where the 'thc' field is filled on only `present` of them."""
    out = []
    for i in range(n):
        r = {"name": "x", "price": "9"}
        r["thc"] = "5mg" if i < present else ""
        out.append(r)
    return {"rows": out, "engine": "api-json"}

# ── host / platform ───────────────────────────────────────────────────────────
ok(R.host_of("https://www.ABCfws.com/spirits/vodka?x=1") == "abcfws.com", "host_of strips www/scheme/path")
ok(R.host_of("http://binnys.com") == "binnys.com", "host_of plain")
ok(R.platform_of({"data_api": {"url": "https://p16j4k.a.searchspring.io/api/search"}}) == "searchspring", "platform searchspring")
ok(R.platform_of({"data_api": {"url": "https://x.com/products.json"}}) == "shopify", "platform shopify")
ok(R.platform_of({"source_type": "static HTML table"}) not in ("searchspring", "shopify"), "platform generic-ish")

# ── save_config creates a candidate ───────────────────────────────────────────
book = {}
book = R.save_config(book, "https://abcfws.com/x", {"data_api": {"url": "https://p16j4k.a.searchspring.io/api"},
                                                    "scrape_prompt": "P", "available_fields": [{"name": "name"}]}, ts=1)
rec = R.get(book, "https://abcfws.com")
ok(rec and rec["status"] == "candidate", "save_config -> candidate")
ok(rec["platform"] == "searchspring" and rec["config"]["scrape_prompt"] == "P", "save_config stores platform+config")

# ── promotion: baseline -> proven after MIN_CLEAN clean runs ───────────────────
book, v1 = R.record_run(book, "https://abcfws.com/x", rows(100), ts=2)
ok(v1["run"] == "baseline" and v1["status"] == "candidate" and v1["clean_runs"] == 1, "first run sets baseline")
book, v2 = R.record_run(book, "https://abcfws.com/x", rows(100), ts=3)
ok(v2["run"] == "ok" and v2["status"] == "proven" and v2["promoted"] is True, "second clean run promotes to proven")

# ── drift: >40% row drop demotes proven -> drifting + needs_heal ───────────────
book, v3 = R.record_run(book, "https://abcfws.com/x", rows(50), ts=4)
ok(v3["run"] == "drift" and v3["status"] == "drifting" and v3["needs_heal"] is True, "row drop -> drift/needs_heal")
ok(v3["clean_runs"] == 0, "drift resets clean_runs")

# ── recovery: a clean run re-proves (drifting needs MIN_CLEAN again) ────────────
book, v4 = R.record_run(book, "https://abcfws.com/x", rows(100), ts=5)
ok(v4["run"] == "ok" and v4["status"] == "drifting" and v4["clean_runs"] == 1, "one clean run after drift not yet proven")
book, v5 = R.record_run(book, "https://abcfws.com/x", rows(100), ts=6)
ok(v5["status"] == "proven", "second clean run re-proves")

# ── broken: zero rows after a baseline ────────────────────────────────────────
book, v6 = R.record_run(book, "https://abcfws.com/x", {"rows": [], "engine": "api-json"}, ts=7)
ok(v6["run"] == "broken" and v6["status"] == "broken" and v6["needs_heal"], "zero rows -> broken")
book, v7 = R.record_run(book, "https://abcfws.com/x", {"error": "blocked"}, ts=8)
ok(v7["run"] == "broken", "fetch error -> broken")

# ── fill-rate drop is drift ───────────────────────────────────────────────────
b2 = {}
b2, _ = R.record_run(b2, "https://s.com", sparse(100, 100), ts=1)   # thc 100% filled -> baseline
b2, _ = R.record_run(b2, "https://s.com", sparse(100, 100), ts=2)   # proven
b2, vf = R.record_run(b2, "https://s.com", sparse(100, 20), ts=3)   # thc filled on 20/50 sampled = 40%
ok(vf["run"] == "drift" and "thc" in vf["reason"], "fill-rate drop -> drift")

# ── schema drift: missing columns ─────────────────────────────────────────────
b3 = {}
b3, _ = R.record_run(b3, "https://c.com", {"rows": [{"a": 1, "b": 2, "c": 3, "d": 4}] * 10}, ts=1)
b3, _ = R.record_run(b3, "https://c.com", {"rows": [{"a": 1, "b": 2, "c": 3, "d": 4}] * 10}, ts=2)
b3, vs = R.record_run(b3, "https://c.com", {"rows": [{"a": 1}] * 10}, ts=3)
ok(vs["run"] == "drift" and "missing" in vs["reason"], "missing columns -> drift")

# ── apply_heal resets to candidate + overwrites config ────────────────────────
book = R.apply_heal(book, "https://abcfws.com/x", {"data_api": {"url": "https://new.searchspring.io"},
                                                   "scrape_prompt": "P2", "available_fields": []}, ts=9)
rec = R.get(book, "https://abcfws.com")
ok(rec["status"] == "candidate" and rec["config"]["scrape_prompt"] == "P2" and rec["clean_runs"] == 0, "apply_heal -> candidate + new config")

# ── proven config is stable across a plain save_config (only heal overwrites) ──
b4 = {}
b4, _ = R.record_run(b4, "https://p.com", rows(10), ts=1)
b4, _ = R.record_run(b4, "https://p.com", rows(10), ts=2)   # proven
b4 = R.save_config(b4, "https://p.com", {"scrape_prompt": "SHOULD_NOT_REPLACE"}, ts=3)
ok(R.get(b4, "https://p.com")["config"].get("scrape_prompt") != "SHOULD_NOT_REPLACE", "proven config not clobbered by save_config")

# ── platform reuse: one proven SearchSpring store hints the next ──────────────
bp = {}
bp = R.save_config(bp, "https://abcfws.com/x", {"data_api": {"url": "https://a.a.searchspring.io/api"}}, ts=1)
bp, _ = R.record_run(bp, "https://abcfws.com/x", rows(80), ts=2)
bp, _ = R.record_run(bp, "https://abcfws.com/x", rows(80), ts=3)   # abcfws proven, platform searchspring
ok(len(R.by_platform(bp, "searchspring")) == 1, "by_platform finds the proven searchspring recipe")
ok(R.platform_proven_on(bp, "https://otherstore.com", {"data_api": {"url": "https://b.a.searchspring.io/api"}}) == ["abcfws.com"],
   "platform_proven_on: new searchspring host sees abcfws as proven")
ok(R.platform_proven_on(bp, "https://abcfws.com", {"data_api": {"url": "https://a.a.searchspring.io"}}) == [],
   "platform_proven_on excludes the same host")

# ── config-fill: a recipe proven by extraction alone is still runnable ────────
cf = {}
cf, _ = R.record_run(cf, "https://cann.com", rows(30), ts=1, used={"prompt": "extract products", "target": "https://cann.com/products.json"})
cf, _ = R.record_run(cf, "https://cann.com", rows(30), ts=2, used={"prompt": "extract products", "target": "https://cann.com/products.json"})
rc = R.get(cf, "https://cann.com")
ok(rc["status"] == "proven" and rc["config"].get("scrape_prompt") == "extract products", "extraction-proven recipe carries a runnable prompt")
ok((rc["config"].get("data_api") or {}).get("url") == "https://cann.com/products.json", "extraction-proven recipe carries a target")
# a proven recipe's captured config must survive a later save_config (analyze on a proven host)
cf = R.save_config(cf, "https://cann.com", {"scrape_prompt": "SHOULD_NOT_REPLACE", "data_api": {"url": "https://api"}}, ts=3)
ok(R.get(cf, "https://cann.com")["config"]["scrape_prompt"] == "extract products", "save_config doesn't clobber a proven recipe's config")

# ── cumulative erosion: slow decline below the proven baseline is caught ───────
ce = {}
ce, _ = R.record_run(ce, "https://e.com", rows(100), ts=1)
ce, _ = R.record_run(ce, "https://e.com", rows(100), ts=2)   # proven, proven_baseline=100
ce, e1 = R.record_run(ce, "https://e.com", rows(70), ts=3)   # -30% single-step: under 40%, passes rolling...
ok(e1["run"] == "ok", "a 30% single-step dip is within the rolling threshold")
ce, e2 = R.record_run(ce, "https://e.com", rows(45), ts=4)   # now 45 vs proven 100 = -55% cumulative
ok(e2["run"] == "drift" and "cumulative" in e2["reason"], "slow erosion below the proven baseline is caught")

# ── stats ─────────────────────────────────────────────────────────────────────
s = R.stats(book)
ok(s["total"] >= 1 and "by_status" in s, "stats summary")

print("recipes_test:", _pass, "/", _pass + _fail, "passed")
raise SystemExit(1 if _fail else 0)
