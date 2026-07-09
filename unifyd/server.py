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
import csv, gzip, io, json, os, random, re, time, types, urllib.request, datetime, threading, logging
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
import derive                       # field-derivation / dictionary compiler (rule → DuckDB SQL expression)
import enrich                       # join reference data (census) onto the outlet master by county
import tx_tabc                      # Texas TABC licenses (Socrata) — TX outlets + companies by county
import master                       # unify per-state outlet pulls into one normalized outlets_master
import chicago                      # Chicago liquor-licensed outlets (Socrata) — IL outlets + companies, geocoded
import ct_dcp                       # Connecticut liquor (Socrata) — CT premises + brand/supplier registry
import socrata_outlets              # GENERIC Socrata outlet connector — NY/CO/MO… as config, not modules
import total_wine                    # Total Wine & More direct catalog (mobile-UA + sitemap + microdata)
import vtinfo                        # brand → retailer distribution via the VTInfo/VIP "where to buy" finder
import ab_locator                    # Anheuser-Busch InBev retailer locator (beertech GraphQL) — universal-outlet spine
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

VALID_CONNS = {"ttb-cola", "abc-fws", "specs", "binnys", "shopify-dtc", "instacart", "orlando-accounts", "census-acs", "tx-tabc", "il-chicago", "ct-dcp", "total-wine", "vtinfo", "ab-inbev"} | set(socrata_outlets.VALID)
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
            else total_wine_pull(body) if conn == "total-wine"
            else vtinfo_pull(body) if conn == "vtinfo"
            else ab_inbev_pull(body) if conn == "ab-inbev"
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

def _to_warehouse(ds):
    """Write a pull's full rows to the warehouse as Parquet so the apply engine, the data dictionary,
    and all DuckDB queries can reach them (memory DATASETS alone can't be read by read_parquet, and
    won't scale to the national AB census). Call BEFORE _absorb (which pops _rows_full). Best-effort."""
    for name, d in (ds or {}).items():
        header = d.get("header") or []
        rows = d.get("_rows_full") or d.get("rows") or []
        if not (header and rows):
            continue
        try:
            recs = [dict(zip(header, r)) for r in rows]
            warehouse.write_parquet(name, recs, header)
            app.logger.info("warehouse ← %s: %d rows", name, len(recs))
        except Exception as e:
            app.logger.warning("warehouse write %s failed: %s", name, e)

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

def total_wine_pull(params):
    started = int(time.time() * 1000)
    ds, runs, _ = total_wine.pull(
        cap=int(params.get("cap", 400)), delay=float(params.get("delay", 1.0)),
        out=os.path.join(STATE_DIR, "total_wine"), state_dir=os.path.join(STATE_DIR, "total_wine"),
        log=lambda m: app.logger.info("TOTAL-WINE %s", m))
    _to_warehouse(ds)
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

def vtinfo_pull(params):
    started = int(time.time() * 1000)
    zips = params.get("zips") or ["33601"]
    if isinstance(zips, str):
        zips = [z.strip() for z in zips.split(",") if z.strip()]
    ds, runs, _ = vtinfo.pull(
        brand=params.get("brand", "titos"), zips=zips,
        custID=params.get("custID"), uuid=params.get("uuid"), delay=float(params.get("delay", 1.0)),
        out=os.path.join(STATE_DIR, "vtinfo"), state_dir=os.path.join(STATE_DIR, "vtinfo"),
        log=lambda m: app.logger.info("VTINFO %s", m))
    _to_warehouse(ds)
    DATASETS.update(_absorb(ds))
    run = runs[0]; run["startedAt"] = started; run["finishedAt"] = int(time.time() * 1000)
    run["durationMs"] = run["finishedAt"] - started; run["trigger"] = params.get("trigger", "manual")
    return run

def ab_inbev_pull(params):
    started = int(time.time() * 1000)
    zips = params.get("zips") or ["32819"]
    if isinstance(zips, str):
        zips = [z.strip() for z in zips.split(",") if z.strip()]
    brands = params.get("brands")
    if isinstance(brands, str):
        brands = [b.strip() for b in brands.split(",") if b.strip()]
    ds, runs, _ = ab_locator.pull(
        zips=zips, brands=brands or None, radius=float(params.get("radius", 25.0)),
        delay=float(params.get("delay", 0.3)),
        out=os.path.join(STATE_DIR, "ab"), state_dir=os.path.join(STATE_DIR, "ab"),
        log=lambda m: app.logger.info("AB-INBEV %s", m))
    _to_warehouse(ds)
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

# ---- Product Locator: brand → accounts carrying it near a location (Grey-Goose-style UI) ----
def _titlecase(s):
    return " ".join(w if (w.isupper() and len(w) <= 3) else w.capitalize() for w in str(s).split())

@app.get("/api/locator/brands")
def locator_brands():
    return jsonify(brands=[{"id": k, "name": v.get("name", k)} for k, v in vtinfo.BRANDS.items()])

@app.get("/api/locator")
def locator():
    """Live account list for a brand near a ZIP, shaped for the locator UI. Bounded (max_pages)
    so it stays responsive; the map centers on the mean of returned coordinates."""
    brand = request.args.get("brand", "titos")
    zc = re.sub(r"[^0-9]", "", request.args.get("zip", "")) or "33602"
    b = vtinfo.BRANDS.get(brand)
    if not b:
        return jsonify(accounts=[], error="unknown brand"), 200
    try:
        rows = vtinfo.search_zip(b["custID"], b.get("uuid", ""), zc, delay=0.15,
                                 max_pages=int(request.args.get("pages", 3)),
                                 m=b.get("m", "5"), theme=b.get("theme", "1"),
                                 log=lambda m: app.logger.info("LOCATOR %s", m))
    except Exception as e:
        app.logger.info("LOCATOR error %s", e)
        return jsonify(accounts=[], error=str(e)[:120]), 200
    accts, lats, lngs = [], [], []
    for r in rows:
        if not (r.get("lat") and r.get("lng")):
            continue
        lat, lng = float(r["lat"]), float(r["lng"])
        lats.append(lat); lngs.append(lng)
        accts.append({"name": _titlecase(r["account"]), "street": _titlecase(r["street"]),
                      "city": _titlecase(r["city"]), "state": r["state"], "lat": lat, "lng": lng,
                      "source": r.get("source", ""), "type": r.get("store_type", "")})
    center = [sum(lats) / len(lats), sum(lngs) / len(lngs)] if lats else None
    return jsonify(label=b.get("name", brand), accounts=accts, center=center, zip=zc)

# Source labels + a first-cut scope tree derived from the pulled data.
_SRC_LABEL = {"fl-items": "Florida — Items", "fl-outlets": "Florida — Outlets",
              "ttb-cola": "TTB — COLA Labels", "abc-fws": "ABC FWS — Inventory",
              "specs": "Spec's — Inventory", "binnys": "Binny's — Inventory",
              "shopify-dtc": "Hemp + DTC — Shopify", "instacart": "Instacart — Store-level",
              "census-acs": "US Census — ACS demographics", "tx-tabc": "Texas TABC — licenses",
              "il-chicago": "Chicago — Liquor Licenses", "ct-dcp": "Connecticut — Liquor (DCP)",
              "ny-sla": "New York — SLA licenses", "co-led": "Colorado — Liquor licenses",
              "mo-atc": "Missouri — Alcohol licenses",
              "total-wine": "Total Wine — Catalog (direct)", "vtinfo": "VTInfo — Brand distribution (VIP)",
              "ab-inbev": "AB InBev — Retailer locator (universal outlets)"}
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

def _catalog_map():
    """Every dataset we hold, across all three stores: {key: {header, total, store}}. memory = in-RAM
    sample; full = save_full'd on Tigris; warehouse = Parquet. Precedence memory → full → warehouse."""
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
    return out

@app.get("/api/catalog")
def catalog_ep():
    """The estate model's 'whole thing' view (it polls this so new datasets appear on their own)."""
    return jsonify(_catalog_map())

# ── /api/sources — the SINGLE source of truth for what we hold + what each dataset FEEDS. Driven by the
# mappings/seeds themselves (authoritative) + name heuristics, so labels/tags stay accurate as the list
# grows. Powers the MDM Sources view and keeps the estate tags current (no more hardcoded name map). ──
_FEED_ORDER = ["master", "outlet", "product", "inventory", "pricing", "reference", "other"]
_FEED_GROUP = {"master": "Owned masters", "outlet": "Outlet sources", "product": "Product & label sources",
               "inventory": "Inventory feeds", "pricing": "Pricing feeds", "reference": "Reference data",
               "other": "Unclassified"}
_DS_LABELS = {"outlets_master": "Outlet master", "ab_outlets": "AB InBev · Retailer locator",
              "total_wine_products": "Total Wine · Catalog", "ttb_cola": "TTB · COLA labels",
              "cola_cluster": "COLA · Product clusters", "bc_liquor": "BC Liquor · Price list",
              "or_pricing": "Oregon OLCC · Pricing", "orlando_accounts": "Orlando · On-premise accounts",
              "census_acs": "Census · ACS demographics", "census_reference": "Census · CBP/NES/PEP",
              "outlets_census": "Outlets × census"}

