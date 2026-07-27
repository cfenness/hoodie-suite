"""doordash_full.py — FULL per-store bev-alc catalog via the DoorDash CATEGORY TREE (light Unlocker).

We proved the browser scroll dead-ends (virtualized, capped) — but the store's Alcohol category (id 1024)
fans into SERVER-RENDERED subcategory grids: beer, wine, vodka, whiskey, tequila, rum, gin, brandy,
liqueur, seltzers, RTD cocktails, hemp/CBD (~50 items each), and those split again into sub-sub-categories
(vodka -> vodka + flavored vodka; wine -> red/white/sparkling/...). Every leaf page is server-rendered, so
the LIGHT Unlocker fetches each one — no headless browser. This connector WALKS the alcohol category tree
and unions the leaves into the complete catalog, parsing name/price/image/pack + capturing the outlet.
Lands <chain>_products_full + dated retail_observations.

    python doordash_full.py --chain totalwine --stores 1862062
    python doordash_full.py --chain circlek --stores 1696295
"""
import argparse, os, re, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import doordash as dd          # reuse _api_key, _unlock, _rsc, _parse_items, _parse_pack, _price_val, _parse_outlet, CHAINS
import cocktail_taxonomy as ctx  # classify_beverage -> category / is_alcoholic / beer_style

# The non-alcoholic / zero-proof DEPARTMENT (fast-growing adjacency: N/A beer, dealcoholized wine, zero-proof
# spirits, mocktail RTDs). We also walk category non-alcoholic-1516, but KEEP only the beverage-of-interest
# (signals below) so we don't drag in soda/water/juice.
_NA_INTEREST = re.compile(
    r"non[- ]?alcoholic|alcohol[- ]?removed|alcohol[- ]?free|zero[- ]?proof|de-?alcohol|dealcohol|"
    r"\bn/?a\b|\b0\.0\b|mocktail|athletic brewing|seedlip|ritual zero|\bghia\b|aplos|\bfre\b|clearscape|"
    r"coors edge|heineken 0|budweiser zero|michelob ultra zero|kul mocks|divin|lyre'?s|monday zero|"
    r"three spirit|st\.? ?agrestis|surely|proxies|gnista|for bitter|hop wtr|partake", re.I)

# category name stems that are NOT alcohol (store nav links appear alongside the alcohol tree — skip them)
_NON_ALCOHOL = ("grocery", "household", "meat", "snack", "candy", "frozen", "medicine", "personal care",
                "pet", "deli", "bakery", "prepared", "pantry", "non-alcoholic", "drinks & mixers", "drinks-",
                "bar accessories", "party supplies", "produce", "dairy", "baby", "cleaning", "beauty", "tobacco",
                "nicotine")


def _cat_paths(html, store):
    """Every /category/... and /category/.../sub-category/... path for this store in the page."""
    pat = r'/convenience/store/%s/category/[^"\'\\ ]+?-\d+(?:/sub-category/[^"\'\\ ]+?-\d+)?' % store
    return set(re.findall(pat, html))


def _is_alcohol(path):
    slug = urllib.parse.unquote(path.split("/category/")[1]).lower()
    return not any(x in slug for x in _NON_ALCOHOL)


def full_catalog(store, key, log=print, max_pages=120):
    # ONE persistent connection for this store's ENTIRE walk (~166 fetches across the alcohol tree,
    # term-search union, and non-alc tree) instead of a fresh TLS handshake + proxy connection on every
    # single page — measured live: that reconnect overhead, not the politeness sleep, was the dominant
    # per-store cost (~4-5 min/store). Falls back to per-call connections (dd._session returns None) if
    # curl_cffi/the ISP pool aren't available — same behavior as before, just slower.
    session = dd._session(store)
    root = "/convenience/store/%s/category/alcohol-1024" % store
    items, outlet, seen_cat, pages = {}, None, set(), 0
    queue = [root]
    while queue and pages < max_pages:
        path = queue.pop(0)
        if path in seen_cat:
            continue
        seen_cat.add(path); pages += 1
        try:
            html = dd._unlock("https://www.doordash.com" + path, key, session=session)
        except Exception as e:
            log("  cat %s failed: %s" % (path.split("/category/")[1][:24], str(e)[:40])); continue
        blob = dd._rsc(html)
        for it in dd._parse_items(blob):
            items.setdefault(it["name"], dict(it, department="alcohol"))
        if outlet is None and ('"latitude"' in html):
            outlet = dd._parse_outlet(html, store, "")
        for c in _cat_paths(html, store):                 # enqueue deeper alcohol categories
            if c not in seen_cat and _is_alcohol(c):
                queue.append(c)
        if pages % 8 == 0:
            log("  [%s] walked %d categories · %d items" % (store, pages, len(items)))
        time.sleep(0.4)
    log("  [%s] tree walk: %d categories, %d items" % (store, pages, len(items)))
    # UNION with the term-search — catches items not in the browsable tree (esp. small c-store catalogs
    # where the category tree is shallow but search still finds SKUs). Free on top of the walk.
    # (search_store was referenced here but never implemented until now — every call silently
    # AttributeError'd, caught by the bare except below, paying the sleep for zero completeness benefit.)
    for term in dd.ALCOHOL_TERMS:
        try:
            for it in dd.search_store(store, term, key, session=session):
                items.setdefault(it["name"], dict(it, department="alcohol"))
        except Exception:
            pass
        time.sleep(0.4)
    log("  [%s] + term-search union -> %d distinct items" % (store, len(items)))
    for name, it in _walk_nonalc(store, key, log, session=session).items():   # non-alc / zero-proof dept
        items.setdefault(name, it)
    return list(items.values()), outlet


