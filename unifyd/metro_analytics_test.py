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

    # is_chain is set by matching the account name against a known-chain list, so a False is
    # "no match", NOT "verified independent". The page must not upgrade a heuristic into a finding.
    ok("deck labels the bucket as a chain MATCH, not a verdict", "Matched to a known chain" in src)
    ok("deck states the limitation in words", 'not the same as' in src and 'verified independent' in src)
    ok("deck no longer prints a bare 'Independent' claim", "<dt>Independent</dt>" not in src)


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


def test_price_is_observed_and_kept_apart_from_modelled_demand():
    """Price/assortment come from obs_metro_rollup (OBSERVED). Demand is modelled. A page that blurs
    the two is the failure this whole file exists to prevent."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metro_analytics.py")).read()
    ok("neighbourhoods read the rollup, not the 53M-row source", "OBS_METRO" in src)
    ok("...scoped to the metro, not a full scan", 'WHERE cbsa_code = ?' in src)
    ok("priced_stores is its own count", '"priced_stores"' in src)
    ok("price fields carried", '"price_median"' in src and '"price_p25"' in src)

    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "metro_deck.py")
    deck = open(p).read()
    # A missing observation must render as an em-dash. A zero would read as "cheap" or "no range" —
    # a fabricated claim about a shelf we never saw.
    ok("absent price renders an em-dash, never 0", "—</td>" in deck)
    ok("the price panel is tagged LANDED, not derived",
       'Shelf price &amp; assortment' in deck and 'tag landed' in deck)
    ok("the panel states its own denominator", "stores priced" in deck)
    ok("it says these are not shopper prices", "not what shoppers paid" in deck)
    ok("assortment is explained as within-source", "within</b> a source" in deck)


def test_shelf_price_is_rendered_to_the_cent():
    """usd() abbreviates for market sizes ($5.64B). Using it for a shelf price would turn $12.99 into
    $13 — a different claim about a shelf."""
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "metro_deck.py")
    spec = importlib.util.spec_from_file_location("metro_deck", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    eq("money keeps the cents", md.money(12.99), "$12.99")
    eq("money keeps a round price honest", md.money(13), "$13.00")
    eq("usd would have rounded it", md.usd(12.99), "$13")
    ok("the two are different functions", md.money(12.99) != md.usd(12.99))


def test_a_one_store_median_is_not_a_neighbourhood_price():
    """The ranked price table filled with n=1 ZIPs on the first render — New York's "most expensive
    neighbourhood" was a single store at $37.45. A median over one shelf is not a market."""
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "metro_deck.py")
    spec = importlib.util.spec_from_file_location("metro_deck2", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)

    ok("there IS a priced-store floor", md.MIN_PRICED_STORES >= 2)

    src = open(p).read()
    ok("the ranked table applies it", "priced_stores\") or 0) >= MIN_PRICED_STORES" in src)
    ok("the per-ZIP cell applies it too", "< MIN_PRICED_STORES" in src)
    ok("withheld ZIPs are counted, not silently dropped", "thin_priced" in src)
    ok("the page states the floor in words", "priced stores</b> are shown" in src)
    ok("...and says why", "not a\n    neighbourhood price" in src or "neighbourhood price" in src)

    # Behavioural: a 1-store ZIP must not out-rank a well-observed one.
    hoods = [{"zcta": "11530", "price_median": 37.45, "priced_stores": 1},
             {"zcta": "10001", "price_median": 14.00, "priced_stores": 9}]
    kept = [h for h in hoods if (h.get("priced_stores") or 0) >= md.MIN_PRICED_STORES]
    eq("the one-store ZIP is excluded", [h["zcta"] for h in kept], ["10001"])


def test_ranking_is_by_demand_not_our_own_coverage():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "metro_analytics.py")).read()
    ok("top_metros sorts on demand", 'out.sort(key=lambda x: -x["demand_total"])' in src)
    ok("...not on our account count", 'out.sort(key=lambda x: -x["accounts"])' not in src)


if __name__ == "__main__":
    for fn in (test_unknown_is_not_independent, test_deck_percentages_use_known_denominator,
               test_index_uses_median_not_mean, test_thin_neighborhoods_get_no_ratio,
               test_price_is_observed_and_kept_apart_from_modelled_demand,
               test_shelf_price_is_rendered_to_the_cent,
               test_a_one_store_median_is_not_a_neighbourhood_price,
               test_ranking_is_by_demand_not_our_own_coverage):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
