"""cola_cluster.py — scale filing-cluster / dedup layer for TTB COLA (build-time, DuckDB-native).

COLA is a filing system: suppliers file many COLAs per eventual SKU (label iteration + deliberate
decoy variants). The raw ~2.2M COLA count is NOT the number of distinct products — the CLUSTERED
count (~1–1.3M) is. This layer collapses filing noise into product candidates, preserving full
lineage, and slots BETWEEN raw ingestion (Tier 0) and corroboration (cola_tiering.tier → Tier 1).

Why this and not cola_tiering.build_clusters: that clusterer groups on an EXACT normalized key
(brand+fanciful+class+size+holder) — O(n) and correct for identical filings, but it can't merge
fuzzy variants ("Six & Twenty Rsv" vs "Six and Twenty Reserve"). At 2.2M, naive pairwise fuzzy is
2.2M² — infeasible. So: BLOCK first (only compare plausible candidates), then fuzzy-match WITHIN
each block, in DuckDB using native jaro_winkler — never pulling 2.2M rows into Python loops.

  BLOCK  = normalized permit-holder + Class/Type Code + coarse size bucket
           (huge private-label blocks get a brand-prefix sub-key so no block explodes)
  MATCH  = jaro_winkler(brand+fanciful) within block, 3 outcomes:
             >= HIGH  auto-cluster · [MED,HIGH) cluster + flag for spot-check · < MED leave distinct
  OUTPUT = cola_cluster (one row/product candidate) + cola_cluster_membership (ttb_id → cluster,
           with match_confidence + matched_on) — cola_raw (ttb_cola) is never touched.

Runs as a build-time batch (recommended over in-browser — see run_warehouse) and is incrementally
re-runnable (cluster_incremental: new filings block + match only against existing clusters in their
block, not the whole corpus). Reuses cola_tiering's tested normalization via DuckDB Python UDFs.
"""
import os, sys, hashlib
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cola_tiering as ct

# jaro_winkler thresholds (0..1). Tunable; validate on a medium-confidence sample before trusting.
HIGH, MED = 0.92, 0.86
BIG_BLOCK = 400          # blocks larger than this get a brand-prefix sub-key (private-label bottlers)
# Standard bottle tiers (ml). Net contents snap to the nearest tier within tolerance, else 'other',
# so the same product filed at 750ML / 0.75L / 750 ml lands in one bucket but genuinely different
# sizes don't over-merge.
SIZE_TIERS = [50, 100, 187, 200, 250, 355, 375, 500, 700, 720, 750, 1000, 1500, 1750, 3000]

# Logical field → the candidate column names it may appear under in raw/enriched ttb_cola.
FIELDS = {
    "ttbid":  ["TTB ID", "ttbid", "ttb_id"],
    "brand":  ["Brand Name", "brand", "Brand"],
    "fanc":   ["Fanciful Name", "fanciful"],
    "cls":    ["Class/Type Code", "Class/Type", "class_type"],
    "size":   ["Net Contents", "Total Bottle Capacity", "net_contents", "size"],
    "holder": ["Vendor Code", "Permit Number", "permit", "vendor_code"],
    "date":   ["Completed Date", "Approval Date", "completed_date"],
    "status": ["Status", "status"],
}


def size_bucket(size_str):
    """Coarse size tier for blocking — nearest standard bottle size within tolerance, else 'other'."""
    ml = ct._size_ml(size_str)
    if not ml:
        return "other"
    ml = int(ml)
    best = min(SIZE_TIERS, key=lambda t: abs(t - ml))
    return str(best) if abs(best - ml) <= max(5, best * 0.06) else "other"


def _pick(colnames, candidates):
    low = {c.lower(): c for c in colnames}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    return None


