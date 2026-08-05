#!/usr/bin/env python3
"""master_quality.py — PROVE the master, don't assert it (MOAT-PLAN.md Workstream M / M1+M2).

"We have a matching engine" is good-enough; "P/R measured, every gap a number" is best. This builds a
DETERMINISTIC gold set (no human guessing) and scores the master's identity decision against it:

  GOLD (deterministic, authoritative — no LLM/human needed for v1):
    • POSITIVE pairs — two source records sharing an exact normalized UPC. Same UPC ⇒ same item.
      Tests UNDER-merge (recall): does the master put them under one item_key?
    • NEGATIVE pairs — two source records with different normalized brands. Different brand ⇒ different
      item. Tests OVER-merge (precision) — and it is NOT circular even if the matcher uses UPC, because
      the matcher should never merge across brands regardless.

  SCORE: the master's decision = "same item_key in xwalk_source_sku". Precision / Recall / F1 over the
  balanced gold sample, plus COVERAGE (share of gold pairs both sides could be resolved to an item_key).

  BASELINE + REGRESSION: each run lands master_quality and compares to the prior run — P or R dropping
  beyond a tolerance is flagged. The gate is anti-REGRESSION (ratchet up from the measured baseline),
  not an aspirational target the master doesn't meet yet — the honest posture when recall starts low.

Gold pairs land append-only + versioned in `gold_matches` (every future steward decision extends it).
The hard tail (same brand / adjacent size / no UPC) is the next gold expansion — needs adjudication.

    python master_quality.py            # build gold + score, land gold_matches + master_quality
    python master_quality.py --stats
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GOLD_PER_CLASS = int(os.environ.get("MQ_GOLD_PER_CLASS", "4000"))   # target positive & negative pairs
MAX_PAIRS_PER_UPC = int(os.environ.get("MQ_MAX_PAIRS_PER_UPC", "3"))  # cap so a huge UPC group can't dominate
REG_TOL = float(os.environ.get("MQ_REG_TOL", "0.03"))               # P/R may not drop > this vs baseline

CANON_TABLE = "master_quality_canon"   # the SERVED-canon head-to-head, isolated from the item_key ratchet


def _upc(col):
    return "regexp_replace(CAST(%s AS VARCHAR), '[^0-9]', '', 'g')" % col


def _brand(col):
    return "upper(trim(regexp_replace(CAST(%s AS VARCHAR), '\\s+', ' ', 'g')))" % col


def _mount(con):
    import warehouse
    # ONE ACCESSOR — never `<dir>/*.parquet`. The glob corrupts the read at this scale (measured on
    # this exact table: 3,824 partitions / 51.7M rows, reproduced 4/4 — see warehouse.query_parts).
    warehouse.attach_view(con, "retail_observations", view="ro")
    xu = warehouse.uri("xwalk_source_sku").replace("'", "")
    con.execute("CREATE OR REPLACE VIEW xw AS SELECT * FROM read_parquet('%s')" % xu)
    # resolvable source records: distinct (source, product_id) with a valid UPC + brand + an item_key
    con.execute("""
        CREATE OR REPLACE TEMP TABLE prod AS
        WITH d AS (
            SELECT source, product_id,
                   %s AS upc, %s AS brand
            FROM ro
            WHERE upc IS NOT NULL AND length(%s) >= 11
            GROUP BY source, product_id, upc, brand
        )
        SELECT d.source, d.product_id, d.upc, d.brand, x.item_key
        FROM d JOIN xw x ON x.source = d.source AND x.product_id = d.product_id
        WHERE x.item_key IS NOT NULL AND d.brand IS NOT NULL AND trim(d.brand) <> ''
    """ % (_upc("upc"), _brand("brand"), _upc("upc")))


def build(log=print):
    import warehouse
    con = warehouse.connect()
    _mount(con)
    n_prod = con.execute("SELECT COUNT(*) FROM prod").fetchone()[0]

    # POSITIVES: pairs sharing a UPC (capped per UPC). row_number keeps it O(n) per group.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE pos AS
        WITH r AS (SELECT *, row_number() OVER (PARTITION BY upc ORDER BY source, product_id) rn
                   FROM prod),
        pairs AS (
            SELECT a.source sa, a.product_id pa, a.item_key ka, b.source sb, b.product_id pb, b.item_key kb,
                   a.upc, a.brand ba, b.brand bb
            FROM r a JOIN r b ON a.upc = b.upc AND a.rn < b.rn AND b.rn <= a.rn + %d
        )
        SELECT *, 'same' AS label FROM pairs USING SAMPLE %d ROWS
    """ % (MAX_PAIRS_PER_UPC, GOLD_PER_CLASS))
    n_pos = con.execute("SELECT COUNT(*) FROM pos").fetchone()[0]

    # NEGATIVES: random pairs with DIFFERENT brand AND different UPC (definitely different items).
    con.execute("""
        CREATE OR REPLACE TEMP TABLE neg AS
        WITH s AS (SELECT * FROM prod USING SAMPLE %d ROWS),
        t AS (SELECT * FROM prod USING SAMPLE %d ROWS)
        SELECT a.source sa, a.product_id pa, a.item_key ka, b.source sb, b.product_id pb, b.item_key kb,
               a.upc, a.brand ba, b.brand bb, 'diff' AS label
        FROM (SELECT *, row_number() OVER () rn FROM s) a
        JOIN (SELECT *, row_number() OVER () rn FROM t) b ON a.rn = b.rn
        WHERE a.brand <> b.brand AND a.upc <> b.upc
        LIMIT %d
    """ % (GOLD_PER_CLASS * 3, GOLD_PER_CLASS * 3, GOLD_PER_CLASS))
    n_neg = con.execute("SELECT COUNT(*) FROM neg").fetchone()[0]

    # unify → gold with the master's prediction (same item_key?) and the ground-truth label
    con.execute("""
        CREATE OR REPLACE TEMP TABLE gold AS
        SELECT sa, pa, sb, pb, ba, bb, label, (ka = kb) AS pred_same,
               md5(sa||'|'||pa||'|'||sb||'|'||pb) AS pair_id
        FROM (SELECT sa,pa,ka,sb,pb,kb,ba,bb,label FROM pos
              UNION ALL SELECT sa,pa,ka,sb,pb,kb,ba,bb,label FROM neg)
    """)
    prior_for_ver = _last_quality()
    version = max(int(time.time() * 1000),                    # ms; strictly increasing even for back-to-back runs
                  (int(prior_for_ver.get("version") or 0) + 1) if prior_for_ver else 0)
    gold_rows = con.execute("SELECT sa,pa,sb,pb,label,pred_same,pair_id, %d AS version FROM gold" % version).fetch_arrow_table()
    _land_gold(gold_rows)

    # P/R/F1 over the balanced gold
    m = con.execute("""
        SELECT
          SUM(CASE WHEN label='same' AND pred_same THEN 1 ELSE 0 END) tp,
          SUM(CASE WHEN label='diff' AND pred_same THEN 1 ELSE 0 END) fp,
          SUM(CASE WHEN label='same' AND NOT pred_same THEN 1 ELSE 0 END) fn,
          SUM(CASE WHEN label='diff' AND NOT pred_same THEN 1 ELSE 0 END) tn,
          COUNT(*) n
        FROM gold
    """).fetchone()
    tp, fp, fn, tn, n = [int(x or 0) for x in m]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else 0.0
    rec = dict(version=version, ts=version, n_pairs=n, n_pos=n_pos, n_neg=n_neg, n_resolvable=n_prod,
               tp=tp, fp=fp, fn=fn, tn=tn,
               precision=round(precision, 4) if precision is not None else None,
               recall=round(recall, 4) if recall is not None else None, f1=round(f1, 4))

    prior = _last_quality()
    reg = []
    if prior:
        if rec["precision"] is not None and prior.get("precision") is not None \
           and rec["precision"] < prior["precision"] - REG_TOL:
            reg.append("precision %.3f→%.3f" % (prior["precision"], rec["precision"]))
        if rec["recall"] is not None and prior.get("recall") is not None \
           and rec["recall"] < prior["recall"] - REG_TOL:
            reg.append("recall %.3f→%.3f" % (prior["recall"], rec["recall"]))
    rec["regressions"] = "; ".join(reg)
    warehouse.write_accumulate("master_quality", [rec], key=lambda r: r["version"],
                               fields=list(rec.keys()))
    log("[master_quality] P=%.3f R=%.3f F1=%.3f over %d gold pairs (%d pos / %d neg) · TP=%d FP=%d FN=%d TN=%d%s"
        % (rec["precision"] or 0, rec["recall"] or 0, rec["f1"], n, n_pos, n_neg, tp, fp, fn, tn,
           ("  ⚠ REGRESSION: " + rec["regressions"]) if reg else ""))
    if rec["recall"] is not None and rec["recall"] < 0.5:
        log("  ↳ finding: the master UNDER-MERGES — same-UPC records are landing under different "
            "item_keys (recall %.1f%%). Precision %.1f%% (few/no over-merges). This is the baseline to "
            "ratchet up; matching work is now MEASURED, not asserted." % (rec["recall"]*100, (rec["precision"] or 0)*100))
    return rec


