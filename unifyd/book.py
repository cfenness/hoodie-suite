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
    "channel": "a.channel", "subchannel": "a.subchannel", "chain_status": "a.chain_status",
    "city": "a.city", "market": "a.market",
}
_MEASURE_SQL = {"revenue": "sum(f.revenue)", "cases": "sum(f.cases)",
                "pod": "count(distinct f.account_id)"}


def _con():
    con = warehouse.connect()
    for t in domain.TABLES:
        con.execute("CREATE OR REPLACE VIEW %s AS SELECT * FROM read_parquet('%s')" % (t, warehouse.uri(t)))
    return con


def cuts(dim="category", measure="revenue", limit=100):
    col = DIMS.get(dim, DIMS["category"])
    agg = _MEASURE_SQL.get(measure, _MEASURE_SQL["revenue"])
    con = _con()
    rows = con.execute(
        "SELECT %s AS name, round(%s, 1) AS value "
        "FROM fact_depletion f JOIN dim_product p USING(product_id) JOIN dim_account a USING(account_id) "
        "GROUP BY 1 ORDER BY value DESC LIMIT %d" % (col, agg, int(limit))
    ).fetchall()
    return [{"name": r[0], "value": r[1]} for r in rows]


def summary():
    con = _con()
    r = con.execute("SELECT count(*), round(sum(revenue),2), round(sum(cases),1) FROM fact_depletion").fetchone()
    dims = con.execute("SELECT count(*) FROM dim_product").fetchone()[0]
    accts = con.execute("SELECT count(*) FROM dim_account").fetchone()[0]
    return {"fact_rows": r[0], "revenue": r[1], "cases": r[2], "products": dims, "accounts": accts}
