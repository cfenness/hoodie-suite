#!/usr/bin/env python3
"""monitor.py — the production data-inspection manifest. ONE trustworthy, fast view of everything in the warehouse:
every source/scrape, the account universe, the master dims — with real row counts, real freshness, and real
pull-health (status / errors / deltas). Built to stay stable and instant at 250M+ rows.

WHY it's fast at scale: it NEVER scans the lake. Counts + schema + freshness come from each Parquet FOOTER
(`warehouse.list_datasets` — O(files), not O(rows)); health/errors/deltas come from the connector run log
(`agent_state/runs.json`) and per-source `*_runs` tables (tiny). The assembled manifest is cached and persisted so
reads are a few KB. Every payload is STAMPED (`as_of`, `live`) so the console can prove what you see is real —
there is no embedded/mock fallback: if the warehouse can't be read, the manifest says so rather than inventing data.
"""
import json
import os
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE = os.path.join(_DIR, "agent_state")
_RUNS_JSON = os.path.join(_STATE, "runs.json")
_CONNECTORS = os.path.join(_STATE, "connectors.json")
_HIST = os.path.join(_STATE, "monitor_hist.json")     # {name: [[ts, rows], ...]} — count time-series for deltas

# ── dataset classification: what KIND of thing is each parquet, so the console groups it meaningfully ──────────
# order matters (first match wins). group → how the console buckets it.
_DERIVED_PREFIX = ("wb_", "img_", "xwalk_", "fact_")
_DERIVED_SUFFIX = ("_cluster", "_coherence", "_matches", "_merges", "_queue", "_vec", "_summary", "_signal")
_DERIVED_EXACT = {"match_dict", "catalog_skus", "hoodie_ids", "source_taxonomy", "price_coherence"}
_GROUPS = [
    ("staging",   lambda n: n.startswith("_")),                      # staging / scratch (hidden by default)
    ("runlog",    lambda n: n.endswith("_runs") or n == "scrape_runs"),
    ("master",    lambda n: n.startswith("dim_")),                   # identity-resolved master dims
    ("conformed", lambda n: n.startswith("src_")),                   # per-grain conformed source rows
    # analytical build OUTPUTS (matching/clustering/crosswalk) — not pulled, not a clean dim
    ("derived",   lambda n: n.startswith(_DERIVED_PREFIX) or n.endswith(_DERIVED_SUFFIX) or n in _DERIVED_EXACT),
    ("accounts",  lambda n: n.endswith("_outlets") or n.endswith("_accounts") or "outlet" in n),
    ("timeseries", lambda n: n in ("retail_observations",) or n.endswith("_observations")),
    ("reference", lambda n: n in ("dim_date",) or n.startswith("ref_") or n.endswith("_logos")),
]


def classify(name):
    for g, test in _GROUPS:
        try:
            if test(name):
                return g
        except Exception:
            pass
    return "scrape"                                                   # everything else = a raw source pull


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _runs_index():
    """Parse the connector run log → newest run per connId AND per extract(dataset). This is the authoritative
    health/error/delta source. Returns (by_dataset, by_conn) where by_dataset[name] = {status, ts, rows, delta,
    degraded, warnings, conn, trigger, duration_ms}."""
    runs = _load_json(_RUNS_JSON, [])
    by_ds, by_conn = {}, {}
    for r in runs if isinstance(runs, list) else []:
        conn = r.get("connId") or r.get("id") or ""
        ts = (r.get("finishedAt") or r.get("startedAt") or 0)
        ts = ts / 1000.0 if ts and ts > 1e12 else ts                 # ms → s
        rec = {"conn": conn, "status": r.get("status") or "", "ts": ts,
               "degraded": bool(r.get("degraded")), "warnings": r.get("warnings") or [],
               "trigger": r.get("trigger") or "", "duration_ms": r.get("durationMs"),
               "healed": r.get("healed") or [], "total": r.get("total")}
        if conn and (conn not in by_conn or ts >= by_conn[conn]["ts"]):
            by_conn[conn] = rec
        for ex in (r.get("extracts") or []):
            ds = ex.get("id")
            if not ds:
                continue
            drec = dict(rec, rows=ex.get("rows"), delta=ex.get("delta"), status=ex.get("status") or rec["status"])
            if ds not in by_ds or ts >= by_ds[ds]["ts"]:
                by_ds[ds] = drec
    return by_ds, by_conn