# Normalization is done in PURE SQL (DuckDB regexp/date funcs) — not Python UDFs, which need numpy —
# so the whole per-row pass stays in the engine over Parquet with zero extra dependency. These mirror
# cola_tiering._norm / _size_ml / _day so clustering and the downstream tiering agree on identity.
def _norm_sql(e):
    """lower → strip punctuation → collapse spaces → expand abbreviations (matches ct._norm)."""
    base = ("trim(regexp_replace(regexp_replace(lower(CAST(%s AS VARCHAR)), '[^a-z0-9]+', ' ', 'g'),"
            " ' +', ' ', 'g'))" % e)
    expr = "' ' || (%s) || ' '" % base
    for k, v in ct.ABBREV.items():
        expr = "replace(%s, ' %s ', ' %s ')" % (expr, k, v)
    return "trim(%s)" % expr


def _size_sql(e):
    """Net contents → milliliters (BIGINT) — mirrors ct._size_ml (strip spaces, num+unit, convert)."""
    s = "lower(replace(CAST(%s AS VARCHAR), ' ', ''))" % e
    num = "try_cast(regexp_extract(%s, '([0-9.]+)(ml|l|liter|litre|oz|floz|gal)', 1) AS DOUBLE)" % s
    unit = "regexp_extract(%s, '([0-9.]+)(ml|l|liter|litre|oz|floz|gal)', 2)" % s
    return ("CASE WHEN {n} IS NULL OR {n}=0 THEN NULL "
            "WHEN {u} IN ('l','liter','litre') THEN CAST(round({n}*1000) AS BIGINT) "
            "WHEN {u} IN ('oz','floz') THEN CAST(round({n}*29.5735) AS BIGINT) "
            "WHEN {u}='gal' THEN CAST(round({n}*3785.41) AS BIGINT) "
            "ELSE CAST(round({n}) AS BIGINT) END").format(n=num, u=unit)


def _bucket_sql(ml):
    """Coarse size tier: the NEAREST standard bottle within tolerance, else 'other' — mirrors
    size_bucket() exactly. Must pick nearest (not first band that covers it): adjacent tiers like
    720/750 have overlapping 6% bands, and a first-match CASE would mis-bucket 750→720, breaking the
    block key vs the Python path used incrementally."""
    tiers = "[" + ",".join(str(t) for t in SIZE_TIERS) + "]"
    dists = "list_transform(%s, x -> abs(x - %s))" % (tiers, ml)
    near = "%s[list_position(%s, list_min(%s))]" % (tiers, dists, dists)   # 1-indexed; nearest tier
    return ("CASE WHEN {ml} IS NULL THEN 'other' "
            "WHEN abs(({n}) - {ml}) <= greatest(5, ({n}) * 0.06) THEN CAST(({n}) AS VARCHAR) "
            "ELSE 'other' END").format(ml=ml, n=near)


def _day_sql(e):
    """MM/DD/YYYY or YYYY-MM-DD → epoch-day ordinal (BIGINT) for time-gap signals; NULL if unparseable."""
    x = "CAST(%s AS VARCHAR)" % e
    return ("coalesce(try_cast(epoch(try_strptime(%s, '%%m/%%d/%%Y'))/86400 AS BIGINT),"
            " try_cast(epoch(try_strptime(%s, '%%Y-%%m-%%d'))/86400 AS BIGINT))" % (x, x))


