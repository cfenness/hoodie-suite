"""flow.py — the flow model: a DAG of data-prep steps that compiles to DuckDB SQL.

Tableau-Prep-style visual prep, built on the same idea as derive.py: every step compiles to a
SQL SELECT over Parquet, so the whole flow is ONE nested query DuckDB runs IN PLACE — no per-row
Python, no intermediate copies unless you ask for them. A flow builds a master end to end: land
sources, CLEAN each onto the master schema, UNION them, RESOLVE to golden records (dedup on an
identity key), survive the winning value per attribute + flag CONFLICTS, then OUTPUT a dim_ table.

Node types (each compiles to a SELECT over its input node(s)):

    input    {dataset}                          SELECT * FROM read_parquet('<uri>')  (+ '<ds>' AS _source)
    clean    {source, fields:[{out,rule,normalize}], filters:[expr]}
                                                 project source columns onto the master schema (derive rules)
    union    (many inputs)                       UNION ALL BY NAME of its inputs — stack sources of one master
    resolve  {identity:{strong,natural[]}, fields[], survivors:{field:rule},
              authority[], recency}              one golden row per identity: dedup (redundancy) + per-
                                                 attribute survivorship + <field>__conflict flags
    output   {table}                             passthrough; the endpoint COPYs it to warehouse Parquet

The resolve node is the golden-record loop: REDUNDANCY (rows sharing an identity collapse to one,
carrying `_rows`/`_sources`/`_source_list`) and CONFLICT (when the merged sources disagree on an
attribute, a survivorship RULE picks the winner and `<field>__conflict` marks it for stewardship):

    first      any_value              — keep whatever comes first (the engine's old default)
    authority  arg_max(v, rank)       — trust sources in a ranked order (authority[] best-first)
    frequency  mode(v)                — the value the most sources agree on
    recency    arg_max(v, <date>)     — the freshest source wins (needs recency field)
    longest    arg_max(v, length(v))  — the most complete / least-truncated value
    max|min|sum aggregate a numeric

compile_sql(flow, node, uri_fn)   -> SQL for that node's OUTPUT (what preview/profile/explain read).
profile_sql(inner, cols, sample)  -> per-column fill/distinct profile over any node's output.
conflict_sql(flow, node, field, uri_fn) -> the stewardship queue: identities that disagree on `field`.
propose_flow(entity, datasets, master_fields) -> a SEED flow auto-built from what's already landed.

Pure/stdlib (+ derive, same dir). `python flow.py` self-tests against DuckDB when it is importable.
"""
import re
import derive


# ── survivorship rules → the aggregate that picks the surviving value for one attribute ──
# `c` is the quoted column; `auth` the CASE expr ranking sources; `rec` the recency column.
_NONBLANK = "%s IS NOT NULL AND CAST(%s AS VARCHAR)<>''"


def _survivor_sql(col, rule, auth_expr, recency_col):
    c = derive.col(col)
    nb = _NONBLANK % (c, c)
    rule = (rule or "first").lower()
    if rule == "authority" and auth_expr:
        return "arg_max(%s, %s) FILTER (WHERE %s)" % (c, auth_expr, nb)
    if rule == "frequency":
        return "mode(%s)" % c                                      # mode() already ignores NULLs
    if rule == "recency" and recency_col:
        return "arg_max(%s, %s) FILTER (WHERE %s)" % (c, derive.col(recency_col), nb)
    if rule == "longest":
        return "arg_max(%s, length(CAST(%s AS VARCHAR))) FILTER (WHERE %s)" % (c, c, nb)
    if rule in ("max", "min", "sum"):
        return "%s(try_cast(%s AS DOUBLE))" % (rule, c)
    return "any_value(%s) FILTER (WHERE %s)" % (c, nb)             # 'first' — but skip blanks


def _conflict_sql(col):
    """1 when the merged sources hold >1 distinct non-blank value for this attribute."""
    c = derive.col(col)
    return "count(DISTINCT CAST(%s AS VARCHAR)) FILTER (WHERE CAST(%s AS VARCHAR)<>'') > 1" % (c, c)


def _authority_case(sources):
    """CASE mapping each source to a rank (best-first → highest number), 0 for the rest."""
    if not sources:
        return ""
    whens = " ".join("WHEN %s THEN %d" % (derive._sqlstr(s), len(sources) - i)
                     for i, s in enumerate(sources))
    return "CASE _source %s ELSE 0 END" % whens


