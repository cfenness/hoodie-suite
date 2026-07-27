#!/usr/bin/env python3
"""vip_finder_census.py — enumerate the ENTIRE VIP "brand finder" tenant directory.

WHAT THIS IS
  `finder.vtinfo.com/finder/web/v2/iframe?custID=<CID>` is the widget every VIP-hosted
  brand/distributor "where to buy" page iframes. The custID is the tenant key — and it is a
  **3-character, case-INSENSITIVE alphanumeric mnemonic** (SNB = Sierra Nevada Brewing,
  TTO = Tito's). That is a 36^3 = 46,656 keyspace, and an unknown id is answered with a
  distinct "Invalid customer ID" page. So the whole tenant directory is enumerable.

  `vtinfo.py` reads ONE known brand's carriage (accounts near a ZIP). This module answers the
  prior question — *who is on the platform at all* — turning a hand-harvested 7-brand dict into
  a measured census. Each hit is then a ready-made vtinfo.py target: every discovered custID is
  a brand/distributor whose real VIP depletion-backed account carriage we can pull.

HOW IT WORKS (stdlib only, no auth, no cookie, no uuid)
  1. GET  iframe?custID=<CID>
       → miss: a 1.5 KB page containing the sentinel "Invalid customer ID".
       → hit : a ~20 KB finder page carrying an inline config block (themeVersion, showCaptcha,
               brandCode/brandDescription, menuConfiguration, defaults) + a stateless
               `CSRFToken` (JS-escaped: `\\/` must be unescaped to `/` or the token is rejected).
     The uuid that vtinfo.py's BRANDS map carries is NOT required — custID alone drives it.
  2. POST iframe/filterMenu  {custID, action, source/target=BFBRCD, value=, CSRFToken}
       → the tenant's BRAND LIST as <option> rows. This is the identity payload: a single-brand
         finder returns its own SKUs, a distributor returns its whole book.

  Both stages are recorded. Stage 2 only runs on hits, so it costs ~1 extra request per tenant.

RATE LIMITING (the binding constraint — measured, not assumed)
  The origin 429s a single IP after roughly 5 requests in ~2 seconds. A 46,656-id sweep from one
  address is therefore not viable. This module runs ONE WORKER PER ISP-POOL IP (`resi.isp_pool()`)
  and paces each worker independently with AIMD: on a 429 the worker's interval doubles (and the
  id is requeued), on a clean streak it eases back down toward `--min-interval`. Run `--calibrate`
  first to measure the real safe rate on one IP before committing to a full sweep.

  With no proxy pool configured the sweep REFUSES to run (it would burn the origin IP) unless
  `--allow-direct` is passed. Probing/`--calibrate` on a handful of ids is always allowed.

RESUME
  A 46k sweep is checkpointed to agent_state/vip_finder_census.json after every batch, so a
  killed or rate-limited run resumes where it stopped instead of re-walking the keyspace.

DEGRADED (never silently emit garbage)
  Self-reports `degraded` when the "Invalid customer ID" sentinel is never seen (the miss
  discriminator moved → every id would look like a hit), when the config block stops parsing on
  hits, or when 429s exceed `--max-429-rate` of all requests (the sweep is being shaped by the
  origin, so coverage is not trustworthy). Confirm the parser offline against the captured pages
  in fixtures/ with `--selftest` — no network needed.

TABLES
  vip_finder_tenants  — one row per discovered custID (key: cust_id)
  vip_finder_brands   — one row per (custID, brand) from filterMenu (key: cust_id|brand_value)
"""
import argparse
import html as _html
import json
import os
import re
import string
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi  # noqa: E402

try:
    import warehouse
except Exception:                                    # importable without the warehouse (selftest/offline)
    warehouse = None

BASE = "https://finder.vtinfo.com/finder/web/v2/"
IFRAME = BASE + "iframe"
FILTER_MENU = BASE + "iframe/filterMenu"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# custID is case-insensitive (snb == SnB == SNB) and exactly 3 chars — 2 and 4 both answer
# "Invalid customer ID". Lowercase keeps the keyspace canonical at 36^3 = 46,656.
ALPHABET = string.digits + string.ascii_lowercase
ID_LEN = 3
MISS_SENTINEL = "Invalid customer ID"

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state")
STATE_FILE = os.path.join(STATE_DIR, "vip_finder_census.json")
# The checkpoint also lives in object storage: a CI runner's disk does not survive the run, so
# without this every scheduled bite would restart the 46k keyspace from zero.
STATE_KEY = "state/vip_finder_census.json"
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

