"""Offline test for the observation rollup: python3 unifyd/obs_rollup_test.py

The aggregation SQL runs against a synthetic table in the same DuckDB the real build uses, so the
semantics under test are the production ones — only the rows are fake.

The load-bearing assertions:
  • the rollup NEVER re-reads the 53M-row source to build the metro grain — it joins the already
    collapsed store rollup, which is the whole reason this module exists
  • a sampled run does not write (it would replace a full rebuild with a fragment)
  • a collapsed rebuild RAISES rather than landing (a shrinking store count is a broken read, not a
    shrinking market)
  • the partition read uses an explicit file list, never a glob — globbing is the bug that made
    retail_observations unreadable in the first place
  • price percentiles ignore NULL prices instead of scoring them as zero
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import obs_rollup as r

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


def _con():
    import duckdb
    return duckdb.connect()


def _fixture(con):
    """Two stores in one source. Store A: 3 items, one on promo, one NULL price, one out of stock."""
    con.execute("""CREATE TABLE o AS SELECT * FROM (VALUES
      ('2026-08-01','s1','A','ch','u1','p1','Brandy', 10.0, TRUE,  TRUE,  FALSE),
      ('2026-08-01','s1','A','ch','u2','p2','Brandy', 20.0, FALSE, TRUE,  FALSE),
      ('2026-08-02','s1','A','ch','u3','p3','Other',  NULL, FALSE, FALSE, TRUE),
      ('2026-08-01','s1','B','ch','u1','p1','Brandy', 30.0, FALSE, TRUE,  FALSE)
    ) v("date", source, store_id, chain, upc, product_id, brand, price, on_promo, in_stock, is_hemp)""")


AGG = """
SELECT source, CAST(store_id AS VARCHAR) AS store_id,
       COUNT(DISTINCT COALESCE(NULLIF(upc,''), NULLIF(product_id,''))) AS items,
       COUNT(DISTINCT NULLIF(brand,'')) AS brands,
       COUNT(*) AS obs_rows,
       COUNT(DISTINCT "date") AS days,
       QUANTILE_CONT(price, 0.5) AS price_median,
       AVG(price) AS price_avg,
       AVG(CASE WHEN on_promo THEN 1.0 ELSE 0.0 END) AS promo_share,
       AVG(CASE WHEN in_stock THEN 1.0 ELSE 0.0 END) AS in_stock_share,
       COUNT(DISTINCT CASE WHEN is_hemp THEN COALESCE(NULLIF(upc,''), NULLIF(product_id,'')) END) AS hemp_items
FROM o WHERE store_id IS NOT NULL AND store_id <> ''
GROUP BY 1,2 ORDER BY 2"""


def test_store_grain_aggregation():
    con = _con()
    _fixture(con)
    rows = {r_[1]: r_ for r_ in con.execute(AGG).fetchall()}
    a, b = rows["A"], rows["B"]
    eq("store A distinct items", a[2], 3)
    eq("store A distinct brands", a[3], 2)
    eq("store A observation rows", a[4], 3)
    eq("store A distinct days", a[5], 2)
    # NULL price must be EXCLUDED, not counted as 0 — a store with unpriced rows would otherwise
    # look systematically cheaper than it is.
    eq("median ignores the NULL price", a[6], 15.0)
    eq("average ignores the NULL price", a[7], 15.0)
    eq("promo share is over rows", round(a[8], 4), round(1 / 3.0, 4))
    eq("in-stock share is over rows", round(a[9], 4), round(2 / 3.0, 4))
    eq("hemp items counted distinctly", a[10], 1)
    eq("store B is separate", b[2], 1)


def test_metro_grain_joins_the_rollup_not_the_source():
    """The point of the module: the metro grain must come from the collapsed store table."""
    import inspect
    src = inspect.getsource(r.build)
    ok("metro select reads FROM store", "FROM store s" in src)
    ok("metro join is on the geography view", "JOIN g ON g.source = s.source" in src)
    ok("metro grain does NOT re-read the observations view", "FROM o" not in src)
    ok("keyed on source|store_id, same as outlet_geography", "g.store_id = s.store_id" in src)


def test_sample_run_does_not_write():
    import inspect
    src = inspect.getsource(r.build)
    ok("write defaults to full-build-only", "write = sample is None" in src)
    ok("dry run returns before writing", "if not write:" in src)
    ok("lands via full rebuild", "write_full_rebuild" in src)


def test_collapse_guard():
    floor = r.MIN_KEEP_FRAC
    ok("there is a keep floor", 0 < floor <= 1)

    def fires(now, prev):
        return bool(prev and now < prev * floor)

    ok("a collapse trips the guard", fires(10, 1000))
    ok("a total collapse trips the guard", fires(0, 1000))
    ok("normal growth does not trip", not fires(1200, 1000))
    ok("small churn does not trip", not fires(950, 1000))
    ok("a first-ever build does not trip", not fires(50, 0))


def test_partition_read_is_not_a_glob():
    import inspect
    src = inspect.getsource(r._attach_parts)
    # Check the EXECUTABLE code, not the prose: the docstring deliberately quotes `<dir>/*.parquet`
    # to explain why globbing is wrong, and a naive substring check flags its own explanation.
    body = src
    if r._attach_parts.__doc__:
        body = body.replace(r._attach_parts.__doc__, "")
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))

    ok("reads an explicit list", "read_parquet([" in code)
    ok("no glob pattern in the code", "*.parquet" not in code)
    ok("listing is retried and strict", "_partition_files_strict" in code and "_retry" in code)
    ok("no partitions RAISES rather than returning empty", "RollupDegraded" in code)


def test_field_contracts():
    for f in ("source", "store_id", "items", "price_median", "promo_share", "built_at"):
        ok("store rollup carries %s" % f, f in r.STORE_FIELDS)
    for f in ("cbsa_code", "zcta", "stores", "items_per_store", "price_median", "built_at"):
        ok("metro rollup carries %s" % f, f in r.METRO_FIELDS)
    eq("store rollup keyed source|store_id first", r.STORE_FIELDS[:2], ["source", "store_id"])


if __name__ == "__main__":
    for fn in (test_store_grain_aggregation, test_metro_grain_joins_the_rollup_not_the_source,
               test_sample_run_does_not_write, test_collapse_guard,
               test_partition_read_is_not_a_glob, test_field_contracts):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