def _runs_tables_index(datasets):
    """Fallback health for sources logged in a `<x>_runs` PARQUET (not runs.json): abc_runs, specs_runs, etc.
    Maps the run-table's base name (abc_runs → abc*) to a last {status, ts}. Cheap: reads only the tiny run table."""
    import warehouse
    out = {}
    names = {d["name"] for d in datasets}
    for d in datasets:
        n = d["name"]
        if not n.endswith("_runs"):
            continue
        base = n[:-5]                                                # abc_runs → abc
        try:
            rows = warehouse.query(n, "SELECT * FROM t")
        except Exception:
            continue
        if not rows:
            continue
        # newest by any timestamp-ish column
        tcol = next((c for c in ("at", "finished_at", "ts", "run_at", "started_at") if c in rows[0]), None)
        newest = max(rows, key=lambda r: float(r.get(tcol) or 0)) if tcol else rows[-1]
        ts = float(newest.get(tcol) or 0) if tcol else 0
        ts = ts / 1000.0 if ts > 1e12 else ts
        cnt = next((newest[c] for c in ("cells", "rows", "skus", "total", "records") if c in newest), None)
        out[base] = {"status": newest.get("status") or "", "ts": ts, "rows": cnt,
                     "note": newest.get("note") or "", "conn": base}
    return out


def _match_run(name, run_ds, run_conn, run_tables):
    """Best health record for a dataset. STRICT to preserve trust — a wrong attribution is as bad as a wrong
    status. Order: exact extract-id match (authoritative) → `<base>_runs` table whose base prefixes the name →
    connId that prefixes the name. Never a bare substring-anywhere match (that mis-attributes)."""
    if name in run_ds:                                   # exact: the run log listed this dataset as an extract
        return run_ds[name]
    for base, rec in sorted(run_tables.items(), key=lambda kv: -len(kv[0])):   # longest base first
        if name == base or name.startswith(base + "_"):
            return rec
    for conn, rec in sorted(run_conn.items(), key=lambda kv: -len(kv[0])):
        c = conn.replace("-", "_")
        if c and (name == c or name.startswith(c + "_")):
            return rec
    return None


def warehouse_identity():
    """WHICH warehouse are we actually reading? The console must show this so local/stale data is never mistaken
    for production. Returns {kind: 'production'|'local', label, bucket}."""
    import warehouse
    try:
        if warehouse.remote():
            return {"kind": "production", "label": "s3://%s" % warehouse._bucket(), "bucket": warehouse._bucket()}
    except Exception:
        pass
    return {"kind": "local", "label": "LOCAL %s" % getattr(warehouse, "_LOCAL_DIR", "agent_state/warehouse"),
            "bucket": None}


