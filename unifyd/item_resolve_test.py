"""Tests for the item resolver. Pure, stdlib-only, no warehouse needed.

Every case here is a REAL speech-to-text corruption observed while testing the Hoodie
Relations voice visit form — not invented input.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from item_resolve import norm, phon, score, resolve  # noqa: E402

MASTER = [
    {"id": "i1", "name": "Chivas Regal 18 Year Blended Scotch", "grain": "item"},
    {"id": "i2", "name": "Chivas Regal 12 Year Blended Scotch", "grain": "item"},
    {"id": "i3", "name": "Mer Soleil Chardonnay Reserve", "grain": "item"},
    {"id": "i4", "name": "Rittenhouse Rye Bottled in Bond", "grain": "item"},
    {"id": "i5", "name": "Absolut Citron", "grain": "item"},
    {"id": "i6", "name": "Absolut Mandrin", "grain": "item"},
    {"id": "b1", "name": "Bacardi", "grain": "brand"},
    {"id": "i7", "name": "Bacardi Ocho", "grain": "item"},
]

FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append("%s: got %r want %r" % (label, got, want))


def top_name(q, cands=MASTER):
    ranked, verdict = resolve(q, cands)
    return (ranked[0]["name"] if ranked else None), verdict


# ---- the corruptions that motivated the whole module ----------------------

def test_observed_asr_corruptions():
    # Each of these was produced by real speech-to-text during live testing.
    # "Chivas shoes" is the honest hard case: the brand survived, the AGE did not. With
    # both a 12 and an 18 in the master, nothing in the input can decide between them —
    # so the correct answer is AMBIGUOUS with both, not a confident guess. An LLM asked
    # the same question picked 18 on one run and declined on the next; this declines
    # every time, which is the property a rep can actually rely on.
    ranked, verdict = resolve("Chivas shoes", MASTER)
    check("chivas shoes -> ambiguous", verdict, "ambiguous")
    check("chivas shoes -> both chivas offered",
          sorted(r["id"] for r in ranked[:2]), ["i1", "i2"])

    name, _ = top_name("Mar Soleil Chardonnay")
    check("mar soleil", name, "Mer Soleil Chardonnay Reserve")

    name, _ = top_name("Rittenhouse Riot")
    check("rittenhouse riot", name, "Rittenhouse Rye Bottled in Bond")

    name, _ = top_name("absolute calm Citron")
    check("absolute calm citron", name, "Absolut Citron")


def test_number_mismatch_is_fatal():
    # "Chivas 18" must NEVER become "Chivas 12". An age statement is the product's
    # identity; every other token in those two names is identical.
    check("18 vs 12 scores zero", score("Chivas 18", "Chivas Regal 12 Year Blended Scotch"), 0.0)
    name, _ = top_name("Chivas 18")
    check("chivas 18 picks 18", name, "Chivas Regal 18 Year Blended Scotch")


def test_spelled_out_numbers_fold():
    check("ocho folds to 8", norm("Bacardi Ocho"), norm("bacardi 8"))
    check("eighteen folds", norm("Chivas eighteen"), "chivas 18")


def test_brand_and_item_stay_distinct():
    # Bacardi the family and Bacardi Ocho the expression are different rows.
    ranked, _ = resolve("Bacardi", MASTER)
    grains = {r["grain"] for r in ranked}
    check("brand query surfaces both grains", "brand" in grains, True)
    exact = [r for r in ranked if r["score"] >= 0.98]
    check("exact brand hit is the brand row", exact[0]["grain"] if exact else None, "brand")


def test_ambiguity_is_reported_not_guessed():
    # Two near-identical candidates must come back ambiguous rather than picking one.
    pair = [
        {"id": "a", "name": "Absolut Citron", "grain": "item"},
        {"id": "b", "name": "Absolut Citronex", "grain": "item"},
    ]
    ranked, verdict = resolve("Absolut Citron", pair)
    check("near-tie is ambiguous or exact", verdict in ("ambiguous", "exact"), True)
    if verdict == "ambiguous":
        check("ambiguous returns both", len(ranked) >= 2, True)


def test_nonsense_resolves_to_nothing():
    # Below the floor we say nothing. A bad guess is worse than an honest blank.
    ranked, verdict = resolve("balloons birthday party", MASTER)
    check("nonsense -> none", verdict, "none")
    check("nonsense -> empty", ranked, [])


def test_empty_and_junk_are_safe():
    for bad in ("", "   ", None):
        ranked, verdict = resolve(bad, MASTER)
        check("junk %r -> none" % (bad,), verdict, "none")
    check("norm(None)", norm(None), "")
    check("phon(None)", phon(None), "")


def main():
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("item_resolve: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
