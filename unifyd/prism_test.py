"""Plain-python tests for the Prism data model: python prism_test.py"""
import prism

passed = 0
TOTAL = 10


def ok(name, cond):
    global passed
    print(("ok   " if cond else "FAIL ") + name)
    passed += 1 if cond else 0


b = prism.bundle("vol_growth")
ok("bundle has all sections", all(k in b for k in
   ("measure", "measures", "dimensions", "cuts", "pulse", "provenance")))
ok("measure echoed", b["measure"] == "vol_growth")
ok("bad measure falls back to first", prism.bundle("nope")["measure"] == prism.MEASURES[0]["key"])
ok("one cut per dimension", len(b["cuts"]) == len(prism.DIMENSIONS))
ok("category cut has all members",
   len(b["cuts"][0]["rows"]) == len(prism.DIMENSIONS[0]["members"]))
ok("every row has value/delta/spark(12)/weight",
   all(len(r["spark"]) == 12 and all(k in r for k in ("name", "value", "delta", "weight"))
       for c in b["cuts"] for r in c["rows"]))
ok("weights sum ~1 per dim",
   all(abs(sum(r["weight"] for r in c["rows"]) - 1.0) < 0.02 for c in b["cuts"]))
ok("pulse feed non-empty + narrated",
   len(b["pulse"]) >= 1 and all(p.get("text") for p in b["pulse"]))

# determinism: same measure -> byte-identical bundle
ok("deterministic across calls", prism.bundle("share") == prism.bundle("share"))

# every cut is a value-desc leaderboard (biggest first), for growth and level measures alike
g = prism.cuts("vol_growth")[0]["rows"]
lv = prism.cuts("share")[0]["rows"]
ok("cuts are value-desc leaderboards",
   all(g[i]["value"] >= g[i + 1]["value"] - 1e-9 for i in range(len(g) - 1))
   and all(lv[i]["value"] >= lv[i + 1]["value"] - 1e-9 for i in range(len(lv) - 1)))

print("\n%d/%d passed" % (passed, TOTAL))
raise SystemExit(0 if passed == TOTAL else 1)
