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
    # UPC/EAN → canonical GTIN-14 (strip non-digits, left-pad to 14). The 'lowest level of consistency'
    # for a structured product code: UPC-A(12)/EAN-13/GTIN-14 all collapse to one comparable key, from
    # which any downstream format re-renders. Blank→NULL; non-standard lengths kept digits-only.
    "upc_normalize": ("CASE WHEN regexp_replace(CAST(%s AS VARCHAR),'[^0-9]','','g')='' THEN NULL "
                      "WHEN length(regexp_replace(CAST(%s AS VARCHAR),'[^0-9]','','g')) BETWEEN 8 AND 14 "
                      "THEN lpad(regexp_replace(CAST(%s AS VARCHAR),'[^0-9]','','g'),14,'0') "
                      "ELSE regexp_replace(CAST(%s AS VARCHAR),'[^0-9]','','g') END"),
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
    # ── OUTLET normalizers (the 'lowest level of consistency' for outlet fields) ──
    # ZIP+4 comes concatenated from some sources (e.g. AB gives 327143868) — split to 5+4.
    "zip5":       "NULLIF(substr(regexp_replace(CAST(%s AS VARCHAR),'[^0-9]','','g'),1,5),'')",
    "zip4":       ("CASE WHEN length(regexp_replace(CAST(%s AS VARCHAR),'[^0-9]','','g'))>=9 "
                   "THEN substr(regexp_replace(CAST(%s AS VARCHAR),'[^0-9]','','g'),6,4) ELSE NULL END"),
    "state_abbr": "NULLIF(upper(trim(CAST(%s AS VARCHAR))),'')",              # 2-letter state (upper+trim)
    "name_clean": "NULLIF(trim(regexp_replace(upper(CAST(%s AS VARCHAR)),' +',' ','g')),'')",  # canonical name for matching
    # ── VINTAGE / EDITION — captured as sku ATTRIBUTES but STRIPPED from the sku identity key, so a
    # 2017 vs 2018 vintage (or a Christmas edition) collapses onto ONE stable sku instead of forking it.
    "vintage_year": "NULLIF(regexp_extract(CAST(%s AS VARCHAR),'(19[0-9]{2}|20[0-9]{2})'),'')",   # → the vintage attr
    # strip 4-digit years + common edition/seasonal tokens, lower+collapse — used to build the STABLE key
    "identity_key": ("trim(regexp_replace(regexp_replace(lower(CAST(%s AS VARCHAR)),"
                     "'(19[0-9]{2}|20[0-9]{2}|christmas|holiday|seasonal|limited edition|limited|special edition|"
                     "gift set|gift pack|gift|anniversary|collector|commemorative)','','g'),' +',' ','g'))"),
}
# auto-applied normalizers (declared on a master field, not user-picked in the transform dropdown)
_NORMALIZER_ONLY = {"upc_normalize", "zip5", "zip4", "state_abbr", "name_clean", "identity_key"}
TRANSFORM_NAMES = [t for t in TRANSFORMS if t not in _NORMALIZER_ONLY]   # user-selectable (normalizers auto-apply)

# A master field can declare a canonical NORMALIZE format — applied automatically to whatever source
# value is mapped into it (the base data is never touched). This is the master's "lowest level of
# consistency" contract: map anything in, it comes out canonical. normalize name → transform name.
NORMALIZERS = {"upc": "upc_normalize", "gtin": "upc_normalize",
               "zip5": "zip5", "zip4": "zip4", "state": "state_abbr",
               "phone": "digits_only", "name": "name_clean", "vintage": "vintage_year"}


def identity_expr(col_or_expr):
    """SQL that turns a name column into a STABLE identity token — vintage year + edition tokens stripped,
    lower-cased, whitespace-collapsed. Used to build hierarchy keys so vintages/editions don't fork rows."""
    inner = col(col_or_expr) if col_or_expr.replace("_", "").isalnum() else col_or_expr
    return TRANSFORMS["identity_key"] % inner


def apply_normalizer(normalize, inner):
    """Wrap a compiled expression in the master field's canonical normalizer (or return it unchanged)."""
    t = NORMALIZERS.get(normalize or "")
    return _apply_transform(t, inner) if t else inner


def _sqlstr(s):
    return "'" + str(s).replace("'", "''") + "'"


def col(name):
    """A safely-quoted column reference."""
    return '"%s"' % str(name).replace('"', '')


def _apply_transform(name, inner):
    tmpl = TRANSFORMS.get(name or "none", "%s")
    return tmpl.replace("%s", inner) if "%s" in tmpl else tmpl


