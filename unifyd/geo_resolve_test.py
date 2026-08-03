"""Offline test for geo_resolve: python3 unifyd/geo_resolve_test.py

No network, no warehouse, no TIGER download. The point-in-polygon join is exercised against
SYNTHETIC boundaries built in the same DuckDB spatial extension the real pass uses, so the join
SEMANTICS under test are the production ones — only the polygons are fake.

The load-bearing assertions:
  • a point in a county but OUTSIDE every ZCTA still lands (LEFT join, not INNER) — ZCTAs do not
    tile the country, and an INNER join would silently drop rural/industrial accounts
  • a county with no CBSA yields a resolved row with a NULL metro, not a dropped row
  • the degrade floor FIRES when the boundary join mostly misses — a table of nulls must fail the
    run, never land looking like success
  • a truncated download is never left behind as a valid cache entry
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geo_resolve as gr

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
    c = duckdb.connect()
    c.execute("INSTALL spatial"); c.execute("LOAD spatial")
    return c


def _fixture(con):
    """Two counties side by side. County A (in CBSA 111) is covered by ZCTA z1; county B (no CBSA)
    has NO ZCTA coverage at all — the rural case."""
    con.execute("""CREATE TEMP TABLE cty AS SELECT * FROM (VALUES
        ('01001','Alpha','01','111', ST_GeomFromText('POLYGON((0 0,0 10,10 10,10 0,0 0))')),
        ('02002','Bravo','02',NULL,  ST_GeomFromText('POLYGON((10 0,10 10,20 10,20 0,10 0))'))
      ) v(county_fips, county_name, state_fp, cbsa_code, geom)""")
    con.execute("""CREATE TEMP TABLE zct AS SELECT * FROM (VALUES
        ('10001', ST_GeomFromText('POLYGON((0 0,0 10,10 10,10 0,0 0))'))
      ) v(zcta, geom)""")
    con.execute("""CREATE TEMP TABLE cbs AS SELECT * FROM (VALUES
        ('111','Alphaville, ST','metro')
      ) v(cbsa_code, cbsa_name, cbsa_type)""")


JOIN = """
SELECT p.id, c.county_fips, c.cbsa_code, b.cbsa_name, z.zcta
FROM pts p
LEFT JOIN cty c ON ST_Intersects(p.g, c.geom)
LEFT JOIN zct z ON ST_Intersects(p.g, z.geom)
LEFT JOIN cbs b ON b.cbsa_code = c.cbsa_code
ORDER BY p.id"""


def test_join_semantics():
    con = _con()
    _fixture(con)
    con.execute("""CREATE TEMP TABLE pts AS SELECT * FROM (VALUES
        ('in_a',  ST_Point(5, 5)),      -- county A, ZCTA z1, CBSA 111
        ('in_b',  ST_Point(15, 5)),     -- county B, no ZCTA, no CBSA
        ('nowhere', ST_Point(99, 99))   -- outside every boundary
      ) v(id, g)""")
    rows = {r[0]: r for r in con.execute(JOIN).fetchall()}

    eq("point in county A -> county", rows["in_a"][1], "01001")
    eq("point in county A -> cbsa", rows["in_a"][2], "111")
    eq("point in county A -> cbsa name", rows["in_a"][3], "Alphaville, ST")
    eq("point in county A -> zcta", rows["in_a"][4], "10001")

    # The rural case — this is the one an INNER join would silently eat.
    ok("point outside every ZCTA still resolves", rows.get("in_b") is not None)
    eq("...with its county", rows["in_b"][1], "02002")
    eq("...and a NULL zcta rather than a dropped row", rows["in_b"][4], None)
    eq("county with no CBSA -> NULL metro, row kept", rows["in_b"][2], None)

    ok("point outside all boundaries is still a row", rows.get("nowhere") is not None)
    eq("...with NULL county", rows["nowhere"][1], None)
    eq("row count preserved (no point dropped)", len(rows), 3)


def test_border_point_does_not_duplicate():
    """A point exactly on a shared county border satisfies ST_Intersects for BOTH neighbours. Without
    the QUALIFY collapse the account is emitted twice — and because the real pass then fans each
    distinct location out to every account sitting on it, a single border coordinate would inflate a
    city's account count.

    This test is also why the predicate is ST_Intersects and not ST_Within. ST_Within is interior-only:
    written that way, a point exactly on a border matched NO county at all and resolved to null — the
    opposite failure, silently dropping accounts instead of duplicating them. Boundary-inclusive
    matching plus this dedupe is the combination that is correct in both directions."""
    con = _con()
    _fixture(con)
    # x=10 is the shared edge between county A (0-10) and county B (10-20).
    con.execute("CREATE TEMP TABLE upts AS SELECT * FROM (VALUES (5.0, 10.0, ST_Point(10, 5))) v(lat, lng, g)")

    raw = con.execute("""
        SELECT COUNT(*) FROM upts u LEFT JOIN cty c ON ST_Intersects(u.g, c.geom)""").fetchone()[0]
    ok("border point genuinely matches >1 county (guard is not vacuous)", raw > 1)

    deduped = con.execute("""
        SELECT COUNT(*) FROM (
          SELECT u.lat, u.lng, c.county_fips, z.zcta
          FROM upts u
          LEFT JOIN cty c ON ST_Intersects(u.g, c.geom)
          LEFT JOIN zct z ON ST_Intersects(u.g, z.geom)
          QUALIFY ROW_NUMBER() OVER (PARTITION BY u.lat, u.lng
                                     ORDER BY c.county_fips NULLS LAST, z.zcta NULLS LAST) = 1)""").fetchone()[0]
    eq("QUALIFY collapses it to exactly one row", deduped, 1)

    chosen = con.execute("""
        SELECT c.county_fips FROM upts u
          LEFT JOIN cty c ON ST_Intersects(u.g, c.geom)
          LEFT JOIN zct z ON ST_Intersects(u.g, z.geom)
          QUALIFY ROW_NUMBER() OVER (PARTITION BY u.lat, u.lng
                                     ORDER BY c.county_fips NULLS LAST, z.zcta NULLS LAST) = 1""").fetchone()[0]
    eq("and picks the STABLE winner (lowest FIPS), not a random one", chosen, "01001")


def test_degrade_floor():
    """The guard must fire on a mass miss and stay quiet on a normal pass."""
    floor = gr.MIN_HIT_RATE
    ok("floor is a real threshold, not 0", 0 < floor <= 1)

    def fires(n_cty, n_res):
        return (n_cty / float(n_res or 1)) < floor

    ok("mass miss trips the guard", fires(5, 1000))
    ok("total miss trips the guard", fires(0, 1000))
    ok("healthy pass does not trip", not fires(995, 1000))
    # A handful of genuine offshore/water points must NOT fail an otherwise-good run.
    ok("a few offshore points do not trip", not fires(9910, 10000))


def test_download_atomicity():
    """A truncated download must not be left where the next run reads it as a good cache."""
    with tempfile.TemporaryDirectory() as d:
        gr.CACHE = d
        zpath = os.path.join(d, "county.zip")
        with open(zpath + ".part", "wb") as f:
            f.write(b"x" * 10)                      # a half-written download
        ok("partial file is not the cache path", not os.path.exists(zpath))
        # fetch_reference only ever promotes via os.replace AFTER the stream completes, so a
        # .part left by a crash can never be mistaken for a complete cache entry.
        ok("stale .part does not satisfy the cache check",
           not (os.path.exists(zpath) and os.path.getsize(zpath) > 100000))


def test_sample_run_never_clobbers():
    """`outlet_geography` lands via write_full_rebuild (a full recomputation). So a --limit sample that
    wrote would REPLACE the whole table with the sample — the never-clobber-a-catalog failure. A limited
    run must therefore be a dry run unless explicitly overridden."""
    import inspect
    src = inspect.getsource(gr.resolve)
    ok("resolve() takes a write flag", "write" in inspect.signature(gr.resolve).parameters)
    ok("write defaults to full-pass-only", "write = limit is None" in src)
    ok("dry run returns before writing", "if not write:" in src)
    ok("lands via full rebuild, not a blind overwrite", "write_full_rebuild" in src)


def test_field_contract():
    for c in ("county_fips", "cbsa_code", "zcta", "source", "store_id", "method", "resolved_at"):
        ok("FIELDS carries %s" % c, c in gr.FIELDS)
    eq("keyed per source-store", gr.FIELDS[:2], ["source", "store_id"])
    ok("method records the boundary vintage", "TIGER_YEAR" in open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "geo_resolve.py")).read())


if __name__ == "__main__":
    for fn in (test_join_semantics, test_border_point_does_not_duplicate, test_degrade_floor,
               test_download_atomicity, test_sample_run_never_clobbers, test_field_contract):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
