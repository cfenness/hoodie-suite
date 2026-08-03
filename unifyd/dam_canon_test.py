"""Exercise canon brand resolution — P2's "canon-keyed" exit criterion.

The properties that matter are about what does NOT happen: no fuzzy match, no canon claim we can't
support, and no silently-different key function. Pure stdlib; `dim_brand` is mocked."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dam         # noqa: E402
import dam_canon   # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# ── the key must be THE shared key, never a lookalike ─────────────────────────────────────────────
print("brand key parity:")
try:
    import overlay_match
    PAIRS = ["DEWAR'S", "Dewars", "dewar’s", "JOHN DEWAR & SONS", "BACARDÍ", "Bacardi",
             "GREY GOOSE", "Grey Goose Vodka", "ST~GERMAIN", "St-Germain", "Tito's Handmade"]
    bad = [n for n in PAIRS if dam_canon._key(n) != overlay_match.brand_key(n)]
    check(not bad, "dam_canon._key IS overlay_match.brand_key for every spelling (%s)" % (bad or "all match"))
    check(dam_canon._key("DEWAR'S") == dam_canon._key("Dewars") == dam_canon._key("dewar’s"),
          "possessive/typographic variants of one brand share a key")
    # The exact divergence a hand-rolled fallback would have introduced, pinned so nobody re-adds one.
    check(dam_canon._key("Grey Goose Vodka") == dam_canon._key("GREY GOOSE"),
          "a generic suffix ('Vodka') is dropped — the reason a lookalike fallback was refused")
    check(dam_canon._key("BACARDÍ") == dam_canon._key("Bacardi"), "accents are folded")
except ImportError:
    check(False, "overlay_match must be importable for canon resolution to be meaningful")


# ── resolution against a mocked master ────────────────────────────────────────────────────────────
MASTER = [
    {"brand_key": "bk-001", "brand": "Bacardi", "hoodie_id": "BRD-BACARD"},
    {"brand_key": "bk-002", "brand": "Grey Goose Vodka", "hoodie_id": "BRD-GREYGO"},
    {"brand_key": "bk-003", "brand": "Dewar's", "hoodie_id": "BRD-DEWARS"},
    {"brand_key": "bk-004", "brand": "Some Other Brand", "hoodie_id": "BRD-OTHER"},
]


class FakeWarehouse:
    fail = False

    @staticmethod
    def query(name, sql=None, params=None):
        if FakeWarehouse.fail:
            raise RuntimeError("no duckdb")
        assert name == "dim_brand", name
        return MASTER


sys.modules["warehouse"] = FakeWarehouse

print("\nresolution:")
idx = dam_canon.brand_index(log=lambda *a: None)
check(idx is not None and len(idx) == 4, "the master indexes by brand key (%s rows)" % (len(idx or {})))

matches, report = dam_canon.resolve_brands(
    ["BACARDÍ", "GREY GOOSE", "DEWAR'S", "ST~GERMAIN", "NOT A REAL BRAND"], idx=idx)
check(matches["BACARDÍ"]["hoodie_id"] == "BRD-BACARD", "an accented literal resolves to canon")
check(matches["GREY GOOSE"]["hoodie_id"] == "BRD-GREYGO",
      "a DAM brand resolves to a master row whose spelling carries a generic suffix")
check(matches["DEWAR'S"]["hoodie_id"] == "BRD-DEWARS", "a possessive resolves")
check(matches["ST~GERMAIN"] is None, "a brand absent from the master resolves to NOTHING")
check(matches["NOT A REAL BRAND"] is None, "no fuzzy fallback invents a match")
check(report["brands_resolved"] == 3 and report["brands_unresolved"] == 2, "the report counts both sides")
check(report["available"] is True, "the master was readable")

print("\napply to events:")
EV = [{"event_id": "E1", "brand": "BACARDÍ", "hoodie_brand_id": "dam-bacardi:bacardi",
       "field_provenance": json.dumps({"brand": dam.DETERMINISTIC,
                                       "hoodie_brand_id": dam.DETERMINISTIC})},
      {"event_id": "E2", "brand": "ST~GERMAIN", "hoodie_brand_id": "dam-bacardi:st-germain",
       "field_provenance": json.dumps({"brand": dam.DETERMINISTIC,
                                       "hoodie_brand_id": dam.DETERMINISTIC})}]
rep = dam_canon.apply_to_events(EV, log=lambda *a: None)
a, b = EV
check(a["hoodie_brand_id"] == "BRD-BACARD" and a["brand_key"] == "bk-001",
      "a resolved event carries the CANON id, not the vendor slug")
check(a["canon_brand"] == "Bacardi", "the master's display spelling comes along")
check(a["brand_resolution"] == "canon", "the resolution method is stated on the row")
check(json.loads(a["field_provenance"])["hoodie_brand_id"] == dam.DETERMINISTIC,
      "a canon id is a DETERMINISTIC claim")

check(b["hoodie_brand_id"] == "dam-bacardi:st-germain",
      "an unresolved event KEEPS its vendor-scoped slug (no invented canon id)")
check(b["brand_resolution"] == "unresolved", "...and says so on the row")
check("hoodie_brand_id" not in json.loads(b["field_provenance"]),
      "an unresolved id makes NO provenance claim — we don't assert what we can't support")
check(b["brand_key"] is None and b["canon_brand"] is None, "unresolved rows carry no canon columns")
check(rep["brands_resolved"] == 1, "the report reflects what actually resolved")

print("\nmaster unavailable is not the same as no matches:")
FakeWarehouse.fail = True
EV2 = [{"event_id": "E3", "brand": "BACARDÍ", "hoodie_brand_id": "dam-bacardi:bacardi",
        "field_provenance": "{}"}]
rep2 = dam_canon.apply_to_events(EV2, log=lambda *a: None)
check(rep2["available"] is False, "an unreadable master reports available=False")
check(EV2[0]["brand_resolution"] == "master-unavailable",
      "the row distinguishes 'could not look' from 'looked and missed'")
check(EV2[0]["hoodie_brand_id"] == "dam-bacardi:bacardi",
      "the fact still lands in full — a missing join never costs the event")
FakeWarehouse.fail = False

print("\nno divergent key path:")
check(hasattr(dam_canon, "KeyUnavailable"),
      "the module can report that the shared key function is unavailable")
_real = dam_canon._key


def _boom(_n):
    raise dam_canon.KeyUnavailable("simulated")


dam_canon._key = _boom
check(dam_canon.brand_index(log=lambda *a: None) is None,
      "without the shared key function, resolution REFUSES rather than keying its own way")
dam_canon._key = _real

# ── the schema carries the canon columns ──────────────────────────────────────────────────────────
print("\nschema:")
for col in ("hoodie_brand_id", "brand_key", "canon_brand", "brand_resolution", "abv"):
    check(col in dam.EVENT_FIELDS, "brand_events carries %s" % col)

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
