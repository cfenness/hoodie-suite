"""Exercise the cross-source merge.

An identity error is the expensive kind — it mis-attributes every downstream fact about a product
and nothing in the row looks wrong afterwards. So most of these assert what the rule REFUSES to
merge. Pure stdlib apart from `precleanse`, which is the shared normalization."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xsource_match as xm  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def row(rid, brand, name, size="", upc="", source="s"):
    return {"resolved_id": rid, "brand": brand, "name": name, "size": size, "upc": upc,
            "source": source}


# ── the signature ─────────────────────────────────────────────────────────────────────────────────
print("signature:")
check(xm.brand_key("Tito's Handmade") == xm.brand_key("TITOS HANDMADE"),
      "possessive and case variants share a brand key")
check(xm.size_ml("750ml") == 750 and xm.size_ml("1.75L") == 1750 and xm.size_ml("50 mL") == 50,
      "ml and L parse to millilitres")
check(xm.size_ml("no size here") is None, "an unstated size is None")
check(xm.name_sig("Tito's Handmade Vodka 750ml", "tito")
      == xm.name_sig("Titos Handmade Vodka 750 ML", "tito"),
      "'750ml' and '750 ML' produce the SAME name signature (the bare-number bug)")
check(xm.size_ml("") is None and xm.size_ml("2 pack") is None, "a pack count is not a size")
check(xm.signature("Absolut", "Absolut Citron Vodka") is None,
      "NO SIZE means no signature — a missing size is a refusal, not a wildcard")
check(xm.signature("Absolut", "Absolut Citron Vodka 750ml") is not None, "with a size it resolves")
check(xm.signature("", "Absolut Citron 750ml") is not None,
      "a missing brand column falls back to the name rather than refusing outright")

s1 = xm.signature("Tito's", "Tito's Handmade Vodka 750ml")
s2 = xm.signature("Titos", "Titos Handmade Vodka 750 ML")
check(s1 == s2, "the live failure case — Tito's 750ml from two sources makes ONE signature")
check(xm.signature("Absolut", "Absolut Citron 750ml") != xm.signature("Absolut", "Absolut Citron 1750ml"),
      "different SIZES never share a signature (item grain is product+size)")
check(xm.signature("Bogle", "Bogle Merlot 750ml") != xm.signature("Bogle", "Bogle Cabernet 750ml"),
      "same brand, different varietal — different signatures")

# ── the merge, and its refusals ───────────────────────────────────────────────────────────────────
print("\nmerge:")
m = xm.merge([row("A", "Tito's", "Tito's Handmade Vodka 750ml", source="binnys"),
              row("B", "Titos", "Titos Handmade Vodka 750 ML", source="total-wine")])
check(m.get("A") == m.get("B") and m.get("A") is not None, "two over-split identities merge")
check(m["A"] == "A", "the merged id is deterministic (lowest), so re-runs are stable")

check(not xm.merge([row("A", "Absolut", "Absolut Citron 750ml"),
                    row("B", "Absolut", "Absolut Citron 1750ml")]),
      "different sizes do NOT merge")
check(not xm.merge([row("A", "Absolut", "Absolut Citron")]), "a single identity has nothing to merge")
check(not xm.merge([row("A", "Absolut", "Absolut Citron"), row("B", "Absolut", "Absolut Citron")]),
      "two identities with NO size do not merge — the signature never formed")

print("\n  the UPC conflict guard:")
conf = xm.merge([row("A", "Tito's", "Tito's Handmade Vodka 750ml", upc="619947000020"),
                 row("B", "Tito's", "Tito's Handmade Vodka 750ml", upc="619947000037")])
check(not conf, "two DIFFERENT explicit UPCs never merge, however alike the names")
same = xm.merge([row("A", "Tito's", "Tito's Handmade Vodka 750ml", upc="619947000020"),
                 row("B", "Tito's", "Tito's Handmade Vodka 750 ML", upc="0619947000020")])
check(same.get("A") == same.get("B"), "the same UPC with a leading zero still merges")
part = xm.merge([row("A", "Tito's", "Tito's Handmade Vodka 750ml", upc="619947000020"),
                 row("B", "Tito's", "Tito's Handmade Vodka 750 ML", upc="")])
check(part.get("A") == part.get("B"), "one side missing a UPC is not a conflict")

# ── scoring ───────────────────────────────────────────────────────────────────────────────────────
print("\nscore (gold built from the data itself):")
GOOD = [row("A", "Tito's", "Tito's Handmade Vodka 750ml", upc="619947000020", source="binnys"),
        row("B", "Titos", "Titos Handmade Vodka 750 ML", upc="619947000020", source="abc"),
        row("C", "Absolut", "Absolut Citron 750ml", upc="835229004009", source="binnys"),
        row("D", "Absolut", "Absolut Citron Vodka 750 ml", upc="835229004009", source="abc")]
sc = xm.score(GOOD)
check(sc["precision"] == 1.0, "clean data scores precision 1.0 (%s)" % sc["precision"])
check(sc["recall"] == 1.0, "...and recall 1.0 (%s)" % sc["recall"])
check(sc["scored_pairs"] == 2, "only UPC-bearing pairs are scored (%s)" % sc["scored_pairs"])

BAD = GOOD + [row("E", "Absolut", "Absolut Citron 750ml", upc="999999999999", source="x")]
sb = xm.score(BAD)
check(sb["precision"] is not None, "a conflicting row is scored, not ignored")
check(sb["false_pairs"] == 0,
      "the UPC guard prevents the conflicting row from being merged in at all (%s false pairs)"
      % sb["false_pairs"])

nos = xm.score([row("A", "Absolut", "Absolut Citron 750ml"), row("B", "Absolut", "Absolut Citron 750 ml")])
check(nos["precision"] is None, "with no UPCs anywhere, precision is None — never assumed 1.0")
check(nos["unscoreable_rows"] == 2, "and the unscoreable rows are counted, not hidden")

print("\nthe landing bar:")
check(xm.MIN_PRECISION >= 0.98, "the bar is high (%s) — identity errors are silent and permanent"
      % xm.MIN_PRECISION)

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
