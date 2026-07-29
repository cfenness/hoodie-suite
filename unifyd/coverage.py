#!/usr/bin/env python3
"""coverage.py — EXPECTED vs LANDED store/item counts per source, keyed to the app's real write path.

The gap this closes (found 2026-07-22): `warehouse.write_accumulate` MERGES — a source's catalog table
only ever GROWS. So a blocked/partial scrape that touches 50 of 40,000 items leaves the table at its old
size and the run still logs `ok`/`current`. Cumulative table size therefore CANNOT tell a full pull from
a fragment, and the collapse monitor (which watches for count DROPS) is structurally blind to it because
accumulate never drops.

What CAN tell them apart: how many distinct items/stores THIS RUN actually touched. The write path knows
that — it has the `records`. So the measurement is emitted BY the real write (`record_write`, called from
`write_accumulate`), landed in the `coverage_log` time-series, and read back here — NOT re-derived by a
parallel script. Everything keys to the app: the counts are the app's own writes, grouped by the run token
`run_sources` stamps into the subprocess env (HOODIE_RUN_TOKEN).

EXPECTED is, in priority order: a registry `expected_items`/`expected_stores` (when the true universe is
known — e.g. ABC FWS ≈13.9k SKUs × ≈133 stores), else a self-calibrating HIGH-WATER-MARK of per-run
touched counts (the most a run ever captured). A run whose touched count falls below `expected * floor`
is a `partial` — the signal that was missing. This module only MEASURES; enforcing a write-time gate on
it is the deliberate next step (a gate can block real landings, so it ships separately).
"""
import os
import time

COVERAGE_TABLE = "coverage_log"                    # time-series: one row per write (append-only partitions)
PARTIAL_FLOOR = float(os.environ.get("COVERAGE_FLOOR", "0.80"))   # touched < expected*floor ⇒ partial

# Column auto-detection — first present wins (case-insensitive). A source overrides via the registry
# (`store_col` / `item_col`) when the guess is wrong. Kept generic on purpose: coverage is domain-agnostic.
_STORE_COLS = ["store", "store_id", "storeid", "store_no", "storeno", "store_val", "store_value",
               "storevalue", "store_number", "location_id", "locationid", "facility", "facility_id",
               "outlet_id", "club", "shop_id", "storeid"]
_ITEM_COLS = ["sku", "upc", "gtin", "product_id", "productid", "item_id", "itemid", "usitemid", "ttbid",
              "url", "product_url", "id"]

COVERAGE_FIELDS = ["table", "source", "ts", "run", "wrote_rows", "wrote_items", "wrote_stores"]

_WRITE_SEQ = [0]        # process-local monotonic counter — keeps rapid same-second writes (e.g. Walmart's
                        # per-term landings) from colliding on one partition file and clobbering each other.


def _lc(cols):
    return {str(c).lower(): c for c in cols}


def _pick(cols, candidates, override=None):
    """Resolve a logical column (store/item) to an actual column name, or None. `override` (registry) wins;
    else the first candidate present (case-insensitive)."""
    lc = _lc(cols)
    if override and str(override).lower() in lc:
        return lc[str(override).lower()]
    for c in candidates:
        if c in lc:
            return lc[c]
    return None


def _source_for_table(table):
    """The registry source that owns `table` (for its store_col/item_col/expected overrides), or None.
    Lazy import so warehouse.py → coverage.py → registry never becomes a hard cycle at import time."""
    try:
        import source_registry as reg
        for s in reg.SOURCES:
            if table in (s.get("tables") or []):
                return s
    except Exception:
        pass
    return None


def _distinct(records, col):
    """Distinct non-empty values of `col` across records — the honest 'how many did this write touch'."""
    seen = set()
    for r in records:
        v = r.get(col) if isinstance(r, dict) else None
        if v is not None and str(v) != "":
            seen.add(str(v))
    return len(seen)


def touched(records, table=None, source=None):
    """Distinct items + stores IN THIS WRITE's records — measured, not counted-from-the-catalog. Returns
    {rows, items, stores, item_col, store_col}. No store column ⇒ national scope (stores=None, not 0, so
    'not applicable' never masquerades as 'zero coverage')."""
    records = [r for r in (records or []) if isinstance(r, dict)]
    cols = list(records[0].keys()) if records else []
    src = source or _source_for_table(table)
    ic = _pick(cols, _ITEM_COLS, (src or {}).get("item_col"))
    sc = _pick(cols, _STORE_COLS, (src or {}).get("store_col"))
    items = _distinct(records, ic) if ic else len(records)        # no id column ⇒ each row is an item
    stores = _distinct(records, sc) if sc else None               # no store column ⇒ national/single scope
    return {"rows": len(records), "items": items, "stores": stores, "item_col": ic, "store_col": sc}


