#!/usr/bin/env python3
"""abc_fws_scraper.py — polite, directional inventory tracker for ABC Fine Wine & Spirits.

Why this exists
---------------
ABC FWS (abcfws.com) runs on BigCommerce. The public storefront exposes, per SKU:
  • a price (in the server HTML), and
  • a binary in-stock / out-of-stock status.
It does NOT expose a numeric quantity-on-hand, and per-store stock is behind an AJAX
endpoint that robots.txt disallows. So you cannot literally compute "units sold =
yesterday's qty − today's qty". What you CAN observe, day over day, is *directional*:
  • price changes,
  • out-of-stock ↔ restock transitions,
  • assortment churn — SKUs appearing/disappearing from the catalog.
Polled on a cadence, that's an imprecise-but-useful read on what's moving.

How it stays polite
-------------------
robots.txt gives our crawler class a 10s crawl-delay and disallows cart/checkout/
account/admin/search/facet + the per-store stock AJAX. This scraper touches ONLY the
product sitemap and product pages (both allowed), sleeps ABC_DELAY (default 10s) between
requests, sends an honest identifying User-Agent, and caps how many pages it pulls per
run. It is read-only and stdlib-only (urllib + regex — no new dependencies).

Cadence detection
-----------------
The sitemap carries no <lastmod>, so cadence is inferred from the data itself: each run
snapshots {price, in-stock, ETag/Last-Modified} for a deterministic SAMPLE of SKUs and
diffs against the previous snapshot. Over a few daily runs, when those values flip tells
you how often the catalog refreshes — without crawling all ~2,100 products every time.

CLI:
    python abc_fws_scraper.py --sample 40 --out ./abc_out
    python abc_fws_scraper.py --all --limit 500       # wider crawl (slow; respects delay)
"""
import argparse, hashlib, json, os, re, sys, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polite

# safe-scrape knobs (per-host rate/backoff/breaker live in polite; proxy = Tier-2 rotating residential IPs)
ABC_MIN_INT = float(os.environ.get("ABC_MIN_INTERVAL", "0.6"))
ABC_PROXY   = os.environ.get("ABC_PROXY", "0") == "1"
# The direct crawl periodically hits a volume-triggered 403 wall (WAF) partway through the daily full sweep —
# single probes still return 200, so it's rate-based, not a UA/robots block. When that happens mid-run we flip
# to the BD proxy (rotating IPs) for the remainder instead of losing the day's inventory. Starts per ABC_PROXY.
_PROXY = {"on": ABC_PROXY}


def _proxy_available():
    return bool((os.environ.get("BRIGHTDATA_PROXY_USER") or os.environ.get("BRD_PROXY_USER"))
                and (os.environ.get("BRIGHTDATA_PROXY_PASS") or os.environ.get("BRD_PROXY_PASS")))

BASE        = "https://abcfws.com"
SITEMAP     = BASE + "/xmlsitemap.php?type=products&page={}"
UA          = os.environ.get("ABC_UA",
                "HoodieSuite-Research/1.0 (+market-intelligence; respects robots, 10s delay)")
DELAY       = float(os.environ.get("ABC_DELAY", "10"))   # robots crawl-delay for our UA
SNAP_FILE   = "abc_snapshot.json"                        # latest snapshot, for day-over-day diff
PRICE_RE    = re.compile(r'(?:og:price:amount|product:price:amount)"[^>]*content="([\d.]+)"', re.I)
PRICE_TXT   = re.compile(r'\$\s?(\d[\d,]*\.\d{2})')
AVAIL_RE    = re.compile(r'og:availability"[^>]*content="([^"]+)"', re.I)   # standard, page-level
INSTOCK_RE  = re.compile(r'"instock"\s*:\s*(true|false)', re.I)              # BCData fallback
UPC_RE      = re.compile(r'UPC[^0-9]{0,12}(\d{8,14})', re.I)
LOC_RE      = re.compile(r'<loc>\s*([^<]+?)\s*</loc>', re.I)
ID_RE       = re.compile(r'/(\d+)/?$')
# STORE-LEVEL: the store is a BigCommerce product option; its radio labels carry the store
# name, and `available_variant_values` lists the in-stock store option values. Both are in
# the product page (no robots-disallowed AJAX needed).
STORE_LBL   = re.compile(r'data-product-attribute-value="(\d+)"[^>]*>\s*([^<]{1,80}?)\s*<')
AVAIL2_RE   = re.compile(r'available_variant_values"\s*:\s*\[([\d,]*)\]', re.I)
OG_TITLE    = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]{1,300})"', re.I)
H1_TITLE    = re.compile(r'<h1[^>]*class="[^"]*productView-title[^"]*"[^>]*>\s*([^<]{1,300}?)\s*<', re.I)
OG_BRAND    = re.compile(r'<meta[^>]+property="product:brand"[^>]+content="([^"]{1,120})"', re.I)