def _stage(con, src, colnames):
    """Normalize the source table into a compact staging table (one row per kept COLA), then block."""
    def col(logical):
        c = _pick(colnames, FIELDS[logical])
        return ('"%s"' % c) if c else "''"
    con.execute("""
        CREATE OR REPLACE TEMP TABLE stg AS
        SELECT ttb_id, b, f, cls, {bucket} AS sizeb, size_ml, holder, day, status, brand_raw, fanc_raw
        FROM (
          SELECT CAST({ttbid} AS VARCHAR)            AS ttb_id,
                 {nb}                                 AS b,
                 {nf}                                 AS f,
                 upper(trim(CAST({cls} AS VARCHAR)))  AS cls,
                 {size}                               AS size_ml,
                 {holder}                             AS holder,
                 {day}                                AS day,
                 lower(CAST({status} AS VARCHAR))     AS status,
                 CAST({braw} AS VARCHAR)              AS brand_raw,
                 CAST({fraw} AS VARCHAR)              AS fanc_raw
          FROM {src}
        )
    """.format(ttbid=col("ttbid"), nb=_norm_sql(col("brand")), nf=_norm_sql(col("fanc")),
               cls=col("cls"), size=_size_sql(col("size")), holder=_norm_sql(col("holder")),
               day=_day_sql(col("date")), status=col("status"), braw=col("brand"), fraw=col("fanc"),
               bucket=_bucket_sql("size_ml"), src=src))
    # Cheap pre-filter: drop expired / surrendered / revoked before clustering (mirrors cola_tiering.kept).
    con.execute("DELETE FROM stg WHERE regexp_matches(coalesce(status,''), 'expired|surrendered|revoked')")
    # Block key: holder + class/type + size bucket. Huge blocks (private-label bottlers) get a
    # brand-prefix tertiary key so no single block explodes the within-block self-join.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE blk AS
        WITH b0 AS (SELECT *, holder || '|' || cls || '|' || sizeb AS block0 FROM stg),
             sz AS (SELECT block0, count(*) AS n FROM b0 GROUP BY block0)
        SELECT b0.*, CASE WHEN sz.n > %d THEN block0 || '|' || substr(b0.b, 1, 4) ELSE block0 END AS block
        FROM b0 JOIN sz USING (block0)
    """ % BIG_BLOCK)


def _block_report(con):
    r = con.execute("""
        WITH s AS (SELECT block, count(*) n FROM blk GROUP BY block)
        SELECT count(*) blocks, coalesce(min(n),0) min_n, coalesce(max(n),0) max_n,
               coalesce(median(n),0) med_n, coalesce(sum(CASE WHEN n>1 THEN 1 ELSE 0 END),0) multi
        FROM s
    """).fetchone()
    total = con.execute("SELECT count(*) FROM blk").fetchone()[0]
    return {"records": total, "blocks": r[0], "min_per_block": r[1], "max_per_block": r[2],
            "median_per_block": float(r[3]), "multi_record_blocks": r[4]}


def _pairs(con, floor):
    """Within-block candidate pairs above the medium threshold — the ONLY comparisons done. Because
    they're constrained to a block, this is sum(block_i^2) not N^2, and sub-blocking keeps block_i small."""
    return con.execute("""
        WITH p AS (
          SELECT a.ttb_id AS x, b.ttb_id AS y,
                 jaro_winkler_similarity(a.b || ' ' || a.f, b.b || ' ' || b.f) AS sim,
                 abs(coalesce(a.day, 0) - coalesce(b.day, 0)) AS daygap
          FROM blk a JOIN blk b ON a.block = b.block AND a.ttb_id < b.ttb_id
        )
        SELECT x, y, sim, daygap FROM p WHERE sim >= ?
    """, [floor]).fetchall()


class _UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb: self.p[ra] = rb


