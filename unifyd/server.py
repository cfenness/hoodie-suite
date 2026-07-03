#!/usr/bin/env python3
"""
server.py — local agent that embeds the pulls into the dashboard.

Run this next to hoodie_mdm.html and ttb_cola_scraper.py:

    pip install flask requests beautifulsoup4
    python server.py
    # open http://127.0.0.1:8765

The dashboard auto-detects the agent (GET /api/health). When present:
  - it loads real datasets (/api/datasets) and run history (/api/runs)
  - "Run now" / "Run all" / the scheduler POST /api/run and execute REAL pulls:
        fl-items / fl-outlets  -> live Florida DBPR/ABT CSV extracts
        ttb-cola               -> ttb_cola_scraper.scrape(...)
When the agent is absent the dashboard falls back to its built-in preview.

State is persisted to ./agent_state/ (datasets.json, runs.json, cola CSV).
"""
import csv, io, json, os, time, types, urllib.request, datetime, threading, logging
from flask import Flask, request, jsonify, send_file, Response, redirect

import ttb_cola_scraper as cola   # the scraper you generated
import abc_fws_scraper as abc      # ABC FWS directional inventory tracker (BigCommerce)
import specs_scraper as specs      # Spec's directional tracker (Next.js, via Bright Data)
import binnys_scraper as binnys    # Binny's directional tracker (Algolia feed, no Bright Data)
import shopify_scraper as shopify  # DTC brands on Shopify (hemp + bev-alc) via public /products.json
import instacart_scraper as instacart  # store-level Instacart via Bright Data managed dataset
import analyze                      # data-reader brain behind "Overlay your data"
import planogram                    # benchmark + shelf-vision + pitch behind the Planogram app
import prism                        # data contract behind the Prism mobile app
import auth_gate                    # Google OIDC login gate (active only when configured)

APP_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(APP_DIR, "agent_state"); os.makedirs(STATE_DIR, exist_ok=True)
FULL_DIR  = os.path.join(STATE_DIR, "full");       os.makedirs(FULL_DIR, exist_ok=True)
HTML_PATH = os.path.join(APP_DIR, "hoodie_mdm.html")

# State store. Local disk by default (./agent_state/). Set STATE_BUCKET (+ optional
# STATE_PREFIX) to persist to S3 instead, so pulled data survives container redeploys.
# The container's local disk is ephemeral; S3 is the durable store. See unifyd/README.md.
STATE_BUCKET = os.environ.get("STATE_BUCKET", "").strip()
STATE_PREFIX = os.environ.get("STATE_PREFIX", "unifyd-state").strip("/")

app = Flask(__name__)
auth_gate.init(app)                # Google OIDC gate (whole origin) — a no-op unless configured

# ---------------- live run jobs (the on-theme console on Run) ----------------
# A run executes in a background thread; the front-end polls /api/run/progress to stream
# the ACTUAL progress. We capture the pulls' existing app.logger.info(...) output per-job
# via a logging handler keyed by the running thread — no scraper changes, real lines only.
JOBS = {}                       # jobId -> {id,connId,status,startedAt,finishedAt,log[],run,error}
_JOB_BY_THREAD = {}             # thread ident -> jobId  (so a log record finds its job)
JOB_LOG_CAP = 800

class _JobLogHandler(logging.Handler):
    def emit(self, record):
        jid = _JOB_BY_THREAD.get(threading.get_ident())
        job = JOBS.get(jid) if jid else None
        if job is None:
            return
        try: msg = record.getMessage()
        except Exception: return
        ln = job["log"]; ln.append({"t": int(time.time() * 1000), "lvl": record.levelname, "msg": msg})
        if len(ln) > JOB_LOG_CAP: del ln[:len(ln) - JOB_LOG_CAP]

_jh = _JobLogHandler(); _jh.setLevel(logging.INFO)
app.logger.addHandler(_jh); app.logger.setLevel(logging.INFO)   # INFO so progress lines flow

VALID_CONNS = {"ttb-cola", "abc-fws", "specs", "binnys", "shopify-dtc", "instacart"}

