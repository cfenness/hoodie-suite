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
    verify   {authority, on:[{field,auth}],       check each row against an AUTHORITY reference (a COLA
              fields:[{field,auth}], id_col}       filing, a registry) — backfill catch-all/empty fields,
                                                 override real conflicts (authority wins), carry the filing
                                                 id as evidence. LEFT JOIN into ONE deduped reference: it
                                                 enriches in place and NEVER changes the row count.
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
    # 'any' (also the default): pick any non-blank value. NOTE this is arbitrary order, not a true
    # 'first' — and authority/frequency ties resolve arbitrarily too; a deterministic tie-break
    # (e.g. add source_rank as a secondary sort) is a follow-up, flagged in MDM_FLOW.md.
    return "any_value(%s) FILTER (WHERE %s)" % (c, nb)


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


# ── verify: check a record against an AUTHORITY reference (a COLA filing, a licence registry) and
# backfill/override from it. The five states, ordered — the CASE falls through them exactly once:
_VERIFY_STATUSES = ("agreed", "backfilled", "overrode", "unmatched", "unverified")


def _blank_expr(qcol):
    """Literally null/empty. The AUTHORITY side is read literally — only a truly missing value is 'no
    value' to verify against; we don't second-guess a registry's own placeholders."""
    return "(%s IS NULL OR CAST(%s AS VARCHAR)='')" % (qcol, qcol)


def _empty_or_catchall(qcol):
    """The UPSTREAM states verify may replace: missing OR a catch-all placeholder ('other', 'misc', …).
    A catch-all is populated-but-empty — exactly the field the authority should fill. (Uses the same
    _CATCHALL vocabulary the profile counts, so 'looks known but isn't' means one thing across the engine.)"""
    return "(%s IS NULL OR CAST(%s AS VARCHAR)='' OR %s)" % (qcol, qcol, _catchall_pred(qcol))


def _verify_case(uv, av, matched, value):
    """One field's verify decision. `matched` is the truthy expr that an authority row joined; `uv`/`av`
    the upstream/authority value exprs. value=True → the surviving VALUE (authority wins on backfill /
    override); value=False → the STATUS label. Fall-through order is load-bearing and mirrors
    _VERIFY_STATUSES: unmatched (no reference) → unverified (authority silent) → backfilled (upstream
    empty/catch-all) → overrode (real disagreement) → agreed. The authority only ever moves a field OFF
    a blank/wrong value; it never overwrites an agreeing one (that would be edit-for-edit's-sake)."""
    same = "lower(trim(CAST(%s AS VARCHAR))) = lower(trim(CAST(%s AS VARCHAR)))" % (uv, av)
    if value:
        return ("CASE WHEN NOT (%s) THEN %s WHEN %s THEN %s WHEN %s THEN %s WHEN NOT (%s) THEN %s ELSE %s END"
                % (matched, uv, _blank_expr(av), uv, _empty_or_catchall(uv), av, same, av, uv))
    return ("CASE WHEN NOT (%s) THEN 'unmatched' WHEN %s THEN 'unverified' WHEN %s THEN 'backfilled' "
            "WHEN NOT (%s) THEN 'overrode' ELSE 'agreed' END"
            % (matched, _blank_expr(av), _empty_or_catchall(uv), same))


def _key_part(spec):
    """One component of an identity key. `spec` is a field name (→ generic `compare_form`) or a
    {field, norm} dict — so the identity NORMALIZER is per-entity config, NOT hardcoded. Alcohol opts
    into `identity_key` (strips vintage/edition); outlets use `compare_form`/`address_core`; another
    domain picks its own. This is the seam that keeps the resolver domain-agnostic."""
    if isinstance(spec, dict):
        field, norm = spec.get("field"), (spec.get("norm") or "compare_form")
    else:
        field, norm = spec, "compare_form"
    # COALESCE to '' so a NULL component doesn't null the WHOLE concatenated key (a missing zip/address
    # would otherwise make `a || b || c` NULL and silently DROP the row from the master). Rows whose key
    # is empty across every component are filtered out at the resolve node instead.
    return "COALESCE(%s, '')" % derive._apply_transform(norm, derive.col(field))