def _assemble(con, pairs):
    """Union-find the pairs into clusters; build cluster + membership records with 3-tier confidence."""
    uf = _UF()
    best = defaultdict(float)      # ttb_id -> best sim edge linking it (its match_confidence)
    any_med = set()                # ttb_ids touched by a medium (not high) edge
    for x, y, sim, _gap in pairs:
        uf.union(x, y)
        best[x] = max(best[x], sim); best[y] = max(best[y], sim)
        if sim < HIGH:
            any_med.add(x); any_med.add(y)
    # every staged record is at least its own singleton cluster
    rows = con.execute("SELECT ttb_id, brand_raw, fanc_raw, cls, size_ml, sizeb, holder, day FROM blk").fetchall()
    comp = defaultdict(list)
    meta = {}
    for tid, braw, fraw, cls, size_ml, sizeb, holder, day in rows:
        comp[uf.find(tid)].append(tid)
        meta[tid] = (braw, fraw, cls, size_ml, sizeb, holder, day)
    clusters, membership = [], []
    for root, ids in comp.items():
        ids_sorted = sorted(ids)
        seed = ids_sorted[0]
        cid = "K" + hashlib.sha1(seed.encode()).hexdigest()[:11]
        days = [meta[i][6] for i in ids if meta[i][6] is not None]
        braw, fraw, cls, size_ml, sizeb, holder, _ = meta[seed]
        flagged = any(i in any_med for i in ids) or len(ids) > 20   # medium edge OR suspiciously large
        confs = [best.get(i, 1.0) if len(ids) > 1 else 1.0 for i in ids]
        clusters.append({
            "cluster_id": cid, "brand": braw, "fanciful": fraw, "class_type": cls,
            "size_ml": size_ml, "size_bucket": sizeb, "supplier": holder or "unknown",
            "member_count": len(ids), "first_day": (min(days) if days else None),
            "last_day": (max(days) if days else None),
            "confidence": round(min(confs), 4), "flagged": flagged, "tier": 0,
        })
        for i in ids:
            single = len(ids) == 1
            mc = 1.0 if single else round(best.get(i, MED), 4)
            membership.append({"ttb_id": i, "cluster_id": cid, "match_confidence": mc,
                               "matched_on": "singleton" if single else
                               ("brand+fanciful+block" if mc >= HIGH else "fuzzy(medium)")})
    return clusters, membership


def _validation(clusters):
    sizes = [c["member_count"] for c in clusters]
    dist = {"1": sum(1 for n in sizes if n == 1),
            "2-5": sum(1 for n in sizes if 2 <= n <= 5),
            "6-20": sum(1 for n in sizes if 6 <= n <= 20),
            "21+": sum(1 for n in sizes if n > 20)}
    return {"clusters": len(clusters), "size_distribution": dist,
            "flagged_for_review": sum(1 for c in clusters if c["flagged"]),
            "largest": max(sizes) if sizes else 0,
            "mean_members": round(sum(sizes) / max(1, len(sizes)), 3)}


def cluster_from_con(con, src="t", colnames=None, log=print):
    """Core: cluster a DuckDB table/view `src` in place. Returns (clusters, membership, report).
    colnames = the source's column list (defaults to introspection)."""
    if colnames is None:
        colnames = [r[1] for r in con.execute("PRAGMA table_info('%s')" % src).fetchall()]
    _stage(con, src, colnames)
    blocks = _block_report(con)
    log("blocking: %(records)s records → %(blocks)s blocks (max %(max_per_block)s/block, median %(median_per_block)s)" % blocks)
    pairs = _pairs(con, MED)
    log("within-block candidate pairs (>= %.2f): %d" % (MED, len(pairs)))
    clusters, membership = _assemble(con, pairs)
    report = {"blocking": blocks, "candidate_pairs": len(pairs), "validation": _validation(clusters)}
    v = report["validation"]
    log("clusters: %d (from %d records) · size dist %s · %d flagged" %
        (v["clusters"], blocks["records"], v["size_distribution"], v["flagged_for_review"]))
    return clusters, membership, report


def run_warehouse(src_name="ttb_cola", log=print, write=True):
    """BUILD-TIME batch: read ttb_cola Parquet from the warehouse, cluster, write cola_cluster +
    cola_cluster_membership Parquet back. This is the recommended execution path — 2.2M rows × fuzzy
    matching is far past what should run in DuckDB-WASM in the browser; dashboard/mdm just QUERY the
    resulting static tables. Re-runnable; for continuous ingestion use cluster_incremental instead."""
    import warehouse
    con = warehouse.connect()          # DuckDB with the Tigris/S3 creds already configured
    con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM read_parquet('%s')" % warehouse.uri(src_name))
    clusters, membership, report = cluster_from_con(con, "t", log=log)
    if write:
        warehouse.write_parquet("cola_cluster", clusters, fields=list(clusters[0].keys()) if clusters else [])
        warehouse.write_parquet("cola_cluster_membership", membership,
                                fields=list(membership[0].keys()) if membership else [])
        log("wrote cola_cluster (%d) + cola_cluster_membership (%d) to the warehouse" %
            (len(clusters), len(membership)))
    return clusters, membership, report