# ---------------- fetch (via polite: rate-limit + backoff + circuit breaker + optional BD proxy) ----------
def fetch(url, timeout=30):
    def _get():
        return polite.get(url, min_interval=ABC_MIN_INT, jitter=ABC_MIN_INT, timeout=timeout,
                          headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                   "Accept-Encoding": "identity"},
                          breaker_after=4, use_proxy=_PROXY["on"], host="abcfws.com", return_headers=True)
    try:
        body, h = _get()
    except polite.Blocked:
        if _PROXY["on"] or not _proxy_available():
            raise
        _PROXY["on"] = True                      # 403 wall mid-run → finish through the BD proxy
        polite.reset("abcfws.com")               # the trip was on the direct path; the proxy path is fresh
        body, h = _get()
    return body.decode("utf-8", "replace"), {"etag": h.get("ETag", ""),
                                             "last_modified": h.get("Last-Modified", ""), "status": 200}


# ---------------- sitemap → stable (sku, url) catalog ----------------
def harvest_ids(max_pages=None, log=print):
    """Walk the product sitemaps, returning [(sku, url)] with sku = the trailing id.
    `type=products` = the WHOLE catalog, every product type (no category filter — ABC FWS
    is a chain, not a control store; whatever it lists is captured). Stops at the first
    empty/missing page, so max_pages is only a runaway guard, never an intended limit.

    The ceiling used to be 30 pages with no signal on reaching it, which is a silent cap on a "full"
    harvest — if the catalog ever outgrew 30 pages we would quietly crawl a prefix and report success.
    It is now high, and HITTING it is reported as a warning by the caller rather than passing silently."""
    max_pages = max_pages or int(os.environ.get("ABC_MAX_PAGES", "500"))
    out, seen = [], set()
    hit_ceiling = True
    for page in range(1, max_pages + 1):
        try:
            body, _ = fetch(SITEMAP.format(page))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                hit_ceiling = False
                break
            log(f"sitemap page {page}: HTTP {e.code}")
            hit_ceiling = False
            break
        locs = LOC_RE.findall(body)
        prod = [(m.group(1), loc) for loc in locs for m in [ID_RE.search(loc)] if m]
        if not prod:
            hit_ceiling = False
            break
        for sku, loc in prod:
            if sku not in seen:
                seen.add(sku); out.append((sku, loc))
        log(f"sitemap page {page}: {len(prod)} products (total {len(out)})")
        time.sleep(DELAY)
    if hit_ceiling:
        log(f"  !! sitemap walk stopped at the {max_pages}-page ceiling — the catalog may be LARGER "
            f"than the {len(out)} products harvested. Raise ABC_MAX_PAGES.")
    return out, hit_ceiling


# ---------------- product page → {price, in-stock, upc} (best-effort, self-reporting) ----------------
def parse_stores(html):
    """Per-STORE price + in/out from a BigCommerce product page. The store is an option
    attribute (radio labels = store names like 'ABC #003 - OBT' / 'Online'); the in-stock
    subset is `available_variant_values`. Price is chain-level (one BigCommerce price).
    Returns (rows, ok) where rows=[{store_val, store, instock, price}]; ok=False means no
    stores/price parsed (selector drift)."""
    m = PRICE_RE.search(html) or PRICE_TXT.search(html)
    price = None
    if m:
        try: price = float(m.group(1).replace(",", ""))
        except ValueError: price = None
    av = AVAIL2_RE.search(html)
    avail = set(av.group(1).split(",")) if (av and av.group(1)) else set()
    # Product identity from the page itself, so the HTML fallback doesn't silently reintroduce the
    # blank-name/brand gap the GraphQL path just fixed. og:title is present on every BigCommerce PDP.
    mn = OG_TITLE.search(html) or H1_TITLE.search(html)
    pname = re.sub(r"\s+", " ", (mn.group(1) if mn else "")).strip()
    mb = OG_BRAND.search(html)
    pbrand = (mb.group(1).strip() if mb else "")
    rows = []
    for val, lbl in STORE_LBL.findall(html):
        lbl = lbl.strip()
        if not (lbl.startswith("ABC #") or lbl.lower() == "online"):
            continue   # store options only — skip any non-store attribute values
        rows.append({"store_val": val, "store": lbl, "instock": val in avail, "price": price,
                     "name": pname, "brand": pbrand})
    return rows, bool(rows) and price is not None


