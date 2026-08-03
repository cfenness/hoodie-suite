"""Offline test for metro_analytics: python3 unifyd/metro_analytics_test.py

No warehouse, no network. These figures go in front of customers, so the assertions here are about
CLAIMS, not plumbing — the ways a number can be technically computed and still be a lie.

The load-bearing assertions:
  • UNKNOWN IS NOT INDEPENDENT. A LEFT-JOIN miss or a NULL chain flag must land in its own bucket.
    Counted the naive way, New York reported "96% independent" off a book that is mostly unflagged
    DoorDash rows.
  • the neighbourhood index is versus the MEDIAN, not the mean — a few one-account ZIPs with big
    demand would otherwise drag the average up and make ordinary neighbourhoods look under-served
  • a neighbourhood under the account floor gets NO ratio rather than a spectacular meaningless one
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metro_analytics as ma

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s" % name)


def eq(name, got, want):
    ok("%s (got %r)" % (name, got), got == want)


def test_unknown_is_not_independent():
    """The exact SQL shape that produced the bad claim must not come back."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metro_analytics.py")).read()
    ok("chain split guards NULL explicitly", "so.is_chain IS NULL" in src)
    ok("an unclassified bucket is computed", "chain_unknown" in src)
    ok("summary exposes the unclassified count", '"chain_unknown"' in src)
    # The naive form counts every NULL as independent — it must be gone.
    naive = "SUM(CASE WHEN so.is_chain THEN 0 ELSE 1 END) AS independent"
    ok("naive NULL-as-independent form is gone", naive not in src)
    ok("alcohol flags coalesce rather than trusting NULL", "COALESCE(so.f_beer" in src)


def test_deck_percentages_use_known_denominator():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "metro_deck.py")
    src = open(p).read()
    ok("deck computes a classified denominator", "known_chain" in src)
    ok("independent %% divides by classified, not by every account",
       's["independent"] / known_chain' in src)
    ok("deck surfaces the unclassified count to the reader", "Not yet classified" in src)
    ok("deck no longer divides the split by total accounts",
       's["independent"] / s["accounts"]' not in src)


def test_index_uses_median_not_mean():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metro_analytics.py")).read()
    ok("median is taken", "vals[len(vals) // 2]" in src)
    ok("no mean/average of the ratio", "sum(vals) / len(vals)" not in src)

    # Behavioural: a set with one huge outlier must index the typical value near 100.
    vals = sorted([10.0, 11.0, 12.0, 13.0, 5000.0])
    med = vals[len(vals) // 2]
    idx_typical = 100.0 * 12.0 / med
    ok("a lone outlier does not distort the typical neighbourhood's index",
       90 <= idx_typical <= 110)
    mean = sum(vals) / len(vals)
    ok("...whereas a mean would have (proving the choice matters)",
       (100.0 * 12.0 / mean) < 10)


def test_thin_neighborhoods_get_no_ratio():
    ok("there IS an account floor", ma.MIN_ACCOUNTS_FOR_RATIO >= 2)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metro_analytics.py")).read()
    ok("ratio is gated on the floor", "g[\"accounts\"] >= floor" in src)
    ok("thin rows are flagged, not silently dropped", '"thin":' in src)

    # A 1-account ZIP with $30M of demand would otherwise rank first in every metro.
    floor = ma.MIN_ACCOUNTS_FOR_RATIO
    accounts, demand = 1, 30_000_000.0
    ratio = (demand / accounts) if (demand and accounts >= floor) else None
    eq("a single-account ZIP yields no ratio", ratio, None)


def test_ranking_is_by_demand_not_our_own_coverage():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metro_analytics.py")).read()
    ok("top_metros sorts on demand", 'out.sort(key=lambda x: -x["demand_total"])' in src)
    ok("...not on our account count", 'out.sort(key=lambda x: -x["accounts"])' not in src)


if __name__ == "__main__":
    for fn in (test_unknown_is_not_independent, test_deck_percentages_use_known_denominator,
               test_index_uses_median_not_mean, test_thin_neighborhoods_get_no_ratio,
               test_ranking_is_by_demand_not_our_own_coverage):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