def _ds_label(name):
    if name in _DS_LABELS:
        return _DS_LABELS[name]
    if name.startswith("dim_"):
        return "Master · " + name[4:].replace("_", " ").title()
    if name.startswith("fact_"):
        return "Fact · " + name[5:].replace("_", " ").title()
    if name.startswith("vtinfo_"):
        return "VTInfo · " + name[7:].replace("-", " ").title()
    return name.replace("_", " ").replace("-", " ").title()

def _ds_feeds(name, mp):
    """Which master(s) a dataset feeds — mappings/seeds are authoritative; heuristics fill unmapped ones."""
    if name.startswith("dim_") or name.startswith("fact_"):
        return ["master"]
    f = set()
    if _seed_for(name) or name in mp["outlet"]:
        f.add("outlet")
    if name in mp["product"]:
        f.add("product")
    for fact in FACTS:
        if _fact_seed_for(fact, name) or name in mp.get(fact, {}):
            f.add(fact)
    if not f:
        n = name.lower()
        if re.search(r"census|_census", n): f.add("reference")
        elif re.search(r"\bcola\b|ttb", n): f.add("product")
        elif re.search(r"outlet|licens|premise|account|_sla|tabc|dcp|\bny_|\bco_|\bmo_|\bct_|\btx_|\bil_", n): f.add("outlet")
        elif re.search(r"pric|bc_liquor|or_pricing|total_wine|iowa_prod|catalog|mont_", n): f.add("product")
        elif re.search(r"binny|spec|abc_|instacart|shopify|iowa_sales", n): f.add("inventory")
    return [x for x in _FEED_ORDER if x in f] or ["other"]

@app.get("/api/sources")
def sources_ep():
    cat = _catalog_map()
    mp = {"outlet": load(ENTITIES["outlet"]["maps"], {}), "product": load(ENTITIES["product"]["maps"], {})}
    for fact in FACTS:
        mp[fact] = load(FACTS[fact]["maps"], {})
    items = []
    for k, v in cat.items():
        feeds = _ds_feeds(k, mp)
        is_master = feeds[0] == "master"
        mapped = (any(k in mp[e] for e in mp) or bool(_seed_for(k))
                  or any(_fact_seed_for(fc, k) for fc in FACTS))
        items.append({"id": k, "label": _ds_label(k), "rows": v.get("total", 0),
                      "store": v.get("store"), "cols": len(v.get("header") or []), "feeds": feeds,
                      "group": _FEED_GROUP[feeds[0]],
                      "status": "master" if is_master else ("mapped" if mapped else "unmapped")})
    gi = {_FEED_GROUP[fk]: i for i, fk in enumerate(_FEED_ORDER)}
    items.sort(key=lambda x: (gi.get(x["group"], 9), -(x["rows"] or 0)))
    present = [_FEED_GROUP[fk] for fk in _FEED_ORDER if any(it["group"] == _FEED_GROUP[fk] for it in items)]
    return jsonify(ok=True, count=len(items), groups=present, sources=items)

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
def _mem_dataset(ds):
    """In-RAM datasets (pull output: outlets, vtinfo, ab_outlets…) as (header, rows) — so the
    data dictionary works on ANY dataset in the catalog, not only warehouse Parquet ones."""
    d = DATASETS.get(ds)
    if not d:
        return None
    header = d.get("header") or []
    rows = d.get("_rows_full") or d.get("rows") or []
    return (header, rows) if header else None

def _ds_columns(ds):
    try:
        s = _cola_q(ds, "SELECT * FROM t LIMIT 1")
        if s:
            return list(s[0].keys())
    except Exception:
        pass
    mem = _mem_dataset(ds)
    return list(mem[0]) if mem else []

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
        mem = _mem_dataset(ds)                                    # in-RAM dataset (e.g. outlets): profile in Python
        if not mem:
            return jsonify(ok=True, landed=False, dataset=ds, fields=[])
        header, rows = mem
        out = []
        for ci, col in enumerate(header[:24]):
            if col.startswith(":@") or col.startswith("_"):
                continue
            vals = [str(r[ci]) for r in rows if ci < len(r) and str(r[ci]).strip() != ""]
            out.append({"field": col, "filled": len(vals), "distinct": len(set(vals)),
                        "fill_pct": round(100 * len(vals) / max(1, len(rows)), 1), "samples": vals[:3]})
        return jsonify(ok=True, landed=True, dataset=ds, total=len(rows), fields=out)
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
    except Exception:
        mem = _mem_dataset(ds)                                    # in-RAM dataset: value counts in Python
        if not mem:
            return jsonify(ok=False, error="dataset not queryable", values=[]), 200
        header, rows_all = mem
        ci = header.index(field)
        from collections import Counter
        c = Counter(str(r[ci]) for r in rows_all if ci < len(r) and str(r[ci]).strip() != ""
                    and (not q or q.lower() in str(r[ci]).lower()))
        return jsonify(ok=True, dataset=ds, field=field, distinct=len(c),
                       values=[{"v": v, "n": n} for v, n in c.most_common(lim)])
    return jsonify(ok=True, dataset=ds, field=field, distinct=distinct, values=rows)

# ── DICTIONARY STORE — named, reusable value dictionaries (synonym-normalize or string-extract) ──
# A dictionary = ordered [{match,value}] + mode (exact|contains|regex) + fallback (original|null). It
# compiles (derive.dict_case) to ONE DuckDB CASE, so 'cab sauv → Cabernet Sauvignon' runs at scale. A
# field-mapping row can reference one by id ({mode:'dict', dict:<id>}); the build resolves it inline.
DICT_MODES = ["exact", "contains", "regex"]
def _dicts():
    return load("dictionaries.json", [])
def _dict_by_id(did):
    return next((d for d in _dicts() if d.get("id") == did), None)

@app.get("/api/dict/store")
def dict_store_list():
    """All saved dictionaries (light): id, name, source field, mode, fallback, entry count."""
    out = [{"id": d.get("id"), "name": d.get("name"), "source_dataset": d.get("source_dataset"),
            "source_field": d.get("source_field"), "target_field": d.get("target_field"),
            "mode": d.get("mode"), "fallback": d.get("fallback"), "entries": len(d.get("entries") or []),
            "updated": d.get("updated"), "by": d.get("by")} for d in _dicts()]
    return jsonify(ok=True, dictionaries=out, modes=DICT_MODES)

@app.get("/api/dict/store/get")
def dict_store_get():
    d = _dict_by_id((request.args.get("id") or "").strip())
    return jsonify(ok=bool(d), dictionary=d) if d else (jsonify(ok=False, error="not found"), 404)

@app.post("/api/dict/store/save")
def dict_store_save():
    """Upsert a dictionary. Body: {id?, name, source_dataset, source_field, target_field, mode,
    fallback, entries:[{match,value}]}. Returns the saved record (with a minted id if new)."""
    b = request.get_json(silent=True) or {}
    name = (b.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, error="name required"), 400
    entries = [{"match": str(e.get("match", "")).strip(), "value": str(e.get("value", "")).strip()}
               for e in (b.get("entries") or []) if str(e.get("match", "")).strip() != ""]
    dicts = _dicts()
    did = (b.get("id") or "").strip()
    rec = _dict_by_id(did) if did else None
    if not rec:
        did = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "dict"
        base, i = did, 2
        while _dict_by_id(did):
            did = "%s-%d" % (base, i); i += 1
        rec = {"id": did}; dicts.append(rec)
    rec.update({"name": name, "source_dataset": (b.get("source_dataset") or "").strip(),
                "source_field": (b.get("source_field") or "").strip(),
                "target_field": (b.get("target_field") or "").strip(),
                "mode": (b.get("mode") if b.get("mode") in DICT_MODES else "contains"),
                "fallback": ("null" if b.get("fallback") == "null" else "original"),
                "entries": entries, "updated": int(time.time()), "by": _user_rec().get("code", "SYS")})
    _save_json("dictionaries.json", dicts)
    return jsonify(ok=True, dictionary=rec)

@app.post("/api/dict/store/delete")
def dict_store_delete():
    did = ((request.get_json(silent=True) or {}).get("id") or "").strip()
    dicts = [d for d in _dicts() if d.get("id") != did]
    _save_json("dictionaries.json", dicts)
    return jsonify(ok=True, count=len(dicts))

