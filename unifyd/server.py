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
import csv, gzip, io, json, os, random, time, types, urllib.request, datetime, threading, logging
from flask import Flask, request, jsonify, send_file, Response, redirect, session

import ttb_cola_scraper as cola   # the scraper you generated
import abc_fws_scraper as abc      # ABC FWS directional inventory tracker (BigCommerce)
import specs_scraper as specs      # Spec's directional tracker (Next.js, via Bright Data)
import binnys_scraper as binnys    # Binny's directional tracker (Algolia feed, no Bright Data)
import shopify_scraper as shopify  # DTC brands on Shopify (hemp + bev-alc) via public /products.json
import instacart_scraper as instacart  # store-level Instacart via Bright Data managed dataset
import analyze                      # data-reader brain behind "Overlay your data"
import planogram                    # benchmark + shelf-vision + pitch behind the Planogram app
import hi_analyst                   # the real Claude analyst behind Hoodie Intelligence Q&A
import prism                        # data contract behind the Prism mobile app
import places                       # restaurant / on-premise-accounts connector (Orlando first)
import census                       # US Census ACS demographics — reference-data connector (by county)
import enrich                       # join reference data (census) onto the outlet master by county
import tx_tabc                      # Texas TABC licenses (Socrata) — TX outlets + companies by county
import master                       # unify per-state outlet pulls into one normalized outlets_master
import chicago                      # Chicago liquor-licensed outlets (Socrata) — IL outlets + companies, geocoded
import ct_dcp                       # Connecticut liquor (Socrata) — CT premises + brand/supplier registry
import socrata_outlets              # GENERIC Socrata outlet connector — NY/CO/MO… as config, not modules
import warehouse                    # Parquet-on-Tigris (or local) queried by DuckDB
import upc                          # UPC/EAN QC + owned prefix->owner crosswalk (deterministic + inference)
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

VALID_CONNS = {"ttb-cola", "abc-fws", "specs", "binnys", "shopify-dtc", "instacart", "orlando-accounts", "census-acs", "tx-tabc", "il-chicago", "ct-dcp"} | set(socrata_outlets.VALID)
# Hosts served by an OWNED, dedicated scraper (search-form / bespoke) — not readable by the
# generalized Source Analyzer. If one is analyzed, we point the user to Pulls instead.
OWNED_HOSTS = {"ttbonline.gov": "ttb-cola", "abcfws.com": "abc-fws", "specsonline.com": "specs"}

def _dispatch_pull(conn, body):
    return (cola_pull(body) if conn == "ttb-cola"
            else abc_pull(body) if conn == "abc-fws"
            else specs_pull(body) if conn == "specs"
            else binnys_pull(body) if conn == "binnys"
            else shopify_pull(body) if conn == "shopify-dtc"
            else instacart_pull(body) if conn == "instacart"
            else places_pull(body) if conn == "orlando-accounts"
            else census_pull(body) if conn == "census-acs"
            else tx_pull(body) if conn == "tx-tabc"
            else il_pull(body) if conn == "il-chicago"
            else ct_pull(body) if conn == "ct-dcp"
            else socrata_pull(conn, body) if conn in socrata_outlets.VALID
            else fl_pull(conn) if conn in FL_CONN else None)

def socrata_pull(conn, body):
    """Generic Socrata outlet pull (NY/CO/MO…) → <state>_outlets, normalised to one schema. NY/CO
    ship a Socrata point so they land pre-geocoded; MO-style feeds geocode later like FL/TX."""
    started = int(time.time() * 1000)
    ds, runs, _ = socrata_outlets.pull(conn, log=lambda m: app.logger.info("SOCRATA %s", m))
    DATASETS.update(_absorb(ds)); save()
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = (body or {}).get("trigger", "manual")
    return run

def tx_pull(body):
    """Texas TABC active retail licenses (Socrata) → tx_outlets + tx_companies, county-keyed for
    the census join. Works from a datacenter (data.texas.gov, unlike the FL/TTB gov hosts)."""
    started = int(time.time() * 1000)
    ds, runs, _ = tx_tabc.pull(status=(body or {}).get("status", "Active"),
                               tier=(body or {}).get("tier", "Retail"),
                               log=lambda m: app.logger.info("TX %s", m))
    DATASETS.update(_absorb(ds)); save()
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = (body or {}).get("trigger", "manual")
    return run

def il_pull(body):
    """Chicago active liquor-licensed outlets (Socrata) → il_outlets + il_companies. Outlets ship
    with latitude/longitude from the portal (no geocoding) and county='Cook' (census-joinable)."""
    started = int(time.time() * 1000)
    ds, runs, _ = chicago.pull(log=lambda m: app.logger.info("IL %s", m))
    DATASETS.update(_absorb(ds)); save()
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = (body or {}).get("trigger", "manual")
    return run

def ct_pull(body):
    """Connecticut liquor (Socrata data.ct.gov) → ct_outlets (CT premises, geocodable) +
    ct_brands (product/supplier registry) + ct_companies. Harvest of what CT publishes cleanly."""
    started = int(time.time() * 1000)
    ds, runs, _ = ct_dcp.pull(log=lambda m: app.logger.info("CT %s", m))
    DATASETS.update(_absorb(ds)); save()
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = (body or {}).get("trigger", "manual")
    return run

def places_pull(body):
    """Run the Orlando on-premise-accounts pull (FL ABT -> normalize -> filter -> Parquet)."""
    return places.pull(county=(body or {}).get("county", places.ORLANDO_COUNTY))

def census_pull(body):
    """Pull US Census ACS demographics by county → lands as the `census_acs` dataset (joinable
    to outlets via county FIPS). Needs the free CENSUS_API_KEY; degrades with a clear warning
    otherwise. `state` scopes it (FIPS, or 'us' for all counties); default = FL/TX/IL."""
    started = int(time.time() * 1000)
    ds, runs, _ = census.pull(state=(body or {}).get("state"),
                              log=lambda m: app.logger.info("CENSUS %s", m))
    DATASETS.update(_absorb(ds)); save()
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = (body or {}).get("trigger", "manual")
    return run

def _new_job(conn):
    jid = "J-%d-%d" % (int(time.time()), len(JOBS) + 1)
    JOBS[jid] = {"id": jid, "connId": conn, "status": "running",
                 "startedAt": int(time.time() * 1000), "finishedAt": None,
                 "log": [], "run": None, "error": None}
    if len(JOBS) > 40:          # keep the last ~40 jobs
        for k in sorted(JOBS, key=lambda k: JOBS[k]["startedAt"])[:len(JOBS) - 40]:
            JOBS.pop(k, None)
    return jid

def _emit_pull_highlights(conn, rec):
    """An owned pull emits a highlight per landed extract — so the estate model pulses that dataset
    node (it matches on `dataset`) and the highlights feed shows owned pulls, not just recipe scrapes."""
    try:
        ts = int(time.time() * 1000)
        for e in (rec.get("extracts") or []):
            n = e.get("rows") or 0
            if n <= 0 or e.get("status") == "failed":
                continue
            HIGHLIGHTS.insert(0, {"host": conn, "dataset": e.get("id"), "engine": "owned pull",
                                  "count": n, "ts": ts,
                                  "headline": "%s · %s rows" % (_SRC_LABEL.get(conn, conn), format(n, ","))})
        del HIGHLIGHTS[30:]
        _save_json("highlights.json", HIGHLIGHTS)
    except Exception as ex:
        app.logger.warning("highlight emit failed for %s: %s", conn, ex)

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
        _emit_pull_highlights(conn, rec)   # so the estate model pulses + the feed shows owned pulls
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

# Opt-in CORS for cross-origin web clients (the production web app). Off by default;
# set API_CORS to an allowed origin (e.g. http://localhost:8082) or "*" to enable.
API_CORS = os.environ.get("API_CORS", "").strip()