# ── the compiler: a node → the SQL that produces its output rows ──
def _index(flow):
    return {n["id"]: n for n in flow.get("nodes", [])}


def _identity_inner(node, upstream_sql):
    """Add the resolve node's identity key as `_id` to its upstream rows — the ONE basis shared by the
    GROUP BY, the conflict queue, and provenance, so all three compute the identical identity.
    node.remap ([[from_id, to_id], …] — steward match DECISIONS, injected by the server from the
    persisted decision store) is applied HERE, before the GROUP BY, so a hand-matched pair re-groups
    and full survivorship reruns over the merged rows: a decision is a rule that re-materializes,
    never an edit to the output (first law)."""
    ident = node.get("identity") or {}
    strong, natural = ident.get("strong"), (ident.get("natural") or [])
    nat = " || '␟' || ".join(_key_part(k) for k in natural) if natural else "''"
    idx = ("COALESCE(NULLIF(CAST(%s AS VARCHAR), ''), %s)" % (derive.col(strong), nat)) if strong else nat
    remap = [r for r in (node.get("remap") or []) if isinstance(r, (list, tuple)) and len(r) == 2 and r[0] != r[1]]
    if remap:
        whens = " ".join("WHEN %s THEN %s" % (derive._sqlstr(a), derive._sqlstr(b)) for a, b in remap)
        idx = "CASE (%s) %s ELSE (%s) END" % (idx, whens, idx)
    return "SELECT *, %s AS _id FROM (%s)" % (idx, upstream_sql)


def candidates_sql(flow, resolve_node_id, uri_fn, min_sim=0.82, limit=200):
    """Match candidates for the steward's two-pane page: pairs of DISTINCT golden identities that look
    like the same real thing — same block (first non-strong natural key component's normalized value,
    e.g. outlet address_core… actually the LAST component, zip-like, is the cheapest block) with
    similar names. Returns (a_id, b_id, a_label, b_label, sim). Pairs the steward already decided are
    filtered by the CALLER (the decision store lives server-side)."""
    n = _index(flow).get(resolve_node_id)
    if not n or n.get("type") != "resolve":
        raise ValueError("candidates_sql needs a resolve node")
    fields = n.get("fields") or []
    label = fields[0] if fields else "_id"
    lc = derive.col(label)
    golden = compile_sql(flow, resolve_node_id, uri_fn)
    sim = "jaro_winkler_similarity(lower(CAST(a.%s AS VARCHAR)), lower(CAST(b.%s AS VARCHAR)))" % (lc, lc)
    # BLOCK the self-join (review major: a bare golden×golden join is O(n²) similarity evaluations —
    # ~1.25B at 50k goldens). First-character-of-label equality is the entity-generic v1 block: cheap,
    # never hides a pair that agrees on its first letter (Twin/TWIN ✓). Configurable per-entity block
    # keys (zip5, token prefix) are the follow-up — a zip block would hide cross-zip dupes.
    blk = ("substr(lower(trim(CAST(a.%s AS VARCHAR))), 1, 1) = substr(lower(trim(CAST(b.%s AS VARCHAR))), 1, 1)"
           % (lc, lc))
    return ("WITH g AS (%s) "
            "SELECT a._id AS a_id, b._id AS b_id, CAST(a.%s AS VARCHAR) AS a_label, "
            "CAST(b.%s AS VARCHAR) AS b_label, round(%s, 3) AS sim "
            "FROM g a JOIN g b ON a._id < b._id AND %s "
            "WHERE %s >= %f ORDER BY sim DESC LIMIT %d"
            % (golden, lc, lc, sim, blk, sim, float(min_sim), int(limit)))


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

    if t == "verify":
        # Check each row against an AUTHORITY reference and enrich IN PLACE: backfill catch-all/empty
        # fields, override real conflicts (authority wins), carry the filing id as evidence. It is a
        # LEFT JOIN into ONE deduped reference — verify NEVER changes the row count (a fan-out would be
        # silent duplication), and a decision to trust a source is a compiled RULE, not an output edit.
        if not ins:
            raise ValueError("verify node %s has no input" % node_id)
        authority = n.get("authority") or n.get("dataset") or ""
        if not authority:
            raise ValueError("verify node %s needs an authority dataset" % node_id)
        on = [p for p in (n.get("on") or []) if p.get("field") and p.get("auth")]
        if not on:
            raise ValueError("verify node %s needs at least one join key (on)" % node_id)
        vfields = [p for p in (n.get("fields") or []) if p.get("field") and p.get("auth")]
        id_col = n.get("id_col") or on[0]["auth"]
        uri = str(uri_fn(authority)).replace("'", "")
        # DEDUPE the authority to one row per join key BEFORE the join — else a reference with N rows for
        # a key multiplies the upstream row N×. row_number()=1 over the join keys is the guard.
        pk = ", ".join(derive.col(p["auth"]) for p in on)
        ref = ("SELECT * FROM read_parquet('%s') QUALIFY row_number() OVER (PARTITION BY %s)=1" % (uri, pk))
        conds = " AND ".join(
            "lower(trim(CAST(u.%s AS VARCHAR))) = lower(trim(CAST(ref.%s AS VARCHAR)))"
            % (derive.col(p["field"]), derive.col(p["auth"])) for p in on)
        matched = "ref.%s IS NOT NULL" % derive.col(id_col)
        sel = ["u.* EXCLUDE (%s)" % ", ".join(derive.col(p["field"]) for p in vfields)] if vfields else ["u.*"]
        for p in vfields:
            uv, av = "u.%s" % derive.col(p["field"]), "ref.%s" % derive.col(p["auth"])
            sel.append("%s AS %s" % (_verify_case(uv, av, matched, True), derive.col(p["field"])))
            sel.append("%s AS %s" % (_verify_case(uv, av, matched, False), derive.col(p["field"] + "__verify")))
        sel.append("CAST(ref.%s AS VARCHAR) AS _verify_ref" % derive.col(id_col))   # the filing id: evidence
        sel.append("%s AS _verify_src" % derive._sqlstr(authority))
        sel.append("%s AS _verify_matched" % matched)
        return "SELECT %s FROM (%s) u LEFT JOIN (%s) ref ON %s" % (", ".join(sel), ins[0], ref, conds)

    if t == "resolve":
        if not ins:
            raise ValueError("resolve node %s has no input" % node_id)
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
        inner = _identity_inner(n, ins[0])
        # keep any row with at least one non-empty key component; drop only all-empty keys (the '␟'
        # separators alone don't count). Never a blanket `_id <> ''`, which would drop partial keys.
        return "SELECT %s FROM (%s) WHERE replace(_id, '␟', '') <> '' GROUP BY _id" % (", ".join(out), inner)

    if t == "output":
        if not ins:
            raise ValueError("output node %s has no input" % node_id)
        return ins[0]                                              # materialization is the endpoint's job

    raise ValueError("unknown node type: %s" % t)


