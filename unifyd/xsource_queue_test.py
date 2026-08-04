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

print("\nordering — the most informative pair first:")
src = {"A": 3, "B": 3, "X": 1, "Y": 1}
seen = {}
check(q.priority(pair("p1", "brand_spelling"), seen, src) == 0,
      "an UNSEEN difference cause sorts first — that is where an answer changes a rule")
seen = {"size_format": 9}
check(q.priority(pair("p2", "size_format"), seen, src) > 0,
      "a cause already answered nine times does NOT sort first")
check(q.priority(pair("p3", "size_format", stratum="near_miss", rule=True), {"size_format": 9}, src) == 1,
      "a rule/stratum disagreement is the boundary, and sorts next")
check(q.priority(pair("p4", "size_format", a="A", b="B"), {"size_format": 9}, src) == 2,
      "a widely-carried item outranks a thinly-carried one (%d sources)" % (src["A"] + src["B"]))
check(q.priority(pair("p5", "size_format", a="X", b="Y"), {"size_format": 9}, src) == 3,
      "a two-source item sorts last")

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