@app.after_request
def _cors(resp):
    if API_CORS and request.path.startswith("/api/"):
        resp.headers["Access-Control-Allow-Origin"] = API_CORS
        if API_CORS != "*":
            resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    # Anti-scraping / no-index defense-in-depth on top of the OIDC gate. The suite iframes
    # its own apps (launcher, sources.html, mdm.html) so framing is SAMEORIGIN, not DENY.
    resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    # Always revalidate the CODE (html/js/css/json) so a deploy is picked up immediately —
    # browser heuristic caching was serving a stale launcher/app after a redeploy (a mix of
    # old shell + new app). ETag/Last-Modified still make it a cheap 304 when unchanged.
    ct = (resp.content_type or "")
    if ct.startswith("text/html") or "javascript" in ct or ct.startswith("text/css") or ct.startswith("application/json"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.after_request
def _gzip(resp):
    """Gzip large text/JSON responses (stdlib, no dep). The coverage map's /api/outlets/geo ships
    100k+ light points — very repetitive JSON that compresses ~5:1 — so the whole national footprint
    transfers in a few MB instead of tens. Guarded so it never touches streamed/binary/already-encoded
    responses."""
    try:
        if resp.direct_passthrough or resp.status_code != 200:
            return resp
        if "gzip" not in request.headers.get("Accept-Encoding", "") or resp.headers.get("Content-Encoding"):
            return resp
        ct = resp.content_type or ""
        if not (ct.startswith("application/json") or ct.startswith("text/") or "javascript" in ct):
            return resp
        data = resp.get_data()
        if len(data) < 1024:
            return resp
        resp.set_data(gzip.compress(data, 5))
        resp.headers["Content-Encoding"] = "gzip"
        resp.headers["Content-Length"] = str(len(resp.get_data()))
        resp.headers["Vary"] = "Accept-Encoding"
    except Exception:
        pass
    return resp


# Light per-IP rate limit — the OIDC gate already restricts to allowlisted humans; this
# just stops an authenticated session (or the login endpoint) from being hammered to pull
# the whole book/catalog fast. In-memory sliding window (single Fly machine). Generous for
# a human, restrictive for a scraper. Tune with RATE_MAX / RATE_WINDOW; 0 disables.
_RATE = {}
RATE_MAX    = int(os.environ.get("RATE_MAX", "600"))     # requests per window per IP on /api
RATE_WINDOW = int(os.environ.get("RATE_WINDOW", "60"))   # seconds

def _client_ip():
    return (request.headers.get("Fly-Client-IP")
            or (request.headers.get("X-Forwarded-For", "").split(",")[0].strip())
            or request.remote_addr or "?")

@app.before_request
def _ratelimit():
    if RATE_MAX <= 0 or not request.path.startswith("/api/") or request.path == "/api/health":
        return
    now = time.time(); ip = _client_ip()
    hits = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
    hits.append(now); _RATE[ip] = hits
    if len(_RATE) > 5000:                                   # cap memory: drop the coldest IPs
        for k in [k for k, v in list(_RATE.items()) if not v or now - v[-1] > RATE_WINDOW]:
            _RATE.pop(k, None)
    if len(hits) > RATE_MAX:
        return jsonify(ok=False, error="rate limited — slow down"), 429


@app.get("/robots.txt")
def robots():
    # Belt-and-suspenders: the OIDC gate already blocks anonymous crawlers; this tells the
    # polite ones to stay out entirely and keeps the site out of search indexes.
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

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
# Service desk — data-quality reports raised from the suite (e.g. clicking a "degraded"
# pill in Hoodie Pulls). Logged here, surfaced in the CRM's Service tab, tracked to
# resolution. Same disk-or-S3 persistence as the rest of the agent state.
SERVICE  = load("service_reports.json", [])
# Self-reinforcing scrape recipes — host -> proven config + validation baseline.
# Each analyze/extract folds into this book (see recipes.py); persisted like the rest.
RECIPES  = load("recipes.json", {})
# Scrape highlights — a rolling feed of 'what the last pulls yielded' (counts, ranges,
# geo, top categories), surfaced as a card on completion and in Hoodie Intelligence.
HIGHLIGHTS = load("highlights.json", [])
# Per-account planograms — shelf facings for an account (entered by hand or derived from
# a photo in the Planogram tool). Keyed by account id; persisted so the numbers stick.
PLANOGRAM = load("planograms.json", {})
# Admin console: usage events (app opens, per user) + feature flags (app visibility /
# status overrides, feature toggles). Both persisted with the rest of the agent state.
USAGE = load("usage.json", [])
FLAGS = load("admin_flags.json", {"apps": {}, "features": {}})
# UPC prefix->owner crosswalk (built by upc_crosswalk.py from enriched COLA; empty until uploaded).
# Deterministic UPC checks work with or without it; owner-agreement activates once it's present.
UPC_XWALK = load("upc_crosswalk.json", {})
# Hoodie Relations extras — per-account delivery day + goals (goals matched to the
# master via relations.match_goal). Keyed by account id; persisted so it's reportable.
RELATIONS = load("relations.json", {})

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
    # keep a tiny index (did → header+total) so /api/catalog can list the full store without reading
    # every (possibly 30MB) blob — this is what lets the estate model see save_full'd datasets.
    try:
        idx = _full_index(); idx[did] = {"header": header, "total": len(rows)}
        blob = json.dumps(idx)
        if STATE_BUCKET:
            _s3().put_object(Bucket=STATE_BUCKET, Key=_key("full/_index.json"),
                             Body=blob.encode("utf-8"), ContentType="application/json")
        else:
            open(os.path.join(FULL_DIR, "_index.json"), "w").write(blob)
    except Exception as e:
        app.logger.warning("full index update %s failed: %s", did, e)

def _full_index():
    """did → {header, total} for everything in the full store (from full/_index.json)."""
    if STATE_BUCKET:
        try:
            obj = _s3().get_object(Bucket=STATE_BUCKET, Key=_key("full/_index.json"))
            return json.loads(obj["Body"].read())
        except Exception:
            return {}
    try:
        return json.load(open(os.path.join(FULL_DIR, "_index.json")))
    except Exception:
        return {}

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
def _fl_looks_blocked(t):
    head = (t or "")[:800].lower()
    return ("just a moment" in head) or ("cloudflare" in head) or ("<html" in head and "cf-" in head)

def _fl_fetch(url):
    """Download an FL DBPR extract. FL's host (myfloridalicense.com) is now behind Cloudflare, so:
    try direct first; if it 403s / returns a 'Just a moment' challenge, fall back to Bright Data
    Web Unlocker (which solves Cloudflare — needs BRIGHTDATA_API_KEY). Returns (csv_text, via)."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; UnifydMDM/1.0; +data-pipeline)"})
        txt = urllib.request.urlopen(req, timeout=180).read().decode("utf-8", "replace")
        if txt and not _fl_looks_blocked(txt):
            return txt, "direct"
    except Exception:
        pass                                     # 403 / cert / timeout → try the unlocker
    try:
        import brightdata
    except Exception:
        brightdata = None
    if brightdata and brightdata.enabled():
        txt = brightdata.fetch(url, data_format="html", timeout=180)
        if txt and not _fl_looks_blocked(txt):
            return txt, "bright-data"
        raise RuntimeError("Bright Data returned a blocked/empty response for FL")
    raise RuntimeError("Cloudflare-blocked — set BRIGHTDATA_API_KEY to unlock FL DBPR (Web Unlocker)")

def fl_pull(conn_id):
    started = int(time.time() * 1000); exs = []; warns = []
    extracts = FL_CONN[conn_id]
    for i, (eid, hashdr, n) in enumerate(extracts):
        try:
            app.logger.info("downloading %s (%d/%d) from Florida…", eid, i + 1, len(extracts))
            txt, via = _fl_fetch(f"{FL_BASE}/{eid}.csv")
            app.logger.info("%s: %s KB downloaded via %s, parsing…", eid, len(txt) // 1024, via)
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
            msg = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in msg or "local issuer" in msg:
                msg = "TLS certificate verify failed for the FL data host (CA chain incomplete on this host)"
            elif "403" in msg or "Forbidden" in msg:
                msg = "Cloudflare-blocked — set BRIGHTDATA_API_KEY to unlock FL DBPR via Web Unlocker"
            warns.append("%s: %s" % (eid, msg[:140]))
            exs.append({"id": eid, "rows": 0, "delta": 0, "status": "failed"})
    fin = int(time.time() * 1000)
    status = "failed" if all(e["status"] == "failed" for e in exs) else \
             "partial" if any(e["status"] == "failed" for e in exs) else "success"
    return {"id": "R-" + format(int(time.time()) % 100000, "05d"), "connId": conn_id,
            "startedAt": started, "finishedAt": fin, "durationMs": fin - started,
            "status": status, "trigger": "manual", "total": sum(e["rows"] for e in exs),
            "degraded": status == "partial", "warnings": warns, "extracts": exs}

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
    return jsonify(ok=True, agent="unifyd-local", sources=list(FL_CONN) + ["ttb-cola", "abc-fws", "specs", "binnys", "shopify-dtc", "instacart", "census-acs"],
                   datasets=len(DATASETS), runs=len(RUNS),
                   state=("s3:" + STATE_BUCKET) if STATE_BUCKET else "disk",
                   warehouse=("tigris:" + os.environ.get("BUCKET_NAME", "")) if warehouse.remote() else "local")

# Source labels + a first-cut scope tree derived from the pulled data.
_SRC_LABEL = {"fl-items": "Florida — Items", "fl-outlets": "Florida — Outlets",
              "ttb-cola": "TTB — COLA Labels", "abc-fws": "ABC FWS — Inventory",
              "specs": "Spec's — Inventory", "binnys": "Binny's — Inventory",
              "shopify-dtc": "Hemp + DTC — Shopify", "instacart": "Instacart — Store-level",
              "census-acs": "US Census — ACS demographics", "tx-tabc": "Texas TABC — licenses",
              "il-chicago": "Chicago — Liquor Licenses", "ct-dcp": "Connecticut — Liquor (DCP)",
              "ny-sla": "New York — SLA licenses", "co-led": "Colorado — Liquor licenses",
              "mo-atc": "Missouri — Alcohol licenses"}
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

@app.get("/api/catalog")
def catalog_ep():
    """Every dataset we actually hold, across ALL THREE stores — the estate model's 'whole thing'
    view (it polls this so new datasets appear on their own). {key: {header, total, store}}. Light:
    header + count + store only, no row data. memory = in-RAM sample; full = save_full'd on Tigris;
    warehouse = Parquet. Precedence memory → full → warehouse so a real count wins over a stub."""
    out = {}
    for k, ds in DATASETS.items():
        out[k] = {"header": ds.get("header") or [],
                  "total": ds.get("total") or len(ds.get("rows") or []), "store": "memory"}
    for k, v in (_full_index() or {}).items():
        if k not in out or not out[k].get("total"):
            out[k] = {"header": v.get("header") or [], "total": v.get("total") or 0, "store": "full"}
    try:
        import warehouse
        for d in warehouse.list_datasets():
            k = d.get("name", "")
            if k and not k.startswith("_"):
                out.setdefault(k, {"header": d.get("fields") or [], "total": d.get("rows") or 0,
                                   "store": "warehouse"})
    except Exception as e:
        app.logger.warning("warehouse catalog failed: %s", e)
    return jsonify(out)

# ── Product registrations (TTB COLA) — label-approval filings + distinct-product clusters ──
def _cola_q(name, sql, params=None):
    import warehouse
    return warehouse.query(name, sql, params or [])

@app.get("/api/cola/stats")
def cola_stats_ep():
    """Overview for the product-registration page: total filings, distinct product clusters, UPC
    coverage, filings-by-year, top class/types + applicants. Reads the warehouse Parquet via DuckDB."""
    try:
        total = _cola_q("ttb_cola", "SELECT count(*) c FROM t")[0]["c"]
    except Exception:
        return jsonify(ok=True, landed=False, total=0, note="ttb_cola not landed yet")
    def q(sql):
        try: return _cola_q("ttb_cola", sql)
        except Exception: return []
    by_year = q("SELECT substr(\"Completed Date\",7,4) yr, count(*) n FROM t "
                "WHERE length(\"Completed Date\")>=10 GROUP BY 1 ORDER BY yr")
    top_class = q("SELECT \"Class/Type\" k, count(*) n FROM t WHERE \"Class/Type\"<>'' GROUP BY 1 ORDER BY n DESC LIMIT 12")
    top_appl = q("SELECT \"Applicant\" k, count(*) n FROM t WHERE \"Applicant\"<>'' GROUP BY 1 ORDER BY n DESC LIMIT 12")
    upc = q("SELECT count(*) c FROM t WHERE \"UPC\"<>''")
    clusters = None
    try: clusters = _cola_q("cola_cluster", "SELECT count(*) c FROM t")[0]["c"]
    except Exception: pass
    return jsonify(ok=True, landed=True, total=total, clusters=clusters,
                   with_upc=(upc[0]["c"] if upc else 0), by_year=by_year,
                   top_class=top_class, top_applicant=top_appl)

@app.get("/api/cola/registrations")
def cola_regs_ep():
    """Paged + searchable COLA filings (brand / applicant / TTB ID / UPC), server-side over ~1M+ rows."""
    q = (request.args.get("q") or "").strip()
    try:
        off = max(0, int(request.args.get("offset", "0") or 0)); lim = min(200, max(1, int(request.args.get("limit", "50") or 50)))
    except ValueError:
        off, lim = 0, 50
    where, params = "1=1", []
    if q:
        where = "(\"Brand Name\" ILIKE ? OR \"Applicant\" ILIKE ? OR \"TTB ID\"=? OR \"UPC\"=?)"
        params = ["%" + q + "%", "%" + q + "%", q, q]
    cols = '"TTB ID","Brand Name","Fanciful Name","Class/Type","Origin","Applicant","Completed Date","Net Contents","UPC"'
    try:
        total = _cola_q("ttb_cola", "SELECT count(*) c FROM t WHERE " + where, params)[0]["c"]
        rows = _cola_q("ttb_cola", "SELECT %s FROM t WHERE %s ORDER BY \"TTB ID\" DESC LIMIT %d OFFSET %d"
                       % (cols, where, lim, off), params)
    except Exception as e:
        return jsonify(ok=False, landed=False, error=str(e)[:140], rows=[]), 200
    return jsonify(ok=True, total=total, offset=off, limit=lim, rows=rows)

@app.get("/api/cola/clusters")
def cola_clusters_ep():
    """Paged distinct-PRODUCT clusters (collapsed filing noise) — brand/fanciful/class/size/supplier,
    member count, confidence, flagged. From cola_cluster (the scale dedup layer)."""
    q = (request.args.get("q") or "").strip()
    try:
        off = max(0, int(request.args.get("offset", "0") or 0)); lim = min(200, max(1, int(request.args.get("limit", "50") or 50)))
    except ValueError:
        off, lim = 0, 50
    where, params = "1=1", []
    if q:
        where = "(brand ILIKE ? OR fanciful ILIKE ? OR supplier ILIKE ?)"
        params = ["%" + q + "%"] * 3
    try:
        total = _cola_q("cola_cluster", "SELECT count(*) c FROM t WHERE " + where, params)[0]["c"]
        rows = _cola_q("cola_cluster", "SELECT cluster_id, brand, fanciful, class_type, size_ml, supplier, "
                       "member_count, confidence, flagged FROM t WHERE %s ORDER BY member_count DESC LIMIT %d OFFSET %d"
                       % (where, lim, off), params)
    except Exception as e:
        return jsonify(ok=False, landed=False, error=str(e)[:140], rows=[]), 200
    return jsonify(ok=True, total=total, offset=off, limit=lim, rows=rows)

COLA_FIELDS = ["Brand Name", "Fanciful Name", "Class/Type", "Origin", "Applicant",
               "Status", "Net Contents", "UPC"]

@app.get("/api/cola/profile")
def cola_profile_ep():
    """Field-level DATA DICTIONARY for ttb_cola — per column: fill-rate + distinct cardinality + a few
    samples. The basis for string extraction: which registration fields carry structured signal worth
    parsing into controlled vocabularies + derived fields."""
    try:
        total = _cola_q("ttb_cola", "SELECT count(*) c FROM t")[0]["c"]
    except Exception:
        return jsonify(ok=True, landed=False, fields=[])
    out = []
    for col in COLA_FIELDS:
        try:
            r = _cola_q("ttb_cola", 'SELECT count(*) FILTER (WHERE "%s"<>\'\') filled, '
                        'count(DISTINCT "%s") dct FROM t' % (col, col))[0]
            samp = _cola_q("ttb_cola", 'SELECT "%s" v FROM t WHERE "%s"<>\'\' LIMIT 3' % (col, col))
        except Exception:
            continue
        out.append({"field": col, "filled": r["filled"], "distinct": r["dct"],
                    "fill_pct": round(100 * r["filled"] / max(1, total), 1),
                    "samples": [s["v"] for s in samp]})
    return jsonify(ok=True, landed=True, total=total, fields=out)

@app.get("/api/cola/dictionary")
def cola_dictionary_ep():
    """The value DICTIONARY (controlled vocabulary) for one field: distinct values + counts, desc —
    the raw material for a data dictionary and for deriving a normalized field from free text."""
    field = (request.args.get("field") or "Class/Type").strip()
    if field not in COLA_FIELDS:
        return jsonify(ok=False, error="unknown field"), 400
    try:
        lim = min(500, max(1, int(request.args.get("limit", "150") or 150)))
    except ValueError:
        lim = 150
    q = (request.args.get("q") or "").strip()
    where, params = '"%s"<>\'\'' % field, []
    if q:
        where += ' AND "%s" ILIKE ?' % field; params = ["%" + q + "%"]
    try:
        distinct = _cola_q("ttb_cola", 'SELECT count(DISTINCT "%s") c FROM t WHERE %s' % (field, where), params)[0]["c"]
        rows = _cola_q("ttb_cola", 'SELECT "%s" v, count(*) n FROM t WHERE %s GROUP BY 1 ORDER BY n DESC LIMIT %d'
                       % (field, where, lim), params)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:140], values=[]), 200
    return jsonify(ok=True, field=field, distinct=distinct, values=rows)

# ── Generic field dictionary — profile + value vocabulary for ANY item/product dataset ──
def _ds_columns(ds):
    try:
        s = _cola_q(ds, "SELECT * FROM t LIMIT 1")
        return list(s[0].keys()) if s else []
    except Exception:
        return []

@app.get("/api/item/profile")
def item_profile_ep():
    """Field-level DATA DICTIONARY for ANY warehouse item/product dataset (?dataset=bc_liquor,
    or_pricing, iowa_products, ttb_cola…). Introspects columns, then per column: fill-rate + distinct
    cardinality + samples — the basis for string extraction into derived fields on any dataset."""
    ds = (request.args.get("dataset") or "ttb_cola").strip()
    cols = _ds_columns(ds)[:24]
    if not cols:
        return jsonify(ok=True, landed=False, dataset=ds, fields=[])
    try:
        total = _cola_q(ds, "SELECT count(*) c FROM t")[0]["c"]
    except Exception:
        return jsonify(ok=True, landed=False, dataset=ds, fields=[])
    out = []
    for col in cols:
        if col.startswith(":@") or col.startswith("_"):
            continue
        try:
            r = _cola_q(ds, 'SELECT count(*) FILTER (WHERE CAST("%s" AS VARCHAR)<>\'\') filled, '
                        'count(DISTINCT "%s") dct FROM t' % (col, col))[0]
            samp = _cola_q(ds, 'SELECT "%s" v FROM t WHERE CAST("%s" AS VARCHAR)<>\'\' LIMIT 3' % (col, col))
        except Exception:
            continue
        out.append({"field": col, "filled": r["filled"], "distinct": r["dct"],
                    "fill_pct": round(100 * r["filled"] / max(1, total), 1),
                    "samples": [str(s["v"]) for s in samp]})
    return jsonify(ok=True, landed=True, dataset=ds, total=total, fields=out)

@app.get("/api/item/dictionary")
def item_dictionary_ep():
    """Value DICTIONARY (controlled vocabulary) for one field of ANY item dataset: distinct values +
    counts, desc. ?dataset=&field=&q=. The raw material for deriving a normalized field from text."""
    ds = (request.args.get("dataset") or "ttb_cola").strip()
    field = (request.args.get("field") or "").strip()
    if field not in _ds_columns(ds):
        return jsonify(ok=False, error="unknown field for dataset"), 400
    try:
        lim = min(500, max(1, int(request.args.get("limit", "150") or 150)))
    except ValueError:
        lim = 150
    q = (request.args.get("q") or "").strip()
    where, params = 'CAST("%s" AS VARCHAR)<>\'\'' % field, []
    if q:
        where += ' AND CAST("%s" AS VARCHAR) ILIKE ?' % field; params = ["%" + q + "%"]
    try:
        distinct = _cola_q(ds, 'SELECT count(DISTINCT "%s") c FROM t WHERE %s' % (field, where), params)[0]["c"]
        rows = _cola_q(ds, 'SELECT CAST("%s" AS VARCHAR) v, count(*) n FROM t WHERE %s GROUP BY 1 ORDER BY n DESC LIMIT %d'
                       % (field, where, lim), params)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:140], values=[]), 200
    return jsonify(ok=True, dataset=ds, field=field, distinct=distinct, values=rows)

# ── Field mapping — persist source.field → master.field crosswalks, with pre/post transforms ──
DEFAULT_MASTER_FIELDS = [
    {"name": "brand", "type": "string", "desc": "Brand name"},
    {"name": "product_name", "type": "string", "desc": "Full product / long name"},
    {"name": "category", "type": "string", "desc": "Category / class-type"},
    {"name": "packsize", "type": "string", "desc": "Pack / container size (as filed)"},
    {"name": "size_ml", "type": "number", "desc": "Net contents in mL (derived)"},
    {"name": "abv", "type": "number", "desc": "Alcohol % by volume"},
    {"name": "upc", "type": "string", "desc": "UPC / GTIN — auto-normalized to GTIN-14", "normalize": "upc"},
    {"name": "price", "type": "number", "desc": "Price"},
    {"name": "supplier", "type": "string", "desc": "Supplier / vendor"},
    {"name": "origin", "type": "string", "desc": "Country / region of origin"},
]
# Data-dictionary transforms that can apply BEFORE (on the source value) or AFTER (on the mapped
# master value). Names only here — the apply-engine consumes them when materializing the master.
MAP_TRANSFORMS = ["none", "trim", "upper", "lower", "title_case", "digits_only",
                  "size_to_ml", "year_from_date"]
# Canonical normalizers keyed by master field NAME — ensured on read so the 'lowest level of
# consistency' contract holds even for a schema saved before a normalizer was added.
_NORMALIZE_DEFAULTS = {"upc": "upc", "gtin": "upc"}

def _master_schema():
    fields = load("master_schema.json", DEFAULT_MASTER_FIELDS)
    for f in fields:
        if isinstance(f, dict) and not f.get("normalize") and f.get("name") in _NORMALIZE_DEFAULTS:
            f["normalize"] = _NORMALIZE_DEFAULTS[f["name"]]
    return fields

@app.get("/api/master/schema")
def master_schema_get():
    return jsonify(ok=True, fields=_master_schema(), transforms=MAP_TRANSFORMS)

@app.post("/api/master/schema")
def master_schema_post():
    """Create a master field (start building the master schema) or replace the whole set."""
    body = request.get_json(silent=True) or {}
    fields = load("master_schema.json", DEFAULT_MASTER_FIELDS)
    if isinstance(body.get("fields"), list):
        fields = body["fields"]
    else:
        nm = (body.get("name") or "").strip()
        if not nm:
            return jsonify(ok=False, error="name required"), 400
        if not any(f.get("name") == nm for f in fields):
            fields.append({"name": nm, "type": body.get("type", "string"), "desc": body.get("desc", "")})
    _save_json("master_schema.json", fields)
    return jsonify(ok=True, fields=fields)

@app.get("/api/mappings")
def mappings_get():
    """Persisted field mappings. ?dataset= for one source dataset's rows, else the whole map."""
    ds = (request.args.get("dataset") or "").strip()
    m = load("field_mappings.json", {})
    return jsonify(ok=True, dataset=ds or None, mappings=(m.get(ds, []) if ds else m))

@app.post("/api/mappings")
def mappings_post():
    """Persist a source dataset's mapping rows: [{source_field, master_field, pre, post}].
    pre/post are data-dictionary transform names applied before/after the map."""
    body = request.get_json(silent=True) or {}
    ds = (body.get("dataset") or "").strip()
    if not ds:
        return jsonify(ok=False, error="dataset required"), 400
    m = load("field_mappings.json", {})
    m[ds] = body.get("mappings", [])
    _save_json("field_mappings.json", m)
    return jsonify(ok=True, dataset=ds, count=len(m[ds]))

@app.post("/api/master/preview")
def master_preview_ep():
    """Apply ONE derivation rule to a sample of the dataset → [{raw, derived}], so a rule can be
    verified before it's committed. Body: {dataset, rule:{source_field,mode,pre,post,pattern,group,map,expr}}."""
    body = request.get_json(silent=True) or {}
    ds = (body.get("dataset") or "").strip()
    if not ds:
        return jsonify(ok=False, error="dataset required"), 400
    try:
        import master_apply
        rule = body.get("rule") or {}
        fields = _master_schema()
        nz = next((f.get("normalize") for f in fields if isinstance(f, dict) and f.get("name") == rule.get("master_field")), None)
        rows = master_apply.preview(ds, rule, limit=int(body.get("limit", 12)), normalize=nz)
        return jsonify(ok=True, dataset=ds, normalize=nz, rows=rows)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:180]), 200