TENANT_FIELDS = ["cust_id", "theme_version", "show_captcha", "brand_code", "brand_description",
                 "menu_fields", "n_brands", "default_zip", "default_address", "default_miles",
                 "analytics", "map_style_code", "use_online_vendor", "n_bytes", "first_seen", "last_seen"]
BRAND_FIELDS = ["cust_id", "brand_value", "brand_label", "last_seen"]


# ── keyspace ──────────────────────────────────────────────────────────────────────────────────
def keyspace(prefix=""):
    """Every candidate custID, optionally restricted to those starting with `prefix`
    (lets a sweep be split across hosts/shards: --prefix a, --prefix b, …)."""
    prefix = (prefix or "").lower()
    if len(prefix) >= ID_LEN:
        return [prefix[:ID_LEN]]
    out, rest = [], ID_LEN - len(prefix)

    def rec(stem, depth):
        if depth == 0:
            out.append(stem); return
        for c in ALPHABET:
            rec(stem + c, depth - 1)
    rec(prefix, rest)
    return out


# ── fetch ─────────────────────────────────────────────────────────────────────────────────────
class RateLimited(Exception):
    pass


def _fetch(url, data=None, opener=None, timeout=25, referer=None):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
         "Origin": "https://finder.vtinfo.com", "Referer": referer or BASE}
    if data is not None:
        h["X-Requested-With"] = "XMLHttpRequest"
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        op = opener.open if opener else urllib.request.urlopen
        with op(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise RateLimited("HTTP %d" % e.code)
        raise


# ── parse ─────────────────────────────────────────────────────────────────────────────────────
def _js_val(page, name):
    """Pull `name = <literal>` out of the inline config block. Returns str/None."""
    m = re.search(r'\b%s\s*=\s*(null|true|false|"((?:[^"\\]|\\.)*)"|-?[\d.]+)' % re.escape(name), page)
    if not m:
        return None
    if m.group(2) is not None:
        return m.group(2).replace('\\/', '/')
    v = m.group(1)
    return None if v == "null" else v


def parse_tenant(cust_id, page):
    """The inline config block on a hit page → one tenant record. Returns None if the page is a
    miss; raises ValueError if it is a hit whose config block did not parse (drift → degraded)."""
    if MISS_SENTINEL in page:
        return None
    tok = re.search(r'CSRFToken\s*=\s*"((?:[^"\\]|\\.)*)"', page)
    if not tok:
        raise ValueError("hit page for %s carries no CSRFToken (config block drifted)" % cust_id)
    fields = ""
    mm = re.search(r'menuConfiguration\s*=\s*(\[.*?\]),\s*\n', page, re.S)
    if mm:
        try:
            fields = ",".join(d.get("columnName", "") for d in json.loads(_html.unescape(mm.group(1))))
        except Exception:
            fields = ""
    now = int(time.time() * 1000)
    return {
        "cust_id": cust_id,
        "theme_version": _js_val(page, "themeVersion") or "",
        "show_captcha": (_js_val(page, "showCaptcha") or "false"),
        "brand_code": _js_val(page, "brandCode") or "",
        "brand_description": _js_val(page, "brandDescription") or "",
        "menu_fields": fields,
        "n_brands": 0,
        "default_zip": _js_val(page, "defaultZip") or "",
        "default_address": _js_val(page, "defaultAddress") or "",
        "default_miles": _js_val(page, "defaultMileSelection") or "",
        "analytics": _js_val(page, "analytics") or "",
        "map_style_code": _js_val(page, "mapStyleCode") or "",
        "use_online_vendor": _js_val(page, "useOnlineVendor") or "false",
        "n_bytes": len(page),
        "first_seen": now,
        "last_seen": now,
    }, tok.group(1).replace('\\/', '/')


def parse_options(fragment):
    """filterMenu's HTML fragment → [(value, label)], minus the 'Any …' placeholder."""
    out = []
    for m in re.finditer(r'<option value="([^"]*)"[^>]*>([^<]*)', fragment):
        val = _html.unescape(m.group(1)).strip()
        lab = _html.unescape(m.group(2)).strip()
        if val:
            out.append((val, lab))
    return out


def brands_for(cust_id, token, opener=None, timeout=25):
    """The tenant's brand list via filterMenu. [] when the tenant exposes no brand menu."""
    ref = "%s?custID=%s" % (IFRAME, cust_id)
    body = [("custID", cust_id), ("action", "results"), ("m", "5"), ("d", ""),
            ("source", "BFBRCD"), ("target", "BFBRCD"), ("value", ""),
            ("targetDefaultOption", "Any Brand"), ("CSRFToken", token)]
    frag = _fetch(FILTER_MENU, body, opener=opener, timeout=timeout, referer=ref)
    if "Invalid token" in frag:
        raise ValueError("filterMenu rejected the CSRF token for %s" % cust_id)
    return parse_options(frag)


# ── worker (one per exit IP, self-pacing) ─────────────────────────────────────────────────────
class Worker(threading.Thread):
    """Probes ids off a shared queue through ONE exit IP, pacing itself with AIMD: a 429 doubles
    the interval and requeues the id; a clean streak eases it back toward min_interval."""

    def __init__(self, wid, proxy_url, queue, qlock, sink, args):
        super().__init__(daemon=True)
        self.wid, self.proxy_url, self.queue, self.qlock, self.sink, self.args = \
            wid, proxy_url, queue, qlock, sink, args
        self.interval = args.min_interval
        self.opener = None
        if proxy_url:
            self.opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
        self.n_req = self.n_429 = self.n_err = 0
        self.streak = 0

    def _next(self):
        with self.qlock:
            return self.queue.pop() if self.queue else None

    def _requeue(self, cid):
        with self.qlock:
            self.queue.append(cid)

    def run(self):
        while not self.sink.stop.is_set():
            cid = self._next()
            if cid is None:
                return
            try:
                self._probe(cid)
                self.streak += 1
                if self.streak >= 20 and self.interval > self.args.min_interval:
                    self.interval = max(self.args.min_interval, self.interval * 0.7)
                    self.streak = 0
            except RateLimited:
                self.n_429 += 1
                self.streak = 0
                self.interval = min(self.args.max_interval, max(self.interval, 0.25) * 2)
                self._requeue(cid)
                self.sink.note_429()
                time.sleep(self.interval * 2)
                continue
            except Exception as e:
                self.n_err += 1
                self.sink.note_error(cid, str(e)[:120])
            time.sleep(self.interval)

    def _probe(self, cid):
        page = _fetch("%s?custID=%s" % (IFRAME, cid), opener=self.opener, timeout=self.args.timeout)
        self.n_req += 1
        if MISS_SENTINEL in page:
            self.sink.note_miss(cid)
            return
        parsed = parse_tenant(cid, page)          # raises ValueError on drift
        if parsed is None:
            self.sink.note_miss(cid)
            return
        rec, token = parsed
        brands = []
        if not self.args.no_brands:
            time.sleep(self.interval)
            try:
                brands = brands_for(cid, token, opener=self.opener, timeout=self.args.timeout)
                self.n_req += 1
            except RateLimited:
                raise
            except Exception as e:
                self.sink.note_error(cid, "brands: %s" % str(e)[:100])
        rec["n_brands"] = len(brands)
        self.sink.note_hit(rec, brands)


class Sink:
    """Thread-safe result accumulator + checkpointer."""

    def __init__(self, state, log=print):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.state = state
        self.log = log
        self.n_miss = self.n_hit = self.n_429 = 0
        self.errors = []
        self.new_hits = []

    def note_miss(self, cid):
        with self.lock:
            self.state["done"][cid] = 0
            self.n_miss += 1

    def note_hit(self, rec, brands):
        with self.lock:
            cid = rec["cust_id"]
            self.state["done"][cid] = 1
            prior = self.state["hits"].get(cid)
            if prior and prior.get("first_seen"):
                rec["first_seen"] = prior["first_seen"]
            self.state["hits"][cid] = rec
            self.state["brands"][cid] = [list(b) for b in brands]
            self.n_hit += 1
            self.new_hits.append(rec)
            self.log("  ✓ %s  theme=%s  brands=%-4d %s" % (
                cid, rec["theme_version"], rec["n_brands"],
                (rec["brand_description"] or (brands[0][1] if brands else ""))[:44]))

    def note_429(self):
        with self.lock:
            self.n_429 += 1

    def note_error(self, cid, msg):
        with self.lock:
            self.errors.append("%s: %s" % (cid, msg))
            if len(self.errors) > 400:
                self.errors = self.errors[-400:]


# ── state ─────────────────────────────────────────────────────────────────────────────────────
def _blank():
    return {"done": {}, "hits": {}, "brands": {}, "started": int(time.time() * 1000)}


def _normalize(s):
    s.setdefault("done", {}); s.setdefault("hits", {}); s.setdefault("brands", {})
    return s


def load_state():
    """Checkpoint, preferring the WAREHOUSE copy. A CI runner's disk is ephemeral, so a purely
    local checkpoint would make every scheduled run re-walk the 46k keyspace from zero; the
    warehouse copy is what makes `--deadline`-bounded bites across separate runs actually add up.
    Falls back to the local file (and merges it in) when object storage isn't configured."""
    remote = {}
    if warehouse is not None:
        try:
            raw = warehouse.get_bytes(STATE_KEY)
            if raw:
                remote = _normalize(json.loads(raw.decode("utf-8")))
        except Exception:
            remote = {}
    local = {}
    if os.path.exists(STATE_FILE):
        try:
            local = _normalize(json.load(open(STATE_FILE, encoding="utf-8")))
        except Exception:
            local = {}
    if not remote and not local:
        return _blank()
    if not remote:
        return local
    if not local:
        return remote
    for k in ("done", "hits", "brands"):                 # union — neither copy is authoritative
        remote[k].update(local[k])
    return remote


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    blob = json.dumps(state).encode("utf-8")
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, STATE_FILE)
    if warehouse is not None and getattr(warehouse, "remote", lambda: False)():
        try:
            warehouse.put_bytes(STATE_KEY, blob)
        except Exception:
            pass                                         # a failed checkpoint push must not kill the sweep