# Catch-all values are "populated-but-empty" — they masquerade as known (worse than NULL, which is
# honestly unknown). We DON'T null them (some are legitimate) — we COUNT them, so a field that's 100%
# 'filled' but mostly 'other' screams in the profile instead of passing completeness. Accuracy first.
_CATCHALL = ("other", "others", "misc", "miscellaneous", "general", "generic", "unspecified",
             "unknown", "undefined", "default", "various", "n/a", "na", "none", "tbd", "null")


def _catchall_pred(qcol):
    return "lower(trim(CAST(%s AS VARCHAR))) IN (%s)" % (qcol, ",".join(derive._sqlstr(v) for v in _CATCHALL))


def profile_sql(inner_sql, columns, sample=0):
    """Per-column fill-rate + distinct cardinality + CATCH-ALL count over any node's output — the profile
    pane. `sample` (rows) caps the scan with TABLESAMPLE so profiling a huge node stays instant.
    'informative' fill is filled minus catch-all — the number that actually means the field is known."""
    src = inner_sql
    if sample and int(sample) > 0:
        src = "SELECT * FROM (%s) USING SAMPLE %d ROWS" % (inner_sql, int(sample))
    parts = ["count(*) AS _n"]
    for c in columns:
        q = derive.col(c)
        parts.append("count(*) FILTER (WHERE CAST(%s AS VARCHAR)<>'') AS %s" % (q, derive.col(c + "†fill")))
        parts.append("count(DISTINCT %s) AS %s" % (q, derive.col(c + "†dct")))
        parts.append("count(*) FILTER (WHERE %s) AS %s" % (_catchall_pred(q), derive.col(c + "†other")))
    return "SELECT %s FROM (%s) q" % (", ".join(parts), src)