def cluster_incremental(new_rows, existing_clusters, log=print):
    """Incremental: assign NEW COLA filings to existing clusters (or new ones) without re-clustering
    the whole corpus. Each new row is blocked + fuzzy-compared only against existing cluster seeds in
    its own block. Returns (assignments, new_clusters). `existing_clusters` = prior cola_cluster rows.
    Pure-Python (small batch); the full corpus stays in the build-time warehouse tables."""
    import duckdb
    con = duckdb.connect()
    # index existing clusters by block, keyed on their seed brand+fanciful
    con.execute("CREATE TEMP TABLE ex (cluster_id VARCHAR, block VARCHAR, bf VARCHAR)")
    for c in existing_clusters:
        block = "%s|%s|%s" % (ct._norm(c["supplier"]), (c["class_type"] or "").upper().strip(), c.get("size_bucket") or size_bucket(c.get("size_ml", "")))
        bf = ct._norm(c["brand"]) + " " + ct._norm(c.get("fanciful", ""))
        con.execute("INSERT INTO ex VALUES (?,?,?)", [c["cluster_id"], block, bf])
    assignments, new_clusters = [], []
    for r in new_rows:
        if not ct.kept(r):
            continue
        block = "%s|%s|%s" % (ct._norm(ct._holder(r)), (ct._cls(r) or "").upper().strip(), size_bucket(ct._size(r)))
        bf = ct._norm(ct._brand(r)) + " " + ct._norm(ct._fanc(r))
        hit = con.execute(
            "SELECT cluster_id, jaro_winkler_similarity(bf, ?) s FROM ex WHERE block=? ORDER BY s DESC LIMIT 1",
            [bf, block]).fetchone()
        tid = ct._ttbid(r)
        if hit and hit[1] >= MED:
            assignments.append({"ttb_id": tid, "cluster_id": hit[0], "match_confidence": round(hit[1], 4),
                                "matched_on": "brand+fanciful+block" if hit[1] >= HIGH else "fuzzy(medium)"})
        else:
            cid = "K" + hashlib.sha1(str(tid).encode()).hexdigest()[:11]
            new_clusters.append({"cluster_id": cid, "brand": ct._brand(r), "fanciful": ct._fanc(r),
                                 "class_type": ct._cls(r), "size_ml": ct._size_ml(ct._size(r)),
                                 "size_bucket": size_bucket(ct._size(r)), "supplier": ct._holder(r) or "unknown",
                                 "member_count": 1, "confidence": 1.0, "flagged": False, "tier": 0})
            assignments.append({"ttb_id": tid, "cluster_id": cid, "match_confidence": 1.0, "matched_on": "new"})
            con.execute("INSERT INTO ex VALUES (?,?,?)", [cid, block, bf])
    log("incremental: %d new filings → %d matched to existing, %d new clusters" %
        (len(new_rows), len(assignments) - len(new_clusters), len(new_clusters)))
    return assignments, new_clusters