@app.post("/api/master/apply")
def master_apply_ep():
    """Materialize dim_product from ALL persisted mappings + the master schema (one DuckDB pass per
    source over Parquet → UNION → warehouse). Returns per-source counts + any skipped sources."""
    try:
        import master_apply
        fields = _master_schema()
        maps = load("field_mappings.json", {})
        res = master_apply.build(fields, maps, log=lambda mm: app.logger.info("APPLY %s", mm))
        return jsonify(ok=True, **res)
    except Exception as e:
        app.logger.exception("apply failed")
        return jsonify(ok=False, error=str(e)[:200]), 200

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


# ─────────────────────────────────────────────────────────────────────────────
# Service desk — data-quality reports (raised from the suite, tracked in the CRM)
# ─────────────────────────────────────────────────────────────────────────────
_SERVICE_STATES = ("open", "acknowledged", "reported", "resolved")

def save_service():
    name = "service_reports.json"
    if STATE_BUCKET:
        try:
            _s3().put_object(Bucket=STATE_BUCKET, Key=_key(name),
                             Body=json.dumps(SERVICE).encode("utf-8"),
                             ContentType="application/json")
        except Exception as e:
            app.logger.warning("S3 save %s failed: %s", name, e)
    else:
        try:
            json.dump(SERVICE, open(os.path.join(STATE_DIR, name), "w"))
        except Exception as e:
            app.logger.warning("save %s failed: %s", name, e)