# ── sweep ─────────────────────────────────────────────────────────────────────────────────────
def sweep(prefix="", limit=None, args=None, log=print):
    args = args or parse_args([])
    state = load_state() if not args.restart else {"done": {}, "hits": {}, "brands": {},
                                                   "started": int(time.time() * 1000)}
    todo = [c for c in keyspace(prefix) if c not in state["done"]]
    if limit:
        todo = todo[:limit]
    if not todo:
        log("vip-finder-census: nothing to do (%d ids already probed)" % len(state["done"]))
        return state, []

    pool = resi.isp_pool()
    if not pool:
        if not args.allow_direct:
            raise SystemExit(
                "REFUSING to sweep %d ids with no ISP proxy pool — one IP 429s after ~5 rapid\n"
                "requests, so this would only burn the origin address. Configure ISP_PROXIES /\n"
                "unifyd/isp_proxies.txt, or pass --allow-direct to accept that." % len(todo))
        log("!! no proxy pool — sweeping DIRECT at %.1fs/req (origin IP exposed)" % args.min_interval)
        pool = [None]
    workers_n = min(args.workers or len(pool), len(pool))
    log("vip-finder-census: %d ids to probe · %d exit IPs · %.2fs start interval"
        % (len(todo), workers_n, args.min_interval))

    todo.reverse()                                   # pop() from the end == alphabetical order
    qlock = threading.Lock()
    sink = Sink(state, log=log)
    workers = [Worker(i, pool[i % len(pool)], todo, qlock, sink, args) for i in range(workers_n)]
    t0 = time.time()
    for w in workers:
        w.start()
        time.sleep(0.15)                             # stagger so the pool doesn't burst in lockstep
    try:
        while any(w.is_alive() for w in workers):
            time.sleep(args.checkpoint_every)
            with sink.lock:
                save_state(state)
                done = sink.n_hit + sink.n_miss
            rate = done / max(1e-6, time.time() - t0)
            log("  … %d probed (%d hits, %d 429s) · %.1f id/s · %d queued"
                % (done, sink.n_hit, sink.n_429, rate, len(todo)))
            if args.deadline and time.time() - t0 > args.deadline:
                log("  deadline reached — stopping cleanly (resumable)")
                sink.stop.set()
    except KeyboardInterrupt:
        log("  interrupted — checkpointing (resumable)")
        sink.stop.set()
    for w in workers:
        w.join(timeout=30)
    save_state(state)

    n_req = sum(w.n_req for w in workers)
    warns = []
    if sink.n_miss == 0 and sink.n_hit:
        warns.append("miss sentinel %r never seen in %d probes — the discriminator moved; every id "
                     "would read as a hit" % (MISS_SENTINEL, sink.n_hit))
    if sink.n_hit == 0 and sink.n_miss:
        warns.append("0 hits in %d probes — parse or keyspace drift" % sink.n_miss)
    rate429 = sink.n_429 / max(1, n_req + sink.n_429)
    if rate429 > args.max_429_rate:
        warns.append("429 rate %.1f%% exceeds %.1f%% — the origin is shaping the sweep, coverage is "
                     "not trustworthy" % (rate429 * 100, args.max_429_rate * 100))
    if sink.errors:
        warns.append("%d request errors (first: %s)" % (len(sink.errors), sink.errors[0]))
    log("vip-finder-census: %d hits / %d probed (%.2f%%) · %d 429s · %ds"
        % (sink.n_hit, sink.n_hit + sink.n_miss,
           100.0 * sink.n_hit / max(1, sink.n_hit + sink.n_miss), sink.n_429, time.time() - t0))
    return state, warns