def _selftest():
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE t (\"TTB ID\" VARCHAR, \"Brand Name\" VARCHAR, \"Fanciful Name\" VARCHAR, "
                "\"Class/Type Code\" VARCHAR, \"Net Contents\" VARCHAR, \"Vendor Code\" VARCHAR, "
                "\"Completed Date\" VARCHAR, \"Status\" VARCHAR)")
    rows = [
        # 4 fuzzy label-iterations of ONE bourbon (abbrev + punctuation + size-format variants) → 1 cluster
        ("1", "Six and Twenty", "Bourbon Reserve", "BOURBON WHISKY", "750ML", "24008", "01/05/2025", "APPROVED"),
        ("2", "Six & Twenty", "Bourbon Rsv", "BOURBON WHISKY", "750 ML", "24008", "02/10/2025", "APPROVED"),
        ("3", "SIX AND TWENTY", "Bourbon  Reserve", "BOURBON WHISKY", "0.75L", "24008", "03/01/2025", "APPROVED"),
        ("4", "Six and Twenty", "Bourbon Reserve", "BOURBON WHISKY", "750ML", "24008", "03/20/2025", "APPROVED"),
        # same supplier+class+size but a genuinely different product → its OWN cluster (low sim, no merge)
        ("5", "Palmetto Moon", "Carolina Rye", "BOURBON WHISKY", "750ML", "24008", "02/01/2025", "APPROVED"),
        # a surrendered filing → pre-filtered out entirely
        ("6", "Dead Label", "", "VODKA", "1L", "99", "01/01/2025", "SURRENDERED"),
        # different size bucket → different block → distinct even if name matches
        ("7", "Six and Twenty", "Bourbon Reserve", "BOURBON WHISKY", "1.75L", "24008", "01/06/2025", "APPROVED"),
    ]
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?,?,?,?)", rows)
    clusters, membership, report = cluster_from_con(con, "t", log=lambda m: None)
    by = {c["cluster_id"]: c for c in clusters}
    # 6 kept filings (surrendered dropped) → expect: {1,2,3,4} together, {5} alone, {7} alone = 3 clusters
    assert report["blocking"]["records"] == 6, report["blocking"]
    assert len(clusters) == 3, [ (c["brand"], c["member_count"]) for c in clusters ]
    big = max(clusters, key=lambda c: c["member_count"])
    assert big["member_count"] == 4, big                      # 4 fuzzy iterations collapsed
    assert {m["ttb_id"] for m in membership if m["cluster_id"] == big["cluster_id"]} == {"1", "2", "3", "4"}, big
    assert all(c["member_count"] == 1 for c in clusters if c["cluster_id"] != big["cluster_id"]), clusters
    # membership carries a confidence + matched_on for every kept filing, none for the surrendered one
    assert len(membership) == 6 and "6" not in {m["ttb_id"] for m in membership}
    # incremental: a 5th iteration of the bourbon joins the existing cluster, a brand-new product opens one
    newrows = [
        {"TTB ID": "8", "Brand Name": "Six and Twenty", "Fanciful Name": "Bourbon Reserve", "Class/Type Code": "BOURBON WHISKY", "Net Contents": "750ML", "Vendor Code": "24008", "Completed Date": "04/01/2025", "Status": "APPROVED"},
        {"TTB ID": "9", "Brand Name": "Brand New Thing", "Fanciful Name": "Debut", "Class/Type Code": "BOURBON WHISKY", "Net Contents": "750ML", "Vendor Code": "24008", "Completed Date": "04/02/2025", "Status": "APPROVED"},
    ]
    asg, newc = cluster_incremental(newrows, clusters, log=lambda m: None)
    a8 = next(a for a in asg if a["ttb_id"] == "8")
    assert a8["cluster_id"] == big["cluster_id"], (a8, big["cluster_id"])   # joined the existing cluster
    assert len(newc) == 1 and newc[0]["brand"] == "Brand New Thing", newc   # the novel product opened one
    print("cola_cluster self-test: OK —", report["validation"], "| incremental:", len(asg), "assigned,", len(newc), "new")


if __name__ == "__main__":
    if "--warehouse" in sys.argv:
        # Build-time batch: cluster ttb_cola from the warehouse → cola_cluster + membership Parquet.
        src = next((a for a in sys.argv[1:] if not a.startswith("-")), "ttb_cola")
        _, _, rep = run_warehouse(src)
        print("done:", rep["validation"])
    else:
        _selftest()