@app.post("/api/dict/coverage")
def dict_coverage():
    """Score a dictionary against real source data — the feedback loop for authoring. Body: {dataset,
    field, mode, entries}. Returns rows total/covered (+%) plus what each entry captured (by_value) and
    the biggest still-uncovered raw values (uncovered) so you know what to add next, and raw→derived
    samples. Coverage is computed with a NULL fallback (covered = an entry matched), independent of the
    dictionary's own fallback choice."""
    b = request.get_json(silent=True) or {}
    ds = (b.get("dataset") or "ttb_cola").strip()
    field = (b.get("field") or "").strip()
    if field not in _ds_columns(ds):
        return jsonify(ok=False, error="unknown field for dataset"), 400
    mode = b.get("mode") if b.get("mode") in DICT_MODES else "contains"
    entries = b.get("entries") or []
    try:
        derived = derive.dict_case(derive.col(field), entries, mode, "null")
    except Exception as e:
        return jsonify(ok=False, error="bad entries: %s" % str(e)[:120]), 200
    nonempty = 'CAST("%s" AS VARCHAR)<>\'\'' % field
    try:
        tot = _cola_q(ds, "SELECT count(*) c FROM t WHERE %s" % nonempty)[0]["c"]
        cov = _cola_q(ds, "SELECT count(*) c FROM t WHERE %s AND (%s) IS NOT NULL" % (nonempty, derived))[0]["c"]
        by_value = _cola_q(ds, "SELECT (%s) v, count(*) n FROM t WHERE %s AND (%s) IS NOT NULL "
                           "GROUP BY 1 ORDER BY n DESC LIMIT 40" % (derived, nonempty, derived))
        uncovered = _cola_q(ds, 'SELECT CAST("%s" AS VARCHAR) v, count(*) n FROM t WHERE %s AND (%s) IS NULL '
                            "GROUP BY 1 ORDER BY n DESC LIMIT 30" % (field, nonempty, derived))
        samples = _cola_q(ds, 'SELECT CAST("%s" AS VARCHAR) raw, (%s) derived FROM t WHERE %s LIMIT 12'
                          % (field, derived, nonempty))
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:160]), 200
    return jsonify(ok=True, dataset=ds, field=field, mode=mode, total=tot, covered=cov,
                   pct=round(100 * cov / max(1, tot), 1), by_value=by_value,
                   uncovered=uncovered, samples=samples)

def _resolve_dict_refs(maps):
    """Expand any field-mapping row that references a saved dictionary ({mode:'dict', dict:<id>}) into
    inline entries the compiler understands, so the master build applies it. A row that already carries
    inline dict_entries is left as-is. Non-destructive (returns a resolved copy)."""
    if not isinstance(maps, dict):
        return maps
    out = {}
    for ds, rules in maps.items():
        rr = []
        for r in (rules or []):
            if isinstance(r, dict) and r.get("mode") == "dict" and r.get("dict") and not r.get("dict_entries"):
                d = _dict_by_id(r.get("dict"))
                if d:
                    r = dict(r, dict_entries=d.get("entries") or [], dict_mode=d.get("mode") or "contains",
                             dict_fallback=d.get("fallback") or "original")
            rr.append(r)
        out[ds] = rr
    return out

# ── Walmart bev-alc tracker — Bright Data walmart_product → warehouse (walmart_products / walmart_runs).
# The tracking surface reads live counts + data-quality concerns computed over the current snapshot. ──
_WM_CONCERNS = [
    ("high", "not_alcohol",  "is_alcohol = false",
     "not classified under Alcohol/Spirits/Wine/Beer — possible non-beverage contamination"),
    ("high", "missing_price", "price IS NULL",
     "no price captured (often an availability/parse failure)"),
    ("med", "missing_abv",  "abv IS NULL",              "ABV missing from Walmart specs"),
    ("med", "missing_size", "size_ml IS NULL",          "size (mL) not parseable"),
    ("med", "missing_upc",  "CAST(upc AS VARCHAR)=''",  "no UPC / GTIN on the listing"),
    ("med", "out_of_stock", "in_stock = false",         "out of stock at the resolved store"),
    ("low", "on_promo",     "on_promo = true",          "final price below list (promo)"),
]

@app.get("/api/walmart/products")
def walmart_products_ep():
    import warehouse
    try:
        rows = warehouse.query("walmart_products",
            "SELECT product_name, brand, category, is_alcohol, size_ml, abv, "
            "CAST(upc AS VARCHAR) upc, price, list_price, on_promo, availability, in_stock, "
            "store, store_loc, rating, reviews, url FROM t ORDER BY brand, product_name")
        return jsonify(ok=True, landed=True, count=len(rows), products=rows)
    except Exception as e:
        return jsonify(ok=True, landed=False, error=str(e)[:140], products=[]), 200

@app.get("/api/walmart/runs")
def walmart_runs_ep():
    """Run history + data-quality concerns + field fill-rates over the current snapshot."""
    import warehouse
    try:
        total = warehouse.query("walmart_products", "SELECT count(*) c FROM t")[0]["c"]
    except Exception as e:
        return jsonify(ok=True, landed=False, error=str(e)[:140], runs=[], concerns=[], total=0), 200
    concerns = []
    for sev, key, cond, msg in _WM_CONCERNS:
        try:
            n = warehouse.query("walmart_products", "SELECT count(*) c FROM t WHERE %s" % cond)[0]["c"]
        except Exception:
            n = 0
        if n:
            concerns.append(dict(severity=sev, key=key, count=n, message=msg))
    try:
        d = warehouse.query("walmart_products",
            "SELECT count(*) c FROM (SELECT upc FROM t WHERE CAST(upc AS VARCHAR)<>'' "
            "GROUP BY upc HAVING count(*)>1)")[0]["c"]
        if d:
            concerns.append(dict(severity="med", key="duplicate_upc", count=d,
                                 message="same UPC on more than one product"))
    except Exception:
        pass
    fills = {}
    for f in ("brand", "category", "size_ml", "abv", "upc", "price"):
        try:
            filled = warehouse.query("walmart_products",
                "SELECT count(*) c FROM t WHERE %s IS NOT NULL AND CAST(%s AS VARCHAR)<>''" % (f, f))[0]["c"]
            fills[f] = round(100 * filled / max(1, total), 1)
        except Exception:
            fills[f] = None
    runs = []
    try:
        runs = warehouse.query("walmart_runs", 'SELECT * FROM t ORDER BY "at" DESC')   # "at" is a DuckDB reserved word
    except Exception:
        pass
    return jsonify(ok=True, landed=True, total=total, runs=runs, concerns=concerns, fills=fills,
                   sev_order={"high": 0, "med": 1, "low": 2})

def _walmart_concern_count():
    import warehouse
    try:
        warehouse.query("walmart_products", "SELECT 1 FROM t LIMIT 1")
    except Exception:
        return 0
    n = 0
    for sev, key, cond, msg in _WM_CONCERNS:
        try:
            n += warehouse.query("walmart_products", "SELECT count(*) c FROM t WHERE %s" % cond)[0]["c"]
        except Exception:
            pass
    return n

# ── Scrape/crawl tracker — every active pull that lands in the warehouse, with freshness + concerns. ──
_SCRAPES = [
    {"id": "ttb-crawl",  "name": "TTB COLA · crawl (index)",  "kind": "crawl",  "source": "ttbonline.gov",
     "table": "ttb_cola_index",  "feeds": "product master", "detail": None},
    {"id": "ttb-detail", "name": "TTB COLA · label detail",   "kind": "detail", "source": "ttbonline.gov",
     "table": "ttb_cola_detail", "feeds": "product master", "detail": None},
    {"id": "walmart",    "name": "Walmart · bev-alc products", "kind": "product", "source": "walmart.com (Bright Data)",
     "table": "walmart_products", "feeds": "pricing + inventory", "detail": "walmart"},
]

@app.get("/api/scrapes")
def scrapes_ep():
    """Every tracked scrape/crawl: rows, freshness (last warehouse landing), status, concerns."""
    import warehouse, time
    ds = {d["name"]: d for d in warehouse.list_datasets()}
    now = time.time(); out = []
    for s in _SCRAPES:
        d = ds.get(s["table"], {})
        rows = d.get("rows", 0); mod = d.get("modified")
        fresh = (now - mod) if mod else None
        status = "active" if (fresh is not None and fresh < 1800) else ("idle" if rows else "empty")
        out.append(dict(s, rows=rows, modified=mod,
                        fresh_secs=(int(fresh) if fresh is not None else None), status=status,
                        concerns=(_walmart_concern_count() if s["id"] == "walmart" else 0)))
    return jsonify(ok=True, now=int(now), scrapes=out)

@app.get("/api/walmart/match")
def walmart_match_ep():
    """Cross-source matches for one Walmart product — TTB COLA filings by brand (potential same product)."""
    import warehouse
    brand = (request.args.get("brand") or "").strip()
    if not brand:
        return jsonify(ok=True, brand="", matches=[])
    try:
        rows = warehouse.query("ttb_cola_detail",
            "SELECT ttb_id, CAST(brand_name AS VARCHAR) brand_name, CAST(class_type_desc AS VARCHAR) class_type, "
            "CAST(net_contents AS VARCHAR) net_contents, CAST(approval_date AS VARCHAR) approval_date "
            "FROM t WHERE lower(CAST(brand_name AS VARCHAR)) LIKE ? "
            "ORDER BY approval_date DESC LIMIT 12", ["%" + brand.lower() + "%"])
        for r in rows:
            r["source"] = "TTB COLA"; r["how"] = "brand match"
        return jsonify(ok=True, brand=brand, matches=rows)
    except Exception as e:
        return jsonify(ok=True, brand=brand, matches=[], error=str(e)[:120])