# ── land ──────────────────────────────────────────────────────────────────────────────────────
def land(state, log=print):
    if warehouse is None:
        log("warehouse unavailable — not landing"); return 0, 0
    tenants = [{k: h.get(k, "") for k in TENANT_FIELDS} for h in state["hits"].values()]
    now = int(time.time() * 1000)
    brands = [{"cust_id": cid, "brand_value": b[0], "brand_label": b[1] if len(b) > 1 else b[0],
               "last_seen": now}
              for cid, bl in state["brands"].items() for b in bl]
    if tenants:
        warehouse.write_accumulate("vip_finder_tenants", tenants,
                                   key=lambda r: r["cust_id"], fields=TENANT_FIELDS)
    if brands:
        warehouse.write_accumulate("vip_finder_brands", brands,
                                   key=lambda r: (r["cust_id"], r["brand_value"]), fields=BRAND_FIELDS)
    log("landed %d tenants / %d brand rows" % (len(tenants), len(brands)))
    return len(tenants), len(brands)


def pull(prefix="", limit=None, argv=None, log=print):
    """Registry entrypoint. Sweeps (resumable), lands, returns a run record."""
    args = parse_args(argv or [])
    started = int(time.time() * 1000)
    state, warns = sweep(prefix=prefix or args.prefix, limit=limit or args.limit, args=args, log=log)
    n_t, n_b = land(state, log=log)
    status = "degraded" if warns else "success"
    return {"connId": "vip-finder-census", "startedAt": started, "finishedAt": int(time.time() * 1000),
            "status": status, "degraded": bool(warns), "warnings": warns, "total": n_t,
            "extracts": [{"id": "vip_finder_tenants", "rows": n_t, "status": status},
                         {"id": "vip_finder_brands", "rows": n_b, "status": status}]}