def _list_datasets_fast(workers=80):
    """Same as warehouse.list_datasets but reads the Parquet footers CONCURRENTLY. The reads are pure I/O against
    object storage (each ~tens of ms locally but far higher from the cloud host), so heavy parallelism is the lever —
    80 workers turns a ~130s serial sweep into a fraction of that. Falls back to the sequential warehouse version."""
    import warehouse
    try:
        if not warehouse.remote():
            return warehouse.list_datasets()
        import pyarrow.parquet as pq
        from pyarrow import fs as pafs
        from concurrent.futures import ThreadPoolExecutor
        s3 = pafs.S3FileSystem(endpoint_override=warehouse._endpoint(), access_key=warehouse._env("AWS_ACCESS_KEY_ID"),
                               secret_key=warehouse._env("AWS_SECRET_ACCESS_KEY"), region=warehouse._region(),
                               scheme="https")
        base = "%s/%s" % (warehouse._bucket(), warehouse._prefix())
        infos = [i for i in s3.get_file_info(pafs.FileSelector(base, recursive=False))
                 if i.type == pafs.FileType.File and i.path.endswith(".parquet")]

        def read_one(info):
            name = info.path.rsplit("/", 1)[-1][:-8]
            mod = None
            try:
                mod = info.mtime.timestamp() if info.mtime else None
            except Exception:
                pass
            try:
                md = pq.read_metadata(info.path, filesystem=s3)
                return {"name": name, "rows": md.num_rows, "fields": list(md.schema.names), "modified": mod}
            except Exception:
                return {"name": name, "rows": 0, "fields": [], "modified": mod}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(read_one, infos))
    except Exception:
        return warehouse.list_datasets()


def _load_hist():
    return _load_json(_HIST, {})


def _save_hist(hist):
    try:
        with open(_HIST, "w") as f:
            json.dump(hist, f)
    except Exception:
        pass


def build(record_history=True, hist_cap=60):
    """Assemble the manifest from cheap sources only (footers + run logs). Returns a stamped dict. Also appends the
    current per-source counts to the history so the console can chart record-counts-over-time and show true deltas."""
    now = time.time()
    wh = warehouse_identity()
    try:
        datasets = _list_datasets_fast()
    except Exception as e:
        return {"as_of": now, "live": False, "warehouse": wh,
                "error": "warehouse unreadable: %s" % str(e)[:120], "sources": [], "totals": {}}

    run_ds, run_conn = _runs_index()
    run_tables = _runs_tables_index(datasets)
    hist = _load_hist()

    sources, totals_by_group = [], {}
    grand_rows = 0
    health = {"ok": 0, "degraded": 0, "error": 0, "stale": 0, "unknown": 0}
    STALE_S = 3 * 86400                                              # >3 days since data changed = stale

    for d in datasets:
        n = d["name"]
        grp = classify(n)
        rows = d.get("rows") or 0
        grand_rows += rows
        mod = d.get("modified")
        age = (now - mod) if mod else None
        run = _match_run(n, run_ds, run_conn, run_tables)

        # delta: prefer the run log's own delta, else diff vs our saved history
        delta = run.get("delta") if run and run.get("delta") is not None else None
        prev = hist.get(n)
        if delta is None and prev:
            delta = rows - prev[-1][1]

        # health status. Only PULL-cadence sources get a health verdict (is data coming in?). Derived outputs
        # (master dims, conformed grain, reference, run logs, staging) are marked "derived" — not a health signal.
        if grp in ("scrape", "accounts", "timeseries"):
            if run and (run.get("status") in ("error", "failed") or run.get("degraded")):
                status = "degraded" if run.get("degraded") and run.get("status") not in ("error", "failed") else "error"
            elif age is not None and age > STALE_S:
                status = "stale"
            elif (run and run.get("status") in ("ok", "success")) or rows > 0:
                status = "ok"
            else:
                status = "unknown"
            health[status] = health.get(status, 0) + 1
        else:
            status = "derived"

        totals_by_group[grp] = totals_by_group.get(grp, {"rows": 0, "count": 0})
        totals_by_group[grp]["rows"] += rows
        totals_by_group[grp]["count"] += 1

        sources.append({
            "name": n, "group": grp, "rows": rows, "fields": len(d.get("fields") or []),
            "modified": mod, "age_s": age, "delta": delta, "status": status,
            "run_status": (run or {}).get("status") or "", "run_ts": (run or {}).get("ts"),
            "warnings": (run or {}).get("warnings") or [], "degraded": bool((run or {}).get("degraded")),
            "trigger": (run or {}).get("trigger") or "", "conn": (run or {}).get("conn") or "",
            "note": (run or {}).get("note") or "",
        })

        if record_history:
            series = hist.get(n, [])
            if not series or series[-1][1] != rows:                 # only append on change (keeps it small)
                series.append([round(now), rows])
                hist[n] = series[-hist_cap:]

    if record_history:
        _save_hist(hist)

    # connector rollup. Connector health comes STRAIGHT from the run log (authoritative — no dataset name-guessing),
    # so a broken connector (census-acs) is reported honestly even when its datasets can't be exactly linked. We
    # then augment each with the dataset stats we CAN confidently attribute.
    rank = {"error": 3, "degraded": 2, "stale": 1, "ok": 0, "derived": 0, "unknown": 0}
    conns = {}
    for conn, rec in run_conn.items():                  # every connector the run log knows about
        st = rec.get("status") or ""
        status = "error" if st in ("error", "failed") else ("degraded" if rec.get("degraded") else "ok")
        conns[conn] = {"conn": conn, "datasets": 0, "rows": 0, "status": status,
                       "run_status": st, "run_ts": rec.get("ts"),
                       "warnings": len(rec.get("warnings") or []), "trigger": rec.get("trigger") or ""}
    for s in sources:                                   # attribute datasets we could confidently match
        c = s["conn"]
        if not c:
            continue
        cc = conns.setdefault(c, {"conn": c, "datasets": 0, "rows": 0, "status": s["status"],
                                  "run_status": s["run_status"], "run_ts": s["run_ts"], "warnings": 0, "trigger": ""})
        cc["datasets"] += 1
        cc["rows"] += s["rows"]
        if rank.get(s["status"], 0) > rank.get(cc["status"], 0):
            cc["status"] = s["status"]

    # headline rollups the console leads with — real numbers, one place
    def _rows(name):
        return next((s["rows"] for s in sources if s["name"] == name), 0)
    totals = {
        "grand_rows": grand_rows,
        "datasets": len(datasets),
        "sources": totals_by_group.get("scrape", {}).get("count", 0),
        "accounts": max(_rows("src_outlets"), _rows("dim_outlet")),
        "items": _rows("dim_item"),
        "products": _rows("dim_product"),
        "skus": _rows("dim_sku"),
        "by_group": totals_by_group,
        "health": health,
        "last_activity": max((s["modified"] for s in sources if s["modified"]), default=None),
    }
    sources.sort(key=lambda s: (s["modified"] or 0), reverse=True)  # freshest first
    connectors = sorted(conns.values(), key=lambda c: ({"error": 0, "degraded": 1, "stale": 2}.get(c["status"], 3),
                                                        -(c["run_ts"] or 0)))
    return {"as_of": now, "live": True, "warehouse": wh, "build_ms": round((time.time() - now) * 1000),
            "totals": totals, "connectors": connectors, "sources": sources}