# ── Field mapping — persist source.field → master.field crosswalks, with pre/post transforms ──
# The PRODUCT master is the WIDE hierarchy schema — every field declares its GRAIN (brand|product|item|
# sku|supplier). The apply engine SHREDS one mapped source into dim_brand → dim_product → dim_item →
# dim_sku by grain (+ dim_supplier). Attribute lives at its level: brand_group@brand, flavor/abv@product,
# size@item, upc@sku. Vintage/edition are sku ATTRIBUTES but excluded from the sku identity (releases
# collapse to one sku). Price is NOT here — it's the pricing FACT, held separately.
DEFAULT_MASTER_FIELDS = [
    {"name": "brand", "grain": "brand", "type": "string", "desc": "Brand (e.g. Absolut)"},
    {"name": "brand_group", "grain": "brand", "type": "string", "desc": "Brand-group / family section within the brand"},
    {"name": "product_name", "grain": "product", "type": "string", "desc": "Product / variant (e.g. Absolut Blue)"},
    {"name": "flavor", "grain": "product", "type": "string", "desc": "Flavor / variant (applied at product level)"},
    {"name": "style", "grain": "product", "type": "string", "desc": "Style / sub-type"},
    {"name": "category", "grain": "product", "type": "string", "desc": "Category / class-type"},
    {"name": "abv", "grain": "product", "type": "number", "desc": "Alcohol % by volume (product level)"},
    {"name": "origin", "grain": "product", "type": "string", "desc": "Country / region of origin"},
    {"name": "size_ml", "grain": "item", "type": "number", "desc": "Net contents in mL (item grain)"},
    {"name": "container", "grain": "item", "type": "string", "desc": "Container (bottle / can / keg)"},
    {"name": "packsize", "grain": "item", "type": "string", "desc": "Pack / container size (as filed)"},
    {"name": "pack", "grain": "sku", "type": "string", "desc": "Pack configuration (single / case / n-pack)"},
    {"name": "upc", "grain": "sku", "type": "string", "desc": "UPC / GTIN — auto-normalized to GTIN-14", "normalize": "upc"},
    {"name": "vintage", "grain": "sku", "type": "string", "desc": "Vintage year — attribute, NOT part of sku identity", "normalize": "vintage"},
    {"name": "edition", "grain": "sku", "type": "string", "desc": "Special/seasonal edition — attribute, NOT part of sku identity"},
    {"name": "supplier", "grain": "supplier", "type": "string", "desc": "Supplier / owner (brand↔supplier SCD, not a hierarchy level)"},
]
# The OUTLET master schema — the canonical, atomic outlet fields every outlet source maps INTO
# (the 'lowest level of consistency'). Same machinery as products, different entity.
DEFAULT_OUTLET_FIELDS = [
    {"name": "outlet_name", "type": "string", "desc": "Outlet / account name", "normalize": "name"},
    {"name": "dba", "type": "string", "desc": "Doing-business-as / display name", "normalize": "name"},
    {"name": "address", "type": "string", "desc": "Street address"},
    {"name": "city", "type": "string", "desc": "City"},
    {"name": "state", "type": "string", "desc": "State (2-letter)", "normalize": "state"},
    {"name": "zip5", "type": "string", "desc": "ZIP (5-digit)", "normalize": "zip5"},
    {"name": "zip4", "type": "string", "desc": "ZIP+4 add-on", "normalize": "zip4"},
    {"name": "county_fips", "type": "string", "desc": "County FIPS"},
    {"name": "lat", "type": "number", "desc": "Latitude"},
    {"name": "lng", "type": "number", "desc": "Longitude"},
    {"name": "phone", "type": "string", "desc": "Phone (digits)", "normalize": "phone"},
    {"name": "outlet_type", "type": "string", "desc": "On/off-premise or channel"},
    {"name": "license_num", "type": "string", "desc": "State license / permit number"},
    {"name": "source_ref", "type": "string", "desc": "Source outlet id (e.g. AB vpid) — strong key when present"},
    {"name": "carriage", "type": "string", "desc": "Brands/products seen at this outlet (carriage signal)"},
]
# Master entities: name → (default schema, state-file basenames). product keeps the legacy filenames
# for back-compat; outlet gets its own. Adding an entity = one row here.
ENTITIES = {
    "product": {"defaults": DEFAULT_MASTER_FIELDS, "schema": "master_schema.json",
                "maps": "field_mappings.json", "mapmeta": "mappings_meta.json", "schemameta": "schema_meta.json"},
    "outlet":  {"defaults": DEFAULT_OUTLET_FIELDS, "schema": "master_schema_outlet.json",
                "maps": "field_mappings_outlet.json", "mapmeta": "mappings_meta_outlet.json", "schemameta": "schema_meta_outlet.json"},
}
def _entity(v):
    return v if v in ENTITIES else "product"

# Seed mappings for OUR OWN outlet connectors — so the outlet master builds turnkey (no hand-mapping
# each source; a saved mapping always overrides). Key = dataset name; a trailing '_' key = name prefix.
SEED_OUTLET_MAPPINGS = {
    "ab_outlets": [
        {"source_field": "VPID", "master_field": "source_ref"},
        {"source_field": "Name", "master_field": "outlet_name"},
        {"source_field": "Address", "master_field": "address"},
        {"source_field": "City", "master_field": "city"},
        {"source_field": "State", "master_field": "state"},
        {"source_field": "Zip", "master_field": "zip5"},
        {"source_field": "Zip", "master_field": "zip4"},
        {"source_field": "Lat", "master_field": "lat"},
        {"source_field": "Lng", "master_field": "lng"},
        {"source_field": "AB_Brands", "master_field": "carriage"},
    ],
    "vtinfo_": [   # prefix — every vtinfo_<brand> dataset
        {"source_field": "Account", "master_field": "outlet_name"},
        {"source_field": "Street", "master_field": "address"},
        {"source_field": "City", "master_field": "city"},
        {"source_field": "State", "master_field": "state"},
        {"source_field": "Zip", "master_field": "zip5"},
        {"source_field": "Phone", "master_field": "phone"},
        {"source_field": "Lat", "master_field": "lat"},
        {"source_field": "Lng", "master_field": "lng"},
        {"source_field": "StoreType", "master_field": "outlet_type"},
        {"source_field": "Brand", "master_field": "carriage"},
    ],
}
def _seed_for(ds):
    if ds in SEED_OUTLET_MAPPINGS:
        return SEED_OUTLET_MAPPINGS[ds]
    for k, v in SEED_OUTLET_MAPPINGS.items():
        if k.endswith("_") and ds.startswith(k):
            return v
    return None

def _outlet_maps_effective():
    """Persisted outlet mappings + seed mappings for any recognized warehouse dataset lacking one, so
    the outlet master builds turnkey. A saved mapping always wins over its seed."""
    out = dict(load(ENTITIES["outlet"]["maps"], {}))
    try:
        for d in warehouse.list_datasets():
            nm = d.get("name", "")
            if nm and nm not in out and _seed_for(nm):
                out[nm] = _seed_for(nm)
    except Exception as e:
        app.logger.warning("outlet seed merge failed: %s", e)
    return out

# ── FACT tables: inventory + pricing (sku × outlet × date). A fact schema = the FK-identity fields
# (outlet + product, to compute the keys) + measures + obs_date. Same config-driven build as dimensions.
_FACT_IDENTITY = [
    {"name": "source_ref", "grain": "outlet", "type": "string", "desc": "Outlet id (vpid) — outlet key"},
    {"name": "outlet_name", "grain": "outlet", "type": "string", "desc": "Outlet name", "normalize": "name"},
    {"name": "address", "grain": "outlet", "type": "string", "desc": "Outlet street"},
    {"name": "city", "grain": "outlet", "type": "string", "desc": "Outlet city"},
    {"name": "state", "grain": "outlet", "type": "string", "desc": "Outlet state", "normalize": "state"},
    {"name": "zip5", "grain": "outlet", "type": "string", "desc": "Outlet ZIP5", "normalize": "zip5"},
    {"name": "brand", "grain": "brand", "type": "string", "desc": "Brand (→ brand/sku key)"},
    {"name": "product_name", "grain": "product", "type": "string", "desc": "Product / variant"},
    {"name": "size_ml", "grain": "item", "type": "number", "desc": "Size mL (→ sku grain)"},
    {"name": "upc", "grain": "sku", "type": "string", "desc": "UPC (→ sku key)", "normalize": "upc"},
    {"name": "obs_date", "grain": "fact", "type": "string", "desc": "Observation date (else build date)"},
]
DEFAULT_INVENTORY_FIELDS = _FACT_IDENTITY + [
    {"name": "in_stock", "grain": "fact", "type": "string", "desc": "In stock (Y/N)"},
    {"name": "on_hand", "grain": "fact", "type": "number", "desc": "On-hand units"},
    {"name": "units", "grain": "fact", "type": "number", "desc": "Units / movement"},
    {"name": "carriage", "grain": "fact", "type": "string", "desc": "Brands/products carried (presence)"},
]
DEFAULT_PRICING_FIELDS = _FACT_IDENTITY + [
    {"name": "price", "grain": "fact", "type": "number", "desc": "Price"},
    {"name": "promo", "grain": "fact", "type": "string", "desc": "Promo / strike price"},
    {"name": "currency", "grain": "fact", "type": "string", "desc": "Currency"},
]
FACTS = {
    "inventory": {"defaults": DEFAULT_INVENTORY_FIELDS, "schema": "fact_schema_inventory.json",
                  "maps": "field_mappings_inventory.json", "mapmeta": "mappings_meta_inventory.json"},
    "pricing":   {"defaults": DEFAULT_PRICING_FIELDS, "schema": "fact_schema_pricing.json",
                  "maps": "field_mappings_pricing.json", "mapmeta": "mappings_meta_pricing.json"},
}
def _fact(v):
    return v if v in FACTS else "inventory"