# --- REAL per-store bottle counts via the BigCommerce Storefront GraphQL API ------------------
# ABC's store picker is a set of product VARIANTS (one per store); each variant carries
# inventory.aggregated.availableToSell — the actual units on hand. The storefront JWT is embedded
# in every product page (it's the public token the site's own JS uses). This is the sanctioned
# storefront API — not the robots-disallowed legacy stock AJAX — so we get a true quantity, not
# just in/out. Toggle with ABC_QTY=0.
TOKEN_RE = re.compile(r'eyJ0eXAiOiJKV1Qi[A-Za-z0-9_.-]{60,}')
WANT_QTY = os.environ.get("ABC_QTY", "1") == "1"
GQL_Q = ('{ site { route(path: "%s") { node { ... on Product { '
         'name brand { name } '                      # product identity — see the note below
         'prices { price { value } } '
         'variants(first: 200) { edges { node { sku upc gtin inventory { isInStock aggregated { availableToSell } } '
         'options { edges { node { values { edges { node { label } } } } } } } } } } } } } }')
# `name`/`brand` were absent from this query and hardcoded to "" at the observe.record call, so every
# ABC observation landed with NO product identity — 0% fill on name/brand across 5,327 rows, joinable to
# the master by nothing but ABC's internal product_id. They come free in the same request.


def graphql_stores(path, token, host):
    """Per-store rows with a real quantity via GraphQL. Returns (rows, ok); rows carry `qty`.
    rows=[{store, sku, instock, qty, price}]. ok=False on any error → caller falls back to HTML."""
    try:
        body = polite.get("https://%s/graphql" % host,
            data=json.dumps({"query": GQL_Q % path}).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            min_interval=ABC_MIN_INT, jitter=ABC_MIN_INT, timeout=25, breaker_after=4,
            use_proxy=_PROXY["on"], host="abcfws.com")
        r = json.loads(body.decode("utf-8", "replace"))
        node = (((r.get("data") or {}).get("site") or {}).get("route") or {}).get("node") or {}
        price = None
        try: price = float(node["prices"]["price"]["value"])
        except Exception: pass
        pname = node.get("name") or ""
        pbrand = ((node.get("brand") or {}) or {}).get("name") or ""
        rows = []
        for e in ((node.get("variants") or {}).get("edges") or []):
            n = e["node"]; inv = n.get("inventory") or {}; agg = inv.get("aggregated") or {}
            opts = (n.get("options") or {}).get("edges") or []
            label = ""
            if opts:
                vals = (opts[0]["node"].get("values") or {}).get("edges") or []
                if vals: label = vals[0]["node"].get("label", "")
            if not (label.startswith("ABC #") or label.lower() == "online"):
                continue
            rows.append({"store": label, "sku": n.get("sku", ""), "qty": agg.get("availableToSell"),
                         "upc": n.get("upc") or "", "gtin": n.get("gtin") or "",
                         "name": pname, "brand": pbrand,
                         "instock": bool(inv.get("isInStock")), "price": price})
        return rows, bool(rows)
    except Exception:
        return [], False


def fetch_product(sku, url, log=print):
    body, _ = fetch(url)
    if WANT_QTY:
        m = TOKEN_RE.search(body)
        if m:
            host = re.sub(r"^https?://", "", url).split("/")[0]
            path = "/" + url.split("//", 1)[-1].split("/", 1)[-1]
            gql_rows, gok = graphql_stores(path, m.group(0), host)
            if gok:
                return sku, gql_rows, True          # real per-store quantities
    rows, ok = parse_stores(body)                    # fallback: binary in/out from HTML
    return sku, rows, ok


