#!/usr/bin/env python3
"""obs_rollup.py — pre-aggregate retail_observations to STORE grain so analytics can query it live.

WHY THIS EXISTS. `retail_observations` is 53.5M rows across ~3,800 daily partitions. Once the read
was fixed it became queryable, but not INTERACTIVE: a grouped aggregate with two COUNT(DISTINCT)
over the whole table was still running after 16 minutes. That is fine for a batch job and useless
behind a page. Nothing in the metro reports needs row-level observations — they need "how deep is
this store's assortment, what does it charge, how often is it on promo". That is a few hundred
thousand rows, not 53 million.

So this lands two derived tables, both rebuilt from scratch (no network, no new collection):

  obs_store_rollup   one row per (source, store_id) — the join partner for outlet_geography, which
                     is keyed the same way. Assortment breadth, price distribution, promo intensity,
                     stock health, observation window.
  obs_metro_rollup   obs_store_rollup ⋈ outlet_geography collapsed to (cbsa, zcta) — exactly the
                     grain the neighbourhood tables render, so a deck reads ONE small table.

Measured on the first full build: 53.5M rows / 3,864 partitions -> 211,690 stores in 386s, after
which a grouped aggregate over the store rollup runs in 2.3s and over the metro rollup in 0.8s.

WHAT THE NUMBERS MEAN, precisely:
  - price percentiles are over OBSERVED shelf price rows, per store, across the whole window. They
    describe what a store charges, NOT what a shopper paid.
  - `items` counts distinct product identity as the source gave it (upc, else product_id). Sources
    differ in how completely they populate those, so `items` compares stores WITHIN a source far
    better than across sources — `sources` is carried on the metro rows so a reader can see the mix.
    The spread is real and large: binnys ~17.8k items/store and abc-fws ~13.3k (full retail catalogs)
    against ubereats ~89 (a delivery menu). Averaging across sources mixes different things.
  - promo_share is the share of observation ROWS flagged on_promo, not of distinct items. IT IS
    ~0.000 FOR EVERY SOURCE EXCEPT binnys (~0.046), because most scrapers never populate on_promo.
    That is missing instrumentation, NOT an absence of promotions — do not render it as "promo
    intensity" anywhere customer-facing until the scrapers set the flag.

THE LINKAGE CAVEAT — read this before using obs_metro_rollup. The metro grain is thin (990 cells on
the first build) and that is NOT a bug in this module: the observation layer and the account layer
are keyed incompatibly, measured 2026-08-03.

    doordash    obs store_id = '1748913' (numeric)   vs  outlet_geography store_id = a NAME
                ('blue jay coffees milwaukie'). A bridge through doordash_stores.name works
                (424 of 433 stores, 115 ZIPs) but only 433 doordash stores have observations at all.
    target      same shape — obs '1001' vs geography 'Birmingham 280 Corridor'.
    ubereats    both sides are UUIDs, but different UUID SETS: 208,624 observed stores vs 29,845
                geography rows, overlapping 2,069; observations ∩ ubereats_sitemap is 15.
    18 of the 22 sources that land observations have NO rows in outlet_geography at all.

So price/assortment can be attributed to a neighbourhood only for the small overlap today. Closing
that is an outlet-identity problem (outlet_ident / outlet_union territory), not a rollup problem, and
it is the blocker to putting price depth in the metro reports.

    python3 unifyd/obs_rollup.py --sample 200      # time it on a few partitions first
    python3 unifyd/obs_rollup.py                   # full rebuild
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

SRC = "retail_observations"
GEO = "outlet_geography"
STORE_TABLE = "obs_store_rollup"
METRO_TABLE = "obs_metro_rollup"

STORE_FIELDS = ["source", "store_id", "chain", "items", "brands", "obs_rows", "stores_seen_days",
                "first_date", "last_date",
                "price_min", "price_p25", "price_median", "price_p75", "price_max", "price_avg",
                "promo_rows", "promo_share", "in_stock_share", "hemp_items", "built_at"]

METRO_FIELDS = ["cbsa_code", "cbsa_name", "zcta", "county_fips", "stores", "sources",
                "items_total", "items_per_store", "obs_rows",
                "price_p25", "price_median", "price_p75", "price_avg",
                "promo_share", "in_stock_share", "built_at"]

# A rebuild that lands far fewer stores than the last one is a broken read, not a shrinking market —
# same discipline as geo_resolve's hit floor. `write_parquet` already refuses a 0-row overwrite; this
# catches the subtler collapse.
MIN_KEEP_FRAC = float(os.environ.get("ROLLUP_MIN_KEEP", "0.80"))


class RollupDegraded(Exception):
    """The rebuild produced an implausible result — raise instead of landing it."""


def _attach(con, name, view):
    if not warehouse.attach_view(con, name, view=view):
        raise RollupDegraded("%s resolved to no parquet parts" % name)


def _attach_parts(con, name, view):
    """Same as attach_view but for a date-partitioned table. Passes an EXPLICIT file list — handing
    DuckDB a `<dir>/*.parquet` glob corrupts the read on a large partition set (that is the bug that
    made this table unreadable in the first place; see warehouse.query_parts)."""
    files = warehouse._retry(lambda: warehouse._partition_files_strict(name),
                             what="list partitions %s" % name)
    if not files:
        raise RollupDegraded("%s has no partitions" % name)
    src = ["s3://%s" % f for f in files] if warehouse.remote() else files
    lst = ", ".join("'%s'" % p.replace("'", "") for p in src)
    con.execute("CREATE OR REPLACE VIEW %s AS SELECT * FROM read_parquet([%s], union_by_name=true)"
                % (view, lst))
    return len(files)


def build_store(sample=None, log=print):
    """Collapse retail_observations to one row per (source, store_id)."""
    con = warehouse.connect()
    t0 = time.time()
    n_parts = _attach_parts(con, SRC, "o")
    log("[rollup] %s: %d partitions attached" % (SRC, n_parts))

    lim = "USING SAMPLE %d ROWS" % int(sample) if sample else ""
    # ONE pass, columnar: only the columns below are read off disk. The 16-minute query that
    # motivated this module also pulled `name` and `brand` text for every row.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE store AS
        SELECT source,
               CAST(store_id AS VARCHAR)                          AS store_id,
               ANY_VALUE(chain)                                   AS chain,
               COUNT(DISTINCT COALESCE(NULLIF(upc,''), NULLIF(product_id,''))) AS items,
               COUNT(DISTINCT NULLIF(brand,''))                   AS brands,
               COUNT(*)                                           AS obs_rows,
               COUNT(DISTINCT "date")                             AS stores_seen_days,
               MIN("date")                                        AS first_date,
               MAX("date")                                        AS last_date,
               MIN(price)                                         AS price_min,
               QUANTILE_CONT(price, 0.25)                         AS price_p25,
               QUANTILE_CONT(price, 0.50)                         AS price_median,
               QUANTILE_CONT(price, 0.75)                         AS price_p75,
               MAX(price)                                         AS price_max,
               AVG(price)                                         AS price_avg,
               SUM(CASE WHEN on_promo THEN 1 ELSE 0 END)          AS promo_rows,
               AVG(CASE WHEN on_promo THEN 1.0 ELSE 0.0 END)      AS promo_share,
               AVG(CASE WHEN in_stock THEN 1.0 ELSE 0.0 END)      AS in_stock_share,
               COUNT(DISTINCT CASE WHEN is_hemp
                     THEN COALESCE(NULLIF(upc,''), NULLIF(product_id,'')) END) AS hemp_items
        FROM (SELECT * FROM o %s)
        WHERE store_id IS NOT NULL AND store_id <> ''
        GROUP BY 1, 2""" % lim)
    n = con.execute("SELECT COUNT(*) FROM store").fetchone()[0]
    log("[rollup] %s -> %d stores in %.0fs" % (SRC, n, time.time() - t0))
    if not n:
        raise RollupDegraded("rollup produced 0 stores from a non-empty table")
    return con, n


