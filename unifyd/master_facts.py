#!/usr/bin/env python3
"""master_facts.py — inventory + pricing FACT tables (sku × outlet × date).

Dimensions describe things; FACTS record observations that link a sku to an outlet over time. Same
config-driven engine as the dimensions: a fact source maps into a wide fact schema (outlet-identity
fields + product-identity fields + measures + obs_date), and the builder computes the SAME deterministic
keys the dimension resolve uses — outlet_key (master_apply.ENTITY_IDENTITY['outlet']) and the sku/brand
key (master_apply._keyexprs) — INLINE, so a fact links to the dimensions with no join. It groups by
(outlet_key, product_ref, obs_date) and aggregates the measures.

Grain is the FINEST available: `sku` when size_ml/upc are mapped, else `brand` (e.g. AB carriage is a
brand×outlet observation; ABC FWS in/out is sku×outlet; a price list with no outlet is sku×market). No
outlet fields mapped → outlet_key resolves to the empty/market bucket (a list price, not a shelf price).
Runs entirely in DuckDB over Parquet → fact_<name>. Same (result dict) shape as master_apply.build.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import derive
import master_apply as ma

# measure field -> aggregation when collapsing to the (outlet, product, date) grain
FACTS = {
    "inventory": {"measures": {"in_stock": "max", "on_hand": "max", "units": "max", "carriage": "any_value"}},
    "pricing":   {"measures": {"price": "avg", "promo": "any_value", "currency": "any_value"}},
}


def _outlet_key(mnames):
    """The SAME outlet identity key the dimension resolve builds — strong source_ref else name+addr+zip5."""
    spec = ma.ENTITY_IDENTITY["outlet"]
    strong, nat = spec["strong"], spec["natural"]
    nk_cols = [c for c in nat if c in mnames]
    nk = "||'|'||".join("lower(coalesce(CAST(%s AS VARCHAR),''))" % derive.col(c) for c in nk_cols) or "''"
    if strong in mnames:
        sc = derive.col(strong)
        return "CASE WHEN %s IS NOT NULL AND %s<>'' THEN 's:'||%s ELSE md5(%s) END" % (sc, sc, sc, nk)
    return "md5(%s)" % nk


def build(fact, master_fields, mappings_by_ds, built_by="SYS", built_at=None, log=print):
    built_at = built_at or int(time.time())
    import warehouse
    con = warehouse.connect()
    spec = FACTS.get(fact) or FACTS["inventory"]
    mnames = ma._mnames(master_fields)
    selects, per_source, warnings = [], [], []
    for ds, rules in mappings_by_ds.items():
        if not any(r.get("master_field") for r in (rules or [])):
            continue
        sel = ma.source_select(ds, rules, master_fields)
        try:
            n = con.execute("SELECT count(*) FROM (%s)" % sel).fetchone()[0]
        except Exception as e:
            warnings.append("%s skipped: %s" % (ds, str(e)[:120])); log(warnings[-1]); continue
        selects.append(sel); per_source.append({"dataset": ds, "rows": n})
        log("%s: %d rows" % (ds, n))
    if not selects:
        return {"rows": 0, "sources": 0, "per_source": [], "warnings": warnings, "note": "no usable mappings"}
    raw = warehouse.uri("fact_%s_raw" % fact).replace("'", "")
    con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (" UNION ALL ".join(selects), raw))

    keys = ma._keyexprs(mnames)                       # brand..sku identity keys (vintage-stripped)
    okey = _outlet_key(mnames)
    skucols = [c for c in ("upc", "size_ml") if c in mnames]
    has_sku = ("(" + " OR ".join("CAST(%s AS VARCHAR)<>''" % derive.col(c) for c in skucols) + ")") if skucols else "false"
    product_ref = "CASE WHEN %s THEN %s ELSE %s END" % (has_sku, keys["sku"], keys["brand"])
    grain = "CASE WHEN %s THEN 'sku' ELSE 'brand' END" % has_sku
    if "obs_date" in mnames:
        obs = "coalesce(NULLIF(CAST(%s AS VARCHAR),''), strftime(to_timestamp(%d),'%%Y-%%m-%%d'))" % (derive.col("obs_date"), built_at)
    else:
        obs = "strftime(to_timestamp(%d),'%%Y-%%m-%%d')" % built_at
    meas = [("%s(%s) AS %s" % (agg, derive.col(m), derive.col(m))) for m, agg in spec["measures"].items() if m in mnames]
    # keep whichever dimension keys are present, so a fact row is joinable both ways
    dimk = [k for k in ("brand", "sku") if any(c in mnames for c in ma.GRAIN_KEY.get(k, []))]
    dimsel = ", ".join("any_value(%s) AS %s_key" % (keys[k], k) for k in dimk)

    dst = warehouse.uri("fact_%s" % fact).replace("'", "")
    audit = "%s AS built_by, %d AS built_at" % (derive._sqlstr(built_by), built_at)
    sql = ("WITH b AS (SELECT *, %s AS outlet_key, %s AS product_ref, %s AS grain, %s AS obs_date "
           "FROM read_parquet('%s')) "
           "SELECT outlet_key, product_ref, grain, obs_date%s%s, count(*) AS obs_rows, "
           "count(DISTINCT _source) AS sources, list_distinct(list(_source)) AS source_list, %s "
           "FROM b GROUP BY outlet_key, product_ref, grain, obs_date"
           % (okey, product_ref, grain, obs, raw,
              (", " + dimsel) if dimsel else "", (", " + ", ".join(meas)) if meas else "", audit))
    con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (sql, dst))
    total = con.execute("SELECT count(*) FROM read_parquet('%s')" % dst).fetchone()[0]
    log("fact_%s: %d observations from %d sources → %s" % (fact, total, len(selects), dst))
    return {"rows": total, "observations": total, "sources": len(selects), "per_source": per_source,
            "warnings": warnings, "uri": warehouse.uri("fact_%s" % fact)}


if __name__ == "__main__":
    # compile-level smoke: key exprs resolve, no warehouse needed
    mf = [{"name": "source_ref"}, {"name": "outlet_name"}, {"name": "address"}, {"name": "zip5"},
          {"name": "brand"}, {"name": "carriage"}]
    print("outlet_key:", _outlet_key([f["name"] for f in mf])[:80], "…")
    print("facts:", list(FACTS))
    print("master_facts smoke: OK")