# ── the compiler: a node → the SQL that produces its output rows ──
def _index(flow):
    return {n["id"]: n for n in flow.get("nodes", [])}


def compile_sql(flow, node_id, uri_fn, _seen=None):
    """Compile the node `node_id` to the SQL SELECT producing its OUTPUT. `uri_fn(dataset)` returns
    the Parquet URI for an input dataset. Recurses through inputs; raises on cycles / bad refs."""
    _seen = _seen or set()
    if node_id in _seen:
        raise ValueError("flow has a cycle at node %s" % node_id)
    _seen = _seen | {node_id}
    nodes = _index(flow)
    n = nodes.get(node_id)
    if not n:
        raise ValueError("no such node: %s" % node_id)
    t = n.get("type")
    ins = [compile_sql(flow, i, uri_fn, _seen) for i in n.get("inputs", [])]

    if t == "input":
        ds = n.get("dataset") or ""
        uri = str(uri_fn(ds)).replace("'", "")
        return "SELECT *, %s AS _source FROM read_parquet('%s')" % (derive._sqlstr(ds), uri)

    if t == "clean":
        if not ins:
            raise ValueError("clean node %s has no input" % node_id)
        src_label = n.get("source") or ""
        cols = []
        for f in n.get("fields", []):
            out = f.get("out")
            if not out:
                continue
            expr = derive.apply_normalizer(f.get("normalize"), derive.compile_rule(f))
            cols.append("%s AS %s" % (expr, derive.col(out)))
        # carry provenance: prefer an upstream _source, else this node's label
        cols.append("%s AS _source" % (("_source" ) if src_label == "" else derive._sqlstr(src_label)))
        sel = ", ".join(cols) if cols else "*"
        where = ""
        filters = [x for x in (n.get("filters") or []) if str(x).strip()]
        if filters:
            where = " WHERE " + " AND ".join("(%s)" % x for x in filters)
        return "SELECT %s FROM (%s) src%s" % (sel, ins[0], where)

    if t == "union":
        if not ins:
            raise ValueError("union node %s has no inputs" % node_id)
        return " UNION ALL BY NAME ".join("SELECT * FROM (%s)" % s for s in ins)

    if t == "resolve":
        if not ins:
            raise ValueError("resolve node %s has no input" % node_id)
        ident = n.get("identity") or {}
        strong = ident.get("strong")
        natural = ident.get("natural") or []
        nat_expr = " || '␟' || ".join(derive.identity_expr(k) for k in natural) if natural else "''"
        if strong:
            id_expr = "COALESCE(NULLIF(CAST(%s AS VARCHAR), ''), %s)" % (derive.col(strong), nat_expr)
        else:
            id_expr = nat_expr
        auth = _authority_case(n.get("authority") or [])
        rec = n.get("recency")
        survivors = n.get("survivors") or {}
        fields = n.get("fields") or []
        out = ["_id"]
        for f in fields:
            out.append("%s AS %s" % (_survivor_sql(f, survivors.get(f), auth, rec), derive.col(f)))
            out.append("%s AS %s" % (_conflict_sql(f), derive.col(f + "__conflict")))
        out.append("count(*) AS _rows")
        out.append("count(DISTINCT _source) AS _sources")
        out.append("list(DISTINCT _source) AS _source_list")
        inner = "SELECT *, %s AS _id FROM (%s)" % (id_expr, ins[0])
        return "SELECT %s FROM (%s) WHERE _id <> '' GROUP BY _id" % (", ".join(out), inner)

    if t == "output":
        if not ins:
            raise ValueError("output node %s has no input" % node_id)
        return ins[0]                                              # materialization is the endpoint's job

    raise ValueError("unknown node type: %s" % t)


def profile_sql(inner_sql, columns, sample=0):
    """Per-column fill-rate + distinct cardinality over any node's output — the profile pane.
    `sample` (rows) caps the scan with TABLESAMPLE so profiling a huge node stays instant."""
    src = inner_sql
    if sample and int(sample) > 0:
        src = "SELECT * FROM (%s) USING SAMPLE %d ROWS" % (inner_sql, int(sample))
    parts = ["count(*) AS _n"]
    for c in columns:
        q = derive.col(c)
        parts.append("count(*) FILTER (WHERE CAST(%s AS VARCHAR)<>'') AS %s" % (q, derive.col(c + "†fill")))
        parts.append("count(DISTINCT %s) AS %s" % (q, derive.col(c + "†dct")))
    return "SELECT %s FROM (%s) q" % (", ".join(parts), src)


