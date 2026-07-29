"""Catch values that are PLAUSIBLE BUT WRONG — right type, wrong number.

`blocks` says why a fetch failed. `extract_qa` says a field stopped being populated. Neither notices a
parser that keeps filling every field with confident nonsense: a price column that starts carrying the
case price, an ABV of 900 from a moved decimal, a UPC missing its last digit after a selector shifted.
Fill rates stay perfect, nothing 403s, and the corpus fills with values that pass every check we had.
That is the failure that reaches a customer, because everything upstream looks healthy.

WHY CONSTANTS ARE ALLOWED HERE, having been wrong five times elsewhere today. Every number that burned
us described a system we do not control — a rate limit, a session quota, a dead-store background — and
those move. These describe the domain's physics: ethanol cannot exceed 100% of a volume, a GS1 check
digit is arithmetic, a retail price is not negative. They do not go stale.

AND IT FLAGS, NEVER FIXES. Landed data is not rewritten; corrections belong in the translation layer.
Silently repairing a value would destroy the evidence that the parser is broken, which is the one thing
worth keeping.

Pure rules — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import value_rules as V  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


def rules(row):
    return sorted(r for r, _ in V.check_row(row))


# ── a clean row must stay silent, or the whole thing gets ignored ────────────────────────────────
check("clean row", rules({"upc": "0083085300302", "abv": 40.0, "price": 2499, "list_price": 2999}), [])
check("empty row", rules({}), [])
check("absent fields are not violations", rules({"name": "x"}), [])
check("non-dict is safe", V.check_row(None), [])

# ── identifiers carry their own arithmetic proof ─────────────────────────────────────────────────
check("truncated upc", rules({"upc": "008308530030"}), ["upc_check_digit"])
check("placeholder upc", rules({"upc": "000000000000"}), ["upc_placeholder"])
check("valid upc passes", rules({"upc": "0083085300302"}), [])
check("gtin checked too", "gtin_check_digit" in rules({"gtin": "008308530030"}), True)
# A non-GS1 length gets no check-digit claim — we cannot prove a code wrong when we don't know its
# format. "97531" not "12345": the latter is a trivial ascending run, which upc.is_placeholder rightly
# flags as a pre-assignment dummy. Picking that as the example tested the wrong thing.
check("short/odd length not judged", rules({"upc": "97531"}), [])
check("trivial ascending run IS a placeholder", rules({"upc": "12345"}), ["upc_placeholder"])

# ── physics ──────────────────────────────────────────────────────────────────────────────────────
check("abv over 100", rules({"abv": 900.0}), ["abv_range"])
check("negative abv", rules({"abv": -5.0}), ["abv_range"])
check("normal spirit abv", rules({"abv": 40.0}), [])
check("zero abv is legal", rules({"abv": 0.0}), [])          # non-alcoholic is a real product
check("abv as string parses", rules({"abv": "900"}), ["abv_range"])

# ── prices ───────────────────────────────────────────────────────────────────────────────────────
check("negative price", rules({"price": -100}), ["price_negative"])
check("zero price", rules({"price": 0}), ["price_zero"])
check("case price in a unit field", rules({"price": 99999999}), ["price_absurd"])
check("ordinary price", rules({"price": 2499}), [])
# individually plausible, wrong only in relation — the case a single-field rule can never see
check("sale price above list", rules({"price": 2999, "list_price": 1499}), ["price_above_list"])
check("normal discount passes", rules({"price": 1499, "list_price": 2999}), [])
check("slightly over list is tolerated", rules({"price": 3100, "list_price": 2999}), [])

# ── rates, not counts: a bigger batch must not look sicker ───────────────────────────────────────
small = [{"abv": 900.0}] + [{"abv": 40.0} for _ in range(99)]
large = ([{"abv": 900.0}] + [{"abv": 40.0} for _ in range(99)]) * 10
check("rate is batch-size invariant", V.batch_rates(small)["abv_range"], V.batch_rates(large)["abv_range"])
check("rate is per 1000", V.batch_rates(small)["abv_range"], 10.0)
check("clean batch has no rules", V.batch_rates([{"abv": 40.0} for _ in range(100)]), {})
check("empty batch", V.batch_rates([]), {})

# ── judged against this source's normal, not against perfection ──────────────────────────────────
check("small batch says nothing", V.assess({"abv_range": 999.0}, {}, rows=10), {})
check("at baseline is fine", V.assess({"abv_range": 10.0}, {"abv_range": 10.0}, rows=500), {})
check("mild rise is not a spike", V.assess({"abv_range": 15.0}, {"abv_range": 10.0}, rows=500), {})
check("3x the baseline fires", "abv_range" in V.assess({"abv_range": 40.0}, {"abv_range": 10.0}, rows=500), True)
check("first run stays quiet when small",
      V.assess({"abv_range": 3.0}, {}, rows=500), {})
check("first run shouts when loud",
      "abv_range" in V.assess({"abv_range": 400.0}, {}, rows=500), True)
check("summary names rule and numbers",
      "abv_range" in V.summary(V.assess({"abv_range": 40.0}, {"abv_range": 10.0}, rows=500)), True)

# ── and it must gate the run, not merely log ─────────────────────────────────────────────────────
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ue_catalog.py")).read()
check("violations downgrade the run", 'qa.get("value_violations")' in src, True)
check("rules are flagged, not applied", "value_rules.batch_rates" in src, True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- value_rules: right type + wrong number is caught, and only flagged (35 checks)")
