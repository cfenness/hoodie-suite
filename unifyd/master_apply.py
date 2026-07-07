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
    Each mapped field is auto-wrapped in its master field's normalizer (e.g. upc → GTIN-14)."""
    import warehouse
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
    return "SELECT %s FROM read_parquet('%s')" % (", ".join(cols), uri)


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


def build(master_fields, mappings_by_ds, log=print):
    """Compile every source's mappings → UNION → dim_product Parquet in the warehouse. A broken source
    (bad expr / missing column) is skipped with a warning rather than failing the whole build."""
    import warehouse
    con = warehouse.connect()
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
    dst = warehouse.uri("dim_product").replace("'", "")
    con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (union, dst))
    total = con.execute("SELECT count(*) FROM read_parquet('%s')" % dst).fetchone()[0]
    log("dim_product: %d rows from %d sources → %s" % (total, len(selects), dst))
    resolved = resolve(master_fields, dst, con, log=log)
    return {"rows": total, "sources": len(selects), "per_source": per_source, "warnings": warnings,
            "uri": warehouse.uri("dim_product"), "products": resolved.get("rows"),
            "resolved_uri": resolved.get("uri")}


def resolve(master_fields, dim_uri, con, log=print):
    """Collapse the per-source dim_product rows into ONE product per SKU → dim_product_resolved.
    Identity key: the canonical UPC (GTIN-14) when present, else normalized brand+product_name+size_ml.
    Each product keeps the first non-null value per field + which/how many sources contributed it."""
    import warehouse
    mnames = _mnames(master_fields)
    namekeys = [c for c in ("brand", "product_name", "size_ml") if c in mnames]
    nk = "||'|'||".join("lower(coalesce(CAST(%s AS VARCHAR),''))" % derive.col(c) for c in namekeys) or "''"
    if "upc" in mnames:
        keyexpr = "CASE WHEN upc IS NOT NULL AND upc<>'' THEN 'u:'||upc ELSE 'n:'||%s END" % nk
    else:
        keyexpr = "'n:'||%s" % nk
    aggs = ["max(upc) AS upc" if mf == "upc" else "any_value(%s) AS %s" % (derive.col(mf), derive.col(mf))
            for mf in mnames]
    rdst = warehouse.uri("dim_product_resolved").replace("'", "")
    sql = ("WITH b AS (SELECT *, %s AS product_key FROM read_parquet('%s')) "
           "SELECT product_key, %s, count(*) AS source_rows, count(DISTINCT _source) AS sources, "
           "list_distinct(list(_source)) AS source_list FROM b GROUP BY product_key"
           % (keyexpr, dim_uri, ", ".join(aggs)))
    con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (sql, rdst))
    n = con.execute("SELECT count(*) FROM read_parquet('%s')" % rdst).fetchone()[0]
    log("dim_product_resolved: %d distinct products → %s" % (n, rdst))
    return {"rows": n, "uri": warehouse.uri("dim_product_resolved")}


if __name__ == "__main__":
    # compile-level smoke test (no warehouse needed)
    ms = [{"name": "brand"}, {"name": "size_ml"}, {"name": "origin"}]
    maps = {"or_pricing": [{"source_field": "description", "master_field": "brand", "mode": "copy"},
                           {"source_field": "size", "master_field": "size_ml", "post": "size_to_ml"}]}
    sel = source_select.__wrapped__ if hasattr(source_select, "__wrapped__") else None
    print("compiled brand:", derive.compile_rule(maps["or_pricing"][0]))
    print("compiled size_ml:", derive.compile_rule(maps["or_pricing"][1])[:60], "…")
    print("master_apply smoke: OK")