# ── calibrate / selftest ──────────────────────────────────────────────────────────────────────
def calibrate(log=print):
    """Measure the real 429 threshold on ONE exit IP: how many back-to-back requests land before
    the origin pushes back, at a few candidate intervals. Run this before a full sweep."""
    pool = resi.isp_pool()
    proxy = pool[0] if pool else None
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})) if proxy else None
    log("calibrating on %s" % ("ISP pool IP #0" if proxy else "the ORIGIN IP (no pool configured)"))
    probe_ids = keyspace("zz")                       # a dense block of near-certain misses (cheap 1.5 KB)
    for interval in (0.0, 0.25, 0.5, 1.0, 2.0):
        ok = 0
        for cid in probe_ids[:25]:
            try:
                _fetch("%s?custID=%s" % (IFRAME, cid), opener=opener, timeout=20)
                ok += 1
            except RateLimited:
                break
            except Exception:
                break
            time.sleep(interval)
        log("  interval %.2fs → %2d/25 clean%s" % (interval, ok, "  ✓ sustainable" if ok == 25 else "  ← 429"))
        if ok == 25:
            log("\nrecommended: --min-interval %.2f  (× %d pool IPs ≈ %.0f id/s ≈ %.0f min for 46,656)"
                % (interval, max(1, len(pool)), max(1, len(pool)) / max(interval, 0.05),
                   46656 / max(1e-6, max(1, len(pool)) / max(interval, 0.05)) / 60))
            return interval
        time.sleep(20)                               # let the limiter's window drain between trials
    log("no sustainable interval found up to 2.0s — needs a bigger pool or a slower pace")
    return None


