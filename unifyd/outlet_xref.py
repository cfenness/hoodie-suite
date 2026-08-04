#!/usr/bin/env python3
"""outlet_xref.py — bridge OBSERVATION store ids to GEOCODED outlets, so price/assortment can be
attributed to a place.

THE GAP (measured 2026-08-03). retail_observations and outlet_geography are both keyed
`source|store_id`, but the ids are not the same ids:

    ubereats   the SAME id in TWO ENCODINGS — the sitemap holds it base64url
               ('3GYoBDgAU6-me98dDz_kSw'), the catalog hex-dashed
               ('dc662804-3800-53af-a67b-df1d0f3fe44b'). 208,624 observed stores, 15 matched.
    doordash   observations '1748913' (numeric)   vs geography 'blue jay coffees milwaukie' (a NAME)
    target     observations '1001'                vs geography 'Birmingham 280 Corridor'
    18 of the 22 sources that land observations have NO rows in outlet_geography at all

So obs_metro_rollup could place only ~990 (cbsa, zcta) cells out of a 211,690-store rollup, and the
metro reports have no price or assortment depth. This module is the missing join.

A CORRECTION WORTH KEEPING. The UberEats case first read as a catastrophic capture gap — "~188k
observed stores were never landed as outlets" — and it was nothing of the kind: every one of them is
in the sitemap, spelled differently. Decoding takes the match from 15 to 208,599 (100.0% of observed
UberEats stores) with no re-scraping at all. The tell was the UUID version nibble; see ue_ids.py.
Before concluding that a source failed to capture something, check that both sides spell the id the
same way.

DESIGN — A SIDE TABLE, AND STRATEGIES THAT MEASURE THEMSELVES.
It writes `outlet_xref` and touches neither source table. src_outlets already has three writers that
read->merge->rewrite the whole thing and must run serially; outlet_geography is derived. Adding a
crosswalk as its own table keeps every existing writer untouched and lets the mapping be rebuilt or
thrown away freely.

Each row records the METHOD that produced it and a confidence tier, because these bridges are not
equally trustworthy and a caller must be able to demand the strong ones:

    direct   1.00  same source, same store_id. Not a guess.
    encoding 1.00  the same id in two spellings (base64url vs hex-dashed). Lossless, see ue_ids.py.
    id_map   0.95  the source's OWN id->name table (doordash_stores) resolves the observation id to
                   the name geography is keyed by. Deterministic, but depends on that table.
    name     0.70  normalised store name within one source. A name is not an identity — two outlets
                   of one chain in a metro share it — so a name that resolves to MORE THAN ONE
                   geocoded outlet is DROPPED rather than assigned arbitrarily. OFF by default;
                   measured 0 yield (see NAME_STRATEGY).

The run reports yield per method, so which bridges are worth keeping is a measurement, not a guess.
First full build, 211,690 observation stores:

    encoding   7,233   2,716 ZIPs   331 metros    confidence 1.00
    direct     2,094     995 ZIPs   147 metros    confidence 1.00
    id_map       240     103 ZIPs    19 metros    confidence 0.95
    name           0   — scanned 213,179 candidate names, matched nothing; now opt-in
    TOTAL      9,567 of 211,690 (4.5%) across 3,351 ZIPs — against 990 (cbsa,zcta) cells before

97% of the mapping is confidence 1.00, i.e. exact rather than heuristic.

WHAT 4.5% MEANS, because the number invites the wrong conclusion. It is NOT that 95% of stores are
unidentifiable — 208,599 of 208,624 UberEats stores MATCH. They are unplaced because their outlet
has no lat/lng yet. Matching is solved; placement is now an aggregator_geo backlog, and every store
it geocodes converts straight into a placed store carrying price and assortment.

ONE OBSERVATION STORE MAPS TO AT MOST ONE OUTLET. Every strategy is applied in confidence order and
the first hit wins; ambiguous name matches are discarded entirely. Silently fanning one store across
several outlets would multiply its items and prices into several neighbourhoods — the same
inflation the geo_resolve border-point dedupe exists to prevent.

    python3 unifyd/outlet_xref.py --dry     # measure yield per method, write nothing
    python3 unifyd/outlet_xref.py           # build and land
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TABLE = "outlet_xref"
OBS = "obs_store_rollup"
GEO = "outlet_geography"

FIELDS = ["obs_source", "obs_store_id", "geo_source", "geo_store_id", "hoodie_outlet",
          "cbsa_code", "cbsa_name", "zcta", "county_fips", "method", "confidence", "built_at"]

# Source-specific id->name tables: the source's own store directory, which holds the numeric id the
# observations use AND the name the geography is keyed by. (id_col, name_col) per table.
ID_MAPS = {
    "doordash": ("doordash_stores", "store_id", "name"),
}

CONFIDENCE = {"direct": 1.00, "encoding": 1.00, "id_map": 0.95, "name": 0.70}

# Sources whose two sides spell the SAME id differently. `encoding` is confidence 1.00 because a
# base64url token is a lossless re-encoding of a UUID (22 chars == 16 bytes), not a heuristic — see
# ue_ids.py. This single strategy took UberEats from 15 matched stores to 208,599.
ENCODED_ID_SOURCES = ("ubereats", "postmates")

# A rebuild that maps far fewer stores than the last one is a broken read, not a shrinking universe.
MIN_KEEP_FRAC = float(os.environ.get("XREF_MIN_KEEP", "0.80"))

# THE NAME STRATEGY IS OFF BY MEASUREMENT, not by opinion. On the first full build it mapped exactly
# ZERO stores while scanning all 3,867 observation partitions to stage 213,179 candidate names. The
# reason is structural, not a tuning problem: a name can only match within its own source, every
# source that HAS geography is already covered by a stronger method (doordash -> id_map,
# ubereats/postmates -> encoding), and Target's observation "name" is just its store id ('2403').
# The code stays because a future source may need it; the full-table scan does not run by default.
NAME_STRATEGY = os.environ.get("XREF_NAME_STRATEGY", "0") == "1"


class XrefDegraded(Exception):
    """The build produced an implausible result — raise instead of landing it."""


def _attach(con, name, view):
    if not warehouse.attach_view(con, name, view=view):
        raise XrefDegraded("%s resolved to no parquet parts" % name)


def _norm_sql(col):
    """Name normalisation used on BOTH sides — lowercase, collapse whitespace, drop punctuation.
    Applied identically or the join is a coin flip."""
    return ("NULLIF(TRIM(REGEXP_REPLACE(LOWER(CAST(%s AS VARCHAR)), '[^a-z0-9]+', ' ', 'g')), '')" % col)


def build(dry=False, log=print):
    con = warehouse.connect()
    _attach(con, OBS, "s")
    _attach(con, GEO, "g")
    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    n_obs = con.execute("SELECT COUNT(*) FROM s").fetchone()[0]
    log("[xref] %d observation stores to place" % n_obs)

    # ── strategy 1: direct — same source, same id ────────────────────────────────────────────────
    con.execute("""
        CREATE OR REPLACE TEMP TABLE m_direct AS
        SELECT s.source AS obs_source, s.store_id AS obs_store_id,
               g.source AS geo_source, g.store_id AS geo_store_id,
               ANY_VALUE(g.hoodie_outlet) AS hoodie_outlet,
               ANY_VALUE(g.cbsa_code) AS cbsa_code, ANY_VALUE(g.cbsa_name) AS cbsa_name,
               ANY_VALUE(g.zcta) AS zcta, ANY_VALUE(g.county_fips) AS county_fips,
               'direct' AS method
        FROM s JOIN g ON g.source = s.source AND g.store_id = s.store_id
        GROUP BY 1,2,3,4""")
    n_direct = con.execute("SELECT COUNT(*) FROM m_direct").fetchone()[0]
    log("[xref]   direct  : %d" % n_direct)

    # ── strategy 2: encoding — the two sides spell the same id differently ───────────────────────
    # UberEats store URLs carry the id base64url-encoded ('3GYoBDgAU6-me98dDz_kSw'), while the
    # catalog/BFF report it hex-dashed ('dc662804-3800-53af-a67b-df1d0f3fe44b'). Same value, two
    # spellings, so every join between them missed and it read as a 188k-store capture gap.
    # Normalising BOTH sides to canonical form is exact, not a guess.
    import ue_ids
    con.execute("""
        CREATE OR REPLACE TEMP TABLE m_enc AS
        SELECT s.source AS obs_source, s.store_id AS obs_store_id,
               g.source AS geo_source, g.store_id AS geo_store_id,
               ANY_VALUE(g.hoodie_outlet) AS hoodie_outlet,
               ANY_VALUE(g.cbsa_code) AS cbsa_code, ANY_VALUE(g.cbsa_name) AS cbsa_name,
               ANY_VALUE(g.zcta) AS zcta, ANY_VALUE(g.county_fips) AS county_fips,
               'encoding' AS method
        FROM s JOIN g
          ON g.source = s.source
         AND %s = %s
        WHERE s.source IN %s
          AND g.store_id <> s.store_id          -- direct already covers the equal case
        GROUP BY 1,2,3,4""" % (ue_ids.sql_canonical("g.store_id"),
                               ue_ids.sql_canonical("s.store_id"),
                               str(tuple(ENCODED_ID_SOURCES))))
    n_enc = con.execute("SELECT COUNT(*) FROM m_enc").fetchone()[0]
    log("[xref]   encoding: %d" % n_enc)
    extra = ["m_enc"]

    # ── strategy 3: id_map — the source's own store directory resolves id -> name ────────────────
    parts = list(extra)
    for src, (tbl, id_col, name_col) in ID_MAPS.items():
        try:
            _attach(con, tbl, "d")
        except Exception as e:
            log("[xref]   id_map %s: %s unavailable (%s)" % (src, tbl, str(e)[:60]))
            continue
        # STAGE, THEN JOIN — and only on UNAMBIGUOUS names. Written as one big join this OOM'd on a
        # live run (6.9 GiB spilled): the directory and the geography both contain many stores
        # called "Starbucks", so the normalised-name join is many-to-many and explodes before any
        # filter applies. Narrowing to this source first (doordash: 433 observation stores, not
        # 211,690) and collapsing each side to names that appear ONCE makes it a small equi-join.
        #
        # The ambiguity rule is not just a performance trick, it is the same correctness rule the
        # `name` strategy already applies: if a name resolves to several outlets, picking one is a
        # guess, and a guess that lands a store in the wrong neighbourhood still renders.
        tag = src.replace("-", "_")
        con.execute("""
            CREATE OR REPLACE TEMP TABLE obs_%s AS
            SELECT source, store_id FROM s WHERE source = '%s'""" % (tag, src))
        con.execute("""
            CREATE OR REPLACE TEMP TABLE dir_%s AS
            SELECT CAST(%s AS VARCHAR) AS did, %s AS nm
            FROM d WHERE %s IS NOT NULL
            QUALIFY COUNT(*) OVER (PARTITION BY %s) = 1""" % (
            tag, id_col, _norm_sql("d." + name_col), _norm_sql("d." + name_col),
            _norm_sql("d." + name_col)))
        con.execute("""
            CREATE OR REPLACE TEMP TABLE geo_%s AS
            SELECT %s AS nm, ANY_VALUE(store_id) AS store_id, ANY_VALUE(hoodie_outlet) AS hoodie_outlet,
                   ANY_VALUE(cbsa_code) AS cbsa_code, ANY_VALUE(cbsa_name) AS cbsa_name,
                   ANY_VALUE(zcta) AS zcta, ANY_VALUE(county_fips) AS county_fips
            FROM g WHERE source = '%s' AND %s IS NOT NULL
            GROUP BY 1 HAVING COUNT(*) = 1""" % (tag, _norm_sql("store_id"), src, _norm_sql("store_id")))
        con.execute("""
            CREATE OR REPLACE TEMP TABLE m_id_%s AS
            SELECT o.source AS obs_source, o.store_id AS obs_store_id,
                   '%s' AS geo_source, x.store_id AS geo_store_id,
                   x.hoodie_outlet, x.cbsa_code, x.cbsa_name, x.zcta, x.county_fips,
                   'id_map' AS method
            FROM obs_%s o
            JOIN dir_%s dd ON dd.did = o.store_id
            JOIN geo_%s x ON x.nm = dd.nm""" % (tag, src, tag, tag, tag))
        n = con.execute("SELECT COUNT(*) FROM m_id_%s" % src.replace("-", "_")).fetchone()[0]
        log("[xref]   id_map %-9s: %d" % (src, n))
        parts.append("m_id_%s" % src.replace("-", "_"))

    # ── strategy 3: name — normalised store name within one source, UNAMBIGUOUS only ─────────────
    # A name that resolves to more than one geocoded outlet in the same source is not an identity.
    # Those are dropped, not assigned: placing a store in the wrong neighbourhood is worse than
    # leaving it unplaced, because the wrong answer still renders.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE gname AS
        SELECT source, %s AS nm,
               COUNT(*) AS n,
               ANY_VALUE(store_id) AS store_id, ANY_VALUE(hoodie_outlet) AS hoodie_outlet,
               ANY_VALUE(cbsa_code) AS cbsa_code, ANY_VALUE(cbsa_name) AS cbsa_name,
               ANY_VALUE(zcta) AS zcta, ANY_VALUE(county_fips) AS county_fips
        FROM g WHERE %s IS NOT NULL
        GROUP BY 1,2""" % (_norm_sql("store_id"), _norm_sql("store_id")))
    amb = con.execute("SELECT COUNT(*) FROM gname WHERE n > 1").fetchone()[0]
    log("[xref]   (ambiguous geography names dropped: %d)" % amb)

    have_names = _obs_names(con, log=log) if NAME_STRATEGY else False
    if not NAME_STRATEGY:
        log("[xref]   name    : SKIPPED (measured 0 yield — set XREF_NAME_STRATEGY=1 to run)")
    if have_names:
        con.execute("""
            CREATE OR REPLACE TEMP TABLE m_name AS
            SELECT o.source AS obs_source, o.store_id AS obs_store_id,
                   n.source AS geo_source, n.store_id AS geo_store_id,
                   n.hoodie_outlet, n.cbsa_code, n.cbsa_name, n.zcta, n.county_fips,
                   'name' AS method
            FROM obsname o JOIN gname n ON n.source = o.source AND n.nm = o.nm
            WHERE n.n = 1""")
        n_name = con.execute("SELECT COUNT(*) FROM m_name").fetchone()[0]
        log("[xref]   name    : %d" % n_name)
        parts.append("m_name")
    else:
        log("[xref]   name    : skipped (observations carry no usable store name)")

    # ── combine, best method wins, exactly one row per observation store ─────────────────────────
    union = " UNION ALL ".join(["SELECT * FROM m_direct"] + ["SELECT * FROM %s" % p for p in parts])
    con.execute("""
        CREATE OR REPLACE TEMP TABLE xref AS
        SELECT obs_source, obs_store_id, geo_source, geo_store_id, hoodie_outlet,
               cbsa_code, cbsa_name, zcta, county_fips, method,
               CASE method WHEN 'direct' THEN 1.00 WHEN 'encoding' THEN 1.00
                    WHEN 'id_map' THEN 0.95 ELSE 0.70 END AS confidence
        FROM (%s)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY obs_source, obs_store_id
            ORDER BY CASE method WHEN 'direct' THEN 0 WHEN 'encoding' THEN 1
                          WHEN 'id_map' THEN 2 ELSE 3 END,
                     geo_store_id) = 1""" % union)

    n_x = con.execute("SELECT COUNT(*) FROM xref").fetchone()[0]
    dup = con.execute("""SELECT COUNT(*) FROM (SELECT obs_source, obs_store_id FROM xref
                         GROUP BY 1,2 HAVING COUNT(*) > 1)""").fetchone()[0]
    if dup:
        raise XrefDegraded("%d observation stores mapped to more than one outlet" % dup)

    by_method = con.execute("SELECT method, COUNT(*) FROM xref GROUP BY 1 ORDER BY 2 DESC").fetchall()
    placed_zips = con.execute("SELECT COUNT(DISTINCT zcta) FROM xref WHERE zcta IS NOT NULL").fetchone()[0]
    log("[xref] mapped %d of %d observation stores (%.1f%%) across %d ZIPs"
        % (n_x, n_obs, 100.0 * n_x / max(n_obs, 1), placed_zips))
    for m, c in by_method:
        log("[xref]   %-7s %7d  (confidence %.2f)" % (m, c, CONFIDENCE.get(m, 0)))

    if dry:
        log("[xref] DRY RUN — nothing written")
        return {"mapped": n_x, "of": n_obs, "zips": placed_zips, "by_method": dict(by_method)}

    if not n_x:
        raise XrefDegraded("no observation store could be mapped — refusing to land an empty crosswalk")
    try:
        prev = warehouse.row_count(TABLE)
    except Exception:
        prev = 0
    if prev and n_x < prev * MIN_KEEP_FRAC:
        raise XrefDegraded("crosswalk collapsed: %d rows now vs %d before (floor %.0f%%)"
                           % (n_x, prev, 100 * MIN_KEEP_FRAC))

    cols = [c for c in FIELDS if c != "built_at"]
    cur = con.execute("SELECT %s FROM xref" % ", ".join(cols))
    names = [d[0] for d in cur.description]
    recs = []
    for row in cur.fetchall():
        d = dict(zip(names, row))
        d["built_at"] = built_at
        recs.append({k: d.get(k) for k in FIELDS})
    warehouse.write_full_rebuild(TABLE, recs, fields=FIELDS)
    log("[xref] %s <- %d rows" % (TABLE, len(recs)))
    return {"mapped": n_x, "of": n_obs, "zips": placed_zips, "by_method": dict(by_method)}