def conflict_sql(flow, resolve_node_id, field, uri_fn, limit=100):
    """The stewardship queue for one attribute: identities whose sources disagree, with the competing
    (value, source) pairs. Recompiles the resolve node's INPUT so we see every candidate value."""
    nodes = _index(flow)
    n = nodes.get(resolve_node_id)
    if not n or n.get("type") != "resolve":
        raise ValueError("conflict_sql needs a resolve node")
    ident = n.get("identity") or {}
    strong, natural = ident.get("strong"), (ident.get("natural") or [])
    nat_expr = " || '␟' || ".join(derive.identity_expr(k) for k in natural) if natural else "''"
    id_expr = ("COALESCE(NULLIF(CAST(%s AS VARCHAR),''), %s)" % (derive.col(strong), nat_expr)) if strong else nat_expr
    upstream = compile_sql(flow, n["inputs"][0], uri_fn) if n.get("inputs") else "SELECT 1"
    c = derive.col(field)
    inner = "SELECT *, %s AS _id FROM (%s)" % (id_expr, upstream)
    return ("SELECT _id, list(DISTINCT CAST(%s AS VARCHAR)) FILTER (WHERE CAST(%s AS VARCHAR)<>'') AS values, "
            "list(DISTINCT _source) AS sources, count(*) AS rows FROM (%s) WHERE _id<>'' GROUP BY _id "
            "HAVING count(DISTINCT CAST(%s AS VARCHAR)) FILTER (WHERE CAST(%s AS VARCHAR)<>'') > 1 "
            "ORDER BY rows DESC LIMIT %d" % (c, c, inner, c, c, int(limit)))


# ── seeding: auto-build a flow for a master from the datasets already landed ──
# canonical synonyms → master field, so a source column auto-maps by name (steward corrects on the canvas)
_SYNONYMS = {
    "brand": ["brand", "brand name", "brandname", "brand_name", "producer brand", "label"],
    "product_name": ["product", "product name", "productname", "fanciful", "fanciful name", "item name",
                     "description", "name", "title"],
    "size_ml": ["size", "net contents", "netcontents", "volume", "pack size", "container"],
    "upc": ["upc", "barcode", "gtin", "ean", "upc code", "item upc"],
    "abv": ["abv", "alcohol", "alc", "proof", "alcohol content"],
    "origin": ["origin", "country", "country of origin", "region", "appellation"],
    "category": ["category", "class", "type", "class type", "class/type", "product class"],
    "supplier": ["supplier", "applicant", "registrant", "company", "importer", "owner", "producer"],
    "name": ["name", "outlet", "outlet name", "business name", "dba", "store", "location name", "licensee"],
    "address": ["address", "street", "addr", "address1", "premise address", "location address"],
    "city": ["city", "town", "municipality"],
    "state": ["state", "st", "state code"],
    "zip5": ["zip", "zip code", "zipcode", "postal", "postal code", "zip5"],
    "license_num": ["license", "license num", "license number", "licensenum", "permit", "permit number"],
    "county": ["county", "parish"],
}
# which master field is the natural identity for each entity (used to score whether a dataset feeds it)
_ENTITY_NATURAL = {"product": ["brand", "product_name", "size_ml"],
                   "outlet":  ["name", "address", "zip5"],
                   "party":   ["supplier"]}
_ENTITY_STRONG = {"product": "upc", "outlet": None, "party": None}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _match_field(col_name, master_fields):
    """Best master field for a source column, by synonym/name match — or None."""
    n = _norm(col_name)
    for mf in master_fields:
        syn = _SYNONYMS.get(mf, [mf])
        if n == _norm(mf) or n in [_norm(s) for s in syn]:
            return mf
    for mf in master_fields:                                       # looser: a synonym token appears in the column
        for s in _SYNONYMS.get(mf, [mf]):
            if _norm(s) and _norm(s) in n:
                return mf
    return None


