"""master_apply.py — materialize dim_product from the field mappings, at scale.

The apply engine behind the Mapping tab. Given the master schema + each source's mapping rows, it
compiles every mapped field to a DuckDB SQL expression (derive.compile_rule) and builds, per source,
a SELECT that projects the source Parquet onto the master schema — then UNIONs them into dim_product
and COPYs it straight to warehouse Parquet. Everything runs in DuckDB over Parquet (no per-row Python),
so a multi-million-row master builds in one pass.

  preview(dataset, rule)     -> raw → derived samples, so a rule can be verified before committing.
  build(master_fields, maps) -> dim_product Parquet in the warehouse + a per-source row count.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import derive


def _mnames(master_fields):
    return [f["name"] if isinstance(f, dict) else f for f in master_fields]


def source_select(ds, rules, master_fields):
    """The per-source SELECT projecting `ds` onto the master schema (unmapped master fields → NULL).
    Each mapped field is auto-wrapped in its master field's normalizer (e.g. upc → GTIN-14). A rule of the
    form {"filter": "<sql>"} (no source/master field) becomes a source-level WHERE — so a catalog can be
    scoped to its bev-alc rows (e.g. category='Adult Beverage') before it feeds the master."""
    import warehouse
    where = next((r["filter"] for r in rules if isinstance(r, dict) and r.get("filter")), None)
    by_master = {r["master_field"]: r for r in rules if r.get("master_field")}
    cols = []
    for f in master_fields:
        mf = f["name"] if isinstance(f, dict) else f
        nz = f.get("normalize") if isinstance(f, dict) else None
        r = by_master.get(mf)
        if r:
            e = derive.apply_normalizer(nz, derive.compile_rule(dict(r)))
            cols.append("%s AS %s" % (e, derive.col(mf)))
        else:
            cols.append("NULL AS %s" % derive.col(mf))
    cols.append("%s AS %s" % (derive._sqlstr(ds), derive.col("_source")))
    uri = warehouse.uri(ds).replace("'", "")
    sql = "SELECT %s FROM read_parquet('%s')" % (", ".join(cols), uri)
    if where:
        sql += " WHERE (%s)" % where
    return sql


def preview(dataset, rule, limit=12, normalize=None):
    """Apply one rule (+ the target master field's normalizer) to a sample → [{raw, derived}] so you
    SEE the derivation is right before committing it. This is the 'get it right' loop."""
    import warehouse
    expr = derive.apply_normalizer(normalize, derive.compile_rule(dict(rule)))
    sf = rule.get("source_field")
    if (rule.get("mode") == "expr") or not sf:
        rows = warehouse.query(dataset, "SELECT %s AS derived FROM t LIMIT %d" % (expr, int(limit)))
        return [{"raw": "", "derived": r.get("derived")} for r in rows]
    src = derive.col(sf)
    rows = warehouse.query(dataset, "SELECT DISTINCT CAST(%s AS VARCHAR) raw, %s AS derived FROM t "
                           "WHERE CAST(%s AS VARCHAR)<>'' LIMIT %d" % (src, expr, src, int(limit)))
    return [{"raw": r.get("raw"), "derived": r.get("derived")} for r in rows]


# Identity strategy per master ENTITY: a STRONG key field (a real unique id — used when present) and
# the NATURAL key (normalized fields that identify the row when the strong key is absent). This is the
# one place the two masters differ; everything else in the engine is entity-agnostic.
ENTITY_IDENTITY = {
    "product": {"strong": "upc",        "natural": ["brand", "product_name", "size_ml"]},
    "outlet":  {"strong": "source_ref", "natural": ["outlet_name", "address", "zip5"]},
}

# ── The PRODUCT hierarchy: brand → product → item → sku. Sources map into ONE wide schema; the build
# SHREDS it by GRAIN into a dimension table per level, each keyed at its grain + carrying its parent's
# key (so the tables link). Supplier is derived as a time-bounded brand↔supplier association (SCD),
# NOT a hierarchy level. This is "apply the attribute at its level" made mechanical. ──
HIERARCHY = ["brand", "product", "item", "sku"]
# the NATURAL-key components ADDED at each grain (cumulative — a level's key includes all ancestors').
GRAIN_KEY = {"brand": ["brand"], "product": ["flavor", "product_name"],
             "item": ["size_ml", "container"], "sku": ["pack", "upc"]}
# The SKU is the matching TARGET (the fully-specified sellable unit), and a UPC is its GLOBAL identity — two
# rows with the same UPC are the same SKU, two different UPCs are two different SKUs. So UPC is in the sku key.
# This relies on sku_match.propagate_upcs() running FIRST (fills missing UPCs across single-UPC item clusters),
# so a no-UPC row doesn't split off from its UPC'd siblings. vintage/edition remain sku attributes.
NAMEISH = {"brand", "product_name", "flavor", "container"}
# default grain per field name when a schema field doesn't declare one
GRAIN_DEFAULTS = {"brand": "brand", "brand_group": "brand",
    "product_name": "product", "flavor": "product", "abv": "product", "style": "product",
    "category": "product", "origin": "product",
    "size_ml": "item", "packsize": "item", "container": "item",
    "pack": "sku", "upc": "sku", "gtin": "sku", "vintage": "sku", "edition": "sku",
    "supplier": "supplier"}

def _grain_of(master_fields):
    g = {}
    for f in master_fields:
        nm = f["name"] if isinstance(f, dict) else f
        g[nm] = (f.get("grain") if isinstance(f, dict) else None) or GRAIN_DEFAULTS.get(nm)
    return g

def _keyexprs(mnames):
    """Cumulative md5 identity key per grain — name parts vintage/edition-stripped so a sku is stable
    across vintages/editions. Returns {grain: sql_md5_expr}."""
    cum, keys = [], {}
    for g in HIERARCHY:
        for c in GRAIN_KEY.get(g, []):
            if c in mnames:
                # coalesce EVERY component to '' — an unmapped name field (NULL) would otherwise make the
                # whole `||` concatenation NULL, collapsing all keys into one group.
                cum.append(("coalesce(%s,'')" % derive.identity_expr(c)) if c in NAMEISH
                           else "lower(coalesce(CAST(%s AS VARCHAR),''))" % derive.col(c))
        keys[g] = "md5(%s)" % ("||'|'||".join(cum) if cum else "''")
    return keys

def resolve_hierarchy(master_fields, dim_uri, con, built_by="SYS", built_at=None, log=print):
    """Shred the wide dim_product into dim_brand / dim_product / dim_item / dim_sku (each = distinct rows
    at its grain, with a stable key + its parent's key + that grain's attributes), plus dim_supplier as a
    brand↔supplier association with active/inactive dates. Every level's attributes are aggregated with
    any_value; provenance (sources/source_list) carried through."""
    import time as _t
    built_at = built_at or int(_t.time())
    import warehouse
    mnames = _mnames(master_fields)
    grain = _grain_of(master_fields)
    keys = _keyexprs(mnames)
    audit = ("%d AS master_created_at, %d AS master_updated_at, %s AS updated_by"
             % (built_at, built_at, derive._sqlstr(built_by)))
    # QUALITY GATE: a record needs the top identity (a brand) to be PROMOTED into the master. Brand-less
    # source rows are NOT masters — they stay in the source (untouched) + eligible for later matching /
    # enrichment. This keeps the master to real, identifiable products.
    gate = (" WHERE nullif(trim(CAST(%s AS VARCHAR)),'') IS NOT NULL" % derive.col("brand")) if "brand" in mnames else ""
    out = {}
    if gate:
        staged = con.execute("SELECT count(*) FROM read_parquet('%s')" % dim_uri).fetchone()[0]
        kept = con.execute("SELECT count(*) FROM read_parquet('%s')%s" % (dim_uri, gate)).fetchone()[0]
        out["_gate"] = {"staged": staged, "qualified": kept, "excluded": staged - kept}
        log("brand gate: %d/%d source rows qualify (%d brand-less held back)" % (kept, staged, staged - kept))
    for i, g in enumerate(HIERARCHY):
        attrs = [f for f in mnames if grain.get(f) == g]
        cols = "%s AS %s_key" % (keys[g], g)
        if i > 0:
            cols += ", any_value(%s) AS %s_key" % (keys[HIERARCHY[i - 1]], HIERARCHY[i - 1])
        if attrs:
            cols += ", " + ", ".join("any_value(%s) AS %s" % (derive.col(a), derive.col(a)) for a in attrs)
        sql = ("WITH b AS (SELECT * FROM read_parquet('%s')%s) "
               "SELECT %s, count(*) AS source_rows, count(DISTINCT _source) AS sources, "
               "list_distinct(list(_source)) AS source_list, %s FROM b GROUP BY %s"
               % (dim_uri, gate, cols, audit, keys[g]))
        rtable = "dim_%s" % g
        rdst = warehouse.uri(rtable).replace("'", "")
        con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (sql, rdst))
        n = con.execute("SELECT count(*) FROM read_parquet('%s')" % rdst).fetchone()[0]
        out[g] = {"rows": n, "uri": warehouse.uri(rtable)}
        log("dim_%s: %d rows" % (g, n))
    # supplier SCD — brand↔supplier association with active/inactive dates (persists items across acquisition)
    sup = [f for f in mnames if grain.get(f) == "supplier"]
    if sup and "brand" in mnames:
        scol = derive.col(sup[0])
        sql = ("WITH b AS (SELECT * FROM read_parquet('%s')) "
               "SELECT %s AS brand_key, %s AS supplier_name, %d AS active_date, NULL::BIGINT AS inactive_date, "
               "count(*) AS source_rows, list_distinct(list(_source)) AS source_list "
               "FROM b WHERE CAST(%s AS VARCHAR)<>'' GROUP BY %s, %s"
               % (dim_uri, keys["brand"], scol, built_at, scol, keys["brand"], scol))
        rdst = warehouse.uri("dim_supplier").replace("'", "")
        con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (sql, rdst))
        n = con.execute("SELECT count(*) FROM read_parquet('%s')" % rdst).fetchone()[0]
        out["supplier"] = {"rows": n, "uri": warehouse.uri("dim_supplier")}
        log("dim_supplier: %d brand↔supplier assocs" % n)
    # vintage AUX — sku↔vintage association (same shape as supplier). A SKU is the individual BOTTLE
    # (product+size+pack+upc); vintages do NOT split it — they're tracked here so "2020 vs 2021 Caymus 750ml"
    # is queryable without multiplying SKUs. Populated by sources that carry a vintage (e.g. TTB wine_vintage).
    if "vintage" in mnames:
        vcol = derive.col("vintage")
        sql = ("WITH b AS (SELECT * FROM read_parquet('%s')%s) "
               "SELECT %s AS sku_key, CAST(%s AS VARCHAR) AS vintage, %d AS active_date, "
               "count(*) AS source_rows, list_distinct(list(_source)) AS source_list "
               "FROM b WHERE nullif(trim(CAST(%s AS VARCHAR)),'') IS NOT NULL GROUP BY %s, %s"
               % (dim_uri, gate, keys["sku"], vcol, built_at, vcol, keys["sku"], vcol))
        rdst = warehouse.uri("dim_vintage").replace("'", "")
        con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (sql, rdst))
        n = con.execute("SELECT count(*) FROM read_parquet('%s')" % rdst).fetchone()[0]
        out["vintage"] = {"rows": n, "uri": warehouse.uri("dim_vintage")}
        log("dim_vintage: %d sku↔vintage assocs" % n)
    return out


def build(master_fields, mappings_by_ds, entity="product", built_by="SYS", built_at=None, log=print):
    """Compile every source's mappings → UNION → dim_<entity> Parquet in the warehouse, then resolve.
    A broken source (bad expr / missing column) is skipped with a warning rather than failing the whole
    build. built_by/built_at stamp the resolved master rows. Entity-parameterized: the SAME engine
    builds the product master OR the outlet master — only the identity rule (resolve) differs."""
    import time as _t
    built_at = built_at or int(_t.time())
    import warehouse
    con = warehouse.connect()
    # the wide UNION goes to a STAGING table (underscore = hidden from the catalog), NOT dim_<entity> —
    # otherwise the product hierarchy's dim_product grain output would read+overwrite the same Parquet.
    dim = "_stage_%s" % entity
    selects, per_source, warnings = [], [], []
    for ds, rules in mappings_by_ds.items():
        if not any(r.get("master_field") for r in (rules or [])):
            continue
        sel = source_select(ds, rules, master_fields)
        try:
            n = con.execute("SELECT count(*) FROM (%s)" % sel).fetchone()[0]   # validate + count
        except Exception as e:
            warnings.append("%s skipped: %s" % (ds, str(e)[:120])); log(warnings[-1]); continue
        selects.append(sel); per_source.append({"dataset": ds, "rows": n,
                          "mapped_fields": len([r for r in rules if r.get("master_field")])})
        log("%s: %d rows, %d mapped fields" % (ds, n, per_source[-1]["mapped_fields"]))
    if not selects:
        return {"rows": 0, "sources": 0, "per_source": [], "warnings": warnings, "note": "no usable mappings"}
    union = " UNION ALL ".join(selects)
    dst = warehouse.uri(dim).replace("'", "")
    con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (union, dst))
    total = con.execute("SELECT count(*) FROM read_parquet('%s')" % dst).fetchone()[0]
    log("%s: %d rows from %d sources → %s" % (dim, total, len(selects), dst))
    if entity == "product":                                    # SHRED the wide source into the hierarchy
        h = resolve_hierarchy(master_fields, dst, con, built_by=built_by, built_at=built_at, log=log)
        return {"rows": total, "sources": len(selects), "per_source": per_source, "warnings": warnings,
                "uri": warehouse.uri(dim), "gate": h.get("_gate"),
                "hierarchy": {k: v["rows"] for k, v in h.items() if isinstance(v, dict) and "rows" in v},
                "skus": h.get("sku", {}).get("rows"), "products": h.get("product", {}).get("rows")}
    resolved = resolve(master_fields, dst, con, entity=entity, built_by=built_by, built_at=built_at, log=log)
    return {"rows": total, "sources": len(selects), "per_source": per_source, "warnings": warnings,
            "uri": warehouse.uri(dim), "resolved_rows": resolved.get("rows"),
            "products": resolved.get("rows"),  # back-compat alias
            "resolved_uri": resolved.get("uri")}


def resolve(master_fields, dim_uri, con, entity="product", built_by="SYS", built_at=None, log=print):
    """Collapse the per-source dim_<entity> rows into ONE row per real thing → dim_<entity>_resolved.
    Identity key (per ENTITY_IDENTITY): the STRONG key (canonical UPC / outlet source_ref) when present,
    else the normalized NATURAL key (product: brand+name+size_ml; outlet: name+address+zip5). Each row
    keeps a value per master field + which/how many sources contributed + audit columns. Fuzzy geo/name
    refinement is a later pass on top of this deterministic key."""
    import time as _t
    built_at = built_at or int(_t.time())
    import warehouse
    spec = ENTITY_IDENTITY.get(entity, ENTITY_IDENTITY["product"])
    strong, natkeys = spec["strong"], spec["natural"]
    mnames = _mnames(master_fields)
    nk_cols = [c for c in natkeys if c in mnames]
    nk = "||'|'||".join("lower(coalesce(CAST(%s AS VARCHAR),''))" % derive.col(c) for c in nk_cols) or "''"
    if strong in mnames:
        sc = derive.col(strong)
        keyexpr = "CASE WHEN %s IS NOT NULL AND %s<>'' THEN 's:'||%s ELSE 'n:'||%s END" % (sc, sc, sc, nk)
    else:
        keyexpr = "'n:'||%s" % nk
    aggs = [("max(%s) AS %s" % (derive.col(mf), derive.col(mf))) if mf == strong
            else "any_value(%s) AS %s" % (derive.col(mf), derive.col(mf)) for mf in mnames]
    keycol = "%s_key" % entity
    rtable = "dim_%s_resolved" % entity
    rdst = warehouse.uri(rtable).replace("'", "")
    audit = ("%d AS master_created_at, %d AS master_updated_at, %s AS updated_by"
             % (built_at, built_at, derive._sqlstr(built_by)))
    sql = ("WITH b AS (SELECT *, %s AS %s FROM read_parquet('%s')) "
           "SELECT %s, %s, count(*) AS source_rows, count(DISTINCT _source) AS sources, "
           "list_distinct(list(_source)) AS source_list, %s FROM b GROUP BY %s"
           % (keyexpr, keycol, dim_uri, keycol, ", ".join(aggs), audit, keycol))
    con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (sql, rdst))
    n = con.execute("SELECT count(*) FROM read_parquet('%s')" % rdst).fetchone()[0]
    log("%s: %d distinct %ss → %s" % (rtable, n, entity, rdst))
    return {"rows": n, "uri": warehouse.uri(rtable)}


if __name__ == "__main__":
    # compile-level smoke test (no warehouse needed)
    ms = [{"name": "brand"}, {"name": "size_ml"}, {"name": "origin"}]
    maps = {"or_pricing": [{"source_field": "description", "master_field": "brand", "mode": "copy"},
                           {"source_field": "size", "master_field": "size_ml", "post": "size_to_ml"}]}
    sel = source_select.__wrapped__ if hasattr(source_select, "__wrapped__") else None
    print("compiled brand:", derive.compile_rule(maps["or_pricing"][0]))
    print("compiled size_ml:", derive.compile_rule(maps["or_pricing"][1])[:60], "…")
    print("master_apply smoke: OK")