def _obs_names(con, log=print):
    """Stage (source, store_id, normalised name) from the observation partitions, if they carry one.

    Reads the raw partitions because obs_store_rollup deliberately does not keep the store NAME —
    it is an identity field, not a metric. Explicit file list, never a glob."""
    files = warehouse._retry(lambda: warehouse._partition_files_strict("retail_observations"),
                             what="list partitions retail_observations")
    if not files:
        return False
    src = ["s3://%s" % f for f in files] if warehouse.remote() else files
    lst = ", ".join("'%s'" % p.replace("'", "") for p in src)
    con.execute("CREATE OR REPLACE VIEW o AS SELECT * FROM read_parquet([%s], union_by_name=true)" % lst)
    # ONLY the sources that actually have geography — a name can only ever match within its own
    # source, so scanning the other 14 sources' partitions is pure cost. This also keeps the scan
    # off the deep retail catalogs (abc-fws alone is 13k items/store) that can never contribute.
    geo_srcs = [r[0] for r in con.execute(
        "SELECT DISTINCT source FROM g WHERE source IS NOT NULL").fetchall()]
    if not geo_srcs:
        return False
    in_list = ", ".join("'%s'" % s.replace("'", "''") for s in geo_srcs)

    # GROUP BY, then dedupe with MIN — not QUALIFY over a grouped select, which DuckDB rejects
    # ("column store_id must appear in the GROUP BY clause") because it will not resolve positional
    # group keys inside a window. MIN also makes the chosen name deterministic across rebuilds.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE obsname AS
        SELECT source, store_id, MIN(nm) AS nm FROM (
            SELECT source, CAST(store_id AS VARCHAR) AS store_id, %s AS nm
            FROM o
            WHERE store IS NOT NULL AND store <> ''
              AND store_id IS NOT NULL AND store_id <> ''
              AND source IN (%s))
        WHERE nm IS NOT NULL
        GROUP BY source, store_id""" % (_norm_sql("store"), in_list))
    n = con.execute("SELECT COUNT(*) FROM obsname WHERE nm IS NOT NULL").fetchone()[0]
    log("[xref]   observation stores carrying a name: %d" % n)
    return n > 0


def run(log=print):
    return build(log=log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="measure yield per method, write nothing")
    a = ap.parse_args()
    build(dry=a.dry)