@app.get("/api/service/reports")
def service_list():
    st = (request.args.get("status") or "").strip().lower()
    items = SERVICE if st not in _SERVICE_STATES else [r for r in SERVICE if r.get("status") == st]
    counts = {s: sum(1 for r in SERVICE if r.get("status") == s) for s in _SERVICE_STATES}
    return jsonify(ok=True, reports=items[:500], total=len(SERVICE), counts=counts)

@app.post("/api/service/reports")
def service_create():
    b = request.get_json(force=True, silent=True) or {}
    now = int(time.time() * 1000)
    by = (b.get("reporter") or "field").strip()[:120]
    rec = {
        "id": "SR-%d" % now,
        "kind": (b.get("kind") or "data-quality").strip()[:40],
        "connId": (b.get("connId") or "").strip()[:80],
        "source": (b.get("source") or b.get("connId") or "—").strip()[:160],
        "dataset": (b.get("dataset") or "").strip()[:160],
        "severity": (b.get("severity") or "degraded").strip()[:40],
        "warnings": [str(w)[:300] for w in (b.get("warnings") or [])][:20],
        "note": (b.get("note") or "").strip()[:2000],
        "url": (b.get("url") or "").strip()[:400],
        "reporter": by,
        "status": "open",
        "createdAt": now, "updatedAt": now,
        "history": [{"at": now, "status": "open", "by": by}],
    }
    SERVICE.insert(0, rec); del SERVICE[1000:]; save_service()
    return jsonify(ok=True, report=rec)

@app.patch("/api/service/reports/<rid>")
def service_update(rid):
    b = request.get_json(force=True, silent=True) or {}
    rec = next((r for r in SERVICE if r.get("id") == rid), None)
    if not rec:
        return jsonify(ok=False, error="not found"), 404
    now = int(time.time() * 1000)
    st = (b.get("status") or "").strip().lower()
    if st in _SERVICE_STATES:
        rec["status"] = st
        rec.setdefault("history", []).append({"at": now, "status": st, "by": (b.get("by") or "ops")})
    if "note" in b:
        rec["note"] = (b.get("note") or "").strip()[:2000]
    rec["updatedAt"] = now
    save_service()
    return jsonify(ok=True, report=rec)


# ─────────────────────────────────────────────────────────────────────────────
# Per-account planograms — shelf facings that persist (Planogram tab in the CRM)
# ─────────────────────────────────────────────────────────────────────────────
def save_planogram():
    name = "planograms.json"
    if STATE_BUCKET:
        try:
            _s3().put_object(Bucket=STATE_BUCKET, Key=_key(name),
                             Body=json.dumps(PLANOGRAM).encode("utf-8"),
                             ContentType="application/json")
        except Exception as e:
            app.logger.warning("S3 save %s failed: %s", name, e)
    else:
        try:
            json.dump(PLANOGRAM, open(os.path.join(STATE_DIR, name), "w"))
        except Exception as e:
            app.logger.warning("save %s failed: %s", name, e)

def _plano_summary(rec):
    rows = rec.get("rows") or []
    ours = sum((r.get("facings") or 0) for r in rows)
    bench = sum((r.get("benchmark") or 0) for r in rows)
    return {"accountId": rec.get("accountId"), "accountName": rec.get("accountName"),
            "skus": len(rows), "facings": ours, "benchmark": bench, "gap": ours - bench,
            "source": rec.get("source") or "manual", "updatedAt": rec.get("updatedAt")}

@app.get("/api/planogram/accounts")
def planogram_list():
    return jsonify(ok=True, planograms=[_plano_summary(r) for r in PLANOGRAM.values()])

@app.get("/api/planogram/accounts/<aid>")
def planogram_get(aid):
    rec = PLANOGRAM.get(aid) or {"accountId": aid, "accountName": None, "rows": [],
                                  "notes": "", "source": "manual", "updatedAt": None}
    return jsonify(ok=True, planogram=rec)

@app.put("/api/planogram/accounts/<aid>")
def planogram_put(aid):
    b = request.get_json(force=True, silent=True) or {}
    now = int(time.time() * 1000)
    rows = []
    for r in (b.get("rows") or [])[:200]:
        rows.append({
            "item": (r.get("item") or "").strip()[:160],
            "facings": max(0, int(r.get("facings") or 0)),
            "benchmark": max(0, int(r.get("benchmark") or 0)),
        })
    PLANOGRAM[aid] = {
        "accountId": aid,
        "accountName": (b.get("accountName") or (PLANOGRAM.get(aid) or {}).get("accountName")),
        "rows": rows,
        "notes": (b.get("notes") or "").strip()[:2000],
        "source": (b.get("source") or "manual").strip()[:20],
        "updatedAt": now,
    }
    save_planogram()
    return jsonify(ok=True, planogram=PLANOGRAM[aid])