# Seed fact mappings for our own connectors — inventory carriage from AB (brand-list) + VTInfo (per-brand).
SEED_FACT_MAPPINGS = {
    "inventory": {
        "ab_outlets": [{"source_field": "VPID", "master_field": "source_ref"},
                       {"source_field": "Name", "master_field": "outlet_name"},
                       {"source_field": "Address", "master_field": "address"},
                       {"source_field": "City", "master_field": "city"},
                       {"source_field": "State", "master_field": "state"},
                       {"source_field": "Zip", "master_field": "zip5"},
                       {"source_field": "AB_Brands", "master_field": "carriage"}],
        "vtinfo_": [{"source_field": "Account", "master_field": "outlet_name"},
                    {"source_field": "Street", "master_field": "address"},
                    {"source_field": "City", "master_field": "city"},
                    {"source_field": "State", "master_field": "state"},
                    {"source_field": "Zip", "master_field": "zip5"},
                    {"source_field": "Brand", "master_field": "brand"},
                    {"source_field": "Brand", "master_field": "carriage"}],
        "walmart_products": [{"source_field": "store_loc", "master_field": "outlet_name"},
                             {"source_field": "store", "master_field": "address"},
                             {"source_field": "brand", "master_field": "brand"},
                             {"source_field": "product_name", "master_field": "product_name"},
                             {"source_field": "size_ml", "master_field": "size_ml"},
                             {"source_field": "upc", "master_field": "upc"},
                             {"source_field": "in_stock", "master_field": "in_stock"}],
    },
    "pricing": {   # price lists (bc_liquor / or_pricing / Total Wine) — mapped in the UI per their headers
        "walmart_products": [{"source_field": "store_loc", "master_field": "outlet_name"},
                             {"source_field": "brand", "master_field": "brand"},
                             {"source_field": "product_name", "master_field": "product_name"},
                             {"source_field": "size_ml", "master_field": "size_ml"},
                             {"source_field": "upc", "master_field": "upc"},
                             {"source_field": "price", "master_field": "price"},
                             {"source_field": "list_price", "master_field": "promo"}],
    },
}
def _fact_seed_for(fact, ds):
    seeds = SEED_FACT_MAPPINGS.get(fact, {})
    if ds in seeds:
        return seeds[ds]
    for k, v in seeds.items():
        if k.endswith("_") and ds.startswith(k):
            return v
    return None

def _fact_schema(fact):
    spec = FACTS[_fact(fact)]
    fields = load(spec["schema"], spec["defaults"])
    for f in fields:
        if isinstance(f, dict) and not f.get("normalize") and f.get("name") in _NORMALIZE_DEFAULTS:
            f["normalize"] = _NORMALIZE_DEFAULTS[f["name"]]
    return fields

def _fact_maps_effective(fact):
    out = dict(load(FACTS[_fact(fact)]["maps"], {}))
    try:
        for d in warehouse.list_datasets():
            nm = d.get("name", "")
            if nm and nm not in out and _fact_seed_for(fact, nm):
                out[nm] = _fact_seed_for(fact, nm)
    except Exception as e:
        app.logger.warning("fact seed merge failed: %s", e)
    return out

# Data-dictionary transforms that can apply BEFORE (on the source value) or AFTER (on the mapped
# master value). Names only here — the apply-engine consumes them when materializing the master.
MAP_TRANSFORMS = ["none", "trim", "upper", "lower", "title_case", "digits_only",
                  "size_to_ml", "year_from_date"]
# Canonical normalizers keyed by master field NAME — ensured on read so the 'lowest level of
# consistency' contract holds even for a schema saved before a normalizer was added.
_NORMALIZE_DEFAULTS = {"upc": "upc", "gtin": "upc", "zip5": "zip5", "zip4": "zip4",
                       "state": "state", "phone": "phone", "outlet_name": "name", "dba": "name"}
# CONTROLLED fields — a field with a DOMAIN accepts only these permissible options (enum). Seeded here
# as sensible starters (editable per-field via /api/master/domain); a dictionary maps raw source strings
# INTO one of these values. Seeded only when the field carries no `domain` key yet (edits/clears win).
_DOMAIN_DEFAULTS = {
    "container": ["Bottle", "Can", "Keg", "Bag-in-Box", "Pouch", "Cask", "Growler"],
    "pack": ["Single", "2-Pack", "4-Pack", "6-Pack", "8-Pack", "12-Pack", "15-Pack",
             "18-Pack", "24-Pack", "30-Pack", "Case"],
    "outlet_type": ["On-Premise", "Off-Premise"],
    # top-level alcohol category — the clean roll-up of COLA Class/Type
    "category": ["Wine", "Beer", "Spirits", "Cider", "Mead", "Sake", "Seltzer/RTD", "Non-Alcoholic"],
    # sub-type within a category — a curated starter (prune per your taxonomy)
    "style": ["Red", "White", "Rosé", "Sparkling", "Dessert/Fortified",
              "Lager", "Ale", "IPA", "Stout", "Porter", "Pilsner", "Wheat", "Sour",
              "Bourbon", "Rye", "Scotch", "Irish Whiskey", "Blended Whiskey",
              "Vodka", "Gin", "Tequila", "Mezcal", "White Rum", "Dark Rum", "Spiced Rum",
              "Brandy", "Cognac", "Liqueur"],
    # country of origin — canonical country vocabulary (US-state origins roll up to United States
    # via a dictionary; add states here if you want state-level origin instead). Seeded from the real
    # COLA Origin distribution (France/Italy/Spain/Argentina/… dominate imports).
    "origin": ["United States", "France", "Italy", "Spain", "Argentina", "Australia", "Germany",
               "Chile", "Portugal", "New Zealand", "South Africa", "Austria", "Mexico", "Scotland",
               "Canada", "Japan", "Greece", "Belgium", "Ireland", "Netherlands", "England", "Hungary",
               "Israel", "Georgia", "Croatia", "Slovenia", "Lebanon", "Brazil", "Peru", "China",
               "India", "South Korea", "Russia", "Poland", "Sweden"],
}

def _master_schema(entity="product"):
    spec = ENTITIES[_entity(entity)]
    fields = [dict(f) for f in load(spec["schema"], spec["defaults"])]   # copy — never mutate the defaults
    for f in fields:
        if not f.get("normalize") and f.get("name") in _NORMALIZE_DEFAULTS:
            f["normalize"] = _NORMALIZE_DEFAULTS[f["name"]]
        if "domain" not in f and f.get("name") in _DOMAIN_DEFAULTS:
            f["domain"] = list(_DOMAIN_DEFAULTS[f["name"]])
    return fields

@app.get("/api/master/schema")
def master_schema_get():
    entity = _entity(request.args.get("entity", "product"))
    return jsonify(ok=True, entity=entity, entities=list(ENTITIES),
                   fields=_master_schema(entity), transforms=MAP_TRANSFORMS)

# ── users (each gets a short CODE) + created/updated audit stamps ──
def _mk_user_code(email, users):
    base = re.sub(r"[^a-z0-9]", "", (email.split("@")[0] if email else "usr")).upper()[:3] or "USR"
    taken = {u.get("code") for u in users.values()}
    code, i = base, 1
    while code in taken:
        code = base + str(i); i += 1
    return code

def _user_rec():
    """The signed-in user (from the OIDC session) with a stable short code; auto-registers on first
    sight so every created/updated stamp is attributable. (Distinct from the admin _current_user,
    which returns just the email.)"""
    email = (session.get("email") or "").lower()
    users = load("users.json", {})
    u = users.get(email)
    if email and not u:
        u = {"email": email, "code": _mk_user_code(email, users), "name": email.split("@")[0]}
        users[email] = u; _save_json("users.json", users)
    return u or {"email": "", "code": "SYS", "name": "system"}