def _dispatch_pull(conn, body):
    return (cola_pull(body) if conn == "ttb-cola"
            else abc_pull(body) if conn == "abc-fws"
            else specs_pull(body) if conn == "specs"
            else binnys_pull(body) if conn == "binnys"
            else shopify_pull(body) if conn == "shopify-dtc"
            else instacart_pull(body) if conn == "instacart"
            else fl_pull(conn) if conn in FL_CONN else None)

def _new_job(conn):
    jid = "J-%d-%d" % (int(time.time()), len(JOBS) + 1)
    JOBS[jid] = {"id": jid, "connId": conn, "status": "running",
                 "startedAt": int(time.time() * 1000), "finishedAt": None,
                 "log": [], "run": None, "error": None}
    if len(JOBS) > 40:          # keep the last ~40 jobs
        for k in sorted(JOBS, key=lambda k: JOBS[k]["startedAt"])[:len(JOBS) - 40]:
            JOBS.pop(k, None)
    return jid

def _run_job(jid, conn, body):
    _JOB_BY_THREAD[threading.get_ident()] = jid
    job = JOBS[jid]
    try:
        app.logger.info("%s: run started", conn)
        rec = _dispatch_pull(conn, body)
        if rec is None:
            job["status"] = "error"; job["error"] = "unknown connId: %s" % conn
            app.logger.warning("%s: unknown connId", conn); return
        RUNS.insert(0, rec); del RUNS[200:]; save()
        job["run"] = rec; job["status"] = rec.get("status", "success")
        app.logger.info("%s: done — %s rows, status %s", conn, rec.get("total"), rec.get("status"))
    except Exception as e:
        app.logger.exception("run failed")
        job["status"] = "error"; job["error"] = str(e)
    finally:
        job["finishedAt"] = int(time.time() * 1000)
        _JOB_BY_THREAD.pop(threading.get_ident(), None)

# ---------------- hardening: optional auth + JSON errors ----------------
# AGENT_TOKEN gates /api/* for non-browser callers (off by default — local dev and
# browser apps behind the CloudFront password function are unaffected). /api/health
# stays open so load-balancer / uptime probes work.
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "").strip()

@app.before_request
def _auth():
    if not AGENT_TOKEN:
        return
    p = request.path
    if p == "/api/health" or not p.startswith("/api/"):
        return
    if request.headers.get("Authorization", "") == "Bearer " + AGENT_TOKEN \
       or request.headers.get("X-Agent-Token", "") == AGENT_TOKEN:
        return
    return jsonify(ok=False, error="unauthorized"), 401

@app.errorhandler(404)
def _e404(e):
    # A browser NAVIGATION that misses (e.g. a mistyped /prism.html, or a post-login
    # redirect to a stale path) should land on the launcher, not a raw JSON blob.
    # API calls and asset requests (image/*, etc.) still get JSON.
    if not request.path.startswith("/api/") and "text/html" in request.headers.get("Accept", ""):
        return redirect("/")
    return jsonify(ok=False, error="not found"), 404