# ─────────────────────────────────────────────────────────────────────────────
# Admin console — usage reporting, feature flags, access, ops snapshot
# ─────────────────────────────────────────────────────────────────────────────
def _current_user():
    try:
        return session.get("email") or None
    except Exception:
        return None

def _save_json(name, obj):
    if STATE_BUCKET:
        try:
            _s3().put_object(Bucket=STATE_BUCKET, Key=_key(name),
                             Body=json.dumps(obj).encode("utf-8"), ContentType="application/json")
        except Exception as e:
            app.logger.warning("S3 save %s failed: %s", name, e)
    else:
        try:
            json.dump(obj, open(os.path.join(STATE_DIR, name), "w"))
        except Exception as e:
            app.logger.warning("save %s failed: %s", name, e)

@app.post("/api/usage/event")
def usage_event():
    b = request.get_json(force=True, silent=True) or {}
    app_id = (b.get("app") or "").strip()[:60]
    if not app_id:
        return jsonify(ok=False, error="app required"), 400
    USAGE.append({"app": app_id, "user": (_current_user() or b.get("user") or "local")[:120],
                  "ts": int(time.time() * 1000)})
    del USAGE[:-20000]                     # keep a rolling window
    _save_json("usage.json", USAGE)
    return jsonify(ok=True)

@app.get("/api/usage/summary")
def usage_summary():
    try:
        days = max(1, min(365, int(request.args.get("days", "30"))))
    except Exception:
        days = 30
    cutoff = int(time.time() * 1000) - days * 86400000
    ev = [e for e in USAGE if (e.get("ts") or 0) >= cutoff]
    by_app, by_user, by_day = {}, {}, {}
    for e in ev:
        by_app[e["app"]] = by_app.get(e["app"], 0) + 1
        by_user[e["user"]] = by_user.get(e["user"], 0) + 1
        day = datetime.datetime.utcfromtimestamp((e.get("ts") or 0) / 1000).strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0) + 1
    top = lambda d: sorted(([k, v] for k, v in d.items()), key=lambda x: -x[1])
    return jsonify(ok=True, days=days, totalOpens=len(ev), totalAllTime=len(USAGE),
                   activeUsers=len(by_user), byApp=top(by_app), byUser=top(by_user),
                   byDay=sorted(([k, v] for k, v in by_day.items())))

@app.get("/api/admin/flags")
def admin_flags_get():
    return jsonify(ok=True, flags=FLAGS)

@app.put("/api/admin/flags")
def admin_flags_put():
    b = request.get_json(force=True, silent=True) or {}
    if isinstance(b.get("apps"), dict):
        FLAGS["apps"] = b["apps"]
    if isinstance(b.get("features"), dict):
        FLAGS["features"] = b["features"]
    _save_json("admin_flags.json", FLAGS)
    return jsonify(ok=True, flags=FLAGS)

@app.get("/api/admin/access")
def admin_access():
    try:
        allow = auth_gate._allowed_emails()
    except Exception:
        allow = []
    users = sorted({e.get("user") for e in USAGE if e.get("user")})
    return jsonify(ok=True, gated=auth_gate.enabled(), currentUser=_current_user(),
                   allowlist=sorted(allow), seenUsers=users)

@app.get("/api/admin/overview")
def admin_overview():
    # book value (best-effort) + open service reports + run count + 7d usage
    book_val = None
    try:
        import book as _book
        s = _book.summary() if hasattr(_book, "summary") else None
        if isinstance(s, dict):
            book_val = s.get("revenue") or s.get("total") or s.get("value")
    except Exception:
        pass
    week = int(time.time() * 1000) - 7 * 86400000
    ev7 = [e for e in USAGE if (e.get("ts") or 0) >= week]
    return jsonify(ok=True,
                   apiOk=True,
                   warehouse=bool(STATE_BUCKET) or True,
                   opens7d=len(ev7),
                   activeUsers7d=len({e.get("user") for e in ev7}),
                   openReports=sum(1 for r in SERVICE if r.get("status") == "open"),
                   runs=len(RUNS),
                   planograms=len(PLANOGRAM),
                   bookValue=book_val)


# ─────────────────────────────────────────────────────────────────────────────
# Hoodie Relations — per-account delivery day + goals (goals matched to the master)
# ─────────────────────────────────────────────────────────────────────────────
def _rel(aid):
    return RELATIONS.get(aid) or {"accountId": aid, "deliveryDay": None, "goals": []}

@app.get("/api/relations/accounts")
def relations_list():
    # reportable roll-up: every account with extras
    out = []
    for aid, r in RELATIONS.items():
        out.append({"accountId": aid, "accountName": r.get("accountName"),
                    "deliveryDay": r.get("deliveryDay"), "goals": len(r.get("goals") or [])})
    return jsonify(ok=True, accounts=out)

@app.get("/api/relations/accounts/<aid>")
def relations_get(aid):
    return jsonify(ok=True, account=_rel(aid))

@app.put("/api/relations/accounts/<aid>")
def relations_put(aid):
    b = request.get_json(force=True, silent=True) or {}
    now = int(time.time() * 1000)
    cur = RELATIONS.get(aid) or {}
    rec = {
        "accountId": aid,
        "accountName": (b.get("accountName") or cur.get("accountName")),
        "deliveryDay": (b.get("deliveryDay") if "deliveryDay" in b else cur.get("deliveryDay")),
        "goals": (b.get("goals") if "goals" in b else (cur.get("goals") or [])),
        "updatedAt": now,
    }
    # bound the goals list a little
    if isinstance(rec["goals"], list):
        rec["goals"] = rec["goals"][:50]
    RELATIONS[aid] = rec
    _save_json("relations.json", RELATIONS)
    return jsonify(ok=True, account=rec)

@app.post("/api/relations/goals/match")
def relations_goal_match():
    b = request.get_json(force=True, silent=True) or {}
    import relations
    match = relations.match_goal((b.get("text") or ""), b.get("accountName"))
    return jsonify(ok=True, match=match, llm=relations.llm_enabled())


# ─────────────────────────────────────────────────────────────────────────────
# Generalized scraper — Claude reads a target URL and returns the data + how to scrape it
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/scraper/analyze")
def scraper_analyze():
    b = request.get_json(force=True, silent=True) or {}
    import source_analyzer, recipes
    url = b.get("url") or ""
    result = source_analyzer.analyze(url, b.get("goal"))
    # If the live page is bot-walled but we already learned this source, fall back to the
    # saved recipe instead of dead-ending — the API + field map are already known.
    if "error" in result and result.get("blocked"):
        synth = recipes.analysis_from_recipe(recipes.get(RECIPES, url))
        if synth:
            result = synth
        else:
            # Some hosts are OWNED connectors with a dedicated scraper (search-form / bespoke) —
            # the generalized analyzer can't read them. Point to Pulls instead of a raw fetch error.
            host = recipes.host_of(url)
            conn = OWNED_HOSTS.get(host)
            if conn:
                result = {"error": "This is an owned connector (%s) with a dedicated scraper — the "
                          "generalized analyzer can't read its page. Run it from Pulls (Hoodie MDM → "
                          "Pulls), where it pulls through the engine pipeline." % conn,
                          "owned_connector": conn, "blocked": True, "attempts": result.get("attempts")}
    recipe = None
    if "error" not in result:
        if not result.get("from_recipe"):           # stash a freshly-read config as a candidate
            recipes.save_config(RECIPES, url, result, int(time.time() * 1000))
            _save_json("recipes.json", RECIPES)
        rec = recipes.get(RECIPES, url)
        recipe = {"host": rec["host"], "platform": rec.get("platform"), "status": rec["status"],
                  "platform_proven_on": recipes.platform_proven_on(RECIPES, url, result)} if rec else None
    return jsonify(ok=("error" not in result), analysis=result, llm=source_analyzer.llm_enabled(), recipe=recipe)

@app.post("/api/scraper/extract")
def scraper_extract():
    b = request.get_json(force=True, silent=True) or {}
    import source_analyzer, recipes
    url = b.get("url") or ""
    prompt = b.get("prompt") or ""
    api = dict(b.get("api") or {})
    # Reuse a proven field map (learned on an earlier run) so this pull normalizes columns
    # deterministically — no LLM call at all.
    rec0 = recipes.get(RECIPES, url)
    if not api.get("field_map") and isinstance(rec0, dict):
        fm = (rec0.get("config") or {}).get("field_map")
        if fm:
            api["field_map"] = fm
    result = source_analyzer.extract(url, prompt, b.get("pages", 1), b.get("limit", 3000), api=api)
    # Fold the run into the recipe book: validate vs baseline, promote/demote. Pass what
    # was actually run so a recipe proven by extraction alone stays runnable recipe-first.
    _, verdict = recipes.record_run(RECIPES, url, result, int(time.time() * 1000),
                                    used={"prompt": prompt, "target": url})
    # Persist a freshly-learned field map onto the recipe so future pulls reuse it (no LLM).
    fm = result.get("field_map") if isinstance(result, dict) else None
    if fm:
        rec = RECIPES.get(recipes.host_of(url))
        if isinstance(rec, dict):
            rec.setdefault("config", {})["field_map"] = fm
    # Self-heal: on drift/broken, re-analyze and write the fresh config back into the recipe.
    if verdict.get("needs_heal") and source_analyzer.llm_enabled():
        try:
            fresh = source_analyzer.analyze(url)
            if "error" not in fresh:
                recipes.apply_heal(RECIPES, url, fresh, int(time.time() * 1000))
                verdict["healed"] = True
        except Exception as e:
            app.logger.warning("recipe self-heal failed for %s: %s", url, e)
    _save_json("recipes.json", RECIPES)
    # Highlights — a 'what did this scrape yield' summary for the completion card + Hoodie Intelligence.
    try:
        hl = source_analyzer.highlights(result.get("rows"), host=recipes.host_of(url), engine=result.get("engine"))
        if hl:
            hl["ts"] = int(time.time() * 1000)
            HIGHLIGHTS.insert(0, hl); del HIGHLIGHTS[30:]
            _save_json("highlights.json", HIGHLIGHTS)
            result["highlights"] = hl
    except Exception as e:
        app.logger.warning("highlights failed for %s: %s", url, e)
    return jsonify(ok=("error" not in result), recipe=verdict, **result)