def _stamp(meta_name, key):
    """created_at/created_by (first write) + updated_at/updated_by (every write) for a config record."""
    now = int(time.time()); who = _user_rec().get("code", "SYS")
    allmeta = load(meta_name, {})
    m = allmeta.get(key, {})
    if not m.get("created_at"):
        m["created_at"] = now; m["created_by"] = who
    m["updated_at"] = now; m["updated_by"] = who
    allmeta[key] = m; _save_json(meta_name, allmeta)
    return m

@app.get("/api/whoami")
def whoami_ep():
    return jsonify(ok=True, user=_user_rec())

@app.get("/api/users")
def users_get():
    return jsonify(ok=True, users=load("users.json", {}))

@app.post("/api/users")
def users_post():
    """Assign / update a user's code + name (keyed by email)."""
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").lower().strip()
    if not email:
        return jsonify(ok=False, error="email required"), 400
    users = load("users.json", {})
    u = users.get(email, {"email": email})
    if b.get("code"): u["code"] = re.sub(r"[^A-Za-z0-9]", "", b["code"]).upper()[:8]
    if b.get("name"): u["name"] = b["name"]
    if not u.get("code"): u["code"] = _mk_user_code(email, users)
    users[email] = u; _save_json("users.json", users)
    return jsonify(ok=True, user=u)

@app.post("/api/master/schema")
def master_schema_post():
    """Create a master field (start building the master schema) or replace the whole set. ?entity=."""
    body = request.get_json(silent=True) or {}
    entity = _entity(body.get("entity") or request.args.get("entity", "product"))
    spec = ENTITIES[entity]
    fields = load(spec["schema"], spec["defaults"])
    if isinstance(body.get("fields"), list):
        fields = body["fields"]
    else:
        nm = (body.get("name") or "").strip()
        if not nm:
            return jsonify(ok=False, error="name required"), 400
        if not any(f.get("name") == nm for f in fields):
            fields.append({"name": nm, "type": body.get("type", "string"), "desc": body.get("desc", ""),
                           **({"normalize": body["normalize"]} if body.get("normalize") else {})})
    _save_json(spec["schema"], fields)
    return jsonify(ok=True, entity=entity, fields=fields, meta=_stamp(spec["schemameta"], "schema"))

# ── Controlled vocabularies — a master field's DOMAIN (permissible options). A field with options is an
# enum; the Dictionary maps raw source values into one of them (off-domain → unmapped). ──
@app.get("/api/master/domains")
def master_domains_get():
    """All controlled fields for an entity → {field: [permissible options]}. ?entity=product|outlet."""
    entity = _entity(request.args.get("entity", "product"))
    fields = _master_schema(entity)
    return jsonify(ok=True, entity=entity,
                   domains={f["name"]: f["domain"] for f in fields if f.get("domain")})

@app.post("/api/master/domain")
def master_domain_post():
    """Set the permissible options for ONE master field. Body: {entity?, field, options:[...]}. An empty
    list clears the constraint (the field becomes free-text again). De-dupes case-insensitively, keeps order."""
    b = request.get_json(silent=True) or {}
    entity = _entity(b.get("entity") or "product")
    spec = ENTITIES[entity]
    field = (b.get("field") or "").strip()
    if not field:
        return jsonify(ok=False, error="field required"), 400
    opts, seen = [], set()
    for o in (b.get("options") or []):
        s = str(o).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower()); opts.append(s)
    fields = [dict(f) for f in load(spec["schema"], spec["defaults"])]   # copy — never mutate the defaults
    if not any(f.get("name") == field for f in fields):
        return jsonify(ok=False, error="unknown field"), 400
    for f in fields:
        if f.get("name") == field:
            f["domain"] = opts                       # explicit key (even []) — blocks the seed default
    _save_json(spec["schema"], fields)
    return jsonify(ok=True, entity=entity, field=field, options=opts,
                   meta=_stamp(spec["schemameta"], "schema"))

@app.post("/api/master/domain/check")
def master_domain_check():
    """Fit a source field's real values against a set of permissible options. Body: {dataset, field,
    options:[...]}. Returns distinct source values split into IN-domain (already a permissible option,
    case-insensitive exact) and OUT (need a dictionary rule), with row counts — so you can see how much
    of the source already conforms and what still has to be mapped."""
    b = request.get_json(silent=True) or {}
    ds = (b.get("dataset") or "ttb_cola").strip()
    field = (b.get("field") or "").strip()
    if field not in _ds_columns(ds):
        return jsonify(ok=False, error="unknown field for dataset"), 400
    allowed = {str(o).strip().lower() for o in (b.get("options") or []) if str(o).strip()}
    try:
        vals = _cola_q(ds, 'SELECT CAST("%s" AS VARCHAR) v, count(*) n FROM t '
                       "WHERE CAST(\"%s\" AS VARCHAR)<>'' GROUP BY 1 ORDER BY n DESC LIMIT 400" % (field, field))
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:160]), 200
    ins, out, in_rows, out_rows = [], [], 0, 0
    for r in vals:
        if str(r["v"]).strip().lower() in allowed:
            ins.append(r); in_rows += r["n"]
        else:
            out.append(r); out_rows += r["n"]
    return jsonify(ok=True, dataset=ds, field=field, in_domain=ins[:60], out_domain=out[:60],
                   in_rows=in_rows, out_rows=out_rows,
                   fit_pct=round(100 * in_rows / max(1, in_rows + out_rows), 1))

@app.get("/api/mappings")
def mappings_get():
    """Persisted field mappings + audit meta. ?entity= (product|outlet), ?dataset= for one source's rows."""
    entity = _entity(request.args.get("entity", "product"))
    spec = ENTITIES[entity]
    ds = (request.args.get("dataset") or "").strip()
    m = load(spec["maps"], {})
    meta = load(spec["mapmeta"], {})
    if ds:
        rows = m.get(ds)
        seeded = False
        if rows is None and entity == "outlet":                  # pre-fill our own connectors' mapping
            rows = _seed_for(ds); seeded = rows is not None
        return jsonify(ok=True, entity=entity, dataset=ds, mappings=rows or [],
                       seeded=seeded, meta=meta.get(ds))
    return jsonify(ok=True, entity=entity, dataset=None, mappings=m, meta=meta)

@app.post("/api/mappings")
def mappings_post():
    """Persist a source dataset's mapping rows: [{source_field, master_field, pre, post}]. Stamps
    created/updated + user, and — when a row maps to master.upc — auto-drives the UPC healer. ?entity=."""
    body = request.get_json(silent=True) or {}
    entity = _entity(body.get("entity") or request.args.get("entity", "product"))
    spec = ENTITIES[entity]
    ds = (body.get("dataset") or "").strip()
    if not ds:
        return jsonify(ok=False, error="dataset required"), 400
    m = load(spec["maps"], {})
    m[ds] = body.get("mappings", [])
    _save_json(spec["maps"], m)
    meta = _stamp(spec["mapmeta"], ds)
    # auto-drive the UPC healer if a source field is mapped to master.upc (base data untouched)
    upc_rows = [r for r in m[ds] if r.get("master_field") == "upc" and r.get("source_field")]
    healed = False
    if upc_rows:
        healed = True
        ucol = upc_rows[0]["source_field"]
        appl = next((r["source_field"] for r in m[ds] if r.get("master_field") == "supplier"), None)
        def _heal():
            try:
                import upc_heal
                rep = upc_heal.heal(ds, ucol, appl, log=lambda x: app.logger.info("UPCHEAL %s", x))
                reports = load("upc_reports.json", {}); reports[ds] = rep; _save_json("upc_reports.json", reports)
            except Exception as e:
                app.logger.warning("upc heal %s failed: %s", ds, e)
        threading.Thread(target=_heal, daemon=True).start()
    return jsonify(ok=True, dataset=ds, count=len(m[ds]), meta=meta, upc_heal_started=healed)

@app.get("/api/upc/report")
def upc_report_ep():
    """The UPC health report for a dataset (auto-generated when a upc mapping is assigned)."""
    ds = (request.args.get("dataset") or "").strip()
    reports = load("upc_reports.json", {})
    return jsonify(ok=True, dataset=ds or None, report=(reports.get(ds) if ds else reports))

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
        rule = _resolve_dict_refs({ds: [rule]})[ds][0]      # a {mode:'dict', dict:<id>} rule → inline entries
        fct = body.get("fact")                              # fact preview uses the fact schema's normalizers
        fields = _fact_schema(fct) if fct in FACTS else _master_schema(_entity(body.get("entity") or "product"))
        nz = next((f.get("normalize") for f in fields if isinstance(f, dict) and f.get("name") == rule.get("master_field")), None)
        rows = master_apply.preview(ds, rule, limit=int(body.get("limit", 12)), normalize=nz)
        return jsonify(ok=True, dataset=ds, normalize=nz, rows=rows)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:180]), 200