def dict_case(base, entries, dmode="contains", fallback="original"):
    """Compile a value DICTIONARY — an ordered list of {match, value} — into a DuckDB CASE expression.
    This is how a controlled vocabulary ('cab sauv' -> 'Cabernet Sauvignon') OR a string EXTRACTION
    (pull the varietal out of a free-text Class/Type) becomes one scalable SQL expression.
      dmode    : exact    — whole value equals the match (case-insensitive, trimmed)
                 contains — the match appears anywhere in the value (substring; longer matches win)
                 regex    — the value matches the regex
      fallback : original — rows that match nothing keep the source value (synonym-normalize a column)
                 null     — rows that match nothing become NULL (extract an attribute from free text)
    `base` is a compiled column/expression. Returns a string; safe on empty entries."""
    els = "CAST(%s AS VARCHAR)" % base if (fallback or "original") == "original" else "NULL"
    ordered = sorted(entries, key=lambda e: -len(str(e.get("match", "")))) if dmode == "contains" else entries
    whens = []
    for e in ordered:
        m, v = str(e.get("match", "")), str(e.get("value", ""))
        if m == "":
            continue
        if dmode == "exact":
            cond = "lower(trim(CAST(%s AS VARCHAR)))=%s" % (base, _sqlstr(m.strip().lower()))
        elif dmode == "regex":
            cond = "regexp_matches(CAST(%s AS VARCHAR), %s)" % (base, _sqlstr(m))
        else:  # contains — case-insensitive substring (escape LIKE wildcards in the literal)
            lit = m.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            cond = "CAST(%s AS VARCHAR) ILIKE %s ESCAPE '\\'" % (base, _sqlstr("%" + lit + "%"))
        whens.append("WHEN %s THEN %s" % (cond, _sqlstr(v)))
    return "CASE %s ELSE %s END" % (" ".join(whens), els) if whens else els


def compile_rule(rule):
    """Compile a mapping/derivation rule → a DuckDB SQL expression string. Modes: copy | transform |
    regex | map | dict | expr. Unknown/blank → the raw column. Raises ValueError only on a missing source."""
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
    if mode == "dict":                                    # a named/inline value dictionary (see dict_case)
        return dict_case(base, rule.get("dict_entries") or [],
                         rule.get("dict_mode") or "contains", rule.get("dict_fallback") or "original")
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
    # dict mode: contains (extract, null fallback) orders longer match first; exact (synonym, keep original)
    dc = compile_rule({"source_field": "ct", "mode": "dict", "dict_mode": "contains", "dict_fallback": "null",
                       "dict_entries": [{"match": "cab", "value": "Cabernet Sauvignon"},
                                        {"match": "cabernet sauvignon", "value": "Cabernet Sauvignon"}]})
    assert dc.index("cabernet sauvignon") < dc.index("ILIKE '%cab%'"), dc  # specific before generic
    assert dc.endswith("ELSE NULL END"), dc
    de = compile_rule({"source_field": "ct", "mode": "dict", "dict_mode": "exact",
                       "dict_entries": [{"match": "Cab Sauv", "value": "Cabernet Sauvignon"}]})
    assert "lower(trim(CAST(\"ct\" AS VARCHAR)))='cab sauv'" in de and de.endswith("ELSE CAST(\"ct\" AS VARCHAR) END"), de
    # optional live check against DuckDB if present
    try:
        import duckdb
        con = duckdb.connect()
        con.execute("CREATE TABLE t(size VARCHAR, origin VARCHAR)")
        con.executemany("INSERT INTO t VALUES (?,?)", [("750ML", "FRANCE"), ("1.75L", "USA"), ("", "SPAIN")])
        ml = compile_rule({"source_field": "size", "post": "size_to_ml"})
        oc = compile_rule({"source_field": "origin", "mode": "map", "map": {"FRANCE": "France"}})
        vals = dict((r[1], r[0]) for r in con.execute("SELECT %s ml, size FROM t" % ml).fetchall())
        assert vals.get("750ML") == 750 and vals.get("1.75L") == 1750 and vals.get("") is None, vals
        omap = dict((r[1], r[0]) for r in con.execute("SELECT %s d, origin FROM t" % oc).fetchall())
        assert omap.get("FRANCE") == "France" and omap.get("USA") == "USA", omap   # mapped + passthrough
        # dict extraction: pull the varietal from a free-text class/type, longer match wins
        con.execute("CREATE TABLE ct(x VARCHAR)")
        con.executemany("INSERT INTO ct VALUES (?)", [("CABERNET SAUVIGNON RESERVE",), ("CAB",), ("PINOT NOIR",)])
        dex = dict_case(col("x"), [{"match": "cab", "value": "Cabernet Sauvignon"},
                                   {"match": "cabernet sauvignon", "value": "Cabernet Sauvignon"},
                                   {"match": "pinot noir", "value": "Pinot Noir"}], "contains", "null")
        dv = dict((r[1], r[0]) for r in con.execute("SELECT %s d, x FROM ct" % dex).fetchall())
        assert dv.get("CABERNET SAUVIGNON RESERVE") == "Cabernet Sauvignon" and dv.get("CAB") == "Cabernet Sauvignon" \
            and dv.get("PINOT NOIR") == "Pinot Noir", dv
        print("derive self-test: OK — 6 modes compile; size_to_ml 750ML→750; map FRANCE→France; dict cab→Cabernet Sauvignon")
    except ImportError:
        print("derive self-test: OK (compile-only; duckdb not present for eval)")


if __name__ == "__main__":
    _selftest()