@app.get("/api/highlights")
def highlights_list():
    return jsonify(highlights=HIGHLIGHTS[:int(request.args.get("limit", 20) or 20)])

@app.post("/api/query")
def data_query():
    """Declarative run from a node in the Estate model: given a dataset + field + op, resolve it
    against the live data (the in-memory sample of the pulled dataset). Deterministic."""
    b = request.get_json(force=True, silent=True) or {}
    ds, field = b.get("dataset"), b.get("field")
    op = (b.get("op") or "distinct").lower()
    limit = max(1, min(int(b.get("limit", 60) or 60), 500))
    d = DATASETS.get(ds)
    if not isinstance(d, dict):
        return jsonify(error="unknown dataset"), 404
    header = d.get("header") or []
    rows = d.get("rows") or []
    if field not in header:
        return jsonify(error="unknown field"), 400
    ix = header.index(field)
    vals = []
    for r in rows:
        v = (r[ix] if isinstance(r, list) and len(r) > ix else (r.get(field) if isinstance(r, dict) else None))
        if v not in (None, ""):
            vals.append(str(v).strip())
    sampled, total = len(rows), (d.get("total") or len(rows))
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    base = {"dataset": ds, "field": field, "op": op, "sampled": sampled, "total": total, "distinct": len(counts)}
    if op == "count":
        return jsonify(**base, result=len(vals))
    if op == "top":
        items = sorted(counts.items(), key=lambda x: -x[1])[:limit]
        return jsonify(**base, items=[{"value": k, "n": n} for k, n in items])
    dv = sorted(counts.keys())
    return jsonify(**base, values=dv[:limit], truncated=len(dv) > limit)

@app.post("/api/scraper/fingerprint")
def scraper_fingerprint():
    """Discover: given a list of chains, classify each one's platform + whether we have a
    native pull path — so we can enumerate who's on Algolia/Shopify/Yext/… at a glance."""
    b = request.get_json(force=True, silent=True) or {}
    import source_analyzer
    urls = b.get("urls") or ([b["url"]] if b.get("url") else [])
    urls = [u for u in urls if isinstance(u, str) and u.strip()][:40]   # polite cap
    out = []
    for u in urls:
        try:
            fp = source_analyzer.fingerprint(u)
        except Exception as e:
            fp = {"url": u, "platform": None, "signals": [], "native": False, "note": str(e)[:80]}
        if fp:
            out.append(fp)
    return jsonify(results=out)

@app.get("/api/recipes")
def recipes_list():
    import recipes
    items = sorted(RECIPES.values(), key=lambda r: (r.get("status") != "proven", r.get("host", "")))
    return jsonify(recipes=items, stats=recipes.stats(RECIPES))

@app.delete("/api/recipes/<host>")
def recipes_delete(host):
    existed = RECIPES.pop(host, None) is not None
    if existed:
        _save_json("recipes.json", RECIPES)
    return jsonify(ok=existed, host=host)

@app.post("/api/recipes/<host>/reset")
def recipes_reset(host):
    """Re-prove a recipe from scratch — keep the config/field-map, clear the proof state
    so it must earn 'proven' again over the next clean runs."""
    rec = RECIPES.get(host)
    if not rec:
        return jsonify(ok=False, error="no such recipe"), 404
    rec["status"] = "candidate"
    rec["clean_runs"] = 0
    rec["baseline"] = None
    rec["proven_baseline"] = None
    _save_json("recipes.json", RECIPES)
    return jsonify(ok=True, host=host, status="candidate")

@app.get("/api/scraper/recipe")
def scraper_recipe():
    import recipes
    rec = recipes.get(RECIPES, request.args.get("url", ""))
    return jsonify(recipe=rec)

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
        msg = ("%s: %s" % (type(e).__name__, e)).replace("\n", " ").strip()[:400]
        if "407" in msg:
            msg = "Proxy rejected the credentials (407) — check BRIGHTDATA_PROXY_USER/PASS. · " + msg
        rec = {"id": "R-ERR", "connId": conn, "startedAt": int(time.time()*1000),
               "finishedAt": int(time.time()*1000), "durationMs": 0, "status": "failed",
               "trigger": body.get("trigger", "manual"), "total": 0, "extracts": [],
               "warnings": [msg], "error": msg}
    if rec is None:
        return jsonify(error="unknown connId"), 400
    RUNS.insert(0, rec); del RUNS[200:]; save()
    _emit_pull_highlights(conn, rec)   # estate model pulses + feed shows owned pulls (sync path too)
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

# ── Hoodie Intelligence analyst — real Claude answers + a pause switch ────────────────
_AI_PAUSE_FILE = os.path.join(STATE_DIR, "ai_paused.json")

def _ai_paused():
    try: return bool(json.load(open(_AI_PAUSE_FILE)).get("paused"))
    except Exception: return False

def _ai_set_paused(v):
    try: json.dump({"paused": bool(v)}, open(_AI_PAUSE_FILE, "w"))
    except Exception: pass
    return bool(v)

@app.get("/api/ai/status")
def ai_status_ep():
    """Is the live Claude analyst available, and is it currently paused? Drives the toggle."""
    return jsonify(enabled=hi_analyst.enabled(), paused=_ai_paused())

@app.post("/api/ai/pause")
def ai_pause_ep():
    """Pause/resume the live analyst — the 'switch it off while I work on the app' control.
    Persists so it stays put across restarts until flipped back."""
    b = request.get_json(force=True, silent=True) or {}
    return jsonify(paused=_ai_set_paused(b.get("paused", True)))

@app.post("/api/ask")
def ai_ask_ep():
    """Answer a free-form Q&A question with Claude, grounded in the exact figures the front-end
    computed (rbComputeMeasure). Returns {paused:true} when switched off → the browser falls
    back to its deterministic synthesizer. 503 without a key."""
    if _ai_paused():
        return jsonify(paused=True)
    b = request.get_json(force=True, silent=True) or {}
    q, facts = (b.get("question") or "").strip(), b.get("facts") or []
    if not q or not facts:
        return jsonify(error="need question + facts"), 400
    result = hi_analyst.ask(q, facts, vocab=b.get("vocab") or {})
    if "error" not in result:
        return jsonify(result)
    return jsonify(result), (503 if result["error"] == "llm-disabled" else 502)

@app.post("/api/enrich/census")
def enrich_census_ep():
    """Reference-data ENRICH (not master ingest): join census_acs county demographics onto the
    outlet master by county+state → lands `outlets_census`. Auto-picks the biggest outlet dataset
    (has county+state) unless `outlet_dataset` is given. Run the US Census pull first."""
    packs = [v for k, v in DATASETS.items()
             if k.startswith("census_") and isinstance(v, dict) and v.get("rows")]
    if not packs:
        return jsonify(error="no census data yet — run the US Census pull first"), 400
    census_ds = enrich.merge_census(packs)   # merge demographic + economic + housing → one county table
    body = request.get_json(silent=True) or {}
    # FL DBPR extracts all share one header, so items/brands "look" like outlets. Restrict to the
    # ACTUAL outlet tables (retail/wholesale/permits + ABC store cells + the places accounts), never
    # items/registrants/brands, then pick whichever genuinely matches census best.
    OUTLET_IDS = {"outlets_master", "bd4006lic", "bd4005lic", "bd4002lic", "abc_store_cells", "tx_outlets", "il_outlets", "ct_outlets", "ny_outlets", "co_outlets", "mo_outlets"}
    NON_OUTLET = {"bd4008lic", "bd4011lic", "abtbrands", "census_acs", "outlets_census"}
    def _places(d):
        h = [str(x).lower() for x in (d.get("header") or [])]
        return "county" in h and ("account_id" in h or "premise" in h)
    want = body.get("outlet_dataset")
    if want and isinstance(DATASETS.get(want), dict):
        cands = [(want, DATASETS[want])]
    else:
        cands = [(k, v) for k, v in DATASETS.items()
                 if isinstance(v, dict) and v.get("rows") and k not in NON_OUTLET
                 and (k in OUTLET_IDS or _places(v))]
    if not cands:
        return jsonify(error="no outlet dataset to join — run FL Outlets (bd4006lic) or the Orlando accounts pull first"), 400
    best = None
    for k, v in cands:
        r = enrich.join_census_to_outlets(census_ds, v)
        if "error" in r:
            continue
        if best is None or r["matched"] > best[2]["matched"]:
            best = (k, v, r)
    if best is None:
        return jsonify(error="outlet datasets present but none have a joinable county/state column"), 400
    oid, outlets, res = best
    DATASETS.update(_absorb({"outlets_census": {"header": res["header"], "rows": res["rows"][:800],
                             "total": len(res["rows"]), "_rows_full": res["rows"]}})); save()
    cov = round(100.0 * res["matched"] / res["total"], 1) if res["total"] else 0
    return jsonify(ok=True, outlet_dataset=oid, matched=res["matched"], total=res["total"],
                   coverage=cov, counties=res["counties_indexed"], demo_cols=res["demo_cols"],
                   landed="outlets_census", header=res["header"], sample=res["rows"][:5])