@app.post("/api/master/apply")
def master_apply_ep():
    """Materialize dim_<entity> + dim_<entity>_resolved from ALL persisted mappings + the master schema
    (one DuckDB pass per source over Parquet → UNION → resolve → warehouse). ?entity=product|outlet.
    Returns per-source counts + any skipped sources."""
    try:
        import master_apply
        body = request.get_json(silent=True) or {}
        entity = _entity(body.get("entity") or request.args.get("entity", "product"))
        spec = ENTITIES[entity]
        fields = _master_schema(entity)
        maps = _outlet_maps_effective() if entity == "outlet" else load(spec["maps"], {})
        maps = _resolve_dict_refs(maps)            # expand {mode:'dict', dict:<id>} rows to inline entries
        who = _user_rec().get("code", "SYS")
        res = master_apply.build(fields, maps, entity=entity, built_by=who, built_at=int(time.time()),
                                 log=lambda mm: app.logger.info("APPLY[%s] %s", entity, mm))
        return jsonify(ok=True, entity=entity, built_by=who, **res)
    except Exception as e:
        app.logger.exception("apply failed")
        return jsonify(ok=False, error=str(e)[:200]), 200

# ── FACT tables (inventory + pricing) — same config-driven build, keyed sku × outlet × date ──
@app.get("/api/master/fact/schema")
def fact_schema_get():
    fact = _fact(request.args.get("fact", "inventory"))
    return jsonify(ok=True, fact=fact, facts=list(FACTS),
                   fields=_fact_schema(fact), transforms=MAP_TRANSFORMS)

@app.get("/api/master/fact/mappings")
def fact_mappings_get():
    fact = _fact(request.args.get("fact", "inventory"))
    spec = FACTS[fact]
    ds = (request.args.get("dataset") or "").strip()
    m = load(spec["maps"], {})
    if ds:
        rows = m.get(ds)
        seeded = False
        if rows is None:
            rows = _fact_seed_for(fact, ds); seeded = rows is not None
        return jsonify(ok=True, fact=fact, dataset=ds, mappings=rows or [], seeded=seeded)
    return jsonify(ok=True, fact=fact, dataset=None, mappings=m)

@app.post("/api/master/fact/mappings")
def fact_mappings_post():
    body = request.get_json(silent=True) or {}
    fact = _fact(body.get("fact") or request.args.get("fact", "inventory"))
    spec = FACTS[fact]
    ds = (body.get("dataset") or "").strip()
    if not ds:
        return jsonify(ok=False, error="dataset required"), 400
    m = load(spec["maps"], {})
    m[ds] = body.get("mappings", [])
    _save_json(spec["maps"], m)
    return jsonify(ok=True, fact=fact, dataset=ds, count=len(m[ds]),
                   meta=_stamp(spec["mapmeta"], ds))

# ── MDM workbench: browse the resolved OUTLET master (dim_outlet_resolved) ──
_OUTM_COLS = "outlet_key,outlet_name,dba,address,city,state,zip5,zip4,county_fips,lat,lng,phone,outlet_type,license_num,source_ref,carriage,source_rows,sources,source_list"
@app.get("/api/master/outlets")
def master_outlets_browse_ep():
    """Paged, filterable view of the resolved outlet master. ?q= (name/addr/city), ?state=, ?page=, ?size=.
    Returns rows + total + a state facet, so the workbench can browse + filter the real master."""
    import warehouse
    q = (request.args.get("q") or "").strip().lower()
    state = (request.args.get("state") or "").strip().upper()
    try:
        page = max(0, int(request.args.get("page", 0))); size = min(200, max(1, int(request.args.get("size", 50))))
    except ValueError:
        page, size = 0, 50
    where, params = [], []
    if state:
        where.append("upper(CAST(state AS VARCHAR)) = ?"); params.append(state)
    if q:
        where.append("(lower(CAST(outlet_name AS VARCHAR)) LIKE ? OR lower(CAST(address AS VARCHAR)) LIKE ? OR lower(CAST(city AS VARCHAR)) LIKE ?)")
        qq = "%" + q + "%"; params += [qq, qq, qq]
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    try:
        total = warehouse.query("dim_outlet_resolved", "SELECT count(*) c FROM t" + wsql, params)[0]["c"]
        rows = warehouse.query("dim_outlet_resolved",
            "SELECT %s FROM t%s ORDER BY state, city, outlet_name LIMIT %d OFFSET %d"
            % (_OUTM_COLS, wsql, size, page * size), params)
        facet = warehouse.query("dim_outlet_resolved",
            "SELECT CAST(state AS VARCHAR) state, count(*) n FROM t%s GROUP BY 1 ORDER BY n DESC LIMIT 60" % wsql, params)
    except Exception as e:
        return jsonify(ok=True, landed=False, error=str(e)[:140], outlets=[], total=0), 200
    return jsonify(ok=True, landed=True, total=total, page=page, size=size,
                   outlets=rows, states=[{"state": r["state"], "n": r["n"]} for r in facet])

@app.get("/api/master/outlet")
def master_outlet_one_ep():
    """One resolved outlet's full record (all fields + which sources contributed) for the detail panel."""
    import warehouse
    key = (request.args.get("key") or "").strip()
    if not key:
        return jsonify(ok=False, error="key required"), 400
    try:
        rows = warehouse.query("dim_outlet_resolved",
            "SELECT %s, master_created_at, master_updated_at, updated_by FROM t WHERE outlet_key = ? LIMIT 1"
            % _OUTM_COLS, [key])
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:140]), 200
    return jsonify(ok=True, outlet=(rows[0] if rows else None))

# ── MDM workbench: CONFIRM / CONFORM — reconcile the same outlet across sources into Tier-1 truth ──
# The deterministic resolve merges on exact key; cross-VENDOR duplicates (AB vpid vs VTInfo, which lacks
# zip) slip through, so we FUZZY-match: cross-source pairs in the same ~1km cell, high name similarity,
# within ~0.5 mi. Accepting a candidate records that the two outlet_keys are the SAME real outlet.
_CONFIRM_SQL = (
    "WITH o AS (SELECT outlet_key, outlet_name, address, city, state, carriage, "
    " try_cast(lat AS DOUBLE) lat, try_cast(lng AS DOUBLE) lng, source_list "
    " FROM t WHERE try_cast(lat AS DOUBLE) IS NOT NULL) "
    "SELECT a.outlet_key ka, a.outlet_name na, a.address aa, a.city, a.state, CAST(a.carriage AS VARCHAR) ca, a.source_list sa, "
    " b.outlet_key kb, b.outlet_name nb, b.address ba, CAST(b.carriage AS VARCHAR) cb, b.source_list sb, "
    " round(jaro_winkler_similarity(lower(a.outlet_name),lower(b.outlet_name)),3) sim, "
    " round(sqrt(power((a.lat-b.lat)*69,2)+power((a.lng-b.lng)*54.6,2)),2) dist_mi "
    "FROM o a JOIN o b ON a.outlet_key<b.outlet_key AND round(a.lat,2)=round(b.lat,2) AND round(a.lng,2)=round(b.lng,2) "
    "WHERE a.source_list<>b.source_list "
    " AND jaro_winkler_similarity(lower(a.outlet_name),lower(b.outlet_name)) > ? "
    " AND sqrt(power((a.lat-b.lat)*69,2)+power((a.lng-b.lng)*54.6,2)) < 0.5 "
    "ORDER BY sim DESC, dist_mi ASC LIMIT ?")

@app.get("/api/master/confirm/candidates")
def master_confirm_candidates_ep():
    """Fuzzy cross-source outlet-match candidates (same place, different source) to review + confirm.
    ?min= name-similarity threshold (default .86), ?limit=. Already-decided pairs are dropped."""
    import warehouse
    try:
        mn = float(request.args.get("min", 0.86)); lim = min(500, int(request.args.get("limit", 150)))
    except ValueError:
        mn, lim = 0.86, 150
    try:
        rows = warehouse.query("dim_outlet_resolved", _CONFIRM_SQL, [mn, lim + 60])
    except Exception as e:
        return jsonify(ok=True, landed=False, error=str(e)[:160], candidates=[]), 200
    decided = load("outlet_confirms.json", {})
    out = []
    for r in rows:
        key = "|".join(sorted([str(r["ka"]), str(r["kb"])]))
        if key in decided:
            continue
        out.append(r)
        if len(out) >= lim:
            break
    return jsonify(ok=True, landed=True, count=len(out), candidates=out)

@app.post("/api/master/confirm")
def master_confirm_post_ep():
    """Record a confirm decision on a candidate pair: {a, b, decision:'same'|'different'}. 'same' = the
    two outlet_keys are one real outlet (Tier-1 confirmed); 'different' = not a match (won't resurface)."""
    b = request.get_json(silent=True) or {}
    ka, kb = (b.get("a") or "").strip(), (b.get("b") or "").strip()
    if not (ka and kb):
        return jsonify(ok=False, error="a + b required"), 400
    key = "|".join(sorted([ka, kb]))
    confirms = load("outlet_confirms.json", {})
    confirms[key] = {"a": ka, "b": kb, "decision": (b.get("decision") or "same"),
                     "at": int(time.time()), "by": _user_rec().get("code", "SYS")}
    _save_json("outlet_confirms.json", confirms)
    same = sum(1 for v in confirms.values() if v.get("decision") == "same")
    return jsonify(ok=True, decided=len(confirms), confirmed=same)

