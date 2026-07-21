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
import re
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE = os.path.join(_DIR, "agent_state")
_RUNS_JSON = os.path.join(_STATE, "runs.json")
_CONNECTORS = os.path.join(_STATE, "connectors.json")
_HIST = os.path.join(_STATE, "monitor_hist.json")     # {name: [[ts, rows], ...]} — count time-series for deltas

# ── dataset classification: what KIND of thing is each parquet, so the console groups it meaningfully ──────────
# order matters (first match wins). group → how the console buckets it.
_DERIVED_PREFIX = ("wb_", "img_", "xwalk_", "fact_", "bottle_dims", "master_enriched", "master_decisions")
_DERIVED_SUFFIX = ("_cluster", "_coherence", "_matches", "_merges", "_queue", "_vec", "_summary", "_signal")
_DERIVED_EXACT = {"match_dict", "catalog_skus", "hoodie_ids", "source_taxonomy", "price_coherence",
                  "master_decisions", "smoketest", "ingest_selftest"}
_GROUPS = [
    ("staging",   lambda n: n.startswith("_") or "scratch" in n),    # staging / scratch (hidden by default)
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


# ── canonical SOURCE per dataset: so EVERY source we have shows up ONCE in the console, its data rolled up, no
# matter how its datasets are named (Total Wine = totalwine/total_wine/master_enriched_tw → one source) or how it
# runs (connector / launchd / manual). Curated for the known families; anything unmatched falls back to its prefix
# so nothing is ever dropped. Order = most specific first.
_SOURCE_DEFS = [
    (("ubereats",),        "ubereats",    "Uber Eats",                 "aggregator"),
    (("sevennow",),        "sevennow",    "7NOW · 7-Eleven",           "aggregator"),
    (("instacart",),       "instacart",   "Instacart",                 "aggregator"),
    (("doordash", "orlando_merchants"), "doordash", "DoorDash",        "aggregator"),
    (("abc",),             "abc",         "ABC Fine Wine & Spirits",   "retail chain"),
    (("binnys",),          "binnys",      "Binny's",                   "retail chain"),
    (("specs",),           "specs",       "Spec's",                    "retail chain"),
    (("totalwine", "total_wine", "master_enriched_tw"), "total_wine", "Total Wine & More", "retail chain"),
    (("haskell",),         "haskells",    "Haskell's",                 "retail chain"),
    (("walmart",),         "walmart",     "Walmart",                   "grocery"),
    (("target",),          "target",      "Target",                    "grocery"),
    (("kroger",),          "kroger",      "Kroger",                    "grocery"),
    (("publix",),          "publix",      "Publix",                    "grocery"),
    (("albertsons",),      "albertsons",  "Albertsons",                "grocery"),
    (("circlek", "circle_k"), "circlek",  "Circle K",                  "convenience"),
    (("cvs",),             "cvs",         "CVS",                       "drug"),
    (("cityhive",),        "cityhive",    "City Hive network",         "off-premise"),
    (("offprem",),         "offprem",     "Off-premise sweep",         "off-premise"),
    (("bevalc_chains", "bev_alc_chains"), "chains-catalog", "Chain reachability catalog", "reference"),
    (("shopify",),         "shopify",     "Shopify DTC",               "off-premise"),
    (("bottlecapps", "national_bottlecapps"), "bottlecapps", "Bottlecapps network", "off-premise"),
    (("bbg",),             "bbg",         "BBG e-commerce",            "off-premise"),
    (("winebow",),         "winebow",     "Winebow (distributor)",     "distributor"),
    (("vtinfo",),          "vtinfo",      "VTInfo where-to-buy",       "reference"),
    (("ab_outlets", "ab_inbev"), "ab-inbev", "AB InBev locator",       "reference"),
    (("census",),          "census",      "US Census ACS",             "reference"),
    (("ttb",),             "ttb",         "TTB COLA registry",         "federal"),
    (("hemp",),            "hemp",        "Hemp feeds",                "off-premise"),
    (("or_pricing",),      "or-control",  "Oregon (control state)",    "control state"),
    (("ut_pricing",),      "ut-control",  "Utah (control state)",      "control state"),
    (("nc_pricing",),      "nc-control",  "North Carolina (control)",  "control state"),
    (("mt_pricing",),      "mt-control",  "Montana (control state)",   "control state"),
    (("me_pricing",),      "me-control",  "Maine (control state)",     "control state"),
    (("al_pricing",),      "al-control",  "Alabama (control state)",   "control state"),
    (("id_products",),     "id-control",  "Idaho (control state)",     "control state"),
    (("bc_liquor",),       "bc-control",  "British Columbia (control)", "control state"),
    (("mont",),            "montgomery",  "Montgomery County MD",      "control state"),
    (("tx_",),             "tx-tabc",     "Texas TABC",                "state licenses"),
    (("il_",),             "il-chicago",  "Chicago / Illinois",        "state licenses"),
    (("ct_",),             "ct-dcp",      "Connecticut DCP",           "state licenses"),
    (("ca_",),             "ca-abc",      "California ABC",            "state licenses"),
    (("fl_", "fl-", "bd400", "abtbrands"), "fl-dbpr", "Florida DBPR",  "state licenses"),
    (("naop",),            "naop",        "NAOP on-premise",           "market"),
    (("orlando",),         "orlando",     "Orlando market",            "market"),
    (("geocode", "match_dict", "menu"), "misc", "Misc / enrichment",   "reference"),
]


def source_of(name):
    """(key, label, kind) — the canonical source this dataset belongs to."""
    for prefixes, key, label, kind in _SOURCE_DEFS:
        for p in prefixes:
            pat = p if p.endswith(("_", "-")) else p + "_"
            if name == p or name.startswith(pat):
                return key, label, kind
    m = re.match(r"^(.+?)_offprem", name)                             # <city>_offprem_* → the off-premise sweep
    if m:
        return "offprem", "Off-premise sweep", "off-premise"
    key = re.split(r"_(products|catalog|outlets|accounts|census|pricing|inventory|stores|merchants|snapshot|"
                   r"full|liquor|purch|sales|brands|titos|labels|detail|hours)\b", name)[0]
    return key, key.replace("_", " ").title(), "other"


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


def _source_runs_index():
    """Latest run per source from `source_runs` (written by run_sources.py) → {table_name: run_record}. This is the
    authoritative per-source outcome: status (ok/failed/timeout/no-change), delta, error, when. Powers the console's
    'last run' + failure reporting so nothing is silently dropped."""
    import warehouse
    runs = []
    for fn, name in ((warehouse.query, "source_runs"),           # legacy table (history)
                     (warehouse.query_parts, "source_runs_log")):  # append-only log (authoritative now)
        try:
            runs += fn(name, "SELECT * FROM t")
        except Exception:
            pass
    if not runs:
        return {}
    latest = {}
    for r in runs:
        sid = r.get("source")
        if sid and (sid not in latest or (r.get("ts_end") or 0) > (latest[sid].get("ts_end") or 0)):
            latest[sid] = r
    out = {}
    for r in latest.values():
        for t in str(r.get("tables") or "").split(","):
            t = t.strip()
            if t:
                out[t] = r
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


_FOOTER_CACHE = {}                                            # name -> (mtime, rows, fields) — skip unchanged footers


def _list_datasets_fast(workers=80):
    """Same as warehouse.list_datasets but reads the Parquet footers CONCURRENTLY — and, unlike the warehouse
    version, ALSO surfaces PARTITIONED tables (warehouse/<name>/<part>.parquet — retail_observations, fact_*,
    scrape_runs), which a flat top-level listing silently drops. A partitioned table comes back as ONE dataset
    entry with rows summed across parts, `partitioned: True`, and a `parts` list [{part, rows, modified}] so the
    manifest can attribute time-series rows per source from part NAMES alone (parts are `<date>_<source>…`).
    The reads are pure I/O against object storage, so heavy parallelism is the lever. Falls back to the
    sequential warehouse version (top-level files only) on any error."""
    import warehouse
    try:
        import pyarrow.parquet as pq
        from concurrent.futures import ThreadPoolExecutor
        if warehouse.remote():
            from pyarrow import fs as pafs
            s3 = pafs.S3FileSystem(endpoint_override=warehouse._endpoint(),
                                   access_key=warehouse._env("AWS_ACCESS_KEY_ID"),
                                   secret_key=warehouse._env("AWS_SECRET_ACCESS_KEY"),
                                   region=warehouse._region(), scheme="https")
            base = "%s/%s" % (warehouse._bucket(), warehouse._prefix())
            infos = [(i.path[len(base) + 1:], i.path, i) for i
                     in s3.get_file_info(pafs.FileSelector(base, recursive=True))
                     if i.type == pafs.FileType.File and i.path.endswith(".parquet")]
        else:
            import glob as _glob
            s3 = None
            root = warehouse._LOCAL_DIR
            infos = []
            for p in _glob.glob(os.path.join(root, "**", "*.parquet"), recursive=True):
                rel = os.path.relpath(p, root)
                infos.append((rel.replace(os.sep, "/"), p, None))

        def _mtime(path, info):
            if info is not None:
                try:
                    return info.mtime.timestamp() if info.mtime else None
                except Exception:
                    return None
            try:
                return os.path.getmtime(path)
            except Exception:
                return None

        def read_one(item):
            rel, path, info = item
            mod = _mtime(path, info)
            # INCREMENTAL: a footer (rows/schema) only changes when the file is rewritten. The listing gives
            # mtime cheaply — re-read the footer ONLY when mtime moved; reuse the cache otherwise. Turns a
            # ~40s full sweep into ~2s once warm, so the console's counts refresh near-live as pulls land.
            cached = _FOOTER_CACHE.get(rel)
            if cached and cached[0] == mod and mod is not None:
                return rel, mod, cached[1], cached[2]
            try:
                md = pq.read_metadata(path, filesystem=s3) if s3 is not None else pq.read_metadata(path)
                _FOOTER_CACHE[rel] = (mod, md.num_rows, list(md.schema.names))
                return rel, mod, md.num_rows, list(md.schema.names)
            except Exception:
                return rel, mod, (cached[1] if cached else 0), (cached[2] if cached else [])

        with ThreadPoolExecutor(max_workers=workers) as ex:
            footers = list(ex.map(read_one, infos))

        out, parted = [], {}
        for rel, mod, rows, fields in footers:
            if "/" not in rel:                                       # top-level <name>.parquet — as before
                out.append({"name": rel[:-8], "rows": rows, "fields": fields, "modified": mod})
                continue
            top = rel.split("/", 1)[0]
            if top == "_manifest":                                   # v2 layout manifests, not data
                continue
            d = parted.setdefault(top, {"name": top, "rows": 0, "fields": [], "modified": None,
                                        "partitioned": True, "parts": []})
            d["rows"] += rows
            d["modified"] = max(d["modified"] or 0, mod or 0) or None
            if (mod or 0) >= (d.get("_ffrom") or 0) and fields:      # fields from the newest part
                d["fields"], d["_ffrom"] = fields, (mod or 0)
            d["parts"].append({"part": rel.rsplit("/", 1)[-1][:-8], "rows": rows, "modified": mod})
        for d in parted.values():
            d.pop("_ffrom", None)
            out.append(d)
        return out
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

    # per-source TIME-SERIES attribution: shared partitioned tables (retail_observations) land one part per
    # (date, source), so part NAMES alone tell us how many observation rows each SOURCE contributed and when it
    # last landed — no lake scan. This is what lets the console answer "which sources have per-store inventory,
    # and is it still coming in" and drill a source down to its accounts-with-inventory.
    observations = {}                                                # roster key -> {rows, latest, parts:[names]}
    _PART_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")
    for d in datasets:
        if not d.get("partitioned") or classify(d["name"]) != "timeseries":
            continue
        for p in d.get("parts") or []:
            m = _PART_RE.match(p["part"])
            if not m:
                continue
            date, src = m.group(1), m.group(2)
            key, label, kind = source_of(src.replace("-", "_"))
            o = observations.setdefault(key, {"rows": 0, "latest": "", "parts": [], "table": d["name"],
                                              "label": label})
            o["rows"] += p["rows"] or 0
            o["latest"] = max(o["latest"], date)
            o["parts"].append({"part": p["part"], "date": date, "rows": p["rows"], "table": d["name"]})

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
            "partitioned": bool(d.get("partitioned")),
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

    # SOURCE roster — every INGEST source we have, rolled up from its (possibly many, possibly oddly-named) datasets,
    # so the console shows the full source list at a glance and nothing is missed (e.g. Uber Eats, 7NOW, control
    # states — which run outside the connector run-log). Outputs (master/derived/staging/runlog) are not sources.
    rank = {"error": 3, "degraded": 2, "stale": 1, "ok": 0, "unknown": 0, "derived": 0}
    run_by_table = _source_runs_index()                  # authoritative per-source run outcomes (run_sources.py)
    roster = {}
    for s in sources:
        if s["group"] not in ("scrape", "accounts", "timeseries"):
            continue
        if s["group"] == "timeseries" and s.get("partitioned"):
            # a SHARED time-series spine (retail_observations) — every source lands into it, so it is not
            # itself a source. Its rows are attributed per source above (`observations`) instead.
            s["source"], s["source_label"] = "observations", "every source · shared time-series"
            continue
        key, label, kind = source_of(s["name"])
        s["source"], s["source_label"] = key, label
        r = roster.setdefault(key, {"key": key, "label": label, "kind": kind, "datasets": [], "rows": 0,
                                    "modified": None, "status": "unknown", "conn": ""})
        r["datasets"].append(s["name"])
        r["rows"] += s["rows"]
        r["modified"] = max(r["modified"] or 0, s["modified"] or 0) or None
        if s.get("conn") and not r["conn"]:
            r["conn"] = s["conn"]
        if rank.get(s["status"], 0) >= rank.get(r["status"], 0):
            r["status"] = s["status"]
        run = run_by_table.get(s["name"])                # attach the last run_sources outcome (status/error/when)
        if run and (not r.get("run_ts") or run.get("ts_end", 0) > r.get("run_ts", 0)):
            r["run_status"] = run.get("status")
            r["run_ts"] = run.get("ts_end")
            r["run_delta"] = run.get("delta")
            r["run_error"] = run.get("error") or ""
            r["run_duration"] = run.get("duration_s")
    # attach the per-source time-series attribution (inventory rows + when they last landed). A source whose
    # ONLY footprint is observation parts (no snapshot table of its own) still gets a roster entry — nothing
    # that lands inventory is ever invisible.
    for key, o in observations.items():
        r = roster.get(key)
        if r is None:
            r = roster.setdefault(key, {"key": key, "label": o["label"], "kind": "other", "datasets": [],
                                        "rows": 0, "modified": None, "status": "ok", "conn": ""})
        r["obs_rows"] = o["rows"]
        r["obs_latest"] = o["latest"]

    roster = sorted(roster.values(), key=lambda r: (r["modified"] or 0), reverse=True)
    for r in roster:
        r["dataset_count"] = len(r["datasets"])
    totals["source_count"] = len(roster)

    # ── SLO overlay + dispatcher state (NRT-PLAN §5/§6): the freshness CONTRACT per source, not just age.
    # Each registry source carries interval_h (its near-real-time knob); the verdict compares the ledger's
    # last attempt against it: fresh (< interval) / due (< 2×) / breach (≥ 2×) / never. The dispatcher block
    # shows the loop itself — window state, what's due right now, master-build ages — so the console proves
    # the near-real-time system is alive, not just that files changed.
    dispatcher = {}
    try:
        import source_registry as _sreg
        import run_sources as _rs
        ledger, _lok = _rs.ledger_last()          # unions the legacy table + the append-only log
        _sev = {"breach": 3, "due": 2, "never": 1, "fresh": 0}
        slo_by_key = {}
        for s in _sreg.SOURCES:
            if not s.get("enabled"):
                continue
            iv = float(s.get("interval_h") or (168 if s.get("cadence") == "weekly" else 24))
            age_h = round((now - ledger[s["id"]]) / 3600, 1) if ledger.get(s["id"]) else None
            slo = "never" if age_h is None else ("fresh" if age_h < iv else "due" if age_h < 2 * iv else "breach")
            for t in s.get("tables", []):
                k = source_of(t)[0]
                cur = slo_by_key.get(k)
                if not cur or _sev[slo] > _sev[cur["slo"]]:      # a source with several registry rows shows its worst
                    slo_by_key[k] = {"slo": slo, "slo_interval_h": iv, "slo_age_h": age_h, "slo_source_id": s["id"]}
        slo_counts = {}
        for r in roster:
            e = slo_by_key.get(r["key"])
            if e:
                r.update(e)
                slo_counts[e["slo"]] = slo_counts.get(e["slo"], 0) + 1
        builds = []
        for b in getattr(_sreg, "BUILDS", []):
            if not b.get("enabled"):
                continue
            age_h = round((now - ledger[b["id"]]) / 3600, 1) if ledger.get(b["id"]) else None
            builds.append({"id": b["id"], "label": b["label"], "interval_h": b.get("interval_h"),
                           "age_h": age_h, "tables": b.get("tables", [])})
        dispatcher = {"mac_window_open": _rs.mac_window_open(now), "mac_hours": os.environ.get("MAC_HOURS", "20-8"),
                      "due_now": [s["id"] for s in _rs.due_sources(now=now)],
                      "builds_due": [b["id"] for b in _rs.due_builds(now=now)],
                      "builds": builds, "slo_counts": slo_counts}
    except Exception as e:
        dispatcher = {"error": str(e)[:150]}

    return {"as_of": now, "live": True, "warehouse": wh, "build_ms": round((time.time() - now) * 1000),
            "totals": totals, "connectors": connectors, "roster": roster, "sources": sources,
            "dispatcher": dispatcher,
            "observations": {k: {"rows": o["rows"], "latest": o["latest"], "table": o["table"],
                                 "parts": o["parts"]} for k, o in observations.items()}}


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


def snapshot(max_age=45):
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


def read_expr(name):
    """The DuckDB source expression for dataset `name` — a single-file read for plain tables, a glob
    (union_by_name — per-source part schemas can differ) for PARTITIONED tables. Every console read path
    (preview / sample / search) goes through this so partitioned tables (retail_observations) are readable
    like any other dataset instead of 404ing on a nonexistent single file."""
    import warehouse
    s = next((x for x in (_CACHE["data"] or {}).get("sources", []) if x["name"] == name), None)
    if s and s.get("partitioned"):
        glob = ("s3://%s/%s/%s/*.parquet" % (warehouse._bucket(), warehouse._prefix(), name)) \
            if warehouse.remote() else os.path.join(warehouse._LOCAL_DIR, name, "*.parquet")
        return "read_parquet('%s', union_by_name=true)" % glob.replace("'", "")
    return "read_parquet('%s')" % warehouse.uri(name).replace("'", "")


# ── SOURCE DRILL-IN: a source's ACCOUNTS-WITH-INVENTORY — the per-store rollup of its latest observation day.
# Bounded by design: it reads ONLY the part files of THAT source's LATEST date (parts are one file per
# date×source, so the filename list from the manifest is the pruning index — no full-table scan ever).
_SRC_DETAIL_CACHE = {}                                   # key -> {sig, at, payload}
_SRC_DETAIL_LOCK = threading.Lock()


def source_detail(key, timeout_s=45):
    """Accounts-with-inventory for one roster source: per-store {skus, in_stock, units, avg price} on the
    source's most recent observation date, plus which observation source-ids and parts fed it. Cached until the
    source lands a new part. Returns an honest {accounts: [], note} when the source has no per-store data."""
    import warehouse
    snap = _CACHE["data"] or {}
    obs = (snap.get("observations") or {}).get(key)
    roster = next((r for r in snap.get("roster", []) if r.get("key") == key), {})
    base = {"key": key, "label": roster.get("label") or key, "datasets": roster.get("datasets") or [],
            "obs_rows": (obs or {}).get("rows"), "obs_latest": (obs or {}).get("latest")}
    if not obs or not obs.get("parts"):
        return dict(base, accounts=[], date=None,
                    note="no per-store inventory observations for this source yet")
    latest = max(p["date"] for p in obs["parts"])
    parts = [p for p in obs["parts"] if p["date"] == latest]
    sig = "|".join(sorted(p["part"] for p in parts))
    with _SRC_DETAIL_LOCK:
        c = _SRC_DETAIL_CACHE.get(key)
        if c and c["sig"] == sig:
            return c["payload"]
    if warehouse.remote():
        uris = ["s3://%s/%s/%s/%s.parquet" % (warehouse._bucket(), warehouse._prefix(), p["table"], p["part"])
                for p in parts]
    else:
        uris = [os.path.join(warehouse._LOCAL_DIR, p["table"], p["part"] + ".parquet") for p in parts]
    con = warehouse.connect()
    expr = "read_parquet([%s], union_by_name=true)" % ", ".join("'%s'" % u.replace("'", "") for u in uris)
    timed = {"v": False}

    def kill():
        timed["v"] = True
        try:
            con.interrupt()
        except Exception:
            pass
    timer = threading.Timer(timeout_s, kill)
    timer.start()
    try:
        cur = con.execute(
            "SELECT coalesce(nullif(store_id,''), store) AS store_id, max(store) AS store, "
            "count(*) AS skus, sum(CASE WHEN in_stock THEN 1 ELSE 0 END) AS in_stock, "
            "sum(CASE WHEN qty IS NOT NULL THEN qty ELSE 0 END) AS units, "
            "count(qty) AS qty_n, round(avg(price), 2) AS avg_price "
            "FROM %s GROUP BY 1 ORDER BY in_stock DESC, skus DESC LIMIT 2000" % expr)
        cols = [c2[0] for c2 in cur.description]
        accounts = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        return dict(base, accounts=[], date=latest,
                    note=("read timed out — try again" if timed["v"] else "read failed: %s" % str(e)[:120]))
    finally:
        timer.cancel()
        try:
            con.close()
        except Exception:
            pass
    for a in accounts:                                    # qty is None-heavy for binary in/out sources — be honest
        if not a.pop("qty_n", 0):
            a["units"] = None
        # translation-layer heal (landed data is never rewritten): early abc-fws days landed store as
        # "ABC #<full label>" (double prefix). store_id was always clean — prefer it when store just wraps it.
        if a.get("store") and a.get("store_id") and a["store"] != a["store_id"] \
                and a["store"].endswith(str(a["store_id"])):
            a["store"] = a["store_id"]
    payload = dict(base, date=latest, accounts=accounts,
                   account_count=len(accounts),
                   with_inventory=sum(1 for a in accounts if (a.get("in_stock") or 0) > 0))
    with _SRC_DETAIL_LOCK:
        _SRC_DETAIL_CACHE[key] = {"sig": sig, "at": time.time(), "payload": payload}
    return payload


# ── PRECOMPUTED preview samples: the drawer must stay instant as datasets grow to 100M+ rows. Reading a live
# LIMIT-N off a growing remote parquet is O(file complexity) — it degrades. Instead we sample each dataset ONCE in
# the background (bounded to N rows), hold it in memory, and serve the drawer from there → 0-latency, and the store
# is bounded by dataset COUNT (grows slowly), not row count (explodes). Rebuilt incrementally: only datasets whose
# data changed (mtime moved) are re-sampled, and only a few per cycle, so background cost stays flat at any scale.
_SAMPLES_S3 = "_monitor_samples.json"
_SAMPLES = {"at": 0, "wh": None, "data": {}}              # name -> {at, mod, columns, rows}
_SAMPLES_LOCK = threading.Lock()
_SAMPLE_CON = {"con": None}
SAMPLE_ROWS = 60


def _sample_con():
    if _SAMPLE_CON["con"] is None:
        import warehouse
        con = warehouse.connect()                         # one warm httpfs/S3 connection, reused for all sampling
        try:
            con.execute("SET enable_progress_bar=false")  # keep server logs clean
        except Exception:
            pass
        _SAMPLE_CON["con"] = con
    return _SAMPLE_CON["con"]


def _load_samples():
    """Warm the in-memory sample store from S3 (once), identity-gated so a local/dev sample set is never served
    as production."""
    if _SAMPLES["data"]:
        return
    try:
        import warehouse
        raw = warehouse.get_bytes(_SAMPLES_S3)
        d = json.loads(raw) if raw else None
        if d and d.get("data") and (not d.get("wh") or d["wh"] == warehouse_identity().get("kind")):
            _SAMPLES.update(at=d.get("at", 0), wh=d.get("wh"), data=d["data"])
    except Exception:
        pass


def sample(name):
    """The precomputed preview for one dataset → {columns, rows, at} or None (drawer serves this instantly)."""
    if not _SAMPLES["data"]:
        _load_samples()
    return _SAMPLES["data"].get(name)


def _read_sample(name, limit):
    con = _sample_con()
    con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM %s" % read_expr(name))
    cur = con.execute("SELECT * FROM t LIMIT %d" % limit)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:                                        # never ship the bulky raw_json blob over the wire
        if r.get("raw_json") is not None:
            r["raw_json"] = "…"
    return cols, rows


def refresh_samples(max_per_cycle=12, limit=SAMPLE_ROWS):
    """Incrementally (re)sample datasets whose data changed since last sampled — a few per cycle so the background
    cost stays flat no matter how many/how big the datasets get. Persists the whole (small) store to S3. Returns the
    number re-sampled this cycle. Reads the freshest datasets first (they're the ones users are watching)."""
    sources = (_CACHE["data"] or {}).get("sources", [])
    if not sources:
        return 0
    with _SAMPLES_LOCK:
        _load_samples()
        store = _SAMPLES["data"]
        done = 0
        for s in sources:                                 # sources are pre-sorted freshest-first
            if done >= max_per_cycle:
                break
            name, mod = s["name"], s.get("modified")
            have = store.get(name)
            if have and have.get("mod") == mod:           # unchanged since last sample → skip
                continue
            try:
                cols, rows = _read_sample(name, limit)
                store[name] = {"at": time.time(), "mod": mod, "columns": cols, "rows": rows}
                done += 1
            except Exception:
                store.setdefault(name, {"at": time.time(), "mod": mod, "columns": [], "rows": [], "err": True})
        if done:
            _SAMPLES.update(at=time.time(), wh=warehouse_identity().get("kind"), data=store)
            try:
                import warehouse
                warehouse.put_bytes(_SAMPLES_S3, json.dumps(
                    {"at": time.time(), "wh": _SAMPLES["wh"], "data": store}).encode("utf-8"))
            except Exception:
                pass
        return done


def samples_status():
    """How complete the sample store is (for the console to show 'previews ready N/total')."""
    if not _SAMPLES["data"]:
        _load_samples()
    total = len((_CACHE["data"] or {}).get("sources", []))
    return {"ready": len(_SAMPLES["data"]), "total": total, "at": _SAMPLES["at"]}


# ── SEARCH EVERYTHING: one query across all datasets / all rows / all columns. Substring search has no index to
# prune with, so a live full scan of 250M+ rows is impossible — a miss on a big table just times out. The design:
# (1) INSTANT layer — search the in-memory samples + dataset/column names, returned immediately; (2) DEEP layer — a
# parallel federated sweep (`WHERE concat_ws(all cols) ILIKE ?`) across every dataset on a warm connection pool, each
# dataset interrupt-capped and the whole job time-budgeted, streamed in progressively with an honest coverage report
# (which datasets were only partially scanned). Smallest datasets first so hits appear fast. Results are cached per
# query so a repeat is instant.
from concurrent.futures import ThreadPoolExecutor, as_completed

_SEARCH_JOBS = {}
_SEARCH_JOBS_LOCK = threading.Lock()
_SEARCH_TLS = threading.local()
_SEARCH_EXEC = ThreadPoolExecutor(max_workers=8, thread_name_prefix="search")


def _search_con():
    """A warm, thread-LOCAL DuckDB connection (DuckDB connections aren't safe to share across concurrent queries;
    thread-local gives each pool worker its own, warmed once)."""
    con = getattr(_SEARCH_TLS, "con", None)
    if con is None:
        import warehouse
        con = warehouse.connect()
        try:
            con.execute("SET enable_progress_bar=false")
        except Exception:
            pass
        _SEARCH_TLS.con = con
    return con


def warm_search_pool():
    """Pre-warm each pool thread's connection (the ~6s httpfs/S3 setup) at boot so the first search isn't slow."""
    for _ in range(8):
        try:
            _SEARCH_EXEC.submit(_search_con)
        except Exception:
            pass


def _esc_like(q):
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _match_cols(rows, ql):
    cols = set()
    for r in rows:
        for k, v in r.items():
            if v is not None and ql in str(v).lower():
                cols.add(k)
    return sorted(cols)


def _search_one(name, q, limit, timeout_s):
    """Search ONE dataset's full data for the substring across all columns; interrupt-capped so a big-table miss
    aborts instead of hanging. Returns (rows, columns, capped)."""
    con = _search_con()
    try:
        src = read_expr(name)
        # exclude the bulky raw_json blob from the scan (per-row it can be 8-16KB — searching it would scan GBs);
        # its extracted fields are already real columns. Fall back to plain * if the table has no raw_json.
        try:
            con.execute("CREATE OR REPLACE VIEW t AS SELECT * EXCLUDE (raw_json) FROM %s" % src)
        except Exception:
            con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM %s" % src)
    except Exception:
        return [], [], False
    capped = {"v": False}

    def kill():
        capped["v"] = True
        try:
            con.interrupt()
        except Exception:
            pass
    timer = threading.Timer(timeout_s, kill)
    timer.start()
    try:
        # to_json(t) renders the WHOLE row (all columns) as text — searching it hits any column. (concat_ws over
        # COLUMNS(*) silently collapsed to the first column, so it must NOT be used here.)
        cur = con.execute(
            "SELECT * FROM t WHERE to_json(t) ILIKE ? ESCAPE '\\' LIMIT %d" % limit,
            ["%" + _esc_like(q) + "%"])
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("raw_json") is not None:
                r["raw_json"] = "…"
        return rows, cols, capped["v"]
    except Exception:
        return [], [], capped["v"]                       # interrupted (cap) or read error
    finally:
        timer.cancel()


def _search_instant(q):
    """Immediate hits from the in-memory samples + dataset/column NAMES (zero I/O)."""
    ql = q.lower()
    out = []
    if not _SAMPLES["data"]:
        _load_samples()
    for name, smp in _SAMPLES["data"].items():
        hitrows = [r for r in smp.get("rows", []) if any(v is not None and ql in str(v).lower() for v in r.values())]
        col_hits = [c for c in smp.get("columns", []) if ql in c.lower()]
        if hitrows or col_hits or ql in name.lower():
            out.append({"dataset": name, "in_name": ql in name.lower(), "match_columns": col_hits,
                        "columns": smp.get("columns", []), "rows": hitrows[:6],
                        "match_cols": _match_cols(hitrows, ql)})
    out.sort(key=lambda h: (not h["in_name"], -len(h["rows"])))
    return out


def _job_view(job, max_ds=60):
    hits = sorted(job["hits"], key=lambda h: -len(h["rows"]))[:max_ds]
    return {"q": job["q"], "done": job["done"], "searched": job["searched"], "total": job["total"],
            "instant": job["instant"], "hits": hits, "capped": job.get("capped", []),
            "total_rows": sum(len(h["rows"]) for h in job["hits"]),
            "elapsed": round(time.time() - job["started"], 1)}


def _search_run(job, q, per_ds_limit, per_ds_timeout, budget):
    ql = q.lower()
    srcmap = {s["name"]: s for s in (_CACHE["data"] or {}).get("sources", [])}
    names = [n for n, s in srcmap.items() if s.get("group") not in ("runlog", "staging") and (s.get("rows") or 0) > 0]
    names.sort(key=lambda n: srcmap.get(n, {}).get("rows", 0))    # small datasets first → hits stream fast
    job["total"] = len(names)
    deadline = time.time() + budget
    futs = {_SEARCH_EXEC.submit(_search_one, n, q, per_ds_limit, per_ds_timeout): n for n in names}
    for fut in as_completed(futs):
        n = futs[fut]
        try:
            rows, cols, capped = fut.result()
        except Exception:
            rows, cols, capped = [], [], False
        job["searched"] += 1
        if rows:
            job["hits"].append({"dataset": n, "columns": cols, "rows": rows, "rowcount": srcmap.get(n, {}).get("rows"),
                                "match_cols": _match_cols(rows, ql)})
        if capped:
            job["capped"].append(n)
        if time.time() > deadline:
            for f in futs:
                f.cancel()
            break
    job["done"] = True


def search(q, per_ds_limit=12, per_ds_timeout=4, budget=26):
    """Start (or return the cached) 'search everything' job. Returns instant hits immediately + a job snapshot the
    console polls as the deep sweep streams in. Cached per query for 5 min so a repeat is instant."""
    qn = " ".join(q.strip().lower().split())
    if len(qn) < 2:
        return {"q": q, "done": True, "instant": [], "hits": [], "searched": 0, "total": 0,
                "note": "type at least 2 characters"}
    with _SEARCH_JOBS_LOCK:
        job = _SEARCH_JOBS.get(qn)
        if job and (time.time() - job["started"]) < 300:
            return _job_view(job)
        job = {"q": q, "started": time.time(), "done": False, "instant": _search_instant(q),
               "hits": [], "searched": 0, "total": 0, "capped": []}
        _SEARCH_JOBS[qn] = job
        # prune old jobs
        for k in [k for k, v in _SEARCH_JOBS.items() if time.time() - v["started"] > 600]:
            _SEARCH_JOBS.pop(k, None)
    threading.Thread(target=_search_run, args=(job, q, per_ds_limit, per_ds_timeout, budget), daemon=True).start()
    return _job_view(job)


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
