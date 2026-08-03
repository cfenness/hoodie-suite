#!/usr/bin/env python3
"""geo_resolve.py — put every ALREADY-GEOCODED account on the map: lat/lng -> county + CBSA + ZCTA.

THE GAP THIS CLOSES (measured 2026-08-03, live warehouse):

    src_outlets                1,768,869 rows
      ...with lat/lng            888,476 (50%)
      ...with a county_fips       66,576 (3.8%)  <- and 66,185 of those are ONE source (ca-abc)

    Inside the top-20 metro bounding boxes:  284,618 geocoded accounts
      ...carrying a county:                   33,152 (11.6%)

So a New York metro query answered "106 outlets" when the warehouse actually held 39,167 geocoded
accounts there. The accounts were never missing — they were UNADDRESSABLE, because nothing in the
pipeline ever turned a lat/lng into a geography. `geocode.py` derives county_fips, but only as a
by-product of geocoding a STREET ADDRESS for a row that has no lat/lng; a row that arrives already
geocoded (DoorDash: 477k, ab-inbev: 278k) is skipped by every pass and stays countyless forever.

This is that missing pass, and it is the cheap kind: no network per row, no API, no proxy — one
download of the free Census TIGER boundary files, then an offline point-in-polygon.

WHY A SEPARATE TABLE, NOT A COLUMN ON src_outlets. `fast-geo`, `geocode` and `aggregator-geo` each
read → merge → REWRITE the whole src_outlets table, which is why geo_all.py has to run them strictly
back-to-back (concurrent writers clobber each other; see its docstring). Adding a fourth whole-table
rewriter would add a fourth way to lose the other three's work. `outlet_geography` is a derived,
independently-keyed table instead: it never touches src_outlets, so it cannot clobber and can be
rebuilt at will. Landed data is not rewritten — the standing rule.

WHAT IT BUYS, beyond the county:
  - CBSAFP comes straight off the TIGER county file, so "which metro" is the CENSUS definition,
    not a hand-maintained county list that silently rots.
  - ZCTA gives NEIGHBOURHOOD grain, and `trade_area_demand` already holds ZCTA-level demand for
    33,791 ZIPs — so resolved accounts join directly to demand at neighbourhood level.

    python3 unifyd/geo_resolve.py --limit 5000     # sample
    python3 unifyd/geo_resolve.py                  # full pass
"""
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TABLE = "outlet_geography"
CBSA_REF = "geo_cbsa_ref"

FIELDS = ["source", "store_id", "hoodie_outlet", "lat", "lng",
          "county_fips", "county_name", "state_fp", "cbsa_code", "cbsa_name", "cbsa_type",
          "zcta", "method", "resolved_at"]

# Free Census TIGER boundary files. Vintage is pinned: a boundary set that changes under us would
# silently re-assign accounts between metros between runs, which reads as churn in every trend.
TIGER_YEAR = os.environ.get("TIGER_YEAR", "2023")
TIGER = {
    "county": ("https://www2.census.gov/geo/tiger/TIGER%(y)s/COUNTY/tl_%(y)s_us_county.zip",
               "tl_%(y)s_us_county.shp"),
    "zcta":   ("https://www2.census.gov/geo/tiger/TIGER%(y)s/ZCTA520/tl_%(y)s_us_zcta520.zip",
               "tl_%(y)s_us_zcta520.shp"),
    "cbsa":   ("https://www2.census.gov/geo/tiger/TIGER%(y)s/CBSA/tl_%(y)s_us_cbsa.zip",
               "tl_%(y)s_us_cbsa.shp"),
}
CACHE = os.environ.get("TIGER_CACHE", "/tmp/tiger")

# A run that resolves far fewer points than it was handed means the boundary join broke (bad
# shapefile, wrong CRS, empty read) — NOT that America moved. Degrade loudly instead of landing
# a table full of nulls that looks like a successful pass.
MIN_HIT_RATE = float(os.environ.get("GEO_RESOLVE_MIN_HIT", "0.90"))