# ── caching: memory + disk + background refresh, so the console is ALWAYS instant after the first ever build ───
import threading

_CACHE_FILE = os.path.join(_STATE, "monitor_cache.json")
_S3_KEY = "_monitor_cache.json"                          # persisted to the warehouse bucket → survives restarts
_CACHE = {"at": 0, "data": None}
_LOCK = threading.Lock()
_REFRESHING = {"on": False}


def _persist(data):
    payload = json.dumps({"at": time.time(), "data": data})
    try:
        with open(_CACHE_FILE, "w") as f:
            f.write(payload)
    except Exception:
        pass
    try:                                                # ALSO to S3 so a fresh machine has it instantly (no 130s build)
        import warehouse
        warehouse.put_bytes(_S3_KEY, payload.encode("utf-8"))
    except Exception:
        pass


def _valid_for_now(data):
    """A cache is only usable if it was built against the SAME warehouse we're reading now — a local/dev cache must
    never be served as production (or vice-versa)."""
    wh = (data.get("warehouse") or {}).get("kind")
    return not wh or wh == warehouse_identity().get("kind")


def _load_persisted():
    """Warm the in-memory cache from a persisted snapshot: S3 first (survives restarts / new machines), then local
    disk. Returns True if a valid same-warehouse snapshot was loaded. This is what keeps the console INSTANT on a
    cold machine instead of blocking ~130s on a live build against high-latency object storage."""
    for src in ("s3", "disk"):
        d = None
        try:
            if src == "s3":
                import warehouse
                raw = warehouse.get_bytes(_S3_KEY)
                d = json.loads(raw) if raw else None
            else:
                d = _load_json(_CACHE_FILE, None)
        except Exception:
            d = None
        if d and d.get("data") and _valid_for_now(d["data"]):
            _CACHE.update(at=d.get("at", 0), data=d["data"])
            return True
    return False


