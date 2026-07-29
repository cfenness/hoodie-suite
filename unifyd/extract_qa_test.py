"""A 200 that is wrong is worse than a 403 — so validate the OUTPUT, not the connection.

Everything else instrumented today lives on one side of a line: did the request succeed. `has_catalog`
proves a response carried structure, `blocks` names why a fetch failed, `pace` and `sessions` keep us
under the target's limits. All of it is about getting the bytes.

None of it notices a target quietly redesigning its payload so the parser returns nulls. Every fetch is
200, every counter green, the run reports ok, and the corpus rots — normally discovered weeks later by
whoever consumes the data. A block announces itself; parser drift does not.

The comparison is BASELINE-RELATIVE for the same reason the pacer's trip threshold is: an absolute rule
encodes today's guess about a source we may not understand, and every fixed constant in this system has
been wrong within a day. A field that has always been sparse is not drift.

The detector is deliberately quiet — no history is NEW, too few rows is THIN, neither is a pass. A noisy
alarm gets muted, and a muted alarm is worse than none.

Pure statistics — no network, no warehouse.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_qa as Q  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


def rows(n, **kw):
    return [dict(kw) for _ in range(n)]


# ── "populated" means carries information, not merely 'not None' ─────────────────────────────────
check("None is empty", Q._filled(None), False)
check("blank string is empty", Q._filled("   "), False)
check("empty list is empty", Q._filled([]), False)
check("empty dict is empty", Q._filled({}), False)
check("zero is POPULATED", Q._filled(0), True)          # a real price of 0 is data, not absence
check("False is POPULATED", Q._filled(False), True)
check("text is populated", Q._filled("x"), True)

# ── the silent killer: the parser breaks, every fetch still returns 200 ──────────────────────────
base = {"name": 100.0, "price": 98.0, "upc": 95.0}
healthy = Q.field_stats(rows(500, name="a", price=10, upc="1"))
check("healthy batch passes", Q.drifted(Q.assess(healthy, base)), [])

broken = Q.field_stats(rows(500, name="a", price=None, upc="1"))
vd = Q.assess(broken, base)
check("a broken field is caught", Q.drifted(vd), ["price"])
check("healthy fields unaffected", vd["name"], Q.OK)
check("summary names it with numbers", "price" in Q.summary(broken, vd), True)

# a partial break — half the values lost — must also fire
half = rows(250, name="a", price=10, upc="1") + rows(250, name="a", price=None, upc="1")
check("a 50% collapse is drift", Q.drifted(Q.assess(Q.field_stats(half), base)), ["price"])

# ── things that must NOT fire, or the alarm gets muted ───────────────────────────────────────────
check("always-sparse field is not drift",
      Q.drifted(Q.assess(Q.field_stats(rows(500, x=None)), {"x": 3.0})), [])
check("small wobble is not drift",
      Q.drifted(Q.assess(Q.field_stats(rows(500, price=10)), {"price": 100.0})), [])
noisy = rows(460, price=10) + rows(40, price=None)      # 92% vs a 98% baseline
check("normal variation is not drift", Q.drifted(Q.assess(Q.field_stats(noisy), base)), [])

# ── conservative where it lacks evidence ─────────────────────────────────────────────────────────
check("no history is NEW, not a pass", Q.assess(healthy, {})["price"], Q.NEW)
check("thin batch is THIN, not a pass", Q.assess(Q.field_stats(rows(10, price=None)), base)["price"], Q.THIN)
check("thin batch never reports drift", Q.drifted(Q.assess(Q.field_stats(rows(10, price=None)), base)), [])
check("empty input is empty stats", Q.field_stats([]), {})

# ── stats themselves ─────────────────────────────────────────────────────────────────────────────
st = Q.field_stats(rows(300, a=1, b=None))
check("counts rows", st["a"]["rows"], 300)
check("counts filled", st["a"]["filled"], 300)
check("fill_pct for empty field", st["b"]["fill_pct"], 0.0)

# ── and it must actually gate the run, not just log ──────────────────────────────────────────────
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ue_catalog.py")).read()
check("drift downgrades the run", 'if qa.get("drifted"):' in src, True)
check("QA result lands in the record", 'rec["field_qa"] = qa' in src, True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- extract_qa: a broken parser is caught while every fetch returns 200 (24 checks)")