def selftest(log=print):
    """Confirm the parser against captured pages — no network. Add fixtures as:
       fixtures/vip_finder_<custID>.html (a hit) and fixtures/vip_finder_miss.html."""
    ok = True
    miss_fx = os.path.join(FIXTURES, "vip_finder_miss.html")
    if os.path.exists(miss_fx):
        page = open(miss_fx, encoding="utf-8", errors="replace").read()
        got = parse_tenant("zzz", page)
        log("  miss fixture → %s" % ("PASS (classified as miss)" if got is None else "FAIL (read as a hit)"))
        ok &= got is None
    else:
        log("  miss fixture absent (%s)" % miss_fx); ok = False
    for fn in sorted(os.listdir(FIXTURES)) if os.path.isdir(FIXTURES) else []:
        if not (fn.startswith("vip_finder_") and fn.endswith(".html")) or fn == "vip_finder_miss.html":
            continue
        cid = fn[len("vip_finder_"):-len(".html")]
        page = open(os.path.join(FIXTURES, fn), encoding="utf-8", errors="replace").read()
        try:
            rec, token = parse_tenant(cid, page)
            good = bool(token) and rec["theme_version"] and rec["n_bytes"] > 5000
            log("  hit fixture %s → %s (theme=%s captcha=%s menus=%s token=%dch)"
                % (cid, "PASS" if good else "FAIL", rec["theme_version"], rec["show_captcha"],
                   rec["menu_fields"], len(token)))
            ok &= good
            if "\\/" in token:
                log("    FAIL: token still JS-escaped"); ok = False
        except Exception as e:
            log("  hit fixture %s → FAIL (%s)" % (cid, e)); ok = False
    log("selftest: %s" % ("PASS" if ok else "FAIL"))
    return ok


# ── cli ───────────────────────────────────────────────────────────────────────────────────────
def parse_args(argv):
    ap = argparse.ArgumentParser(description="Enumerate the VIP brand-finder tenant directory.")
    ap.add_argument("--prefix", default="", help="restrict the sweep to ids starting with this (shard a sweep)")
    ap.add_argument("--limit", type=int, help="probe at most N ids this run (resumable)")
    ap.add_argument("--workers", type=int, help="default: one per ISP pool IP")
    ap.add_argument("--min-interval", type=float, default=1.0, help="per-IP seconds between requests")
    ap.add_argument("--max-interval", type=float, default=30.0, help="per-IP backoff ceiling")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--checkpoint-every", type=float, default=20.0, help="seconds between checkpoints")
    ap.add_argument("--deadline", type=float, help="stop cleanly after N seconds (resumable)")
    ap.add_argument("--max-429-rate", type=float, default=0.10, help="above this → degraded")
    ap.add_argument("--no-brands", action="store_true", help="skip the filterMenu brand pull on hits")
    ap.add_argument("--allow-direct", action="store_true", help="sweep without a proxy pool (burns the origin IP)")
    ap.add_argument("--restart", action="store_true", help="discard the checkpoint and re-walk the keyspace")
    ap.add_argument("--calibrate", action="store_true", help="measure the safe per-IP rate, then exit")
    ap.add_argument("--selftest", action="store_true", help="parser check against fixtures/, no network")
    ap.add_argument("--report", action="store_true", help="summarize the current checkpoint, no network")
    ap.add_argument("--no-land", action="store_true", help="sweep but do not write to the warehouse")
    return ap.parse_args(argv)


def report(log=print):
    state = load_state()
    hits = state["hits"]
    log("checkpoint: %d ids probed, %d tenants found (%.2f%% hit rate)"
        % (len(state["done"]), len(hits), 100.0 * len(hits) / max(1, len(state["done"]))))
    if not hits:
        return
    by_brands = sorted(hits.values(), key=lambda r: -r.get("n_brands", 0))
    log("\nlargest books (a big brand list = a distributor/supplier portfolio, not one brand):")
    for r in by_brands[:25]:
        sample = ", ".join(b[1] for b in state["brands"].get(r["cust_id"], [])[:3])
        log("  %-4s %5d brands  theme=%-2s %s" % (r["cust_id"], r["n_brands"], r["theme_version"], sample[:60]))
    themes = {}
    for r in hits.values():
        themes[r["theme_version"]] = themes.get(r["theme_version"], 0) + 1
    log("\nthemes: %s · captcha-armed: %d"
        % (themes, sum(1 for r in hits.values() if str(r.get("show_captcha")).lower() == "true")))


def main(argv=None):
    a = parse_args(argv if argv is not None else sys.argv[1:])
    if a.selftest:
        return 0 if selftest() else 1
    if a.report:
        report(); return 0
    if a.calibrate:
        calibrate(); return 0
    state, warns = sweep(prefix=a.prefix, limit=a.limit, args=a)
    if not a.no_land:
        land(state)
    for w in warns:
        print("WARN:", w)
    report()
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