def record_write(table, records, key=None, result=None):
    """Emit ONE coverage row for a write — called from write_accumulate AFTER a successful write, best-effort
    (a failure here must never affect the scrape). Keyed to the run token run_sources stamps, so multi-write
    runs (e.g. Walmart lands per search term) group into one run's coverage."""
    records = [r for r in (records or []) if isinstance(r, dict)]
    if not records:
        return
    src = _source_for_table(table)
    t = touched(records, table=table, source=src)
    row = {"table": table, "source": (src or {}).get("id", ""), "ts": time.time(),   # sub-second: orders runs unambiguously
           "run": os.environ.get("HOODIE_RUN_TOKEN", ""),
           "wrote_rows": t["rows"], "wrote_items": t["items"],
           "wrote_stores": (t["stores"] if t["stores"] is not None else -1)}   # -1 = national (no store dim)
    import warehouse
    _WRITE_SEQ[0] += 1
    part = "%d_%d_%d" % (time.time_ns(), os.getpid(), _WRITE_SEQ[0])   # unique per write — never clobbers a prior row
    warehouse.write_partition(COVERAGE_TABLE, part, [row], fields=COVERAGE_FIELDS)


def _hwm_and_last(table, source_id=None):
    """From coverage_log: the per-run touched totals for one table → (high-water-mark items/stores, last-run
    touched items/stores, last run token/ts). Per-run touched = SUM of that run's writes (a run may write in
    chunks; overlap across chunks is rare and only over-counts, i.e. errs toward NOT crying partial)."""
    import warehouse
    try:
        rows = warehouse.query_parts(COVERAGE_TABLE, "SELECT * FROM t WHERE \"table\" = ?", [table])
    except Exception:
        rows = []
    if not rows:
        return {"hwm_items": 0, "hwm_stores": 0, "last_items": 0, "last_stores": 0, "last_run": None, "last_ts": 0}
    runs = {}                                                     # group by run token (blank ⇒ group by ts)
    for r in rows:
        rk = r.get("run") or ("ts:%s" % r.get("ts"))
        g = runs.setdefault(rk, {"items": 0, "stores": 0, "ts": 0, "nat": True})
        g["items"] += int(r.get("wrote_items") or 0)
        ws = int(r.get("wrote_stores") if r.get("wrote_stores") is not None else -1)
        if ws >= 0:
            g["stores"] += ws
            g["nat"] = False
        g["ts"] = max(g["ts"], float(r.get("ts") or 0))
    hwm_items = max((g["items"] for g in runs.values()), default=0)
    hwm_stores = max((g["stores"] for g in runs.values()), default=0)
    last_rk = max(runs, key=lambda k: runs[k]["ts"])
    last = runs[last_rk]
    return {"hwm_items": hwm_items, "hwm_stores": hwm_stores,
            "last_items": last["items"], "last_stores": (None if last["nat"] else last["stores"]),
            "last_run": last_rk, "last_ts": last["ts"]}


def assess(source):
    """The verdict for one source: EXPECTED vs LANDED (last run's touched) store + item counts, per
    dimension. `source` is a registry dict (or an id). Expected = registry override, else the touched
    high-water-mark. Never invents a store dimension a source doesn't have.

    Returns {source, items:{expected,landed,pct,verdict}, stores:{...}, basis, last_ts}. verdict ∈
    complete | partial | baseline(no history yet) | n/a(dimension absent)."""
    if isinstance(source, str):
        source = _source_for_table_or_id(source)
    sid = (source or {}).get("id", "")
    tables = (source or {}).get("tables") or []
    # aggregate across a source's tables: items sum, stores max (stores are the same universe per table)
    hi = {"hwm_items": 0, "hwm_stores": 0, "last_items": 0, "last_stores": None, "last_ts": 0}
    any_store = False
    for tb in tables:
        h = _hwm_and_last(tb, sid)
        hi["hwm_items"] += h["hwm_items"]
        hi["hwm_stores"] = max(hi["hwm_stores"], h["hwm_stores"])
        hi["last_items"] += h["last_items"]
        if h["last_stores"] is not None:
            any_store = True
            hi["last_stores"] = (hi["last_stores"] or 0) + h["last_stores"] if hi["last_stores"] is not None else h["last_stores"]
        hi["last_ts"] = max(hi["last_ts"], h["last_ts"])
    exp_items = (source or {}).get("expected_items") or hi["hwm_items"] or 0
    exp_stores = (source or {}).get("expected_stores") or hi["hwm_stores"] or 0
    basis = "registry" if ((source or {}).get("expected_items") or (source or {}).get("expected_stores")) else "high-water-mark"

    def _dim(expected, landed, applicable):
        if not applicable:
            return {"expected": None, "landed": None, "pct": None, "verdict": "n/a"}
        if not expected:
            # NOTHING expected and NOTHING landed is not a baseline — it is the absence of any
            # evidence at all, and calling it `baseline` made a source that has never once worked
            # read as a benign first run. Kroger sat at "baseline" through 12 consecutive zero-row
            # runs into a table that does not exist, because a self-calibrating high-water-mark of 0
            # divided by a landed count of 0 looks like a clean start. `baseline` now means what it
            # says: we have landed something and are establishing the mark from it.
            if not landed:
                return {"expected": 0, "landed": 0, "pct": None, "verdict": "unknown"}
            return {"expected": 0, "landed": landed, "pct": None, "verdict": "baseline"}
        pct = round(100.0 * (landed or 0) / expected, 1)
        verdict = "complete" if (landed or 0) >= expected * PARTIAL_FLOOR else "partial"
        return {"expected": expected, "landed": landed or 0, "pct": pct, "verdict": verdict}

    return {"source": sid, "basis": basis, "last_ts": int(hi["last_ts"]),
            "items": _dim(exp_items, hi["last_items"], True),
            "stores": _dim(exp_stores, hi["last_stores"], any_store or bool((source or {}).get("expected_stores")))}