def auto_map(columns, master_fields):
    """Propose {master_field: source_column} for one dataset's columns. First column to claim a field wins."""
    taken, out = set(), {}
    for col in columns:
        mf = _match_field(col, master_fields)
        if mf and mf not in taken:
            out[mf] = col
            taken.add(mf)
    return out


def score_dataset(columns, entity, master_fields):
    """How well a dataset feeds `entity` — the share of the entity's identity fields it can map."""
    amap = auto_map(columns, master_fields)
    need = _ENTITY_NATURAL.get(entity, [])
    have = sum(1 for k in need if k in amap)
    return have / max(1, len(need)), amap


def propose_flow(entity, datasets, master_fields, min_score=0.34):
    """SEED a flow for `entity` from what's landed. `datasets` = [{name, fields:[...]}]. Returns a flow
    dict (input→clean per feeding source → union → resolve→output) — a DRAFT the steward tweaks on the
    canvas. Nothing hidden: every auto-mapped field is a visible, editable clean node."""
    mfields = [f["name"] if isinstance(f, dict) else f for f in master_fields]
    strong = _ENTITY_STRONG.get(entity)
    natural = [k for k in _ENTITY_NATURAL.get(entity, []) if k in mfields]
    nodes, clean_ids = [], []
    for ds in datasets:
        name = ds.get("name")
        cols = ds.get("fields") or []
        sc, amap = score_dataset(cols, entity, mfields)
        if sc < min_score or not amap:
            continue
        iid, cid = "in_" + name, "cl_" + name
        nodes.append({"id": iid, "type": "input", "name": name, "dataset": name, "inputs": []})
        fields = []
        for mf in mfields:                                        # project the FULL master schema (unmapped → NULL)
            src = amap.get(mf)
            if src:
                fields.append({"out": mf, "source_field": src, "mode": "copy",
                               "normalize": (derive.NORMALIZERS.get(mf) and mf) or None})
            else:
                fields.append({"out": mf, "mode": "expr", "expr": "NULL"})
        nodes.append({"id": cid, "type": "clean", "name": name, "source": name,
                      "inputs": [iid], "fields": fields, "filters": []})
        clean_ids.append(cid)
    if not clean_ids:
        return {"entity": entity, "nodes": [], "note": "no landed dataset scored high enough to feed %s" % entity}
    tail_in = clean_ids
    if len(clean_ids) > 1:
        nodes.append({"id": "union", "type": "union", "name": "Union sources", "inputs": clean_ids})
        tail_in = ["union"]
    nodes.append({"id": "resolve", "type": "resolve", "name": "Resolve → golden", "inputs": tail_in,
                  "identity": {"strong": strong if strong in mfields else None, "natural": natural},
                  "fields": [f for f in mfields if f not in ([strong] if strong else [])],
                  "survivors": {}, "authority": [], "recency": None})
    nodes.append({"id": "output", "type": "output", "name": "dim_%s" % entity, "inputs": ["resolve"],
                  "table": "dim_%s" % entity})
    return {"entity": entity, "output": "dim_%s" % entity, "nodes": nodes}


