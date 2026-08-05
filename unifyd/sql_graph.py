"""sql_graph.py — what joins to what, and on what, so nobody has to stitch tables by hand.

THE POINT. We already know how these tables relate: the bucketed manifests DECLARE their key columns,
bucketize declares more, and the warehouse has a small set of columns that are identities by
construction (`store_uuid`, `sku`, `gtin14`, `item_key`, …). Making a person rediscover that by reading
schemas is asking them to re-derive something the system already knows. Drop two tables on a canvas and
the join should propose itself.

WHAT IT WILL NOT DO IS GUESS QUIETLY. Every proposed link carries a BASIS and a rank:

    declared   both sides' columns are a table's declared key (manifest key_cols / bucketize) — strongest
    identity   a shared column that is an identity in this warehouse by construction
    shared     the two tables merely share a column name — plausible, unproven, and labelled as such

A `shared` link is a suggestion, not a fact, and the UI says so. Better still, `probe()` MEASURES one:
it samples distinct keys from each side and reports what fraction actually match, so "we think these
join" becomes "94.1% of src_outlets' keys are present in retail_observations". That is the difference
between a join builder you can trust with a number you will put in front of a customer and one you
cannot.

SQL is generated to be efficient by default, and honest about it: only the selected columns (never
`SELECT *` across a join, which is how you accidentally drag a raw_json column through a hash join),
every column qualified, collisions aliased `<table>_<column>`, and a LIMIT that the page states rather
than hides.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROBE_SAMPLE = int(os.environ.get("SQL_PROBE_SAMPLE", "20000"))   # distinct keys sampled per side
DEFAULT_LIMIT = 200

# Columns that are an IDENTITY in this warehouse by construction, not by coincidence of naming. Each is
# a key some pipeline already joins on; this list is a reflection of that, not an invention. Ordered:
# earlier = stronger evidence of a real relationship.
IDENTITY = [
    ("store_uuid",),            # aggregator store id (ubereats / postmates / doordash)
    ("item_uuid",),             # aggregator menu item
    ("source", "store_id"),     # the src_outlets identity — 6 write_accumulate call sites use it
    ("resolved_id",),           # identity-resolved master id
    ("item_key",), ("sku_key",), ("product_key",), ("brand_key",),
    ("gtin14",), ("gtin",), ("upc",),
    ("dist_item_code",),        # distributor item number (VIP / Salsify / Breakthru SAP)
    ("ttbid",),
    ("sku",),
    ("zcta",), ("cbsa",), ("county_fips",),
]

# Columns that share a name everywhere and mean nothing as a join — joining on these produces a
# cross-product wearing a join's clothes.
NEVER_JOIN = {"name", "price", "brand", "category", "url", "image", "size", "state", "city", "zip",
              "description", "status", "date", "day", "ts", "raw_json", "source", "store_name",
              "product_name", "abv", "type", "region", "country", "count", "n", "rows", "id"}


def declared_keys(name):
    """The key columns a table DECLARES, or [] — manifest first (authoritative), then bucketize."""
    try:
        import warehouse
        man = warehouse.read_manifest(name)
        if man and man.get("key_cols"):
            return list(man["key_cols"])
    except Exception:
        pass
    try:
        import bucketize
        ent = bucketize.CATALOGS.get(name)
        if ent:
            return list(ent[0])
    except Exception:
        pass
    return []


def _cols(name):
    import sql_console
    try:
        return [c["name"] for c in sql_console.columns(name)]
    except Exception:
        return []


def links(tables):
    """Every candidate join between the given tables, best first.

    Each is {left, right, on:[[lcol, rcol], …], basis, rank, why} — `why` is the sentence shown to the
    person, because a join they cannot explain is one they should not run.
    """
    cols = {t: set(_cols(t)) for t in tables}
    keys = {t: declared_keys(t) for t in tables}
    out = []
    for i, a in enumerate(tables):
        for b in tables[i + 1:]:
            shared = cols[a] & cols[b]
            if not shared:
                continue
            best = None
            # 1. DECLARED — one side's declared key is entirely present on the other. This is the
            #    system telling us the relationship, not us inferring it.
            for t, other in ((a, b), (b, a)):
                k = keys.get(t) or []
                if k and set(k) <= shared:
                    best = dict(on=[[c, c] for c in k], basis="declared", rank=0,
                                why="%s declares (%s) as its key, and %s carries every one of those columns."
                                    % (t, ", ".join(k), other))
                    break
            # 2. IDENTITY — a shared column that is an identity here by construction.
            if not best:
                for ident in IDENTITY:
                    if set(ident) <= shared:
                        best = dict(on=[[c, c] for c in ident], basis="identity", rank=1,
                                    why="%s is an identity column in this warehouse — both tables carry it."
                                        % (" + ".join(ident)))
                        break
            # 3. SHARED NAME — a suggestion. Named as such, and the generic columns are excluded
            #    because joining on `name` or `price` is a cross-product wearing a join's clothes.
            if not best:
                cand = sorted(c for c in shared if c.lower() not in NEVER_JOIN and not c.startswith("_"))
                if cand:
                    best = dict(on=[[cand[0], cand[0]]], basis="shared", rank=2,
                                why="Both tables have a column called %s. That is a guess from the name "
                                    "alone — verify it before trusting the result." % cand[0])
            if best:
                out.append(dict(best, left=a, right=b))
    return sorted(out, key=lambda x: (x["rank"], x["left"], x["right"]))


def graph(tables):
    """Columns + declared keys + ranked links for a set of tables. One call per canvas change."""
    tables = [t for t in dict.fromkeys(tables) if t]
    import sql_console
    known = sql_console._known()
    tables = [t for t in tables if t in known]
    return {"tables": [{"name": t, "columns": _cols(t), "keys": declared_keys(t)} for t in tables],
            "links": links(tables)}


def probe(left, right, on, sample=None):
    """MEASURE a proposed join instead of asserting it.

    Samples distinct keys from each side and reports how many of the left's are present on the right.
    Bounded on purpose: a full anti-join across two multi-million-row tables is a real query, and this
    runs while someone is dragging boxes around. The number is therefore reported AS a sample —
    `sampled: true` — and the page says so, because "94%" implies a census unless told otherwise.
    """
    import sql_console
    n = min(int(sample or PROBE_SAMPLE), PROBE_SAMPLE)
    lk = " || '|' || ".join('COALESCE(CAST(l."%s" AS VARCHAR), chr(1))' % c for c, _ in on)
    rk = " || '|' || ".join('COALESCE(CAST(r."%s" AS VARCHAR), chr(1))' % c for _, c in on)
    # USING SAMPLE, not LIMIT. A bare LIMIT on Parquet returns the FIRST n rows, which is the first
    # part, which is ONE source — so the "sample" is a single scraper's keys probed against a slice of
    # the other table that may not contain that scraper at all. Observed exactly that: this probe
    # reported "0 of 20000 keys match" for a join that returns rows the moment you run it. A number
    # that confidently contradicts the thing it is describing is worse than no number.
    sql = ("WITH l AS (SELECT DISTINCT %s k FROM \"%s\" l USING SAMPLE %d ROWS), "
           "     r AS (SELECT DISTINCT %s k FROM \"%s\" r USING SAMPLE %d ROWS) "
           "SELECT (SELECT count(*) FROM l) AS left_keys, "
           "       (SELECT count(*) FROM r) AS right_keys, "
           "       (SELECT count(*) FROM l WHERE k IN (SELECT k FROM r)) AS matched"
           % (lk, left, n, rk, right, n))
    res = sql_console.run(sql)
    if not res.get("ok") or not res.get("rows"):
        return {"ok": False, "error": res.get("error") or "probe failed", "sampled": True}
    lkn, rkn, m = res["rows"][0]
    # If either side was SCOPED to recent parts, this measurement is about that slice, not the table.
    # A low match against 60 of 4,319 parts says nothing about the join and everything about the window.
    scoped = [s["table"] for s in (res.get("scopes") or [])]
    note = ("%d of %d sampled %s keys are present in %s" % (m, lkn, left, right)) if lkn \
        else "%s produced no keys to sample" % left
    if scoped:
        note += (" — but %s %s read only a recent slice of its parts, so a low number here is about "
                 "that window, not about whether the join works."
                 % (" and ".join(scoped), "were" if len(scoped) > 1 else "was"))
    return {"ok": True, "sampled": True, "sample_size": n, "left_keys": lkn, "right_keys": rkn,
            "matched": m, "match_pct": round(100.0 * m / lkn, 1) if lkn else None,
            "elapsed_s": res.get("elapsed_s"), "scoped": scoped, "note": note}


def build_sql(spec):
    """Turn a canvas into SQL.

    spec = {tables:[...], select:[{table, column}], joins:[{left,right,on,type}], where, limit}

    Deliberate choices:
      - never `SELECT *` across a join. One `raw_json` column dragged through a hash join is the
        difference between a 2-second answer and a machine that stops responding.
      - every column qualified, and a name that appears on two tables aliased `<table>_<column>`, so
        the result grid never has two columns with the same header and no way to tell them apart.
      - joins ordered so each new table attaches to something already in the FROM — a join graph that
        isn't connected is an error the person can fix, not a cross-product we quietly run.
      - a LIMIT by default, which the page states.
    """
    tables = [t for t in dict.fromkeys(spec.get("tables") or []) if t]
    if not tables:
        return {"ok": False, "error": "no tables"}
    alias, used = {}, set()
    for t in tables:                                  # short, stable, readable aliases
        base = "".join(w[0] for w in re.split(r"[^a-zA-Z0-9]+", t) if w)[:3].lower() or "t"
        a, i = base, 2
        while a in used:
            a, i = "%s%d" % (base, i), i + 1
        used.add(a)
        alias[t] = a

    sel = [s for s in (spec.get("select") or []) if s.get("table") in alias]
    if not sel:
        return {"ok": False, "error": "pick at least one field"}
    seen = {}
    for s in sel:
        seen[s["column"]] = seen.get(s["column"], 0) + 1
    cols = []
    for s in sel:
        q = '%s."%s"' % (alias[s["table"]], s["column"])
        cols.append(q if seen[s["column"]] == 1 else '%s AS "%s_%s"' % (q, s["table"], s["column"]))

    joins = [j for j in (spec.get("joins") or []) if j.get("left") in alias and j.get("right") in alias]
    placed, lines = {tables[0]}, []
    remaining = list(joins)
    progress = True
    while remaining and progress:
        progress = False
        for j in list(remaining):
            l, r = j["left"], j["right"]
            if (l in placed) == (r in placed):
                continue                              # both placed (redundant) or neither (not yet reachable)
            new, old = (r, l) if l in placed else (l, r)
            on = " AND ".join('%s."%s" = %s."%s"' % (
                alias[l], pair[0], alias[r], pair[1]) for pair in (j.get("on") or []))
            if not on:
                remaining.remove(j)
                continue
            lines.append("%s JOIN \"%s\" %s ON %s" % (
                (j.get("type") or "INNER").upper(), new, alias[new], on))
            placed.add(new)
            remaining.remove(j)
            progress = True
    missing = [t for t in tables if t not in placed]
    if missing:
        # Say it rather than emitting a cross-product. An unjoined table multiplies the row count by
        # its whole length, and the result looks like a real answer.
        return {"ok": False, "error": "no join path to: %s — link %s to one of the others first"
                                      % (", ".join(missing), missing[0])}

    sql = "SELECT %s\nFROM \"%s\" %s" % (",\n       ".join(cols), tables[0], alias[tables[0]])
    if lines:
        sql += "\n" + "\n".join(lines)
    if (spec.get("where") or "").strip():
        sql += "\nWHERE %s" % spec["where"].strip()
    lim = spec.get("limit", DEFAULT_LIMIT)
    if lim:
        sql += "\nLIMIT %d" % int(lim)
    return {"ok": True, "sql": sql, "alias": alias}