def conflict_sql(flow, resolve_node_id, field, uri_fn, limit=100):
    """The stewardship queue for one attribute: identities whose sources disagree, with the competing
    (value, source) pairs. Recompiles the resolve node's INPUT so we see every candidate value."""
    nodes = _index(flow)
    n = nodes.get(resolve_node_id)
    if not n or n.get("type") != "resolve":
        raise ValueError("conflict_sql needs a resolve node")
    upstream = compile_sql(flow, n["inputs"][0], uri_fn) if n.get("inputs") else "SELECT 1"
    c = derive.col(field)
    inner = _identity_inner(n, upstream)
    return ("SELECT _id, "
            "list(DISTINCT CAST(%s AS VARCHAR)) FILTER (WHERE CAST(%s AS VARCHAR)<>'') AS values, "
            "list(DISTINCT {'v': CAST(%s AS VARCHAR), 'src': _source}) FILTER (WHERE CAST(%s AS VARCHAR)<>'') AS pairs, "
            "list(DISTINCT _source) AS sources, count(*) AS rows "
            "FROM (%s) WHERE replace(_id,'␟','')<>'' GROUP BY _id "
            "HAVING count(DISTINCT CAST(%s AS VARCHAR)) FILTER (WHERE CAST(%s AS VARCHAR)<>'') > 1 "
            "ORDER BY rows DESC LIMIT %d" % (c, c, c, c, inner, c, c, int(limit)))


def provenance_sql(flow, resolve_node_id, uri_fn):
    """The upstream (pre-resolve) rows with `_id` attached — filter by one _id to see a golden record's
    constituent SOURCE rows (who supplied what). The evidence behind every mastered value: not 'trust
    me', but 'here are the rows it came from'. Uses the SAME identity as the resolve, so ids line up."""
    n = _index(flow).get(resolve_node_id)
    if not n or n.get("type") != "resolve":
        raise ValueError("provenance_sql needs a resolve node")
    up = compile_sql(flow, n["inputs"][0], uri_fn) if n.get("inputs") else "SELECT 1"
    return _identity_inner(n, up)