@app.errorhandler(405)
def _e405(e): return jsonify(ok=False, error="method not allowed"), 405
@app.errorhandler(Exception)
def _e500(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify(ok=False, error=e.description), e.code
    app.logger.exception("unhandled error")
    return jsonify(ok=False, error="internal error"), 500

# ---------------- persisted state (disk | S3) ----------------
def _s3():
    import boto3                      # lazy — only imported when STATE_BUCKET is set
    return boto3.client("s3")
def _key(name):
    return (STATE_PREFIX + "/" + name) if STATE_PREFIX else name

def load(name, default):
    if STATE_BUCKET:
        try:
            obj = _s3().get_object(Bucket=STATE_BUCKET, Key=_key(name))
            return json.loads(obj["Body"].read())
        except Exception:
            return default            # missing object / first boot -> default
    try: return json.load(open(os.path.join(STATE_DIR, name)))
    except Exception: return default

DATASETS = load("datasets.json", {})
RUNS     = load("runs.json", [])

# Canonical hierarchy served at /api/hierarchy. Curated copy from the state store if
# present, else the bundled seed (unifyd/hierarchy.json). Eventually derived from the
# owned master data; for now it mirrors spine/hierarchy.sample.json.
HIER_SEED = os.path.join(APP_DIR, "hierarchy.json")
def load_hierarchy():
    h = load("hierarchy.json", None)
    if h is not None: return h
    try: return json.load(open(HIER_SEED))
    except Exception: return {"id": "root", "level": "root", "name": "All Portfolios", "children": []}
HIERARCHY = load_hierarchy()

def save():
    blobs = {"datasets.json": DATASETS, "runs.json": RUNS}
    if STATE_BUCKET:
        c = _s3()
        for name, data in blobs.items():
            try:
                c.put_object(Bucket=STATE_BUCKET, Key=_key(name),
                             Body=json.dumps(data).encode("utf-8"),
                             ContentType="application/json")
            except Exception as e:
                app.logger.warning("S3 save %s failed: %s", name, e)
    else:
        for name, data in blobs.items():
            json.dump(data, open(os.path.join(STATE_DIR, name), "w"))

# Full pulled rows live OUTSIDE the in-memory DATASETS sample (kept small for the UI):
# written per-dataset at pull time, read on demand by /api/datasets/download. Same
# disk-or-S3 abstraction as save()/load(), so the complete pull is always extractable.
def save_full(did, header, rows):
    blob = json.dumps({"header": header, "rows": rows, "total": len(rows)})
    if STATE_BUCKET:
        try: _s3().put_object(Bucket=STATE_BUCKET, Key=_key("full/" + did + ".json"),
                              Body=blob.encode("utf-8"), ContentType="application/json")
        except Exception as e: app.logger.warning("S3 full save %s failed: %s", did, e)
    else:
        try: open(os.path.join(FULL_DIR, did + ".json"), "w").write(blob)
        except Exception as e: app.logger.warning("full save %s failed: %s", did, e)

def load_full(did):
    if STATE_BUCKET:
        try:
            obj = _s3().get_object(Bucket=STATE_BUCKET, Key=_key("full/" + did + ".json"))
            return json.loads(obj["Body"].read())
        except Exception: return None
    try: return json.load(open(os.path.join(FULL_DIR, did + ".json")))
    except Exception: return None

def _absorb(ds):
    """Lift the full rows a scraper attached as `_rows_full` out to the on-demand full store
    (for complete CSV/JSON export), then leave the in-memory dataset capped at its UI sample
    so DATASETS / agent_state stay small. No `_rows_full` (e.g. FL/COLA) → unchanged no-op."""
    for did, d in (ds or {}).items():
        full = d.pop("_rows_full", None)
        if full is not None:
            save_full(did, d.get("header") or [], full)
    return ds

# ---------------- Florida pull (live, no extra deps) ----------------
FL_BASE = "https://www2.myfloridalicense.com/sto/file_download/extracts"
FL_HEADER = ["Board","Profession","Owner Name","Series","Modifier","Mail Address 1","Mail Address 2",
    "Mail Address 3","Mail City","Mail State","Mail ZIP","Mail County","DBA","Location Address 1",
    "Location Address 2","Location Address 3","Location City","Location State","Location ZIP",
    "Location County","License Number","Primary Status","Secondary Status","Original Licensure Date",
    "Effective Date","Expiration Date","Tax Stamp Designation","Smoking Designation","Retail Tobacco Indicator"]
FL_CONN = {
  "fl-items":   [("bd4008lic", True, 600), ("bd4011lic", True, 500), ("abtbrands", True, 600)],
  "fl-outlets": [("bd4006lic", True, 600), ("bd4005lic", True, 500),
                 ("bd400revok", False, 500), ("bd4002lic", True, 500)],
}
def fl_pull(conn_id):
    started = int(time.time() * 1000); exs = []
    extracts = FL_CONN[conn_id]
    for i, (eid, hashdr, n) in enumerate(extracts):
        try:
            app.logger.info("downloading %s (%d/%d) from Florida…", eid, i + 1, len(extracts))
            txt = urllib.request.urlopen(f"{FL_BASE}/{eid}.csv", timeout=180).read().decode("utf-8", "replace")
            app.logger.info("%s: %s KB downloaded, parsing…", eid, len(txt) // 1024)
            rows = list(csv.reader(io.StringIO(txt)))
            header = [h.strip() for h in rows[0]] if hashdr else FL_HEADER
            data = [r for r in (rows[1:] if hashdr else rows) if any((c or "").strip() for c in r)]
            DATASETS[eid] = {"header": header, "rows": cola.sample(data, header, n),
                             "total": len(data), "profile": cola.profile(header, data)}
            save_full(eid, header, data)   # keep ALL rows extractable, not just the sample
            prev = next((e for r in RUNS if r["connId"] == conn_id for e in r["extracts"] if e["id"] == eid), None)
            delta = len(data) - (prev["rows"] if prev else len(data))
            app.logger.info("%s: %s rows (%s records) ✓", eid, len(data), len(data))
            exs.append({"id": eid, "rows": len(data), "delta": delta, "status": "success"})
        except Exception as e:
            app.logger.warning("FL %s failed: %s", eid, e)
            exs.append({"id": eid, "rows": 0, "delta": 0, "status": "failed"})
    fin = int(time.time() * 1000)
    status = "failed" if all(e["status"] == "failed" for e in exs) else \
             "partial" if any(e["status"] == "failed" for e in exs) else "success"
    return {"id": "R-" + format(int(time.time()) % 100000, "05d"), "connId": conn_id,
            "startedAt": started, "finishedAt": fin, "durationMs": fin - started,
            "status": status, "trigger": "manual", "total": sum(e["rows"] for e in exs), "extracts": exs}

# ---------------- COLA pull (embeds the scraper) ----------------
def cola_pull(params):
    today = datetime.date.today()
    d_from = params.get("from") or (today - datetime.timedelta(days=params.get("days", 7))).strftime("%m/%d/%Y")
    d_to   = params.get("to") or today.strftime("%m/%d/%Y")
    args = cola.build_args([
        "--from", d_from, "--to", d_to,
        "--chunk-days", str(params.get("chunk_days", 1)),
        "--out", os.path.join(STATE_DIR, "cola"),
        "--resume",
    ] + (["--detail"] if params.get("detail") else []) + (["--ocr"] if params.get("ocr") else []))
    started = int(time.time() * 1000)
    ds, runs, _ = cola.scrape(args, log=lambda m: app.logger.info("COLA %s", m))
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

# ---------------- ABC FWS pull (directional inventory, embeds the scraper) ----------------
def abc_pull(params):
    started = int(time.time() * 1000)
    ds, runs, _ = abc.pull(
        sample=params.get("sample", 40), crawl_all=bool(params.get("all")),
        limit=params.get("limit"), out=os.path.join(STATE_DIR, "abc"),
        state_dir=os.path.join(STATE_DIR, "abc"),
        log=lambda m: app.logger.info("ABC %s", m))
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

def specs_pull(params):
    started = int(time.time() * 1000)
    ds, runs, _ = specs.pull(
        sample=params.get("sample", 40), crawl_all=bool(params.get("all")),
        limit=params.get("limit"), out=os.path.join(STATE_DIR, "specs"),
        state_dir=os.path.join(STATE_DIR, "specs"),
        log=lambda m: app.logger.info("SPECS %s", m))
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

def binnys_pull(params):
    started = int(time.time() * 1000)
    ds, runs, _ = binnys.pull(
        sample=params.get("sample", 300), crawl_all=bool(params.get("all")),
        limit=params.get("limit"), out=os.path.join(STATE_DIR, "binnys"),
        state_dir=os.path.join(STATE_DIR, "binnys"),
        log=lambda m: app.logger.info("BINNYS %s", m))
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

def shopify_pull(params):
    started = int(time.time() * 1000)
    ds, runs, _ = shopify.pull(
        sample=params.get("sample"), crawl_all=bool(params.get("all")), limit=params.get("limit"),
        out=os.path.join(STATE_DIR, "shopify"), state_dir=os.path.join(STATE_DIR, "shopify"),
        domains=params.get("domains"), log=lambda m: app.logger.info("SHOPIFY %s", m))
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

def instacart_pull(params):
    started = int(time.time() * 1000)
    ds, runs, _ = instacart.pull(out=os.path.join(STATE_DIR, "instacart"),
        state_dir=os.path.join(STATE_DIR, "instacart"), urls=params.get("urls"),
        log=lambda m: app.logger.info("INSTACART %s", m))
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

# ---------------- API ----------------
@app.get("/api/health")
def health():
    return jsonify(ok=True, agent="unifyd-local", sources=list(FL_CONN) + ["ttb-cola", "abc-fws", "specs", "binnys", "shopify-dtc", "instacart"],
                   datasets=len(DATASETS), runs=len(RUNS),
                   state=("s3:" + STATE_BUCKET) if STATE_BUCKET else "disk")

# Source labels + a first-cut scope tree derived from the pulled data.
_SRC_LABEL = {"fl-items": "Florida — Items", "fl-outlets": "Florida — Outlets",
              "ttb-cola": "TTB — COLA Labels", "abc-fws": "ABC FWS — Inventory",
              "specs": "Spec's — Inventory", "binnys": "Binny's — Inventory",
              "shopify-dtc": "Hemp + DTC — Shopify", "instacart": "Instacart — Store-level"}
_EXTRACT_SRC = {eid: src for src, exs in FL_CONN.items() for (eid, _h, _n) in exs}
_NAME_COLS = ("Owner Name", "Registrant Name", "Applicant", "Brand Name", "DBA")
def _name_idx(header):
    for n in _NAME_COLS:
        if n in header: return header.index(n)
    return 2 if len(header) > 2 else 0
def derive_hierarchy(datasets, top=50):
    """First-cut scope tree from real data: source -> entity (top-N by row count).
    Returns None when there's nothing to derive, so the caller falls back to the seed."""
    from collections import Counter
    by_src = {}
    for dsid, ds in datasets.items():
        rows = ds.get("rows") or []
        if not rows: continue
        ci = _name_idx(ds.get("header") or [])
        src = _EXTRACT_SRC.get(dsid) or ("ttb-cola" if "cola" in dsid.lower() else "other")
        counts = by_src.setdefault(src, Counter())
        for r in rows:
            v = str(r[ci]).strip() if ci < len(r) else ""
            if v: counts[v] += 1
    if not by_src: return None
    pfs = []
    for src, counts in by_src.items():
        brands = [{"id": f"sc:{src}:{i}", "level": "brand", "name": nm, "count": n, "children": []}
                  for i, (nm, n) in enumerate(counts.most_common(top))]
        pfs.append({"id": f"sc:{src}", "level": "portfolio", "name": _SRC_LABEL.get(src, src.title()),
                    "children": brands})
    pfs.sort(key=lambda p: p["name"])
    return {"id": "root", "level": "root", "name": "All Sources", "children": pfs}

@app.get("/api/datasets")
def datasets():
    q = (request.args.get("q") or "").strip().lower()
    only = request.args.get("dataset")
    src = {only: DATASETS[only]} if only in DATASETS else DATASETS
    if not q:
        return jsonify(src)
    out = {}
    for k, ds in src.items():
        rows = [r for r in (ds.get("rows") or []) if any(q in str(c).lower() for c in r)]
        out[k] = dict(ds, rows=rows, matched=len(rows))
    return jsonify(out)

@app.get("/api/datasets/download")
def dataset_download():
    """Stream the COMPLETE pulled dataset (all rows, not the UI sample) as CSV or JSON."""
    did = (request.args.get("dataset") or "").strip()
    fmt = (request.args.get("format") or "csv").lower()
    full = load_full(did)                                   # full rows if we kept them (FL/COLA)
    if full:
        header, rows = full.get("header") or [], full.get("rows") or []
    elif did in DATASETS:                                   # else the in-memory set (chains: already complete)
        ds = DATASETS[did]; header, rows = ds.get("header") or [], ds.get("rows") or []
    else:
        return jsonify(error="unknown dataset: " + did), 404
    if fmt == "json":
        body = json.dumps([dict(zip(header, r)) for r in rows]); mime, ext = "application/json", "json"
    else:
        buf = io.StringIO(); w = csv.writer(buf); w.writerow(header); w.writerows(rows)
        body, mime, ext = buf.getvalue(), "text/csv", "csv"
    return Response(body, mimetype=mime + "; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="%s.%s"' % (did, ext)})

@app.get("/api/runs")
def runs():
    return jsonify(RUNS[:200])

@app.get("/api/hierarchy")
def hierarchy():
    # derived from the live data when present; else the bundled/curated seed
    return jsonify(derive_hierarchy(DATASETS) or HIERARCHY)

@app.post("/api/run")
def run():
    body = request.get_json(force=True, silent=True) or {}
    conn = body.get("connId")
    if conn not in VALID_CONNS and conn not in FL_CONN:
        return jsonify(error="unknown connId"), 400
    # Async (opt-in via {"stream":true} or ?async=1): run in a thread, stream progress via
    # /api/run/progress. This is what Hoodie Pulls' live console uses.
    if body.get("stream") or request.args.get("async"):
        jid = _new_job(conn)
        threading.Thread(target=_run_job, args=(jid, conn, body), daemon=True).start()
        return jsonify(jobId=jid, connId=conn, status="running"), 202
    # Legacy synchronous path (blocks until the pull finishes) — kept for other callers.
    try:
        rec = _dispatch_pull(conn, body)
    except Exception as e:
        app.logger.exception("run failed")
        rec = {"id": "R-ERR", "connId": conn, "startedAt": int(time.time()*1000),
               "finishedAt": int(time.time()*1000), "durationMs": 0, "status": "failed",
               "trigger": body.get("trigger", "manual"), "total": 0, "extracts": []}
    if rec is None:
        return jsonify(error="unknown connId"), 400
    RUNS.insert(0, rec); del RUNS[200:]; save()
    return jsonify(rec)

@app.get("/api/run/progress")
def run_progress():
    """Live status of a background run job — log lines, elapsed, and the final record once done."""
    jid = (request.args.get("id") or "").strip()
    job = JOBS.get(jid)
    if not job:
        return jsonify(error="unknown job: %s" % jid), 404
    now = int(time.time() * 1000)
    return jsonify(id=jid, connId=job["connId"], status=job["status"],
                   startedAt=job["startedAt"], finishedAt=job["finishedAt"],
                   elapsedMs=(job["finishedAt"] or now) - job["startedAt"],
                   log=job["log"], run=job["run"], error=job["error"])

@app.post("/api/analyze")
def analyze_ep():
    """Read an uploaded dataset → context-aware first pass + (when it fits) Report Builder
    specs. Front-end sends the RB vocabulary (dimensions/measures/viz) since it lives in
    the dashboard. Falls back gracefully: 503 when no API key, 502 on model error."""
    body = request.get_json(force=True, silent=True) or {}
    header, rows = body.get("header") or [], body.get("rows") or []
    if not header or not rows:
        return jsonify(error="need header + rows"), 400
    result = analyze.analyze(header, rows, filename=body.get("filename", "dataset.csv"),
                             registries=body.get("registries") or {},
                             full=bool(body.get("full", True)))
    if "error" not in result:
        return jsonify(result)
    return jsonify(result), (503 if result["error"] == "llm-disabled" else 502)

@app.post("/api/ai-read")
def ai_read_ep():
    """AI analysis of data opened on its OWN terms (file-as-root). Privacy-preserving: the
    browser sends the PROFILE + COMPUTED AGGREGATES only (no raw rows). 503 without a key."""
    body = request.get_json(force=True, silent=True) or {}
    profile = body.get("profile")
    if not profile:
        return jsonify(error="need profile"), 400
    result = analyze.ai_read(profile, summary=body.get("summary"),
                             filename=body.get("filename", "dataset.csv"),
                             header=body.get("header"), rows=body.get("rows"))
    if "error" not in result:
        return jsonify(result)
    return jsonify(result), (503 if result["error"] == "llm-disabled" else 502)

@app.get("/api/benchmark")
def benchmark_ep():
    """Market NORM for the Planogram app — format-aware. Deterministic (no LLM), so it always
    answers; the seam where real SipSource depletion norms plug in."""
    result = planogram.benchmark(request.args.get("market"), request.args.get("format", "off"))
    return jsonify(result), (404 if result.get("error") == "unknown-market" else 200)


@app.post("/api/shelf-vision")
def shelf_vision_ep():
    """Photo of a shelf/back-bar -> facings per category x tier (Claude vision). 503 without a key."""
    body = request.get_json(force=True, silent=True) or {}
    result = planogram.shelf_count(body.get("image"), media_type=body.get("media_type", "image/jpeg"),
                                   categories=body.get("categories"), tiers=body.get("tiers"))
    if "error" not in result:
        return jsonify(result)
    code = 503 if result["error"] == "llm-disabled" else (400 if result["error"] == "need-image" else 502)
    return jsonify(result), code


@app.post("/api/pitch")
def pitch_ep():
    """Narrate the computed shelf gaps into a buyer pitch (numbers in, wording out). 503 without a key."""
    body = request.get_json(force=True, silent=True) or {}
    result = planogram.pitch(body.get("account"), body.get("market"), body.get("format", "off"),
                             body.get("deltas") or [])
    if "error" not in result:
        return jsonify(result)
    return jsonify(result), (503 if result["error"] == "llm-disabled" else 502)


@app.get("/api/prism")
def prism_ep():
    """Prism mobile app's data contract — the book cut every way, plus the pulse feed.
    Deterministic (no LLM), so it always answers and the app works offline once cached."""
    return jsonify(prism.bundle(request.args.get("measure")))

# ---- optional: serve the static suite from THIS app (all-in-one image, e.g. Fly.io) ----
# When SUITE_ROOT is set, one gunicorn process serves BOTH /api/* and the public suite from a single
# origin, so the apps' same-origin /api/* fetches work with no separate frontend host and no CORS.
# An ALLOWLIST of public top-level entries is enforced so the engine (unifyd/, *.py, scripts/, docs,
# dotfiles, .env) is NEVER web-served — it mirrors the deploy.sh / deploy.yml exclude lists.
SUITE_ROOT = os.environ.get("SUITE_ROOT", "").strip()
_SUITE_OK_TOP = {"index.html", "apps", "spine", "suite.css", "suite-header.js",
                 "suite-export.js", "fullread.js", "dq.js", "dq_frontier.js", "favicon.ico"}

def _suite_send(relpath):
    from flask import abort
    if not SUITE_ROOT:
        abort(404)
    root = os.path.abspath(SUITE_ROOT)
    full = os.path.normpath(os.path.join(root, (relpath or "").lstrip("/")))
    if not (full == root or full.startswith(root + os.sep)):
        abort(403)                                   # no traversal outside the suite root
    # Allowlist the RESOLVED top-level segment (after collapsing ..), so `/apps/../unifyd/x` can't
    # hop from an allowed subtree into the engine. Engine + dotfiles + secrets are never web-served.
    rel = os.path.relpath(full, root)
    top = rel.split(os.sep, 1)[0]
    if top in ("", ".", "..") or top not in _SUITE_OK_TOP:
        abort(404)
    if os.path.isfile(full):
        if full.endswith(".webmanifest"):
            return send_file(full, mimetype="application/manifest+json")
        return send_file(full)
    abort(404)

@app.get("/prism")
@app.get("/prism.html")
def prism_shortcut():
    return redirect("/apps/prism.html")              # friendly short URL for the mobile app

@app.get("/")
def index():
    if SUITE_ROOT:
        return _suite_send("index.html")             # the launcher, not the MDM console
    return send_file(HTML_PATH)

@app.get("/<path:relpath>")
def suite_static(relpath):
    return _suite_send(relpath)                       # /api/* rules are more specific and win over this

if __name__ == "__main__":
    print("Unifyd agent on http://127.0.0.1:8765  (Ctrl-C to stop)")
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