class ResolveDegraded(Exception):
    """The join ran but produced an implausible result — raised so a run FAILS instead of
    quietly landing nulls."""


def _paths(kind):
    url, shp = TIGER[kind]
    y = {"y": TIGER_YEAR}
    return url % y, os.path.join(CACHE, "%s.zip" % kind), shp % y


def fetch_reference(kind, log=print):
    """Download one TIGER zip to the local cache (idempotent). Returns the /vsizip/ path DuckDB reads."""
    url, zpath, shp = _paths(kind)
    os.makedirs(CACHE, exist_ok=True)
    if not (os.path.exists(zpath) and os.path.getsize(zpath) > 100000):
        t = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": "hoodie-suite/geo_resolve"})
        tmp = zpath + ".part"
        with urllib.request.urlopen(req, timeout=900) as r, open(tmp, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        os.replace(tmp, zpath)      # never leave a truncated download looking like a cached one
        log("[geo-resolve] %s %.0f MB in %.0fs" % (kind, os.path.getsize(zpath) / 1e6, time.time() - t))
    return "/vsizip/%s/%s" % (zpath, shp)


def _spatial(con):
    con.execute("INSTALL spatial")
    con.execute("LOAD spatial")


def build_cbsa_ref(log=print):
    """Land the small CBSA attribute crosswalk (code -> name/type) so the analytics layer can label a
    metro without re-reading a 500 MB shapefile. Geometry is NOT stored — only the join keys."""
    con = warehouse.connect()
    _spatial(con)
    src = fetch_reference("cbsa", log=log)
    rows = con.execute("""
        SELECT GEOID AS cbsa_code, NAME AS cbsa_name,
               CASE WHEN LSAD = 'M1' THEN 'metro' ELSE 'micro' END AS cbsa_type
        FROM ST_Read('%s')""" % src).fetchall()
    recs = [{"cbsa_code": a, "cbsa_name": b, "cbsa_type": c} for a, b, c in rows]
    warehouse.write_parquet(CBSA_REF, recs, fields=["cbsa_code", "cbsa_name", "cbsa_type"])
    log("[geo-resolve] %s <- %d CBSAs" % (CBSA_REF, len(recs)))
    return len(recs)


def resolve(limit=None, write=None, log=print):
    """Resolve every src_outlets row that HAS a lat/lng into county + CBSA + ZCTA, and land
    `outlet_geography`. Returns the number of rows written (0 on a dry run).

    `write` defaults to "only on a FULL pass". This table is a complete recomputation of
    src_outlets, so it lands via write_full_rebuild — which means a --limit sample writing to it
    would REPLACE the whole table with the sample. That is exactly the never-clobber-a-catalog
    failure, so a limited run is a dry run unless the caller explicitly opts in."""
    if write is None:
        write = limit is None
    con = warehouse.connect()
    _spatial(con)
    county = fetch_reference("county", log=log)
    zcta = fetch_reference("zcta", log=log)
    cbsa = fetch_reference("cbsa", log=log)

    if not warehouse.attach_view(con, "src_outlets", view="so"):
        raise ResolveDegraded("src_outlets resolved to no parquet parts")

    lim = " LIMIT %d" % int(limit) if limit else ""
    log("[geo-resolve] staging points...")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE pts AS
        SELECT source, CAST(store_id AS VARCHAR) AS store_id,
               CAST(hoodie_outlet AS VARCHAR) AS hoodie_outlet,
               CAST(lat AS DOUBLE) AS lat, CAST(lng AS DOUBLE) AS lng
        FROM so
        WHERE lat IS NOT NULL AND lng IS NOT NULL
          AND CAST(lat AS DOUBLE) BETWEEN -90 AND 90
          AND CAST(lng AS DOUBLE) BETWEEN -180 AND 180
          AND NOT (CAST(lat AS DOUBLE) = 0 AND CAST(lng AS DOUBLE) = 0)
        %s""" % lim)
    n_pts = con.execute("SELECT COUNT(*) FROM pts").fetchone()[0]
    if not n_pts:
        raise ResolveDegraded("no geocoded points in src_outlets — nothing to resolve")

    # RESOLVE DISTINCT LOCATIONS, NOT ROWS. The fast-geo layer places every outlet that has only a
    # city+state at its CITY CENTROID, so an entire city's accounts share one identical coordinate —
    # and point-in-polygon is the expensive step (33,791 ZCTA polygons). Deduping first makes the
    # geometry work proportional to distinct PLACES, then a cheap equi-join fans the answer back out
    # to every row. Measured: 20k rows took 213s undeduped.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE upts AS
        SELECT lat, lng, ST_Point(lng, lat) AS g FROM (SELECT DISTINCT lat, lng FROM pts)""")
    n_uniq = con.execute("SELECT COUNT(*) FROM upts").fetchone()[0]
    log("[geo-resolve] %d geocoded rows -> %d distinct locations (%.1fx dedupe)"
        % (n_pts, n_uniq, n_pts / float(n_uniq or 1)))

    # Boundaries staged as real tables so the join sees a materialised, indexable set rather than
    # re-reading the shapefile per probe.
    con.execute("""CREATE OR REPLACE TEMP TABLE cty AS
        SELECT GEOID AS county_fips, NAME AS county_name, STATEFP AS state_fp,
               NULLIF(CBSAFP,'') AS cbsa_code, geom FROM ST_Read('%s')""" % county)
    con.execute("""CREATE OR REPLACE TEMP TABLE zct AS
        SELECT ZCTA5CE20 AS zcta, geom FROM ST_Read('%s')""" % zcta)
    con.execute("""CREATE OR REPLACE TEMP TABLE cbs AS
        SELECT GEOID AS cbsa_code, NAME AS cbsa_name,
               CASE WHEN LSAD = 'M1' THEN 'metro' ELSE 'micro' END AS cbsa_type
        FROM ST_Read('%s')""" % cbsa)
    log("[geo-resolve] boundaries: %d counties, %d ZCTAs, %d CBSAs" % (
        con.execute("SELECT COUNT(*) FROM cty").fetchone()[0],
        con.execute("SELECT COUNT(*) FROM zct").fetchone()[0],
        con.execute("SELECT COUNT(*) FROM cbs").fetchone()[0]))

    # Two LEFT joins, not one: a point can miss ZCTA coverage (ZCTAs do not tile the whole country —
    # unpopulated land has none) while still landing squarely in a county. An INNER join would drop
    # those accounts entirely and quietly under-count rural/industrial accounts.
    log("[geo-resolve] point-in-polygon over %d distinct locations..." % n_uniq)
    t = time.time()
    # EXACTLY ONE ROW PER DISTINCT LOCATION. A point sitting precisely on a shared border satisfies
    # ST_Within for both neighbours, so the raw join can emit 2+ rows for one coordinate — which the
    # fan-out below would then multiply across every account at that coordinate, inflating a city's
    # account count out of nowhere. QUALIFY collapses it deterministically (lowest FIPS/ZCTA wins) so
    # the choice is arbitrary but STABLE across runs rather than arbitrary and different each time.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE uresolved AS
        SELECT u.lat, u.lng,
               c.county_fips, c.county_name, c.state_fp,
               c.cbsa_code, b.cbsa_name, b.cbsa_type,
               z.zcta
        FROM upts u
        LEFT JOIN cty c ON ST_Intersects(u.g, c.geom)
        LEFT JOIN zct z ON ST_Intersects(u.g, z.geom)
        LEFT JOIN cbs b ON b.cbsa_code = c.cbsa_code
        QUALIFY ROW_NUMBER() OVER (PARTITION BY u.lat, u.lng
                                   ORDER BY c.county_fips NULLS LAST, z.zcta NULLS LAST) = 1""")
    n_ures = con.execute("SELECT COUNT(*) FROM uresolved").fetchone()[0]
    if n_ures != n_uniq:
        raise ResolveDegraded("dedupe failed: %d distinct locations produced %d resolved rows"
                              % (n_uniq, n_ures))
    log("[geo-resolve] geometry done in %.0fs — fanning back out to rows" % (time.time() - t))
    con.execute("""
        CREATE OR REPLACE TEMP TABLE resolved AS
        SELECT p.source, p.store_id, p.hoodie_outlet, p.lat, p.lng,
               r.county_fips, r.county_name, r.state_fp,
               r.cbsa_code, r.cbsa_name, r.cbsa_type, r.zcta
        FROM pts p JOIN uresolved r ON p.lat = r.lat AND p.lng = r.lng""")
    n_res = con.execute("SELECT COUNT(*) FROM resolved").fetchone()[0]
    # The fan-out is an equi-join on a deduped key, so it must be row-preserving. If it is not, the
    # geography is being multiplied across accounts and every count downstream is wrong.
    if n_res != n_pts:
        raise ResolveDegraded("fan-out changed the row count: %d staged -> %d resolved" % (n_pts, n_res))
    n_cty = con.execute("SELECT COUNT(*) FROM resolved WHERE county_fips IS NOT NULL").fetchone()[0]
    n_zip = con.execute("SELECT COUNT(*) FROM resolved WHERE zcta IS NOT NULL").fetchone()[0]
    n_cbsa = con.execute("SELECT COUNT(*) FROM resolved WHERE cbsa_code IS NOT NULL").fetchone()[0]
    log("[geo-resolve] joined %d rows in %.0fs — county %d (%.1f%%), zcta %d, cbsa %d" % (
        n_res, time.time() - t, n_cty, 100.0 * n_cty / max(n_res, 1), n_zip, n_cbsa))

    # A US point that lands in NO county means the join is broken, not that the point is offshore —
    # at 888k points a few genuine offshore/AK-water rows are expected, a mass miss is a bug.
    hit = n_cty / float(n_res or 1)
    if hit < MIN_HIT_RATE:
        raise ResolveDegraded(
            "only %.1f%% of %d points landed in a county (floor %.0f%%) — boundary join looks broken, "
            "refusing to land a table of nulls" % (100 * hit, n_res, 100 * MIN_HIT_RATE))

    if not write:
        log("[geo-resolve] DRY RUN (limit=%s) — resolved %d rows, nothing written. "
            "Pass write=True / --write to land a partial pass deliberately." % (limit, n_res))
        return 0

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cols = ["source", "store_id", "hoodie_outlet", "lat", "lng", "county_fips", "county_name",
            "state_fp", "cbsa_code", "cbsa_name", "cbsa_type", "zcta"]
    recs = []
    for row in con.execute("SELECT %s FROM resolved" % ", ".join(cols)).fetchall():
        d = dict(zip(cols, row))
        d["method"] = "tiger%s-pip" % TIGER_YEAR
        d["resolved_at"] = ts
        recs.append(d)

    # A full recomputation, not an accumulation: every row is re-derived from src_outlets on each
    # pass, so merging would just retain rows for accounts that no longer exist. write_full_rebuild
    # keeps the empty-write guard (a 0-row result raises rather than blanking the table).
    warehouse.write_full_rebuild(TABLE, recs, fields=FIELDS)
    log("[geo-resolve] %s <- %d rows" % (TABLE, len(recs)))
    return len(recs)


def run(limit=None, write=None, log=print):
    build_cbsa_ref(log=log)
    return resolve(limit=limit, write=write, log=log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="resolve only N points (dry run by default)")
    ap.add_argument("--write", action="store_true", help="land a --limit pass deliberately (clobbers!)")
    ap.add_argument("--cbsa-only", action="store_true", help="just rebuild the CBSA crosswalk")
    a = ap.parse_args()
    if a.cbsa_only:
        build_cbsa_ref()
    else:
        run(limit=a.limit, write=True if a.write else None)