@app.post("/api/master/outlets/build")
def master_outlets_ep():
    """Unify the per-state outlet pulls (FL bd4006lic + TX tx_outlets + …) into ONE normalized
    outlets_master — the base outlet spine the coverage map + census-join-at-scale need. Uses FULL
    rows from the on-demand store when present. Run the state outlet pulls first."""
    sources = {}
    for did in master.SOURCES:
        full = load_full(did)
        if full and full.get("rows"):
            sources[did] = full
        elif isinstance(DATASETS.get(did), dict) and DATASETS[did].get("rows"):
            sources[did] = DATASETS[did]
    if not sources:
        return jsonify(error="no outlet pulls yet — run FL Outlets and/or Texas TABC first"), 400
    res = master.build(sources)
    DATASETS.update(_absorb({"outlets_master": {"header": res["header"], "rows": res["rows"][:800],
                             "total": res["total"], "_rows_full": res["rows"]}})); save()
    return jsonify(ok=True, landed="outlets_master", total=res["total"], by_state=res["by_state"],
                   sources=res["sources"], header=res["header"], sample=res["rows"][:5])

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


@app.post("/api/upc")
def upc_ep():
    """Assess one or many UPC/EAN codes. Deterministic QC always (check digit, placeholder,
    restricted ranges); owner-agreement + confidence when the prefix->owner crosswalk is loaded.
    Body: {"upc": "...", "applicant": "..."} OR {"items": [{"upc","applicant"}, ...]}."""
    body = request.get_json(force=True, silent=True) or {}
    items = body.get("items")
    if items is None:
        items = [{"upc": body.get("upc", ""), "applicant": body.get("applicant", "")}]
    xw = UPC_XWALK or None
    out = [upc.assess((it or {}).get("upc", ""), applicant=(it or {}).get("applicant"), crosswalk=xw)
           for it in items[:5000]]
    return jsonify(ok=True, crosswalk_loaded=bool(UPC_XWALK), count=len(out), results=out)


GEO_CAP = 500000
@app.get("/api/outlets/geo")
def outlets_geo_ep():
    """LIGHT geocoded points for the coverage map — enough to render, colour, filter and search the
    whole footprint at once, not just one state. ?dataset=outlets_master (default). Each point is
    {i,lat,lng,name,type,area,county_fips} where i is the row index used by /api/outlets/one to
    fetch the FULL record lazily on click (so we ship ~7 fields/point, not the whole record ×100k).
    Any dataset carrying latitude/longitude works (IL/Chicago native; TX/CT/FL geocoded)."""
    did = request.args.get("dataset", "outlets_master")
    full = load_full(did) or DATASETS.get(did)
    if not isinstance(full, dict) or not full.get("rows"):
        return jsonify(ok=True, dataset=did, count=0, points=[], note="no data pulled yet")
    header = full.get("header", []); rows = full.get("rows", [])
    idx = {str(h).lower(): i for i, h in enumerate(header)}
    def gi(*names):
        for n in names:
            if n in idx: return idx[n]
        return -1
    la, lo = gi("latitude", "lat"), gi("longitude", "lng", "lon")
    if la < 0 or lo < 0:
        return jsonify(ok=True, dataset=did, count=0, points=[], note="dataset has no lat/lng — geocode first")
    ni = gi("dba", "name", "trade_name")
    ti = gi("license_types", "license_type", "type", "credential")
    ai = gi("community_area", "county")
    ci = gi("county_fips", "fips")
    il_cook = "17031" if did == "il_outlets" else None   # Chicago ships lat/lng, no FIPS col → all Cook
    # Optional viewport filter: ?bbox=west,south,east,north — the map sends its current bounds so we
    # ship only the dots in view (a few thousand), not the whole 200k+ footprint on every pan. in_view
    # is the true count matching the bbox (before the cap) so the client can say "zoom in for all".
    west = south = east = north = None
    try:
        bb = request.args.get("bbox", "")
        if bb:
            west, south, east, north = (float(x) for x in bb.split(","))
    except (ValueError, TypeError):
        west = None
    # Return EVERY dot in view (up to a large safety cap) — the whole point of the map is to SEE the
    # gaps, and a sample fills them in. The client draws all of them on a single canvas layer (fast for
    # 100k+), so density/coverage is truthful; the reservoir only trips past GEO_CAP as a runaway guard.
    cap = GEO_CAP
    pts = []; in_view = 0
    for ri, r in enumerate(rows):
        if la >= len(r) or lo >= len(r):
            continue
        try:
            lat, lng = float(r[la]), float(r[lo])
        except (ValueError, TypeError):
            continue
        if not (lat and lng and -90 < lat < 90 and -180 < lng < 180):
            continue
        if west is not None and not (south <= lat <= north and west <= lng <= east):
            continue
        in_view += 1
        p = {"i": ri, "lat": round(lat, 6), "lng": round(lng, 6)}
        if 0 <= ni < len(r) and r[ni]: p["name"] = r[ni]
        if 0 <= ti < len(r) and r[ti]: p["type"] = r[ti]
        if 0 <= ai < len(r) and r[ai]: p["area"] = r[ai]
        cf = (r[ci] if 0 <= ci < len(r) else "") or il_cook
        if cf: p["county_fips"] = cf
        if len(pts) < cap:
            pts.append(p)
        else:
            j = random.randint(0, in_view - 1)      # reservoir: uniform sample across the viewport
            if j < cap:
                pts[j] = p
    return jsonify(ok=True, dataset=did, count=len(pts), in_view=in_view,
                   points=pts, capped=in_view > len(pts))


@app.get("/api/outlets/one")
def outlets_one_ep():
    """The FULL record for one outlet, fetched lazily when a dot is clicked. ?dataset=&i=<row index>
    (the index the /api/outlets/geo point carries). Returns {record: {column: value}} straight from
    the full rows — the detail panel renders the outlet's own data from this."""
    did = request.args.get("dataset", "outlets_master")
    try:
        i = int(request.args.get("i", "-1"))
    except ValueError:
        i = -1
    full = load_full(did) or DATASETS.get(did)
    if not isinstance(full, dict) or not full.get("rows") or not (0 <= i < len(full["rows"])):
        return jsonify(ok=False, error="not found"), 404
    header = full.get("header", []); row = full["rows"][i]
    rec = {str(h): (row[j] if j < len(row) else "") for j, h in enumerate(header)}
    return jsonify(ok=True, dataset=did, record=rec)


@app.get("/api/outlets/table")
def outlets_table_ep():
    """The outlet master AS A TABLE — the data underneath the coverage map. Server-side paged +
    filtered (the master is 255k+ rows, can't ship to the DOM whole). ?dataset=&offset=&limit=&q=
    (name contains) &state= (exact) &type= (contains). Each row carries its index `i` + lat/lng +
    county_fips so a table click reuses the same detail panel + map pan as a dot click."""
    did = request.args.get("dataset", "outlets_master")
    try:
        offset = max(0, int(request.args.get("offset", "0")))
    except ValueError:
        offset = 0
    try:
        limit = min(500, max(1, int(request.args.get("limit", "100"))))
    except ValueError:
        limit = 100
    q = (request.args.get("q", "") or "").strip().lower()
    fstate = (request.args.get("state", "") or "").strip().lower()
    ftype = (request.args.get("type", "") or "").strip().lower()
    full = load_full(did) or DATASETS.get(did)
    if not isinstance(full, dict) or not full.get("rows"):
        return jsonify(ok=True, dataset=did, total=0, filtered=0, offset=offset, limit=limit, rows=[])
    header = full["header"]; rows = full["rows"]
    idx = {str(h).lower(): i for i, h in enumerate(header)}
    def gi(*names):
        for n in names:
            if n in idx:
                return idx[n]
        return -1
    ni = gi("name", "dba", "trade_name"); ti = gi("license_type", "license_types", "credential", "type")
    oi = gi("owner", "backer", "owner name"); cyi = gi("city"); coi = gi("county"); si = gi("state")
    zi = gi("zip"); li = gi("license_num", "credential", "license_id", "outlet_id"); sri = gi("source")
    cfi = gi("county_fips", "fips"); lai = gi("latitude", "lat"); loi = gi("longitude", "lng", "lon")
    def v(r, i):
        return str(r[i]) if 0 <= i < len(r) and r[i] is not None else ""
    out = []; matched = 0
    for ri, r in enumerate(rows):
        nm = v(r, ni); ty = v(r, ti); stt = v(r, si)
        if q and q not in nm.lower():
            continue
        if fstate and fstate != stt.lower():
            continue
        if ftype and ftype not in ty.lower():
            continue
        matched += 1
        if matched <= offset or len(out) >= limit:
            continue
        out.append({"i": ri, "name": nm, "type": ty, "owner": v(r, oi), "city": v(r, cyi),
                    "county": v(r, coi), "state": stt, "zip": v(r, zi), "license_num": v(r, li),
                    "source": v(r, sri), "county_fips": v(r, cfi), "lat": v(r, lai), "lng": v(r, loi)})
    return jsonify(ok=True, dataset=did, total=len(rows), filtered=matched,
                   offset=offset, limit=limit, rows=out)