def _refresh_async():
    if _REFRESHING["on"]:
        return
    _REFRESHING["on"] = True

    def run():
        try:
            data = build()
            with _LOCK:
                _CACHE.update(at=time.time(), data=data)
            _persist(data)
        finally:
            _REFRESHING["on"] = False
    threading.Thread(target=run, daemon=True).start()


def warm():
    """Build + persist the manifest if we don't already have a valid one. Called from the server's startup warm
    thread so the first user request is served from cache, never a cold 130s build."""
    with _LOCK:
        if _CACHE["data"] is not None:
            return
    if not _load_persisted():
        _refresh_async()


def snapshot(max_age=180):
    """The manifest the console polls. NEVER blocks on the slow build: serves the in-memory / persisted (S3→disk)
    snapshot instantly and refreshes in the background when stale. If nothing is cached yet, kicks the build and
    returns a stamped 'building' stub so the console shows a spinner instead of hanging ~130s. Every payload is
    stamped (cached/cache_age_s/building) so freshness is always honest."""
    now = time.time()
    with _LOCK:
        have = _CACHE["data"] is not None
    if not have:
        have = _load_persisted()                        # try S3 → disk (fast); do NOT build synchronously
    if not have:
        _refresh_async()                                # building in the background; tell the console to wait
        return {"as_of": now, "live": True, "building": True, "warehouse": warehouse_identity(),
                "totals": {}, "connectors": [], "sources": [],
                "note": "building the manifest (first run against object storage — a few seconds)…"}
    age = now - _CACHE["at"]
    if age >= max_age:
        _refresh_async()                                # stale → background rebuild, serve current now
    return dict(_CACHE["data"], cached=True, cache_age_s=round(age, 1), refreshing=_REFRESHING["on"])


def history(name):
    """The saved count time-series for one source → [[ts, rows], ...] (for the sparkline / rows-over-time chart)."""
    return _load_hist().get(name, [])


if __name__ == "__main__":
    import sys
    try:                                              # read PRODUCTION, not the local fallback
        import kroger_api
        kroger_api._load_creds()
    except Exception:
        pass
    if "--history" in sys.argv:
        n = sys.argv[sys.argv.index("--history") + 1]
        print(json.dumps(history(n), indent=1))
    else:
        m = build()
        t = m["totals"]
        print("warehouse: %s (%s)" % (m["warehouse"]["label"], m["warehouse"]["kind"]))
        print("as_of=%s live=%s build_ms=%s" % (
            time.strftime("%H:%M:%S", time.localtime(m["as_of"])), m["live"], m.get("build_ms")))
        print("grand_rows=%(grand_rows)s datasets=%(datasets)s accounts=%(accounts)s items=%(items)s" % t)
        print("health:", t["health"])
        print("by_group:", {g: v["count"] for g, v in t["by_group"].items()})
        print("\nfreshest 12:")
        for s in m["sources"][:12]:
            print("  %-26s %12s rows  Δ%-8s %-8s %s" % (
                s["name"], f"{s['rows']:,}", (s["delta"] if s["delta"] is not None else ""),
                s["status"], time.strftime("%m-%d %H:%M", time.localtime(s["modified"])) if s["modified"] else ""))