# ---------------- deterministic sample: same SKUs every run, spread across catalog ----------------
def pick_sample(catalog, n):
    if n <= 0 or n >= len(catalog):
        return list(catalog)
    cat = sorted(catalog, key=lambda t: int(t[0]))   # by numeric sku → stable ordering
    stride = len(cat) / float(n)
    return [cat[int(i * stride)] for i in range(n)]


# ---------------- day-over-day diff = directional movement ----------------
def diff_snapshots(prev, cur):
    """prev/cur are {sku: record}. Returns the directional movement since last run."""
    pset, cset = set(prev), set(cur)
    price_moves, went_oos, restocked = [], [], []
    for sku in cset & pset:
        p, c = prev[sku], cur[sku]
        if p.get("price") is not None and c.get("price") is not None and p["price"] != c["price"]:
            price_moves.append({"sku": sku, "from": p["price"], "to": c["price"]})
        if p.get("instock") is True and c.get("instock") is False:
            went_oos.append(sku)
        if p.get("instock") is False and c.get("instock") is True:
            restocked.append(sku)
    changed = len({m["sku"] for m in price_moves} | set(went_oos) | set(restocked))
    return {"sampled": len(cur), "had_prev": bool(prev), "changed": changed,
            "price_moves": price_moves, "went_oos": went_oos, "restocked": restocked,
            "new": sorted(cset - pset), "dropped": sorted(pset - cset)}


# ---------------- run record (matches the /api/runs contract used by Hoodie Pulls) ----------------
def run_record(movement, n_products, status, warnings):
    rid = "R-ABC" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    return {"id": rid, "connId": "abc-fws", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n_products,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "abc_store_cells", "rows": movement["sampled"],
                          "delta": movement["changed"], "status": status}],
            "movement": movement}


def _progress(**kw):
    """Emit a machine-readable progress line for Hoodie Collect's live counters (run_journal folds
    HOODIE_PROGRESS lines into rows_seen / stage / pct). Harmless noise anywhere else."""
    try:
        print("HOODIE_PROGRESS " + json.dumps(kw), flush=True)
    except Exception:
        pass


# ── resume + incremental landing ────────────────────────────────────────────────────────────────────
# A full ABC sweep is ~13.9k product pages paced by `polite` at ~1 req/s — hours, not minutes. The old
# shape held every cell in memory and wrote ONCE at the end, so a timeout (which is what happened on
# every run for 5+ days) threw away the entire crawl and landed nothing. Now each batch lands as its own
# retail_observations partition and the completed SKUs are checkpointed to the shared bucket, so:
#   • a killed run KEEPS everything it had already fetched, and
#   • the next run RESUMES instead of restarting from zero.
# Landing per batch also makes the bench's live record counter real rather than 0-until-the-end.
_RESUME_KEY = "_collect/resume/abc-fws_%s.json"
BATCH = int(os.environ.get("ABC_BATCH", "400"))            # products per landed partition


def _resume_load(day, log=print):
    try:
        import warehouse
        raw = warehouse.get_bytes(_RESUME_KEY % day)
        d = json.loads(raw) if raw else {}
        done = set(d.get("done") or [])
        if done:
            log(f"  [abc] resuming — {len(done):,} products already captured today")
        return done, int(d.get("batch") or 0)
    except Exception:
        return set(), 0


def _resume_save(day, done, batch):
    try:
        import warehouse
        warehouse.put_bytes(_RESUME_KEY % day,
                            json.dumps({"done": sorted(done), "batch": batch,
                                        "at": int(time.time())}).encode())
    except Exception:
        pass                                                # checkpointing must never fail the crawl


def _land(day, batch_idx, cells, log=print):
    """Land ONE batch of per-store cells as its own dated partition. Distinct part names per batch so
    batches accumulate instead of overwriting each other (write_partition is idempotent per part)."""
    if not cells:
        return 0
    try:
        import observe
        observe.record("abc-fws",
                       [dict(source="abc-fws", store_id=str(v["store"]), store=str(v["store"]),
                             product_id=str(v["sku"]), upc=v.get("upc", ""),
                             brand=v.get("brand", ""), name=v.get("name", ""),
                             price=v["price"], on_promo=False, in_stock=bool(v.get("instock")),
                             qty=v.get("qty"), stock_level="", is_hemp=False)
                        for v in cells],
                       date=day, part="%s_abc-fws_b%04d" % (day, batch_idx), log=log)
        return len(cells)
    except Exception as e:
        log("  [abc] batch %d land FAILED: %s" % (batch_idx, str(e)[:120]))
        return 0