def _rows(con, sql, fields, built_at):
    out = []
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        d["built_at"] = built_at
        out.append({k: d.get(k) for k in fields})
    return out


def build(sample=None, write=None, log=print):
    """Rebuild both rollups. Returns {table: rows}. A sample run does NOT write (it would replace a
    full rebuild with a fragment — the never-clobber-a-catalog rule)."""
    if write is None:
        write = sample is None
    built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con, n_store = build_store(sample=sample, log=log)

    prev = 0
    try:
        prev = warehouse.row_count(STORE_TABLE)
    except Exception:
        prev = 0
    if write and prev and n_store < prev * MIN_KEEP_FRAC:
        raise RollupDegraded(
            "store rollup collapsed: %d stores now vs %d before (floor %.0f%%) — refusing to land"
            % (n_store, prev, 100 * MIN_KEEP_FRAC))

    store_rows = _rows(con, "SELECT * FROM store", STORE_FIELDS, built_at)

    # Metro grain: join the store rollup to geography. outlet_geography is keyed source|store_id, the
    # same key, so this is a plain equi-join — no re-reading of the 53M-row source.
    _attach(con, GEO, "g")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE metro AS
        SELECT g.cbsa_code, ANY_VALUE(g.cbsa_name) AS cbsa_name, g.zcta,
               ANY_VALUE(g.county_fips)            AS county_fips,
               COUNT(*)                            AS stores,
               COUNT(DISTINCT s.source)            AS sources,
               SUM(s.items)                        AS items_total,
               AVG(s.items)                        AS items_per_store,
               SUM(s.obs_rows)                     AS obs_rows,
               QUANTILE_CONT(s.price_p25, 0.5)     AS price_p25,
               QUANTILE_CONT(s.price_median, 0.5)  AS price_median,
               QUANTILE_CONT(s.price_p75, 0.5)     AS price_p75,
               AVG(s.price_avg)                    AS price_avg,
               AVG(s.promo_share)                  AS promo_share,
               AVG(s.in_stock_share)               AS in_stock_share
        FROM store s
        JOIN g ON g.source = s.source AND g.store_id = s.store_id
        WHERE g.cbsa_code IS NOT NULL
        GROUP BY g.cbsa_code, g.zcta""")
    n_metro = con.execute("SELECT COUNT(*) FROM metro").fetchone()[0]
    log("[rollup] %s -> %d (cbsa, zcta) cells" % (METRO_TABLE, n_metro))
    metro_rows = _rows(con, "SELECT * FROM metro", METRO_FIELDS, built_at)

    if not write:
        log("[rollup] DRY RUN (sample=%s) — %d stores / %d cells computed, nothing written"
            % (sample, n_store, n_metro))
        return {STORE_TABLE: 0, METRO_TABLE: 0}

    warehouse.write_full_rebuild(STORE_TABLE, store_rows, fields=STORE_FIELDS)
    log("[rollup] %s <- %d rows" % (STORE_TABLE, len(store_rows)))
    if metro_rows:
        warehouse.write_full_rebuild(METRO_TABLE, metro_rows, fields=METRO_FIELDS)
        log("[rollup] %s <- %d rows" % (METRO_TABLE, len(metro_rows)))
    else:
        log("[rollup] %s: 0 cells — geography join matched nothing, NOT writing" % METRO_TABLE)
    return {STORE_TABLE: len(store_rows), METRO_TABLE: len(metro_rows)}


def run(log=print):
    return build(log=log)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None, help="aggregate only N sampled rows (dry run)")
    ap.add_argument("--write", action="store_true", help="land a sampled run deliberately (clobbers!)")
    a = ap.parse_args()
    build(sample=a.sample, write=True if a.write else None)
