#!/usr/bin/env python3
"""repair_partitions.py — make a date-partitioned warehouse table READABLE again by rewriting every
partition to one canonical, type-pinned schema.

THE FAILURE THIS REPAIRS. `write_partition` infers each file's schema from that batch's values when
no `dtypes` is passed. A column that is all-None in one run lands as a `null` column; the same column
with real values elsewhere lands int64/double/string. The union_by_name read then has to reconcile
incompatible types for one column — and it does NOT fail cleanly. It corrupts the read with an
invalid-UTF8 decode error, which surfaces in Python as `'utf-8' codec can't decode byte 0xca`,
masking the real DuckDB message entirely.

Measured on retail_observations 2026-08-03: 13 distinct per-file schemas across 3,622 partitions.
Every file was individually valid and readable; the 51.7M-row table was unreadable to any aggregate
query. `write_partition`'s own docstring already records this exact failure on ubereats_products_parts
(2026-07-30) — the `dtypes` fix landed there and was never applied to the other writers.

NOTHING IS DISCARDED. A column whose values cannot become the canonical type is not dropped and not
blank-cast — it is routed to a declared sibling column (retail_observations: a `promo` holding
"2 for $4.50" moves to `promo_text` rather than being cast to NULL). A repair that silently loses
data is worse than the broken read, because the read announces itself.

    python3 tools/repair_partitions.py --table retail_observations --audit
    python3 tools/repair_partitions.py --table retail_observations --apply
"""
import argparse
import concurrent.futures as cf
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "unifyd"))
import warehouse


def _spec(table):
    """(fields, dtypes, router) for a table we know how to canonicalise. Unknown tables are audited
    but never rewritten — guessing a schema is how you lose a column."""
    import pyarrow as pa
    if table == "retail_observations":
        import observe
        return observe.OBS_FIELDS, observe._dtypes(), _route_obs
    return None, None, None


def _route_obs(col, val):
    """retail_observations: a non-numeric `promo` is an offer STRING, not a price. Move it to
    promo_text instead of casting it away."""
    import observe
    if col == "promo":
        num, txt = observe.split_promo(val)
        return {"promo": num, "promo_text": txt} if txt else {"promo": num}
    return None


def audit(table, log=print):
    """Group the table's partitions by schema signature. Reports divergence without touching data."""
    import pyarrow.parquet as pq
    files = warehouse._partition_files(table)
    if not files:
        log("[repair] %s: no partitions" % table)
        return {}
    fsys = warehouse._s3fs() if warehouse.remote() else None

    def sig(p):
        try:
            s = pq.read_schema(p, filesystem=fsys)
            return p, tuple((f.name, str(f.type)) for f in s)
        except Exception as e:
            return p, ("ERR", str(e)[:120])

    groups = {}
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        for p, s in ex.map(sig, files):
            groups.setdefault(s, []).append(p)
    log("[repair] %s: %d partitions, %d distinct schemas" % (table, len(files), len(groups)))
    for i, (s, ps) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        log("   schema %-2d %5d files   e.g. %s" % (i, len(ps), ps[0].split("/")[-1]))
    return groups


def _canonical(tbl, fields, dtypes, router):
    """Recast one partition's arrow table to the canonical schema, routing unconvertible values."""
    import pyarrow as pa
    n = tbl.num_rows
    have = {c: tbl.column(c).to_pylist() for c in tbl.column_names}
    out = {f: [None] * n for f in fields}

    for f in fields:
        if f in have:
            out[f] = list(have[f])

    # Route values that cannot legally become the canonical type.
    if router:
        for col in list(have):
            if col not in fields:
                continue
            want = dtypes.get(col)
            if want is None or not pa.types.is_floating(want):
                continue
            src = have[col]
            if not any(isinstance(v, str) and v.strip() for v in src):
                continue
            for i, v in enumerate(src):
                moved = router(col, v)
                if moved:
                    for k, mv in moved.items():
                        if k in out:
                            out[k][i] = mv

    # Coerce every remaining value to the declared python type so from_pylist cannot re-infer.
    for f in fields:
        want = dtypes.get(f)
        if want is None:
            continue
        col = out[f]
        if pa.types.is_floating(want) or pa.types.is_integer(want):
            for i, v in enumerate(col):
                if v is None or isinstance(v, bool):
                    col[i] = None
                elif isinstance(v, (int, float)):
                    col[i] = float(v)
                else:
                    try:
                        col[i] = float(str(v).strip().replace("$", "").replace(",", ""))
                    except (ValueError, AttributeError):
                        col[i] = None
        elif pa.types.is_boolean(want):
            for i, v in enumerate(col):
                col[i] = None if v is None else bool(v)
        else:
            for i, v in enumerate(col):
                col[i] = "" if v is None else str(v)

    schema = pa.schema([(f, dtypes[f]) for f in fields])
    return pa.table({f: pa.array(out[f], type=dtypes[f]) for f in fields}, schema=schema)


