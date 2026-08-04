"""Exercise the continuous queue: ordering, and what it refuses to fabricate.

The queue's job is to never run dry and to put the most INFORMATIVE pair first. Pure stdlib; the
warehouse is mocked, so this proves the ordering and the resolution shape without a live master."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xsource_queue as q  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def pair(pid, diff, stratum="merged", rule=False, a="A", b="B"):
    return {"pair_id": pid, "difference": diff, "stratum": stratum, "rule_merges": rule,
            "a_id": a, "b_id": b}


print("difference (server-side, must agree with the trainer):")
check(q.difference({"a_name": "Baron Herzog Sauvignon Blanc", "a_size": "750ML",
                    "b_name": "Baron Herzog Sauvignon Blanc", "b_size": "750 mL"}) == "size_format",
      "same name, differently formatted size -> size_format")
check(q.difference({"a_name": "Bud Light", "a_size": "7 Oz",
                    "b_name": "Bud Light", "b_size": "25.4 Oz"}) == "size_value",
      "different sizes -> size_value")
check(q.difference({"a_name": "Blue Point Gose", "a_size": "750ML",
                    "b_name": "Blue Point IPA", "b_size": "750ML"}) == "different_tokens",
      "different products -> different_tokens")
check(q.difference({"a_brand": "Mi Campo", "a_name": "Mi Campo Reposado Tequila", "a_size": "750ML",
                    "b_brand": "Mi Campo", "b_name": "Mi Campo Reposado", "b_size": "750ML"})
      == "normalised_away",
      "a difference the matcher's normalisation absorbs is named, not called 'different words'")

print("\ntext coercion (the bug that generated 4,000 pairs and landed none):")
check(q._s(750.0) == "750", "a DuckDB float size loses the '.0' — 750.0 -> '750', not '750.0'")
check(q._s(750) == "750" and q._s("750ML") == "750ML", "ints stringify, strings pass through")
check(q._s(None) is None, "None stays None — a missing value is never the string 'None'")

print("\nordering — the most informative pair first:")
# DISTINCT SOURCES, not row counts: A and B are each carried by two chains.
src = {"A": {"binnys", "abc"}, "B": {"total-wine", "specs"}, "X": {"binnys"}, "Y": {"abc"}}
seen = {}
check(q.priority(pair("p1", "brand_spelling"), seen, src) == 0,
      "an UNSEEN difference cause sorts first — that is where an answer changes a rule")
seen = {"size_format": 9}
check(q.priority(pair("p2", "size_format"), seen, src) > 0,
      "a cause already answered nine times does NOT sort first")
check(q.priority(pair("p3", "size_format", rule=True), {"size_format": 9}, src) == 1,
      "the rule merged them DESPITE a visible difference — the boundary, and it sorts next")
check(q.priority(pair("p4", "identical", rule=True), {"identical": 9}, src) != 1,
      "an IDENTICAL pair the rule merges is not a boundary case — there is nothing to disagree about")
check(q.priority(pair("p5", "size_format", a="A", b="B"), {"size_format": 9}, src) == 2,
      "a widely-carried item outranks a thinly-carried one (4 distinct sources)")
check(q.priority(pair("p6", "size_format", a="X", b="Y"), {"size_format": 9}, src) == 3,
      "a two-source item sorts last")
check(q.priority(pair("p7", "size_format", a="Z", b="Z"), {"size_format": 9}, src) == 3,
      "an id absent from the counts does not crash and does not get promoted")

print("\na failed land is a DEGRADE, not a success:")
GOLD_ROWS = [{"resolved_id": "A", "brand": "Tito's", "name": "Tito's Handmade Vodka 750ml",
              "size": "750ML", "upc": "619947000020", "source": "binnys"},
             {"resolved_id": "B", "brand": "Titos", "name": "Titos Handmade Vodka 750 ML",
              "size": "750 ML", "upc": "619947000020", "source": "abc"}]


class LandFails:
    @staticmethod
    def query(*a, **k):
        return []

    @staticmethod
    def write_accumulate(*a, **k):
        raise RuntimeError("Could not convert '750ML' with type str: tried to convert to int64")


import io  # noqa: E402
import contextlib  # noqa: E402

sys.modules["warehouse"] = LandFails
q._master_rows = lambda **k: GOLD_ROWS
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    pairs, cov = q.build(n=10, log=lambda *a: None)
out = buf.getvalue()
check('"status": "degraded"' in out,
      "a build that generates pairs and lands NONE reports degraded (it reported success once)")
check('"items_done": 0' in out, "items_done is the LANDED count, not the generated count")
check("landed 0" in out or "warnings" in out, "and it says what happened in warnings[]")

print("\na rebuild must not erase an answer:")
KEPT = {}


class Answered:
    @staticmethod
    def query(name, sql=None, params=None):
        return [{"pair_id": pid} for pid in ANSWERED]

    @staticmethod
    def write_accumulate(name, rows, **k):
        KEPT[name] = list(rows)
        return {"rows": len(rows)}


sys.modules["warehouse"] = Answered
ANSWERED = []
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    all_pairs, _ = q.build(n=10, log=lambda *a: None)
n_all = len(KEPT.get("xsource_queue", []))
ANSWERED = [p["pair_id"] for p in all_pairs]
KEPT.clear()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    q.build(n=10, log=lambda *a: None)
check(n_all > 0 and len(KEPT.get("xsource_queue", [])) == 0,
      "a pair already answered is NOT re-landed — candidates() is seeded, so a weekly rebuild "
      "regenerates the same pair_ids and would otherwise blank their labels")
check('"status": "success"' in buf.getvalue(),
      "...and landing 0 because everything is answered is a success, not a degrade")

print("\nresolve:")
LANDED = {}


class FakeWarehouse:
    @staticmethod
    def query(name, sql=None, params=None):
        return [{"pair_id": "P1", "a_brand": "Titos", "b_brand": "Tito's",
                 "a_name": "Titos Handmade", "b_name": "Tito's Handmade",
                 "a_size": "750 mL", "b_size": "750ML"}] if "xsource_queue" in name else []

    @staticmethod
    def write_accumulate(name, rows, key=None, fields=None, coverage=True):
        LANDED.setdefault(name, []).extend(rows)
        return {"rows": len(rows)}

    @staticmethod
    def row_count(name):
        return 1


sys.modules["warehouse"] = FakeWarehouse
out = q.resolve({"pair_id": "P1", "label": "y", "canon_brand": "Tito's",
                 "canon_size": "750mL", "canon_type": "Spirits"}, log=lambda *a: None)
check(out.get("ok"), "a resolution lands")
check(LANDED.get("xsource_gold"), "the labelled pair lands in the gold table")
check(LANDED.get("xsource_dictionary"), "and the value mappings land in the dictionary")

d = LANDED["xsource_dictionary"]
variants = {r["variant_key"] for r in d if r["dimension"] == "canon_size"}
check("750 ml" in variants and "750ml" in variants,
      "BOTH source spellings map to the canonical size (%s)" % sorted(variants))
brands = {r["variant_key"] for r in d if r["dimension"] == "canon_brand"}
check("titos" in brands and "tito's" in brands, "both brand spellings are learned")
types = [r for r in d if r["dimension"] == "canon_type"]
check(types and types[0]["canonical"] == "Spirits",
      "a TYPED value with no source spelling still lands — it maps to itself")

LANDED.clear()
q.resolve({"pair_id": "P1", "label": "n"}, log=lambda *a: None)
check(not LANDED.get("xsource_dictionary"),
      "a 'different' verdict teaches NO vocabulary — there is no canonical value to learn")
check(q.resolve({}, log=lambda *a: None).get("error"), "a resolution with no pair_id is refused")

print("\nempty pool:")


class Dead:
    @staticmethod
    def query(*a, **k):
        raise RuntimeError("no table")

    @staticmethod
    def row_count(n):
        return 0


sys.modules["warehouse"] = Dead
check(q.next_batch(5, log=lambda *a: None) == [],
      "an unbuilt pool returns [] rather than raising — the UI says 'not built', not 'you're done'")
check(q.dictionary(log=lambda *a: None) == {}, "and the dictionary degrades to empty")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