# ─────────────────────────── self-test ───────────────────────────
def _selftest():
    # pure compile checks (no engine)
    flow = {"nodes": [
        {"id": "a", "type": "input", "dataset": "src_a", "inputs": []},
        {"id": "ca", "type": "clean", "source": "src_a", "inputs": ["a"],
         "fields": [{"out": "brand", "source_field": "Brand", "mode": "copy"},
                    {"out": "size_ml", "source_field": "Size", "post": "size_to_ml"}]},
        {"id": "r", "type": "resolve", "inputs": ["ca"],
         "identity": {"strong": None, "natural": ["brand"]}, "fields": ["brand", "size_ml"],
         "survivors": {"size_ml": "max"}, "authority": ["src_a"]},
        {"id": "o", "type": "output", "inputs": ["r"], "table": "dim_product"}]}
    uf = lambda n: "/tmp/%s.parquet" % n
    sql = compile_sql(flow, "o", uf)
    assert "read_parquet('/tmp/src_a.parquet')" in sql, sql
    assert "GROUP BY _id" in sql and "size_ml__conflict" in sql, sql
    assert "max(try_cast(\"size_ml\" AS DOUBLE))" in sql, sql
    # cycle guard
    cyc = {"nodes": [{"id": "x", "type": "clean", "inputs": ["y"], "fields": []},
                     {"id": "y", "type": "clean", "inputs": ["x"], "fields": []}]}
    try:
        compile_sql(cyc, "x", uf); assert False, "cycle not caught"
    except ValueError as e:
        assert "cycle" in str(e)
    # seeding: propose a product flow, brand+upc auto-map, size maps too
    seed = propose_flow("product", [{"name": "cola", "fields": ["Brand", "Fanciful", "UPC", "Net Contents"]}],
                        ["brand", "product_name", "upc", "size_ml", "abv"])
    ids = {n["id"]: n for n in seed["nodes"]}
    assert "cl_cola" in ids and any(f.get("source_field") == "UPC" for f in ids["cl_cola"]["fields"]), seed
    assert ids["resolve"]["identity"]["strong"] == "upc", seed

    # live check against DuckDB if present: a two-source master with a real conflict
    try:
        import duckdb, os, tempfile
        con = duckdb.connect()
        d = tempfile.mkdtemp()
        con.execute("COPY (SELECT * FROM (VALUES ('Titos','750ML','A'),('Titos','750 ML','A')) t(Brand,Size,Origin)) "
                    "TO '%s/s1.parquet'" % d)
        con.execute("COPY (SELECT * FROM (VALUES ('Titos','750ml','B')) t(Brand,Size,Origin)) "
                    "TO '%s/s2.parquet'" % d)
        fl = {"nodes": [
            {"id": "i1", "type": "input", "dataset": "s1", "inputs": []},
            {"id": "i2", "type": "input", "dataset": "s2", "inputs": []},
            {"id": "c1", "type": "clean", "source": "s1", "inputs": ["i1"],
             "fields": [{"out": "brand", "source_field": "Brand", "mode": "copy"},
                        {"out": "size_ml", "source_field": "Size", "post": "size_to_ml"},
                        {"out": "origin", "source_field": "Origin", "mode": "copy"}]},
            {"id": "c2", "type": "clean", "source": "s2", "inputs": ["i2"],
             "fields": [{"out": "brand", "source_field": "Brand", "mode": "copy"},
                        {"out": "size_ml", "source_field": "Size", "post": "size_to_ml"},
                        {"out": "origin", "source_field": "Origin", "mode": "copy"}]},
            {"id": "u", "type": "union", "inputs": ["c1", "c2"]},
            {"id": "r", "type": "resolve", "inputs": ["u"],
             "identity": {"strong": None, "natural": ["brand"]},
             "fields": ["brand", "size_ml", "origin"],
             "survivors": {"origin": "authority", "size_ml": "max"}, "authority": ["s1", "s2"]}]}
        uf2 = lambda n: "%s/%s.parquet" % (d, n)
        rows = con.execute(compile_sql(fl, "r", uf2)).fetchall()
        cols = [c[0] for c in con.description]
        rec = dict(zip(cols, rows[0]))
        assert len(rows) == 1, rows                                # 3 rows → 1 golden (all brand Titos)
        assert rec["_rows"] == 3 and rec["_sources"] == 2, rec     # redundancy collapsed, provenance kept
        assert rec["size_ml"] == 750, rec                          # all normalize to 750ml — agree
        assert rec["origin"] == "A", rec                           # authority: s1 outranks s2
        assert rec["size_ml__conflict"] is False, rec              # 750==750 after normalize → no conflict
        assert rec["origin__conflict"] is True, rec                # A vs B → conflict flagged for stewardship
        # conflict queue surfaces the competing values
        cq = con.execute(conflict_sql(fl, "r", "origin", uf2)).fetchall()
        assert cq and sorted(cq[0][1]) == ["A", "B"], cq
        # profile over the resolved node
        pcols = ["brand", "size_ml", "origin"]
        prof = con.execute(profile_sql(compile_sql(fl, "r", uf2), pcols)).fetchall()
        assert prof[0][0] == 1, prof                               # _n = 1 golden record
        print("flow self-test: OK — 3 rows→1 golden; size normalized+agrees; origin conflict A>B by authority; "
              "conflict queue + profile land")
    except ImportError:
        print("flow self-test: OK (compile-only; duckdb not present for eval)")


if __name__ == "__main__":
    _selftest()