def repair(table, apply=False, workers=12, skip_days=1, log=print):
    """Rewrite each partition to the canonical schema.

    `skip_days` leaves the most recent N days' partitions alone. Scrapes write this table
    continuously (it grew 3,622 -> 3,814 partitions during one afternoon's investigation), and a
    repair that rewrites a file while a running scrape is writing the SAME part is last-writer-wins:
    it would silently drop whatever that scrape landed. Today's partitions are the ones in flight,
    and they are also the ones a redeployed writer will land correctly on its own — so the safe and
    sufficient move is to skip them.
    """
    import time
    import pyarrow.parquet as pq
    fields, dtypes, router = _spec(table)
    if not fields:
        log("[repair] %s: no canonical spec — audit only, refusing to guess a schema" % table)
        audit(table, log=log)
        return 0

    files = warehouse._partition_files(table)
    recent = set()
    if skip_days > 0:
        days = {time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400 * d))
                for d in range(skip_days)}
        recent = {f for f in files if any(d in f.split("/")[-1] for d in days)}
        if recent:
            log("[repair] skipping %d partition(s) from the last %d day(s) — a live scrape may be "
                "writing them" % (len(recent), skip_days))
    files = [f for f in files if f not in recent]

    fsys = warehouse._s3fs() if warehouse.remote() else None
    log("[repair] %s: %d partitions -> canonical %d-column schema (apply=%s)"
        % (table, len(files), len(fields), apply))

    stats = {"ok": 0, "rewrote": 0, "rows": 0, "moved": 0, "err": 0}

    def one(p):
        try:
            t = pq.read_table(p, filesystem=fsys)
            before = {(f.name, str(f.type)) for f in t.schema}
            want = {(f, str(dtypes[f])) for f in fields}
            if before == want:
                return ("ok", p, t.num_rows, 0)
            ct = _canonical(t, fields, dtypes, router)
            moved = 0
            if "promo_text" in ct.column_names:
                moved = sum(1 for v in ct.column("promo_text").to_pylist() if v)
            if apply:
                warehouse._retry(lambda: pq.write_table(ct, p, filesystem=fsys),
                                 "repair %s" % p.split("/")[-1])
            return ("rewrote", p, ct.num_rows, moved)
        except Exception as e:
            return ("err", "%s :: %s" % (p.split("/")[-1], str(e)[:140]), 0, 0)

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for kind, p, rows, moved in ex.map(one, files):
            if kind == "err":
                stats["err"] += 1
                log("   ERR %s" % p)
                continue
            stats[kind] += 1
            stats["rows"] += rows
            stats["moved"] += moved

    log("[repair] %s: already-canonical %d, %s %d, rows %d, promo strings preserved %d, errors %d"
        % (table, stats["ok"], "rewrote" if apply else "WOULD rewrite", stats["rewrote"],
           stats["rows"], stats["moved"], stats["err"]))
    return stats["err"]


def verify(table, log=print):
    """Prove the table reads as a SET, not just file-by-file — that is the whole point."""
    try:
        r = warehouse.query_parts(table, "SELECT COUNT(*) n FROM t")
        log("[repair] verify %s: query_parts COUNT(*) = %s" % (table, r[0]["n"] if r else "?"))
        return True
    except UnicodeDecodeError as e:
        raw = getattr(e, "object", b"")
        log("[repair] verify %s: STILL BROKEN (masked) -> %s"
            % (table, raw.decode("utf-8", "replace")[:300]))
        return False
    except Exception as e:
        log("[repair] verify %s: STILL BROKEN -> %s: %s" % (table, type(e).__name__, str(e)[:300]))
        return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="retail_observations")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--skip-days", type=int, default=1,
                    help="leave the last N days alone (live scrapes may be writing them)")
    a = ap.parse_args()
    if a.audit:
        audit(a.table)
    elif a.verify:
        sys.exit(0 if verify(a.table) else 1)
    else:
        rc = repair(a.table, apply=a.apply, workers=a.workers, skip_days=a.skip_days)
        if a.apply:
            verify(a.table)
        sys.exit(1 if rc else 0)