def score_canon(log=print):
    """Score the SERVED canon identity on the SAME gold the item_key run built — the head-to-head that
    proves the S4 cutover (MATCHING-CONVERGENCE.md). Reads the latest ``gold_matches`` version and
    re-resolves each pair's two source records to their ``canon_item_id`` via ``item_identity`` (what the
    serving path would join). ``pred_same`` becomes "both resolve in canon AND to the same canon item".

    Fully isolated from ``build()``: it never touches ``master_quality``/``gold_matches`` — it lands
    ``master_quality_canon`` and reuses the existing gold, so the item_key ratchet is untouched and the two
    engines are scored on IDENTICAL pairs. COVERAGE = share of gold pairs canon could resolve (both sides);
    P/R are computed over the covered pairs (same honest posture as the item_key path)."""
    import warehouse
    con = warehouse.connect()
    iu = warehouse.uri("item_identity").replace("'", "")
    try:
        # ONE ACCESSOR — never `<dir>/*.parquet`. This one sits inside the try/except below, so a
        # corrupt read here would degrade QUIETLY to "gold_matches unavailable" and the canon engine
        # would simply not be scored. Found by the read_accessor ratchet, not by anyone reading it.
        if not warehouse.attach_view(con, "gold_matches", view="g"):
            raise RuntimeError("gold_matches has no parts")
        con.execute("CREATE OR REPLACE VIEW ii AS SELECT * FROM read_parquet('%s')" % iu)
        ver = con.execute("SELECT max(version) FROM g").fetchone()[0]
    except Exception as e:
        log("[master_quality/canon] gold_matches or item_identity unavailable (run build() + ingest "
            "the canon export first): %s" % str(e)[:70])
        return None
    if ver is None:
        log("[master_quality/canon] no gold yet — run build() (item_key) first")
        return None

    # resolve BOTH sides of every latest-version gold pair to a canon_item_id (NULL where canon can't reach it)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE goldc AS
        SELECT gm.label,
               CAST(ca.canon_item_id AS VARCHAR) AS ka, CAST(cb.canon_item_id AS VARCHAR) AS kb
        FROM g gm
        LEFT JOIN ii ca ON ca.source = gm.sa AND ca.product_id = gm.pa
        LEFT JOIN ii cb ON cb.source = gm.sb AND cb.product_id = gm.pb
        WHERE gm.version = ?
    """, [ver])
    n_all = con.execute("SELECT COUNT(*) FROM goldc").fetchone()[0]
    # score only the COVERED pairs (both sides resolved) — coverage reported separately, like item_key
    m = con.execute("""
        SELECT
          SUM(CASE WHEN label='same' AND ka=kb THEN 1 ELSE 0 END) tp,
          SUM(CASE WHEN label='diff' AND ka=kb THEN 1 ELSE 0 END) fp,
          SUM(CASE WHEN label='same' AND ka<>kb THEN 1 ELSE 0 END) fn,
          SUM(CASE WHEN label='diff' AND ka<>kb THEN 1 ELSE 0 END) tn,
          COUNT(*) n_cov
        FROM goldc WHERE ka IS NOT NULL AND kb IS NOT NULL
    """).fetchone()
    tp, fp, fn, tn, n_cov = [int(x or 0) for x in m]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else 0.0
    coverage = (n_cov / n_all) if n_all else None

    prior = _last_quality(CANON_TABLE)
    version = max(int(time.time() * 1000), (int(prior.get("version") or 0) + 1) if prior else 0)
    rec = dict(version=version, ts=version, identity="canon", gold_version=int(ver),
               n_pairs=n_cov, n_all=n_all, tp=tp, fp=fp, fn=fn, tn=tn,
               precision=round(precision, 4) if precision is not None else None,
               recall=round(recall, 4) if recall is not None else None, f1=round(f1, 4),
               coverage=round(coverage, 4) if coverage is not None else None)
    warehouse.write_accumulate(CANON_TABLE, [rec], key=lambda r: r["version"], fields=list(rec.keys()))
    log("[master_quality/canon] SERVED identity: P=%.3f R=%.3f F1=%.3f  coverage=%.3f  over %d covered "
        "pairs (gold v%s)" % (rec["precision"] or 0, rec["recall"] or 0, rec["f1"], rec["coverage"] or 0,
                              n_cov, ver))
    ik = _last_quality()   # the item_key run on (near-)the same gold, for the head-to-head line
    if ik and ik.get("recall") is not None and rec["recall"] is not None:
        log("  ↳ head-to-head vs item_key: R %.3f→%.3f  P %.3f→%.3f (canon is the served identity)"
            % (ik["recall"], rec["recall"], ik.get("precision") or 0, rec["precision"] or 0))
    return rec


def _land_gold(arrow):
    import pyarrow.parquet as pq
    import warehouse
    # append-only: gold grows across runs (every version's pairs kept). write_partition per version.
    ver = arrow.column("version")[0].as_py()
    rel = "gold_matches/v%s" % ver
    if warehouse.remote():
        warehouse._retry(lambda: pq.write_table(arrow, "%s/%s/%s.parquet" % (warehouse._bucket(), warehouse._prefix(), rel),
                                                filesystem=warehouse._s3fs(), compression="zstd"), rel)
    else:
        p = os.path.join(warehouse._LOCAL_DIR, rel + ".parquet")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        pq.write_table(arrow, p, compression="zstd")


def _last_quality(table="master_quality"):
    import warehouse
    try:
        r = warehouse.query(table, "SELECT * FROM t ORDER BY version DESC LIMIT 1")
        return r[0] if r else None
    except Exception:
        return None


def stats(log=print):
    q = _last_quality()
    if not q:
        log("[master_quality] no run yet")
        return
    log("[master_quality] latest: P=%.3f R=%.3f F1=%.3f over %s pairs (resolvable universe %s)%s"
        % (q.get("precision") or 0, q.get("recall") or 0, q.get("f1") or 0, f"{q.get('n_pairs',0):,}",
           f"{q.get('n_resolvable',0):,}", ("  ⚠ " + q["regressions"]) if q.get("regressions") else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure master identity quality vs a deterministic gold set (MOAT M).")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--canon", action="store_true",
                    help="ALSO score the served canon identity (item_identity) on the same gold — the S4 head-to-head")
    a = ap.parse_args(argv)
    build()
    if a.canon:
        score_canon()
    if a.stats:
        stats()
    return 0


if __name__ == "__main__":
    sys.exit(main())