def _walk_nonalc(store, key, log=print, max_pages=30, session=None):
    """Walk the non-alcoholic-1516 category tree; keep only the zero-proof / N-A beverages of interest."""
    session = session or dd._session(store)
    root = "/convenience/store/%s/category/non-alcoholic-1516" % store
    out, seen, pages = {}, set(), 0
    queue = [root]
    while queue and pages < max_pages:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path); pages += 1
        try:
            html = dd._unlock("https://www.doordash.com" + path, key, session=session)
        except Exception:
            continue
        for it in dd._parse_items(dd._rsc(html)):
            if it["name"] not in out and _NA_INTEREST.search(it["name"]):
                out[it["name"]] = dict(it, department="non-alcoholic")
        for c in _cat_paths(html, store):                      # stay in the non-alc subtree
            slug = urllib.parse.unquote(c.split("/category/")[1]).lower()
            if c not in seen and ("non-alcoholic" in slug or "1516" in c or
                                  re.search(r"zero|mock|alcohol.free|de-?alc|\bna\b", slug)):
                queue.append(c)
        time.sleep(0.4)
    log("  [%s] non-alc dept -> %d zero-proof / N-A items" % (store, len(out)))
    return out


def run(chain, stores=None, log=print, on_store=None):
    """on_store(i, n_stores_total_in_this_call), called after EACH store finishes — the hook a
    caller driving many chains/stores in one job (doordash_chains.py) uses to feed real per-store
    progress into runlog.track(), instead of only knowing something happened once the whole chain
    is done.

    CONCURRENT across stores — same pattern as doordash_naop.py's ThreadPoolExecutor (DDFULL_WORKERS,
    default 10, matching NAOP_WORKERS). A serial per-store walk (the category tree is ~15-30+ page
    fetches per store, each politely paced) measured ~4-5 MINUTES for a single store live — at that
    rate a "full" national batch is a multi-day crawl regardless of batch size, which defeats the
    point of a bounded, converging pull. The flat-rate ISP pool this already runs on has no per-request
    cost, so concurrency is free throughput, not spend."""
    cfg = dd.CHAINS.get(chain, {"name": chain, "stores": []})
    stores = stores or cfg["stores"]
    if not stores:
        log("[%s] no store ids" % chain); return None, 0
    key = dd._api_key()
    run_id = "%sfull-%s" % (chain, time.strftime("%Y%m%d-%H%M%S"))
    workers = int(os.environ.get("DDFULL_WORKERS", "10"))

    import threading
    from concurrent.futures import ThreadPoolExecutor
    all_rows, outlets, done = [], [], [0]
    lock = threading.Lock()

    def _work(store):
        items, outlet = full_catalog(store, key, log=log)
        rows = []
        for it in items:
            dept = it.get("department", "alcohol")
            b = ctx.classify_beverage(it["name"])
            rows.append(dict(it, store=str(store), store_id=str(store), product_id=it["name"][:90],
                             price_value=dd._price_val(it.get("price", "")), source=chain, department=dept,
                             is_alcoholic=(dept == "alcohol"), bev_category=b["category"],
                             beer_style=b.get("beer_style", ""), is_hemp=observe.is_hemp(it["name"]),
                             run_id=run_id, **dd._parse_pack(it["name"])))
        na = sum(1 for r in rows if r["department"] == "non-alcoholic")
        log("  [%s] store %s — %d items (%d alcohol, %d non-alc/zero-proof)" % (chain, store, len(rows), len(rows) - na, na))
        with lock:
            all_rows.extend(rows)
            if outlet:
                outlet["source"] = chain; outlets.append(outlet)
            done[0] += 1
            if on_store:
                try:
                    on_store(done[0], len(stores))
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_work, stores))
    if all_rows:
        # ACCUMULATE, not overwrite: a caller driving this across many runs (doordash_chains.py's
        # resumable batches) passes a DIFFERENT store subset each time — write_parquet would replace
        # the whole table with just that batch and silently erase every previously-landed store's
        # rows. Key = (store, product_id), the same identity a re-pull of that store's item replaces.
        warehouse.write_accumulate(chain + "_products_full", all_rows,
                                   key=lambda r: (r["store"], r["product_id"]))
        observe.record(chain, [dict(store=r["store"], store_id=r["store_id"], product_id=r["product_id"],
                                    brand="", name=r["name"], price=r.get("price_value"),
                                    in_stock=True, qty=None, is_hemp=r.get("is_hemp")) for r in all_rows])
    if outlets:
        warehouse.write_accumulate(chain + "_outlets", outlets, key=lambda r: r.get("store") or r.get("store_id"))
    log("[%s] FULL DONE %d items across %d stores -> %s_products_full" % (chain, len(all_rows), len(stores), chain))
    return run_id, len(all_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", required=True)
    ap.add_argument("--stores", default="")
    a = ap.parse_args()
    run(a.chain, stores=[s.strip() for s in a.stores.split(",") if s.strip()] or None)
