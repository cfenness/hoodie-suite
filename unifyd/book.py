"""book.py — query the canonical star schema (domain.py) via DuckDB.

The single aggregation surface every analytics screen builds on: give it a dimension to cut
by and a measure, it joins fact_depletion to the dims and returns the leaderboard. Replaces
each app inventing its own numbers — one query, numbers reconcile everywhere.
"""
import warehouse
import domain

# dimension key -> qualified column across the joined star
DIMS = {
    "category": "p.category", "subcategory": "p.subcategory", "price_tier": "p.price_tier",
    "brand": "p.brand", "brand_family": "p.brand_family", "portfolio": "p.portfolio",
    "account": "a.name",
    "channel": "a.channel", "subchannel": "a.subchannel", "chain_status": "a.chain_status",
    "city": "a.city", "market": "a.market",
}
# measure key -> its spec (agg + the fact column it operates on). Canonical in domain.py.
_MEASURES = {m["key"]: m for m in domain.MEASURES}
_JOIN = ("FROM fact_depletion f JOIN dim_product p USING(product_id) "
         "JOIN dim_account a USING(account_id)")


def _con():
    con = warehouse.connect()
    for t in domain.TABLES:
        con.execute("CREATE OR REPLACE VIEW %s AS SELECT * FROM read_parquet('%s')" % (t, warehouse.uri(t)))
    return con


def cuts(dim="category", measure="revenue", limit=100):
    """Leaderboard of `dim` by `measure`. Every measure in domain.MEASURES is
    computed for real — sum, distinct count, share of total, per-outlet rate,
    price index, and YoY growth (last 12 months vs the prior 12)."""
    col = DIMS.get(dim, DIMS["category"])
    spec = _MEASURES.get(measure, _MEASURES["revenue"])
    agg = spec["agg"]
    of = spec.get("of") or measure          # the fact column value-based measures aggregate
    con = _con()

    if agg == "distinct":
        val = "count(distinct f.%s)" % of
    elif agg == "share":                     # % of the grand total of `of` (e.g. cases)
        total = con.execute("SELECT sum(%s) FROM fact_depletion" % of).fetchone()[0] or 1
        val = "round(100.0 * sum(f.%s) / %r, 2)" % (of, float(total))
    elif agg == "rate":                      # per point of distribution (per outlet)
        val = "round(sum(f.%s) * 1.0 / nullif(count(distinct f.account_id), 0), 2)" % of
    elif agg == "index":                     # avg price vs the overall book = 100
        t = con.execute("SELECT sum(revenue), sum(cases) FROM fact_depletion").fetchone()
        base_price = (t[0] / t[1]) if (t and t[1]) else 1.0
        val = "round(100.0 * (sum(f.revenue) / nullif(sum(f.cases), 0)) / %r, 1)" % float(base_price)
    elif agg == "yoy":                       # last 12 months vs the prior 12
        q = ("WITH r AS (SELECT date_id, row_number() OVER (ORDER BY year DESC, month DESC) rn FROM dim_date) "
             "SELECT %s AS name, round(100.0 * "
             "(sum(CASE WHEN r.rn <= 12 THEN f.%s ELSE 0 END) - sum(CASE WHEN r.rn BETWEEN 13 AND 24 THEN f.%s ELSE 0 END)) "
             "/ nullif(sum(CASE WHEN r.rn BETWEEN 13 AND 24 THEN f.%s ELSE 0 END), 0), 1) AS value "
             "%s JOIN r USING(date_id) GROUP BY 1 HAVING value IS NOT NULL ORDER BY value DESC LIMIT %d"
             % (col, of, of, of, _JOIN, int(limit)))
        return [{"name": r[0], "value": r[1]} for r in con.execute(q).fetchall()]
    else:                                    # sum (revenue, cases) + fallback
        val = "round(sum(f.%s), 1)" % of

    rows = con.execute(
        "SELECT %s AS name, %s AS value %s GROUP BY 1 HAVING value IS NOT NULL ORDER BY value DESC LIMIT %d"
        % (col, val, _JOIN, int(limit))
    ).fetchall()
    return [{"name": r[0], "value": r[1]} for r in rows]


def summary():
    con = _con()
    r = con.execute("SELECT count(*), round(sum(revenue),2), round(sum(cases),1) FROM fact_depletion").fetchone()
    dims = con.execute("SELECT count(*) FROM dim_product").fetchone()[0]
    accts = con.execute("SELECT count(*) FROM dim_account").fetchone()[0]
    return {"fact_rows": r[0], "revenue": r[1], "cases": r[2], "products": dims, "accounts": accts}
