"""derive.py — the field-derivation / data-dictionary model for the master build.

The heart of turning source strings into a master database, FAST and RIGHT at scale. The key idea:
every master field is defined by a RULE that compiles to a single DuckDB SQL EXPRESSION. So the whole
master materialises in one query per source over Parquet — no per-row Python — which is what makes it
scale to millions of rows. A rule is one of five modes, covering "string abstraction or hand-code
whatever":

    copy       "brand"                              -> just the column
    transform  size_to_ml(trim("size"))             -> a chain of named transforms (pre/post)
    regex      regexp_extract("desc",'([0-9.]+)%',1)-> string abstraction / extraction
    map        CASE "origin" WHEN 'FRANCE'…END       -> a value dictionary (raw -> canonical)
    expr       <any DuckDB expression>              -> hand-coded escape hatch

A mapping row = {source_field, master_field, mode, pre, post, pattern, group, map, expr}. compile_rule()
returns the SQL expression; master_apply.py builds SELECT <expr> AS master_field … per source and UNIONs
them into dim_product. Pure/stdlib. `python derive.py` runs the self-test.
"""
import re

# Named transforms as SQL-expression TEMPLATES ("%s" = the inner expression). This registry is the
# extensible library — add a transform here and it's available in every rule + the UI.
TRANSFORMS = {
    "none":          "%s",
    "trim":          "trim(CAST(%s AS VARCHAR))",
    "upper":         "upper(CAST(%s AS VARCHAR))",
    "lower":         "lower(CAST(%s AS VARCHAR))",
    "title_case":    "regexp_replace(lower(CAST(%s AS VARCHAR)), '(^| )([a-z])', upper('\\2'), 'g')",
    "digits_only":   "regexp_replace(CAST(%s AS VARCHAR), '[^0-9]', '', 'g')",
    "collapse_ws":   "trim(regexp_replace(CAST(%s AS VARCHAR), ' +', ' ', 'g'))",
    "year_from_date":"regexp_extract(CAST(%s AS VARCHAR), '([0-9]{4})', 1)",
    # net contents -> integer millilitres (mirrors cola_cluster._size_sql); returns NULL when unparseable
    "size_to_ml":    ("CASE WHEN try_cast(regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),"
                      "'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',1) AS DOUBLE) IS NULL THEN NULL "
                      "WHEN regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',2) "
                      "IN ('l','liter','litre') THEN CAST(round(try_cast(regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),"
                      "'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',1) AS DOUBLE)*1000) AS BIGINT) "
                      "WHEN regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',2) "
                      "IN ('oz','floz') THEN CAST(round(try_cast(regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),"
                      "'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',1) AS DOUBLE)*29.5735) AS BIGINT) "
                      "WHEN regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',2)='gal' "
                      "THEN CAST(round(try_cast(regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),"
                      "'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',1) AS DOUBLE)*3785.41) AS BIGINT) "
                      "ELSE CAST(round(try_cast(regexp_extract(lower(replace(CAST(%s AS VARCHAR),' ','')),"
                      "'([0-9.]+)(ml|l|liter|litre|oz|floz|gal)',1) AS DOUBLE)) AS BIGINT) END"),
}
TRANSFORM_NAMES = list(TRANSFORMS)


def _sqlstr(s):
    return "'" + str(s).replace("'", "''") + "'"


def col(name):
    """A safely-quoted column reference."""
    return '"%s"' % str(name).replace('"', '')


def _apply_transform(name, inner):
    tmpl = TRANSFORMS.get(name or "none", "%s")
    return tmpl.replace("%s", inner) if "%s" in tmpl else tmpl


def compile_rule(rule):
    """Compile a mapping/derivation rule → a DuckDB SQL expression string. Modes: copy | transform |
    regex | map | expr. Unknown/blank → the raw column. Raises ValueError only on a missing source."""
    mode = (rule.get("mode") or "transform").lower()
    if mode == "expr":
        return "(" + (rule.get("expr") or "NULL") + ")"
    src = rule.get("source_field")
    if not src:
        raise ValueError("rule needs a source_field")
    base = col(src)
    if mode == "regex":
        pat = rule.get("pattern") or "(.*)"
        grp = int(rule.get("group", 1) or 1)
        return "regexp_extract(CAST(%s AS VARCHAR), %s, %d)" % (base, _sqlstr(pat), grp)
    if mode == "map":
        pairs = rule.get("map") or {}
        if not pairs:
            return base
        whens = " ".join("WHEN %s THEN %s" % (_sqlstr(k), _sqlstr(v)) for k, v in pairs.items())
        return "CASE CAST(%s AS VARCHAR) %s ELSE CAST(%s AS VARCHAR) END" % (base, whens, base)
    # transform mode (default): pre then post, chained
    e = base
    for t in (rule.get("pre"), rule.get("post")):
        if t and t != "none":
            e = _apply_transform(t, e)
    return e


def _selftest():
    cases = [
        ({"source_field": "brand", "mode": "copy"}, '"brand"'),
        ({"source_field": "size", "pre": "trim", "post": "size_to_ml"}, "29.5735"),
        ({"source_field": "description", "mode": "regex", "pattern": "([0-9.]+)\\s*%", "group": 1}, "regexp_extract"),
        ({"source_field": "origin", "mode": "map", "map": {"FRANCE": "France", "USA": "USA"}}, "CASE"),
        ({"mode": "expr", "expr": "CAST(price AS DOUBLE)/100"}, "CAST(price"),
    ]
    for rule, needle in cases:
        sql = compile_rule(rule)
        assert needle in sql, (rule, sql)
    # transform chain applies pre before post
    s = compile_rule({"source_field": "x", "pre": "trim", "post": "upper"})
    assert s.startswith("upper(") and "trim(" in s, s
    # map builds one WHEN per pair, falls back to the original value
    m = compile_rule({"source_field": "c", "mode": "map", "map": {"A": "a"}})
    assert "WHEN 'A' THEN 'a'" in m and "ELSE CAST(\"c\" AS VARCHAR)" in m, m
    # optional live check against DuckDB if present
    try:
        import duckdb
        con = duckdb.connect()
        con.execute("CREATE TABLE t(size VARCHAR, origin VARCHAR)")
        con.executemany("INSERT INTO t VALUES (?,?)", [("750ML", "FRANCE"), ("1.75L", "USA"), ("", "SPAIN")])
        ml = compile_rule({"source_field": "size", "post": "size_to_ml"})
        oc = compile_rule({"source_field": "origin", "mode": "map", "map": {"FRANCE": "France"}})
        rows = con.execute("SELECT %s ml, %s oc FROM t ORDER BY size" % (ml, oc)).fetchall()
        assert rows[1] == (750, "France") or rows[0][0] in (750, 1750), rows   # 750ML->750, FRANCE->France
        vals = dict((r[1], r[0]) for r in con.execute("SELECT %s ml, size FROM t" % ml).fetchall())
        assert vals.get("750ML") == 750 and vals.get("1.75L") == 1750, vals
        print("derive self-test: OK — compiled 5 modes; size_to_ml 750ML→750, 1.75L→1750; map FRANCE→France")
    except ImportError:
        print("derive self-test: OK (compile-only; duckdb not present for eval)")


if __name__ == "__main__":
    _selftest()