@app.get("/api/outlets/context")
def outlets_context_ep():
    """Everything the Census reference layer knows about one outlet's geography — the market
    behind a single dot on the coverage map. ?county_fips=17031 (5-digit state+county). Returns the
    county's population (PEP), beer/wine/liquor retailer + bev-alc wholesaler + drinking-place
    establishment/employment/payroll counts (CBP), the state-level nonemployer count (the small/
    independent operators CBP misses), and retailers-per-10k-residents density. Suppressed or
    uncovered cells come back null — never a fabricated zero — so the panel can say 'not reported'."""
    fips = (request.args.get("county_fips") or "").strip()
    if not (fips.isdigit() and len(fips) == 5):
        return jsonify(ok=False, error="county_fips must be a 5-digit FIPS"), 400
    st = fips[:2]
    try:
        import census_ref
    except Exception as e:
        return jsonify(ok=False, error="census layer unavailable: %s" % e), 200

    def cell(dataset, geo_level, gfips, naics, metric):
        try:
            rows = census_ref.query(dataset=dataset, geo_level=geo_level, geo_fips=gfips,
                                    naics=naics, metric=metric, limit=5)
        except Exception:
            return None
        for r in rows:
            if r.get("suppressed"):
                return None
            try:
                return int(float(r.get("metric_value")))
            except (TypeError, ValueError):
                return None
        return None

    NAICS_LABELS = (("44531", "retailers"), ("4248", "wholesalers"), ("722", "onpremise"))
    market = {}
    for naics, label in NAICS_LABELS:
        market[label] = {
            "estab": cell("cbp", "county", fips, naics, "estab"),
            "emp": cell("cbp", "county", fips, naics, "emp"),
            "payann": cell("cbp", "county", fips, naics, "payann"),
            "nonemployer_state": cell("nonemployer", "state", st, naics, "nestab"),
        }
    pop = cell("pep", "county", fips, None, "population")
    ret = market["retailers"]["estab"]
    dens = round(ret / pop * 10000, 2) if (pop and ret) else None
    return jsonify(ok=True, county_fips=fips, state_fips=st, population=pop,
                   per_10k_retailers=dens, market=market)


GEO_JOBS = {}
@app.post("/api/outlets/geocode/build")
def outlets_geocode_build():
    """Geocode an outlet dataset's addresses (free US Census batch) → append latitude/longitude/
    county_fips, so the Coverage Map + census join work for it (county_fips also fixes the FL
    numeric-county gap). Background job (~1 min per 10k). ?dataset=outlets_master (default);
    poll /api/outlets/geocode/progress?id=<jobId>."""
    body = request.get_json(silent=True) or {}
    did = body.get("dataset") or request.args.get("dataset", "outlets_master")
    full = load_full(did) or DATASETS.get(did)
    if not isinstance(full, dict) or not full.get("rows"):
        return jsonify(ok=False, error="no data pulled for %s" % did), 400
    import random as _rnd
    jid = "GEO-" + "".join(_rnd.choices("ABCDEFGHJKMNPQRSTUVWXYZ23456789", k=6))
    GEO_JOBS[jid] = {"jobId": jid, "dataset": did, "status": "running",
                     "total": full.get("total", len(full["rows"])), "matched": 0, "requested": 0, "log": []}
    header, rows = full["header"], full["rows"]
    def run():
        try:
            import geocode
            nh, nr, stats = geocode.geocode_outlets(header, rows,
                                                    log=lambda m: GEO_JOBS[jid]["log"].append(m))
            DATASETS[did] = {"header": nh, "rows": nr[:800], "total": len(nr)}
            save_full(did, nh, nr); save()
            GEO_JOBS[jid].update(status="done", matched=stats["matched"], requested=stats["requested"])
        except Exception as e:
            app.logger.exception("geocode failed")
            GEO_JOBS[jid].update(status="failed", error=str(e)[:200])
    threading.Thread(target=run, daemon=True).start()
    return jsonify(ok=True, jobId=jid, dataset=did, total=GEO_JOBS[jid]["total"])


@app.get("/api/outlets/geocode/progress")
def outlets_geocode_progress():
    j = GEO_JOBS.get(request.args.get("id", ""))
    if not j:
        return jsonify(error="unknown job"), 404
    return jsonify(jobId=j["jobId"], dataset=j.get("dataset", ""), status=j["status"], matched=j.get("matched", 0),
                   requested=j.get("requested", 0), total=j.get("total", 0), error=j.get("error"), log=j["log"][-6:])


@app.post("/api/census/build")
def census_build_ep():
    """Build the census_reference layer (CBP + Nonemployer + PEP → warehouse). Background; needs
    CENSUS_API_KEY. Poll /api/outlets/geocode/progress?id=<jobId> (shared job store)."""
    if not os.environ.get("CENSUS_API_KEY", "").strip():
        return jsonify(ok=False, error="CENSUS_API_KEY not set"), 400
    import random as _r
    jid = "CEN-" + "".join(_r.choices("ABCDEFGHJKMNPQRSTUVWXYZ23456789", k=6))
    GEO_JOBS[jid] = {"jobId": jid, "status": "running", "log": []}
    def run():
        try:
            import census_ref
            res = census_ref.build(log=lambda m: GEO_JOBS[jid]["log"].append(m))
            GEO_JOBS[jid].update(status="done", rows=res["rows"], uri=res["uri"])
        except Exception as e:
            app.logger.exception("census build failed")
            GEO_JOBS[jid].update(status="failed", error=str(e)[:200])
    threading.Thread(target=run, daemon=True).start()
    return jsonify(ok=True, jobId=jid)


@app.get("/api/census/reference")
def census_reference_ep():
    """Query census_reference by geo/naics/metric/vintage — the territory.html join helper. e.g.
    ?dataset=cbp&geo_level=county&geo_fips=17031&naics=44531&metric=estab"""
    import census_ref
    a = request.args
    try:
        rows = census_ref.query(dataset=a.get("dataset"), geo_level=a.get("geo_level"),
                                geo_fips=a.get("geo_fips"), naics=a.get("naics"), metric=a.get("metric"),
                                vintage=a.get("vintage"), limit=int(a.get("limit", 5000)))
        return jsonify(ok=True, count=len(rows), rows=rows)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:160]), 500


@app.get("/api/places")
def places_ep():
    """Query the pulled on-premise accounts (Orlando) from the warehouse. Filters:
    ?premise=on|off|unknown  ?q=<name substring>  ?limit=N. Graceful before the first pull."""
    premise = request.args.get("premise")
    q = (request.args.get("q") or "").strip()
    try:
        limit = max(1, min(2000, int(request.args.get("limit", 200))))
    except ValueError:
        limit = 200
    sql, params = "SELECT * FROM t", []
    where = []
    if premise in ("on", "off", "unknown"):
        where.append("premise = ?"); params.append(premise)
    if q:
        where.append("lower(name) LIKE ?"); params.append("%" + q.lower() + "%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY name LIMIT ?"; params.append(limit)
    try:
        rows = warehouse.query("orlando_accounts", sql, params)
        return jsonify(ok=True, count=len(rows), remote=warehouse.remote(), accounts=rows)
    except Exception as e:
        # No parquet yet (no pull has run) or storage not reachable — answer gracefully.
        return jsonify(ok=True, count=0, accounts=[], note="no data yet — run the orlando-accounts pull (%s)" % str(e)[:120])


@app.post("/api/places/enrich")
def places_enrich_ep():
    """Match the stored accounts against open POI (FSQ/Overture) and re-write enriched.
    Never blocks the spine — self-reports poi-failed / no-accounts. ?source=fsq|overture."""
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(places.enrich_orlando(source=body.get("source", "fsq")))


# ---- canonical book (star schema): synthetic data foundation for the production app ----
@app.post("/api/seed/build")
def seed_build_ep():
    """Generate the coherent synthetic book (dim_product/account/date + fact_depletion)
    into the warehouse. The data to build the production app against until real facts flow."""
    import seed
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(ok=True, **seed.build(**{k: body[k] for k in ("n_products", "n_accounts", "months", "market") if k in body}))


@app.get("/api/book/cuts")
def book_cuts_ep():
    """Aggregate the book by any dimension — the one query surface every screen shares.
    ?dim=category|channel|price_tier|brand|...  ?measure=revenue|cases|pod."""
    import book
    dim = request.args.get("dim", "category")
    measure = request.args.get("measure", "revenue")
    try:
        return jsonify(ok=True, dim=dim, measure=measure, rows=book.cuts(dim, measure))
    except Exception as e:
        return jsonify(ok=True, dim=dim, measure=measure, rows=[],
                       note="no book yet — POST /api/seed/build (%s)" % str(e)[:120])


_OPENAPI_PATH = os.path.join(APP_DIR, "openapi.yaml")
_openapi_cache = None

@app.get("/api/openapi.json")
def openapi_json():
    """Serve the API contract so the app generates its typed client from the running API
    (single source of truth). Gated like the rest of /api* — codegen runs against the
    ungated local engine in dev."""
    global _openapi_cache
    if _openapi_cache is None:
        import yaml
        with open(_OPENAPI_PATH) as f:
            _openapi_cache = yaml.safe_load(f)
    return jsonify(_openapi_cache)

@app.get("/api/openapi.yaml")
def openapi_yaml():
    return send_file(_OPENAPI_PATH, mimetype="application/yaml")

@app.get("/api/territories")
def territories_ep():
    """Named account groups (Territory Builder). Powers target-vs-competition comparisons."""
    import territories
    return jsonify(ok=True, territories=territories.list_territories())


@app.get("/api/territories/<tid>")
def territory_members_ep(tid):
    import territories
    return jsonify(ok=True, **territories.members(tid))


@app.get("/api/book/summary")
def book_summary_ep():
    import book
    try:
        return jsonify(ok=True, **book.summary())
    except Exception as e:
        return jsonify(ok=True, empty=True, note="no book yet — POST /api/seed/build (%s)" % str(e)[:120])

# ---- optional: serve the static suite from THIS app (all-in-one image, e.g. Fly.io) ----
# When SUITE_ROOT is set, one gunicorn process serves BOTH /api/* and the public suite from a single
# origin, so the apps' same-origin /api/* fetches work with no separate frontend host and no CORS.
# An ALLOWLIST of public top-level entries is enforced so the engine (unifyd/, *.py, scripts/, docs,
# dotfiles, .env) is NEVER web-served — it mirrors the deploy.sh / deploy.yml exclude lists.
SUITE_ROOT = os.environ.get("SUITE_ROOT", "").strip()
_SUITE_OK_TOP = {"index.html", "apps", "spine", "suite.css", "suite-header.js",
                 "suite-export.js", "fullread.js", "dq.js", "dq_frontier.js", "datagrid.js",
                 "apps.registry.json", "favicon.ico"}

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