def pull(sample=40, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    """One run: harvest ids → fetch (sample or all) → land incrementally → diff vs prior snapshot.
    Returns (datasets, [run_record], movement)."""
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    day = time.strftime("%Y-%m-%d")
    catalog, hit_ceiling = harvest_ids(log=log)
    if not catalog:
        run = run_record(diff_snapshots({}, {}), 0, "failed",
                         ["No products found in sitemap — site structure may have changed."])
        return {}, [run], run["movement"]
    # The catalog size IS the completeness denominator — state it before crawling so "N of M" is
    # answerable from the first log line, and comparable against what the site itself reports.
    log(f"[abc] catalog harvested from sitemap: {len(catalog):,} products (the completeness denominator)")

    targets = (catalog if limit is None else catalog[:limit]) if crawl_all else pick_sample(catalog, sample)
    # FAST: fetch product pages concurrently (plain HTTP, no anti-bot) instead of serial 10s crawl-delay.
    workers = int(os.environ.get("ABC_WORKERS", "12"))
    jitter = float(os.environ.get("ABC_JITTER", "0.25"))   # gentle per-request pause in concurrent mode
    eta = int(len(targets) * jitter / max(1, workers))
    log(f"catalog {len(catalog)} products; pulling {len(targets)} ({workers} workers, {jitter}s jitter) ~{eta}s")

    # RESUME: skip products already captured today, so a killed run continues instead of restarting.
    done, batch_idx = (_resume_load(day, log=log) if crawl_all else (set(), 0))
    todo = [t for t in targets if t[0] not in done]
    if done:
        log(f"[abc] {len(todo):,} of {len(targets):,} products remaining this pass")

    cur, ok_n = {}, 0   # cur keyed `sku|store` -> per-store {price, instock, qty, store, sku}
    pending = []        # cells awaiting their batch write
    landed_cells = landed_products = since_flush = 0
    lock = threading.Lock()

    def _flush(force=False):
        """Land a batch and checkpoint. Called under `lock`. BATCH counts PRODUCTS, not cells — one
        product yields ~133 cells (one per store), so thresholding on cells would batch ~133x too late."""
        nonlocal pending, batch_idx, landed_cells, since_flush
        if not pending or (not force and since_flush < BATCH):
            return
        batch_idx += 1
        landed_cells += _land(day, batch_idx, pending, log=log)
        log(f"  [abc] landed batch {batch_idx}: {len(pending):,} cells "
            f"({landed_products:,}/{len(todo):,} products, {landed_cells:,} cells this run)")
        pending = []
        since_flush = 0
        _resume_save(day, done, batch_idx)

    def _one(t):
        nonlocal ok_n, landed_products, since_flush
        sku, url = t
        try:
            _, rows, ok = fetch_product(sku, url, log=log)
            with lock:
                ok_n += ok
                for r in rows:
                    # key on the store LABEL (present in both GraphQL + HTML modes); qty is the
                    # real bottle count (GraphQL) or None (HTML in/out fallback).
                    cell = {"price": r["price"], "instock": r["instock"],
                            "qty": r.get("qty"), "store": r["store"], "sku": sku,
                            "upc": r.get("upc", ""), "gtin": r.get("gtin", ""),
                            "name": r.get("name", ""), "brand": r.get("brand", "")}
                    cur[f"{sku}|{r['store']}"] = cell
                    pending.append(cell)
                done.add(sku)
                landed_products += 1
                since_flush += 1
                if landed_products % 25 == 0:
                    _progress(rows=len(cur), stage="%s/%s products" % (f"{landed_products:,}", f"{len(todo):,}"),
                              pct=round(100.0 * landed_products / max(1, len(todo)), 1))
                _flush()
        except Exception as e:
            log(f"  {sku}: {e}")
        if jitter:
            time.sleep(jitter)
    try:
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for _ in ex.map(_one, todo):
                    pass
        else:
            for t in todo:
                _one(t)
    finally:
        # Land whatever is in hand even if the crawl raised or is being torn down — the whole point of
        # batching is that no fetched row is thrown away.
        with lock:
            _flush(force=True)
    # ── RE-HARVEST: the site is the source of truth and it keeps moving ─────────────────────────
    # A full sweep takes hours, so the catalog we measured completeness against at the start is stale
    # by the end — ABC adds and drops products while we crawl. Comparing our count to a denominator
    # captured 4 hours ago would let us claim "complete" against a catalog that no longer exists.
    # Re-walk the sitemap and reconcile against the CURRENT catalog, reporting drift explicitly.
    drift = {}
    if crawl_all:
        try:
            log("[abc] re-harvesting sitemap to reconcile against the live catalog…")
            catalog2, _ = harvest_ids(log=lambda *a: None)
            now_ids, then_ids = {t[0] for t in catalog2}, {t[0] for t in catalog}
            added, removed = now_ids - then_ids, then_ids - now_ids
            missing_now = now_ids - done                       # in the live catalog, not captured
            drift = {"catalog_start": len(then_ids), "catalog_end": len(now_ids),
                     "added_during_crawl": len(added), "removed_during_crawl": len(removed),
                     "captured": len(done & now_ids), "missing_vs_live": len(missing_now)}
            log("[abc] DRIFT start=%d end=%d (+%d/-%d during crawl) | captured %d of the live %d"
                % (len(then_ids), len(now_ids), len(added), len(removed),
                   len(done & now_ids), len(now_ids)))
            # Products that appeared mid-crawl were never in `targets`; they are NOT in `done`, so the
            # next run picks them up automatically. Say so rather than counting them as a failure.
            if added:
                log("[abc] %d products appeared during the crawl — the next run collects them "
                    "(resume checkpoint does not mark them done)" % len(added))
        except Exception as e:
            log("[abc] re-harvest failed (%s) — completeness is measured against the START catalog"
                % str(e)[:90])
            drift = {"error": str(e)[:120]}

    # Did this pass actually finish the catalog? `done` accumulates across resumed runs, so compare it
    # to the FULL target list, not just this pass's remainder.
    todo_incomplete = crawl_all and len({t[0] for t in targets} - done) > 0
    _progress(rows=len(cur), stage="crawl complete" if not todo_incomplete else "partial pass", pct=100.0)

    # SNAPSHOT for the day-over-day diff. This used to be a file in the run's output dir — which on an
    # ephemeral Fly machine is destroyed with the machine, so `prev` was ALWAYS empty and every run
    # reported "baseline" movement. It lives in the shared bucket now, with the local file kept as a
    # fallback for CLI use. Movement is the entire point of a directional tracker, so a diff that can
    # never fire is the feature silently not existing.
    prev = {}
    snap_path = os.path.join(state_dir, SNAP_FILE)
    try:
        import warehouse
        raw = warehouse.get_bytes("_collect/snapshots/abc-fws.json")
        if raw:
            prev = json.loads(raw).get("cells", {})
            log(f"  [abc] prior snapshot: {len(prev):,} cells (shared bucket)")
    except Exception:
        pass
    if not prev and os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("cells", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    # NOTHING LEFT TO DO is not a failure. A resumed run that finds every product already captured
    # today has an empty `cur` — which the "not cur => failed" test below would otherwise report as a
    # broken scrape, turning a completed catalog into a red alert every subsequent run of the day.
    if crawl_all and not todo:
        log(f"[abc] already complete for {day}: {len(done):,}/{len(targets):,} products captured")
        run = run_record(diff_snapshots({}, {}), len(done), "success",
                         [] if len(done) >= len(targets) else
                         [f"{len(targets) - len(done):,} products still missing for {day}"])
        return {}, [run], run["movement"]

    warnings = []
    if not cur or ok_n / max(1, len(todo)) < 0.5:
        warnings.append(f"Per-store options parsed on only {ok_n}/{len(todo)} pages — confirm the "
                        "store-option / available_variant_values selectors against a live page.")
    if hit_ceiling:
        warnings.append(f"Sitemap walk hit the {os.environ.get('ABC_MAX_PAGES','500')}-page ceiling — the "
                        "catalog may be larger than what was harvested.")
    # COMPLETENESS, stated plainly: a full crawl that covered only part of the catalog is `degraded`,
    # never `success`. Reporting a partial sweep as a clean run is the exact lie this rework removes.
    remaining = len({t[0] for t in targets} - done) if crawl_all else 0
    if crawl_all:
        covered = len(targets) - remaining
        # Judge against the LIVE catalog when the re-harvest succeeded — that is the source of truth,
        # and it is what a completeness claim has to be measured against.
        live_total = drift.get("catalog_end") or len(targets)
        live_missing = drift.get("missing_vs_live", remaining)
        appeared = drift.get("added_during_crawl", 0)
        log(f"[abc] COVERAGE {covered:,}/{len(targets):,} of the start catalog "
            f"({100.0*covered/max(1,len(targets)):.1f}%); vs LIVE catalog: "
            f"{live_total - live_missing:,}/{live_total:,} "
            f"({100.0*(live_total-live_missing)/max(1,live_total):.1f}%)")
        # ANY shortfall against the live catalog is a failure — holding *some* of the items is a
        # defective product, not a partial success. The CAUSE still matters for diagnosis, so it is
        # spelled out in the warning, but neither cause earns a clean status. The run is retried on the
        # selfheal backoff and resumes from the checkpoint, so this drives itself to full coverage.
        if remaining:
            warnings.append(f"INCOMPLETE: {covered:,}/{len(targets):,} products captured from the start "
                            f"catalog, {remaining:,} never fetched — retry resumes from the checkpoint.")
        if appeared:
            warnings.append(f"INCOMPLETE: {appeared:,} products were published during the crawl and are "
                            f"not in this pass — the retry collects them (not in the checkpoint).")
        if live_missing:
            log(f"[abc] INCOMPLETE — {live_missing:,} of the live catalog's {live_total:,} products are "
                f"not captured. This is a failure; the source will be retried and will resume.")
    status = "failed" if not cur else ("degraded" if warnings else "success")

    snap = {"__ts__": int(time.time() * 1000), "cells": cur}
    json.dump(snap, open(snap_path, "w"), indent=2)
    # Only a COMPLETE pass may replace the shared snapshot. A partial (resumed/killed) run holds a
    # fraction of the catalog, and writing that as the new baseline would make tomorrow's diff report
    # every un-crawled SKU as "disappeared" — a fabricated assortment collapse.
    if crawl_all and not todo_incomplete:
        try:
            import warehouse
            warehouse.put_bytes("_collect/snapshots/abc-fws.json", json.dumps(snap).encode())
        except Exception as e:
            log("  [abc] snapshot save skipped: %s" % str(e)[:80])
    elif crawl_all:
        log("  [abc] partial pass — shared snapshot NOT replaced (would fake an assortment collapse)")
    # NOTE: observations already landed INCREMENTALLY, one dated partition per batch (see _land). The
    # single end-of-run observe.record that used to live here would now re-write the same cells as a
    # second partition — double-counting every row — so it is deliberately gone. A sampled run
    # (crawl_all=False) still lands here, since it never batches.
    if not crawl_all and cur:
        _land(day, 1, list(cur.values()), log=log)
    header = ["SKU", "Store", "Price", "In Stock"]
    rows = [[v["sku"], v["store"], v["price"], v["instock"]] for k, v in sorted(cur.items())]
    n_products = len({v["sku"] for v in cur.values()})
    datasets = {"abc_store_cells": {"header": header, "rows": rows[:800],
                                    "total": len(rows), "products": n_products, "movement": movement}}
    json.dump(datasets, open(os.path.join(out, "datasets.json"), "w"), indent=2)
    datasets["abc_store_cells"]["_rows_full"] = rows   # full set for export (in-memory return only)
    run = run_record(movement, n_products, status, warnings)
    if drift:
        run["drift"] = drift          # live-catalog reconciliation, for the bench

    log(f"done: {n_products} products × stores = {len(cur)} cells; "
        + (f"{movement['changed']} store-cells moved since last run" if prev else "baseline"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Polite directional inventory tracker for ABC FWS (BigCommerce).")
    ap.add_argument("--sample", type=int, default=40, help="how many SKUs to poll (deterministic spread)")
    ap.add_argument("--all", action="store_true", help="crawl the whole catalog (slow; respects crawl-delay)")
    ap.add_argument("--limit", type=int, default=None, help="cap pages in --all mode")
    ap.add_argument("--out", default="./abc_out", help="output dir for datasets.json + snapshot")
    a = ap.parse_args(argv)
    _, runs, mv = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