@app.get("/api/master/confirm/summary")
def master_confirm_summary_ep():
    """The reconciliation picture: outlet count, auto-confirmed (2+ sources), fuzzy candidates, decisions."""
    import warehouse
    out = {"outlets": None, "auto_confirmed": None, "candidates": None}
    try:
        out["outlets"] = warehouse.query("dim_outlet_resolved", "SELECT count(*) c FROM t")[0]["c"]
        out["auto_confirmed"] = warehouse.query("dim_outlet_resolved", "SELECT count(*) c FROM t WHERE sources>=2")[0]["c"]
        out["candidates"] = warehouse.query("dim_outlet_resolved", "SELECT count(*) c FROM (%s)" % _CONFIRM_SQL, [0.86, 100000])[0]["c"]
    except Exception as e:
        out["error"] = str(e)[:120]
    confirms = load("outlet_confirms.json", {})
    out["accepted"] = sum(1 for v in confirms.values() if v.get("decision") == "same")
    out["rejected"] = sum(1 for v in confirms.values() if v.get("decision") == "different")
    return jsonify(ok=True, **out)

# ── MDM workbench: browse the SKU/product catalog (dim_sku joined up the hierarchy) ──
def _sku_join():
    import warehouse
    u = lambda n: warehouse.uri(n).replace("'", "")
    return ("LEFT JOIN read_parquet('%s') it ON t.item_key=it.item_key "
            "LEFT JOIN read_parquet('%s') p ON it.product_key=p.product_key "
            "LEFT JOIN read_parquet('%s') b ON p.brand_key=b.brand_key"
            % (u("dim_item"), u("dim_product"), u("dim_brand")))
_SKU_SEL = ("t.sku_key, t.upc, t.pack, t.vintage, t.edition, t.sources, t.source_list, "
            "p.product_name, p.category, p.origin, p.abv, it.size_ml, b.brand, b.brand_group")
@app.get("/api/master/skus")
def master_skus_ep():
    """Paged, filterable SKU catalog — dim_sku joined to item/product/brand. ?q= (brand/product/upc)."""
    import warehouse
    q = (request.args.get("q") or "").strip().lower()
    try:
        page = max(0, int(request.args.get("page", 0))); size = min(200, max(1, int(request.args.get("size", 50))))
    except ValueError:
        page, size = 0, 50
    where, params = [], []
    if q:
        where.append("(lower(CAST(b.brand AS VARCHAR)) LIKE ? OR lower(CAST(p.product_name AS VARCHAR)) LIKE ? OR lower(CAST(t.upc AS VARCHAR)) LIKE ?)")
        qq = "%" + q + "%"; params += [qq, qq, qq]
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    jn = _sku_join()
    try:
        total = warehouse.query("dim_sku", "SELECT count(*) c FROM t %s%s" % (jn, wsql), params)[0]["c"]
        rows = warehouse.query("dim_sku", "SELECT %s FROM t %s%s "
                               "ORDER BY (CASE WHEN b.brand IS NULL OR CAST(b.brand AS VARCHAR)='' THEN 1 ELSE 0 END), "
                               "b.brand, p.product_name LIMIT %d OFFSET %d"   # brand-less filings sort last
                               % (_SKU_SEL, jn, wsql, size, page * size), params)
    except Exception as e:
        return jsonify(ok=True, landed=False, error=str(e)[:140], skus=[], total=0), 200
    return jsonify(ok=True, landed=True, total=total, page=page, size=size, skus=rows)

@app.get("/api/master/sku")
def master_sku_one_ep():
    import warehouse
    key = (request.args.get("key") or "").strip()
    if not key:
        return jsonify(ok=False, error="key required"), 400
    try:
        rows = warehouse.query("dim_sku", "SELECT %s, t.master_created_at, t.updated_by FROM t %s WHERE t.sku_key = ? LIMIT 1"
                               % (_SKU_SEL, _sku_join()), [key])
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:140]), 200
    return jsonify(ok=True, sku=(rows[0] if rows else None))

@app.get("/api/master/counts")
def master_counts_ep():
    """Row counts of each master table — for the workbench overview + catalog header."""
    import warehouse
    out = {}
    for t in ("dim_brand", "dim_product", "dim_item", "dim_sku", "dim_supplier", "dim_outlet_resolved",
              "fact_inventory", "fact_pricing"):
        try:
            out[t] = warehouse.query(t, "SELECT count(*) c FROM t")[0]["c"]
        except Exception:
            out[t] = None
    return jsonify(ok=True, counts=out)

@app.get("/api/master/outlets/geo")
def master_outlets_geo_ep():
    """Light geocoded points from the resolved outlet master for the workbench map. Optional ?bbox=
    west,south,east,north (viewport) + ?q= / ?state=. Ships {outlet_key,lat,lng,name,state} only."""
    import warehouse
    where = ["try_cast(lat AS DOUBLE) IS NOT NULL", "try_cast(lng AS DOUBLE) IS NOT NULL"]
    params = []
    bb = request.args.get("bbox", "")
    if bb:
        try:
            w, s, e, n = (float(x) for x in bb.split(","))
            where.append("try_cast(lat AS DOUBLE) BETWEEN ? AND ? AND try_cast(lng AS DOUBLE) BETWEEN ? AND ?")
            params += [s, n, w, e]
        except (ValueError, TypeError):
            pass
    st = (request.args.get("state") or "").strip().upper()
    if st:
        where.append("upper(CAST(state AS VARCHAR)) = ?"); params.append(st)
    q = (request.args.get("q") or "").strip().lower()
    if q:
        where.append("(lower(CAST(outlet_name AS VARCHAR)) LIKE ? OR lower(CAST(city AS VARCHAR)) LIKE ?)")
        qq = "%" + q + "%"; params += [qq, qq]
    wsql = " WHERE " + " AND ".join(where)
    try:
        rows = warehouse.query("dim_outlet_resolved",
            "SELECT outlet_key, try_cast(lat AS DOUBLE) AS lat, try_cast(lng AS DOUBLE) AS lng, "
            "CAST(outlet_name AS VARCHAR) AS \"name\", CAST(state AS VARCHAR) AS state FROM t%s LIMIT 20000" % wsql, params)
    except Exception as e:
        return jsonify(ok=True, landed=False, error=str(e)[:140], points=[]), 200
    return jsonify(ok=True, landed=True, count=len(rows), points=rows)

@app.post("/api/master/fact/apply")
def fact_apply_ep():
    """Materialize fact_<fact> (inventory|pricing) from its mappings — one DuckDB pass per source over
    Parquet → compute outlet_key + sku/brand key inline → group by (outlet, product, date)."""
    try:
        import master_facts
        body = request.get_json(silent=True) or {}
        fact = _fact(body.get("fact") or request.args.get("fact", "inventory"))
        fields = _fact_schema(fact)
        maps = _resolve_dict_refs(_fact_maps_effective(fact))    # expand dict refs to inline entries
        who = _user_rec().get("code", "SYS")
        res = master_facts.build(fact, fields, maps, built_by=who, built_at=int(time.time()),
                                 log=lambda mm: app.logger.info("FACT[%s] %s", fact, mm))
        return jsonify(ok=True, fact=fact, built_by=who, **res)
    except Exception as e:
        app.logger.exception("fact apply failed")
        return jsonify(ok=False, error=str(e)[:200]), 200

# ── "needs dictionary" flags — map a field now, flag it to go back and build its dictionary later ──
@app.get("/api/dict/needs")
def dict_needs_get():
    """Fields flagged as needing a data dictionary. ?dataset= for one source's flags, else all."""
    ds = (request.args.get("dataset") or "").strip()
    needs = load("dict_needs.json", {})
    items = list(needs.values())
    if ds:
        items = [n for n in items if n.get("dataset") == ds]
    return jsonify(ok=True, dataset=ds or None, count=len(items), needs=items)

@app.post("/api/dict/needs")
def dict_needs_post():
    """Toggle a flag: {dataset, field, target, on}. Keyed target|dataset|field."""
    b = request.get_json(silent=True) or {}
    ds = (b.get("dataset") or "").strip(); field = (b.get("field") or "").strip()
    if not (ds and field):
        return jsonify(ok=False, error="dataset + field required"), 400
    target = b.get("target") or "product"
    key = "%s|%s|%s" % (target, ds, field)
    needs = load("dict_needs.json", {})
    if b.get("on", True) and key not in needs:
        needs[key] = {"dataset": ds, "field": field, "target": target,
                      "at": int(time.time()), "by": _user_rec().get("code", "SYS")}
    elif not b.get("on", True):
        needs.pop(key, None)
    _save_json("dict_needs.json", needs)
    return jsonify(ok=True, count=len(needs), on=key in needs)

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
        resp = send_file(full)
        # HTML/JS must REVALIDATE every load (ETag conditional) so a deploy shows immediately instead of
        # being served stale from the browser cache — the "I deployed but it looks unchanged" trap.
        if full.endswith((".html", ".js")):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp
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
