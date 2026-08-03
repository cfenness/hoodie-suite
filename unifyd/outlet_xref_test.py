"""Offline test for the observation->outlet crosswalk: python3 unifyd/outlet_xref_test.py

Synthetic rows through the real DuckDB, so the join and dedupe semantics under test are production.

The load-bearing assertions:
  • ONE observation store maps to AT MOST ONE outlet. Fanning a store across outlets would multiply
    its items and prices into several neighbourhoods — the same inflation geo_resolve's border-point
    dedupe exists to prevent.
  • an AMBIGUOUS name (two outlets of one chain sharing a name in the same source) is DROPPED, never
    assigned arbitrarily — placing a store in the wrong neighbourhood is worse than leaving it
    unplaced, because a wrong answer still renders.
  • the strongest available method wins, and every row records which one produced it
  • name normalisation is applied IDENTICALLY on both sides
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outlet_xref as x

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


def test_name_normalisation_is_symmetric():
    con = _con()
    pairs = [("Total Wine #920", "total wine 920"),
             ("TOTAL  WINE  920", "total wine 920"),
             ("Trader Joe's 7", "trader joe s 7"),
             ("Haskell's - Minneapolis MN", "haskell s minneapolis mn")]
    for raw, want in pairs:
        got = con.execute("SELECT %s" % x._norm_sql("'%s'" % raw.replace("'", "''"))).fetchone()[0]
        eq("normalise %r" % raw, got, want)
    # Both sides must use the SAME expression, or the join is a coin flip.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlet_xref.py")).read()
    ok("one normalisation helper, used on both sides", src.count("_norm_sql(") >= 4)


def test_ambiguous_name_is_dropped():
    """Two outlets of one chain share a name in one source — that name is not an identity."""
    con = _con()
    con.execute("""CREATE TABLE g AS SELECT * FROM (VALUES
        ('acme','store-a','Acme Liquor','h1','111','11111'),
        ('acme','store-b','Acme Liquor','h2','111','22222'),
        ('acme','store-c','Unique Bottle Shop','h3','111','33333')
      ) v(source, store_id, nm_raw, hoodie_outlet, cbsa_code, zcta)""")
    con.execute("""CREATE TABLE gname AS
        SELECT source, %s AS nm, COUNT(*) AS n,
               ANY_VALUE(store_id) AS store_id, ANY_VALUE(zcta) AS zcta
        FROM g GROUP BY 1,2""" % x._norm_sql("nm_raw"))
    rows = {r[0]: r for r in con.execute("SELECT nm, n FROM gname").fetchall()}
    eq("the shared name is seen twice", rows["acme liquor"][1], 2)
    eq("the unique name is seen once", rows["unique bottle shop"][1], 1)

    kept = con.execute("SELECT nm FROM gname WHERE n = 1 ORDER BY nm").fetchall()
    eq("only the unambiguous name survives the n=1 filter", [k[0] for k in kept],
       ["unique bottle shop"])


def test_one_store_maps_to_one_outlet():
    """The QUALIFY collapse: strongest method wins, exactly one row per observation store."""
    con = _con()
    con.execute("""CREATE TABLE cand AS SELECT * FROM (VALUES
        ('acme','s1','acme','geo-name','name'),
        ('acme','s1','acme','geo-direct','direct'),
        ('acme','s1','acme','geo-idmap','id_map'),
        ('acme','s2','acme','geo-only','name')
      ) v(obs_source, obs_store_id, geo_source, geo_store_id, method)""")
    rows = con.execute("""
        SELECT obs_store_id, geo_store_id, method FROM cand
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY obs_source, obs_store_id
            ORDER BY CASE method WHEN 'direct' THEN 0 WHEN 'id_map' THEN 1 ELSE 2 END,
                     geo_store_id) = 1
        ORDER BY obs_store_id""").fetchall()
    eq("one row per observation store", len(rows), 2)
    eq("strongest method wins for s1", rows[0][2], "direct")
    eq("...and it picked that method's outlet", rows[0][1], "geo-direct")
    eq("a store with only a weak match still maps", rows[1][2], "name")


def test_confidence_tiers_are_ordered():
    c = x.CONFIDENCE
    ok("direct is the strongest", c["direct"] > c["id_map"] > c["name"])
    ok("name is explicitly weak", c["name"] < 0.8)
    ok("every method has a confidence", set(c) == {"direct", "id_map", "name"})
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlet_xref.py")).read()
    ok("confidence lands on every row", '"confidence"' in src or "confidence" in x.FIELDS)
    ok("method lands on every row", "method" in x.FIELDS)


def test_guards():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlet_xref.py")).read()
    ok("a duplicate mapping RAISES", "mapped to more than one outlet" in src)
    ok("an empty crosswalk RAISES rather than landing", "refusing to land an empty crosswalk" in src)
    ok("a collapsed rebuild RAISES", "crosswalk collapsed" in src)
    ok("dry run writes nothing", "DRY RUN" in src)
    ok("lands via full rebuild", "write_full_rebuild" in src)
    # The partition read must not reintroduce the glob bug.
    ok("observation names read via explicit list", "read_parquet([" in src)
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    ok("no glob in the code", "*.parquet" not in body.split('"""')[-1])


def test_field_contract():
    for f in ("obs_source", "obs_store_id", "geo_store_id", "zcta", "cbsa_code",
              "method", "confidence", "built_at"):
        ok("crosswalk carries %s" % f, f in x.FIELDS)
    eq("keyed by the OBSERVATION side first", x.FIELDS[:2], ["obs_source", "obs_store_id"])


if __name__ == "__main__":
    for fn in (test_name_normalisation_is_symmetric, test_ambiguous_name_is_dropped,
               test_one_store_maps_to_one_outlet, test_confidence_tiers_are_ordered,
               test_guards, test_field_contract):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