def verify_report_sql(flow, verify_node_id, uri_fn):
    """Telemetry over a verify node: rows total, how many MATCHED the authority, and per verified field
    the count in each status (agreed / backfilled / overrode / unmatched / unverified). This is how you
    SEE the check earning its keep — how many catch-all/empty fields the authority filled, how many real
    conflicts it overrode — instead of trusting that it did."""
    n = _index(flow).get(verify_node_id)
    if not n or n.get("type") != "verify":
        raise ValueError("verify_report_sql needs a verify node")
    inner = compile_sql(flow, verify_node_id, uri_fn)
    vfields = [p for p in (n.get("fields") or []) if p.get("field") and p.get("auth")]
    parts = ["count(*) AS _n", "count(*) FILTER (WHERE _verify_matched) AS _matched"]
    for p in vfields:
        vc = derive.col(p["field"] + "__verify")
        for st in _VERIFY_STATUSES:
            parts.append("count(*) FILTER (WHERE %s = %s) AS %s"
                         % (vc, derive._sqlstr(st), derive.col(p["field"] + "†" + st)))
    return "SELECT %s FROM (%s) q" % (", ".join(parts), inner)


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
    "supplier": ["supplier", "applicant", "registrant", "company", "importer", "producer"],
    # OUTLET: outlet_name (the licensed/account name — the more consistently-populated one, used for the
    # identity key) and dba (the trade name customers see) are kept SEPARATE on purpose — cross-source
    # they diverge ("SMITH ENTERPRISES LLC" vs "Tipsy Tavern"), so collapsing them would reject true
    # matches. Map legal-to-legal / dba-to-dba, never across. (Field names match DEFAULT_OUTLET_FIELDS.)
    # outlet_name is the identity/MATCH name — populate it from whatever name a source carries (many give
    # only a trade name, so trade_name lands here). dba is reserved for an EXPLICIT doing-business-as
    # column, kept alongside when a source has both a legal name AND a dba.
    "outlet_name": ["outlet name", "account name", "legal name", "legalname", "licensee", "licensee name",
                    "backer", "owner", "owner name", "registrant", "business name", "establishment",
                    "trade name", "tradename", "store", "location name", "name", "outlet"],
    "dba": ["dba", "d b a", "doing business as"],
    "address": ["address", "street", "addr", "address1", "premise address", "location address",
                "actual address of premises", "street address", "backer address"],
    "city": ["city", "town", "municipality"],
    "state": ["state", "st", "state code"],
    "zip5": ["zip", "zip code", "zipcode", "postal", "postal code", "zip5"],
    "license_num": ["license", "license num", "license number", "licensenum", "permit", "permit number"],
    "county": ["county", "parish"],
}
# Per-entity IDENTITY: the strong key (when present) + the natural-key components, each with the
# NORMALIZER used to build its key part. This is domain config — the resolver itself stays generic.
# Alcohol/product opts into `identity_key` (collapses vintage/edition); outlets use `address_core`
# + `compare_form`; a non-alcohol domain supplies its own entry. Add an entity = add a row here.
_ENTITY_KEYS = {
    "product": [{"field": "brand", "norm": "identity_key"}, {"field": "product_name", "norm": "identity_key"},
                {"field": "size_ml", "norm": "none"}],
    "outlet":  [{"field": "outlet_name", "norm": "compare_form"}, {"field": "address", "norm": "address_core"},
                {"field": "zip5", "norm": "none"}],
    "party":   [{"field": "outlet_name", "norm": "strip_entity_suffix"}],
}
# the fields that decide whether a dataset FEEDS an entity (score = share it can map)
_ENTITY_NATURAL = {e: [k["field"] for k in ks] for e, ks in _ENTITY_KEYS.items()}
_ENTITY_STRONG = {"product": "upc", "outlet": "source_ref", "party": None}


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


# The engine's own OUTPUT tables — never seeding INPUTS. Without this guard the master eats its own
# materialized output (dim_* maps perfectly onto the master schema, by definition) and every rebuild
# double-counts: a self-ingestion feedback loop. Found live when a run's dim_outlet re-entered the seed.
_OUTPUT_PREFIXES = ("dim_", "fact_", "_stage_")