def _source_for_table_or_id(x):
    try:
        import source_registry as reg
        return reg.by_id(x) or _source_for_table(x)
    except Exception:
        return _source_for_table(x)


# ─────────────────────────── self-test (exercises the REAL write path, not a mimic) ───────────────────────────
def _selftest():
    import tempfile, warehouse
    d = tempfile.mkdtemp()
    os.environ["WAREHOUSE_DIR"] = d if hasattr(warehouse, "_LOCAL_DIR") else d
    # point the warehouse at a scratch local dir (local-disk mode; no Tigris)
    warehouse._LOCAL_DIR = d
    os.environ.pop("STATE_BUCKET", None)
    tbl = "cov_selftest_products"
    keyf = lambda r: "%s|%s" % (r.get("sku"), r.get("store"))

    # a FULL pull: 100 skus × 3 stores = 300 rows, run token R1
    os.environ["HOODIE_RUN_TOKEN"] = "R1"
    full = [{"sku": "S%03d" % i, "store": st, "price": 1.0}
            for i in range(100) for st in ("ABC #1", "ABC #2", "ABC #3")]
    warehouse.write_accumulate(tbl, full, key=keyf)
    src = {"id": "cov-test", "tables": [tbl]}                    # no registry entry — auto-detect sku/store
    a1 = assess(src)
    assert a1["items"]["landed"] == 100, a1                      # 100 distinct skus touched
    assert a1["stores"]["landed"] == 3, a1                       # 3 distinct stores touched
    assert a1["items"]["verdict"] == "complete", a1              # first run sets the HWM ⇒ 100/100 = complete

    # a PARTIAL pull next run: blocked after 10 skus × 1 store, run token R2. The catalog table STILL has
    # 300 rows (accumulate never shrinks) — but coverage must catch that this RUN touched almost nothing.
    os.environ["HOODIE_RUN_TOKEN"] = "R2"
    partial = [{"sku": "S%03d" % i, "store": "ABC #1", "price": 1.0} for i in range(10)]
    warehouse.write_accumulate(tbl, partial, key=keyf)
    assert warehouse.row_count(tbl) == 300, warehouse.row_count(tbl)   # table did NOT shrink — the whole point
    a2 = assess(src)
    assert a2["items"]["landed"] == 10, a2                       # last run touched only 10 skus
    assert a2["items"]["expected"] == 100, a2                    # HWM from R1 is the expected
    assert a2["items"]["verdict"] == "partial", a2              # 10/100 = 10% < floor ⇒ PARTIAL (the missing signal)
    assert a2["stores"]["landed"] == 1 and a2["stores"]["verdict"] == "partial", a2   # 1 of 3 stores

    # a national source (no store column) must report stores n/a, never 0/partial
    ntbl = "cov_selftest_national"
    os.environ["HOODIE_RUN_TOKEN"] = "N1"
    warehouse.write_accumulate(ntbl, [{"upc": "U%d" % i} for i in range(50)], key=lambda r: r["upc"])
    an = assess({"id": "nat", "tables": [ntbl]})
    assert an["items"]["landed"] == 50, an
    assert an["stores"]["verdict"] == "n/a", an                  # no store dim — not applicable, not zero
    print("coverage self-test: OK — full pull 100sku×3st baselines the HWM; a partial next run (10sku×1st) "
          "reads PARTIAL though the table stayed 300 rows; national source → stores n/a. Measured via the "
          "REAL write_accumulate path, not a mimic.")


if __name__ == "__main__":
    _selftest()