def propose_flow(entity, datasets, master_fields, min_score=0.34):
    """SEED a flow for `entity` from what's landed. `datasets` = [{name, fields:[...]}]. Returns a flow
    dict (input→clean per feeding source → union → resolve→output) — a DRAFT the steward tweaks on the
    canvas. Nothing hidden: every auto-mapped field is a visible, editable clean node. The engine's own
    output tables (dim_/fact_/_stage_) are never proposed as inputs."""
    datasets = [d for d in datasets if not str(d.get("name", "")).startswith(_OUTPUT_PREFIXES)]
    mfields = [f["name"] if isinstance(f, dict) else f for f in master_fields]
    strong = _ENTITY_STRONG.get(entity)
    natural = [k for k in _ENTITY_KEYS.get(entity, []) if k["field"] in mfields]   # {field,norm} specs
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

    # identity NORMALIZER is per-entity (fix: the resolver no longer hardcodes product token-stripping).
    # An outlet-style key uses compare_form and must NOT strip edition tokens; product opts into identity_key.
    ob = {"nodes": [{"id": "i", "type": "input", "dataset": "o", "inputs": []},
                    {"id": "r", "type": "resolve", "inputs": ["i"], "fields": ["name"],
                     "identity": {"strong": None, "natural": [{"field": "name", "norm": "compare_form"}]}}]}
    okey = compile_sql(ob, "r", uf)
    assert "strip_accents" in okey and "gift set" not in okey, okey        # would false-merge "Holiday Wine" before
    pb = {"nodes": [{"id": "i", "type": "input", "dataset": "o", "inputs": []},
                    {"id": "r", "type": "resolve", "inputs": ["i"], "fields": ["brand"],
                     "identity": {"strong": None, "natural": [{"field": "brand", "norm": "identity_key"}]}}]}
    assert "gift set" in compile_sql(pb, "r", uf), "product identity should still strip editions"
    # dba vs outlet_name (legal/account) stay SEPARATE when seeding an outlet (the cross-source match-killer)
    oseed = propose_flow("outlet", [{"name": "fl", "fields": ["DBA", "Owner Name", "Location Address 1", "Zip"]}],
                         ["outlet_name", "dba", "address", "zip5", "source_ref"])
    ofm = {f["out"]: f.get("source_field") for f in {n["id"]: n for n in oseed["nodes"]}["cl_fl"]["fields"]}
    assert ofm.get("dba") == "DBA" and ofm.get("outlet_name") == "Owner Name", ofm
    assert oseed["nodes"][-2]["identity"]["strong"] == "source_ref", oseed["nodes"][-2]["identity"]
    # self-ingestion guard: the engine's own outputs are NEVER proposed as inputs
    noself = propose_flow("outlet", [{"name": "dim_outlet", "fields": ["outlet_name", "address", "zip5"]},
                                     {"name": "fl", "fields": ["DBA", "Owner Name", "Location Address 1", "Zip"]}],
                          ["outlet_name", "dba", "address", "zip5"])
    assert all(n.get("dataset") != "dim_outlet" for n in noself["nodes"] if n["type"] == "input"), noself

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
        # provenance: the golden record's constituent source rows (who supplied what) share the id
        prov = con.execute("SELECT _source FROM (%s) WHERE replace(_id,'␟','')<>''" % provenance_sql(fl, "r", uf2)).fetchall()
        assert len(prov) == 3, prov                                    # 3 source rows → the 1 golden
        cq = con.execute(conflict_sql(fl, "r", "origin", uf2)).fetchall()
        assert cq and sorted(cq[0][1]) == ["A", "B"], cq                # values
        pairs = cq[0][2]                                                # (value, source) pairs — who said what
        assert {p["v"] for p in pairs} == {"A", "B"} and {p["src"] for p in pairs} == {"s1", "s2"}, pairs
        # profile over the resolved node
        pcols = ["brand", "size_ml", "origin"]
        prof = con.execute(profile_sql(compile_sql(fl, "r", uf2), pcols)).fetchall()
        assert prof[0][0] == 1, prof                               # _n = 1 golden record
        # steward match decision: remap re-groups BEFORE the GROUP BY, so survivorship reruns over the
        # merged rows (a decision is a rule, not an output edit). 'Titos' vs a variant spelling:
        import copy
        con.execute("COPY (SELECT * FROM (VALUES ('Titoz','750ml','C')) t(Brand,Size,Origin)) TO '%s/s3.parquet'" % d)
        fl2 = copy.deepcopy(fl)                                     # deep copy of the two-source flow
        next(n2 for n2 in fl2["nodes"] if n2["id"] == "i2")["dataset"] = "s3"   # i2 reads the variant source
        ids = sorted(r[0] for r in con.execute(
            "SELECT _id FROM (%s)" % compile_sql(fl2, "r", uf2)).fetchall())
        assert len(ids) == 2, ids                                   # titos + titoz — unmerged residue
        next(n2 for n2 in fl2["nodes"] if n2["id"] == "r")["remap"] = [[ids[1], ids[0]]]   # steward: same
        merged = con.execute(compile_sql(fl2, "r", uf2)).fetchall()
        mcols = [c[0] for c in con.description]
        mrec = dict(zip(mcols, merged[0]))
        assert len(merged) == 1 and mrec["_rows"] == 3, (merged,)   # re-grouped to ONE golden
        assert mrec["origin"] == "A", mrec                          # authority s1>s2 STILL decides — survivorship reran
        # candidates: titos/titoz similarity surfaces the pair for the steward (query the UNremapped flow)
        fl3 = {"nodes": [dict(n2, remap=[]) if n2.get("id") == "r" else n2 for n2 in fl2["nodes"]]}
        cand = con.execute(candidates_sql(fl3, "r", uf2, min_sim=0.8)).fetchall()
        assert any(c[4] >= 0.8 for c in cand), cand
        # catch-all detection: 'other'/'misc' are populated-but-empty — counted, not accepted as filled
        con.execute("CREATE TABLE ca(cat VARCHAR)")
        con.executemany("INSERT INTO ca VALUES (?)", [("Vodka",), ("Other",), ("OTHER",), ("misc",)])
        pcur = con.execute(profile_sql("SELECT * FROM ca", ["cat"]))
        prec = dict(zip([d[0] for d in pcur.description], pcur.fetchone()))
        assert prec["cat†fill"] == 4 and prec["cat†other"] == 3, prec   # 4 'filled' but only 1 informative
        # verify node: check rows against an AUTHORITY reference (COLA-style filing) and enrich in place —
        # backfill catch-all/empty, override real conflicts, NEVER fan out. Synthetic authority here; real
        # TTB/COLA data isn't landed in the sandbox (TLS-blocked), so live COLA end-to-end is the follow-up.
        con.execute("COPY (SELECT * FROM (VALUES "
                    "('TTB001','Titos','Vodka','USA'),('TTB002','Deep Eddy','Vodka','USA'),"
                    "('TTB001b','Titos','Vodka','USA')"                  # dupe key 'Titos' → must NOT fan out
                    ") t(ttbid,brand,class_type,origin)) TO '%s/cola.parquet'" % d)
        con.execute("COPY (SELECT * FROM (VALUES "
                    "('Titos','Other','Russia'),"        # category catch-all → backfill; origin wrong → override
                    "('Deep Eddy','Vodka',''),"          # category agrees; origin empty → backfill
                    "('Ghost Brand','Other','USA')"      # no authority row → unmatched, untouched
                    ") t(brand,category,origin)) TO '%s/up.parquet'" % d)
        vflow = {"nodes": [
            {"id": "vi", "type": "input", "dataset": "up", "inputs": []},
            {"id": "v", "type": "verify", "inputs": ["vi"], "authority": "cola", "id_col": "ttbid",
             "on": [{"field": "brand", "auth": "brand"}],
             "fields": [{"field": "category", "auth": "class_type"}, {"field": "origin", "auth": "origin"}]}]}
        vrows = con.execute("SELECT * FROM (%s) ORDER BY brand" % compile_sql(vflow, "v", uf2)).fetchall()
        vcols = [c[0] for c in con.description]
        by = {r["brand"]: r for r in (dict(zip(vcols, x)) for x in vrows)}
        assert len(vrows) == 3, vrows                                   # deduped authority → NO fan-out on 'Titos'
        assert by["Deep Eddy"]["category__verify"] == "agreed", by["Deep Eddy"]
        assert by["Deep Eddy"]["origin"] == "USA" and by["Deep Eddy"]["origin__verify"] == "backfilled", by["Deep Eddy"]
        assert by["Titos"]["category"] == "Vodka" and by["Titos"]["category__verify"] == "backfilled", by["Titos"]
        assert by["Titos"]["origin"] == "USA" and by["Titos"]["origin__verify"] == "overrode", by["Titos"]
        assert by["Titos"]["_verify_ref"] in ("TTB001", "TTB001b"), by["Titos"]   # the filing id is the evidence
        assert by["Ghost Brand"]["category__verify"] == "unmatched", by["Ghost Brand"]
        assert by["Ghost Brand"]["category"] == "Other", by["Ghost Brand"]   # first law: never fabricate a match
        assert by["Ghost Brand"]["_verify_ref"] is None, by["Ghost Brand"]
        vr = con.execute(verify_report_sql(vflow, "v", uf2))
        vrec = dict(zip([c[0] for c in vr.description], vr.fetchone()))
        assert vrec["_n"] == 3 and vrec["_matched"] == 2, vrec
        assert vrec["origin†backfilled"] == 1 and vrec["origin†overrode"] == 1 and vrec["origin†unmatched"] == 1, vrec
        assert vrec["category†backfilled"] == 1 and vrec["category†agreed"] == 1, vrec
        print("flow self-test: OK — 3 rows→1 golden; size normalized+agrees; origin conflict A>B by authority; "
              "conflict queue + profile land; catch-all: 4 filled → 3 'other' flagged (1 informative); "
              "verify: authority backfills 'other'→Vodka + overrides wrong origin, deduped→no fan-out, "
              "no-match untouched")
    except ImportError:
        print("flow self-test: OK (compile-only; duckdb not present for eval)")


if __name__ == "__main__":
    _selftest()
