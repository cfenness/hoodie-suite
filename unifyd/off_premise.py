"""off_premise.py — first-party catalog/inventory from a retailer's OWN e-commerce site.

The geo sweep showed small independents have no online store (-> aggregators only), but the LARGE-format
independents + chains run real online catalogs on a handful of platforms. Discovery = Google Maps (the store's
site); extraction = a PLATFORM RECIPE (prove the config once, persist it — see the recipe-store idea). This
starts with BigCommerce (Haskell's), which reuses the proven ABC FWS pattern: /xmlsitemap.php?type=products
enumerates every product, and each product page carries og:title + og:price:amount in the SERVER HTML (no JS).
City Hive / Bottlecapps / Shopify / WooCommerce recipes are the next platforms. Lands <slug>_catalog + dated
retail_observations, so off-premise inventory tracks over time like the DoorDash retail pulls.

    python off_premise.py --store haskells --sample 25      # bounded proof
    python off_premise.py --store haskells                  # full catalog
"""
import argparse, html as _html, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import doordash as dd                # reuse _parse_pack (container/size)
import cocktail_taxonomy as ctx      # bev_category / beer_style

# store registry — {slug: {name, base, platform}}. Broudy's (bottlecapps/magento) + Goody Goody (bespoke)
# await their own recipes; the flagged large-format Orlando/Tampa independents get added as discovered.
STORES = {
    "haskells": {"name": "Haskell's", "base": "https://www.haskells.com", "platform": "bigcommerce"},
}

_BC_TITLE = re.compile(r'"og:title"[^>]*content="([^"]+)"|property="og:title"[^>]*content="([^"]+)"', re.I)
_BC_PRICE = re.compile(r'(?:og:price:amount|product:price:amount)"[^>]*content="([\d.]+)"', re.I)
_BC_SKU = re.compile(r'itemprop="sku"[^>]*content="([^"]+)"|"sku":\s*"([^"]+)"|data-product-id="(\d+)"', re.I)
_BC_INSTOCK = re.compile(r'"instock"\s*:\s*(true|false)|product:availability"[^>]*content="(instock|in stock)"', re.I)


def _bd_key():
    k = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    if k:
        return k
    return json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]


def _unlock(url, key):
    body = {"zone": "cli_unlocker", "url": url, "format": "raw"}
    r = urllib.request.Request("https://api.brightdata.com/request", data=json.dumps(body).encode(),
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=90).read().decode("utf-8", "replace")


def _first(m):
    return next((g for g in m.groups() if g), None) if m else None


# ── BigCommerce recipe ──────────────────────────────────────────────────────────────────────────────────
def bigcommerce_ids(base, key, max_pages=40, log=print):
    """Walk /xmlsitemap.php?type=products -> every product URL (the full catalog spine)."""
    urls = []
    for pg in range(1, max_pages + 1):
        try:
            sm = _unlock("%s/xmlsitemap.php?type=products&page=%d" % (base, pg), key)
        except Exception as e:
            log("  sitemap page %d: %s" % (pg, str(e)[:40])); break
        u = [_html.unescape(x) for x in re.findall(r"<loc>([^<]+)</loc>", sm)]
        if not u:
            break
        urls += u
        log("  sitemap page %d: %d products (total %d)" % (pg, len(u), len(urls)))
        time.sleep(1)
    return urls


def bigcommerce_product(url, key):
    """og:title + og:price:amount + sku from the SERVER HTML (no JS). -> dict or None (self-reports drift)."""
    p = _unlock(url, key)
    name = _first(_BC_TITLE.search(p))
    pm = _BC_PRICE.search(p)
    price = float(pm.group(1)) if pm else None
    if not name:
        return None
    im = _BC_INSTOCK.search(p)
    instock = (im.group(1) == "true" or bool(im.group(2))) if im else None
    return {"name": _html.unescape(name).strip(), "price": price, "sku": _first(_BC_SKU.search(p)) or "",
            "in_stock": instock, "url": url}


_RECIPES = {"bigcommerce": (bigcommerce_ids, bigcommerce_product)}


# ── Shopify recipe — the golden path: /products.json returns the whole catalog as JSON (name / vendor /
# price / sku), paginated. sku is very often the UPC. One clean call per page, no per-product fetch. ──
def shopify_catalog(base, key, max_pages=25, log=print):
    rows = []
    for pg in range(1, max_pages + 1):
        j = None
        for _try in range(2):                                   # transient BD failures happen under load
            try:
                j = json.loads(_unlock("%s/products.json?limit=250&page=%d" % (base.rstrip("/"), pg), key))
                break
            except Exception:
                time.sleep(2)
        if j is None:
            break
        ps = j.get("products") or []
        if not ps:
            break
        for p in ps:
            v = (p.get("variants") or [{}])[0]
            try:
                price = float(v.get("price")) if v.get("price") not in (None, "") else None
            except Exception:
                price = None
            sku = (v.get("sku") or "").strip()
            rows.append({"name": (p.get("title") or "").strip(), "brand": (p.get("vendor") or "").strip(),
                         "price": price, "sku": sku,
                         "upc": (sku if sku.isdigit() and 8 <= len(sku) <= 14 else ""),
                         "product_type": (p.get("product_type") or "")})
        if len(ps) < 250:
            break
    return rows


# ── Bottlecapps recipe — the crack: DataDome-protected JS app, but EVERYTHING is keyed by store_id. Category
# tree = /s-<sid>/c-N/buy-<slug> (+ t-/v- subcats); product = /product/s-<sid>/p-<pid>/buy-<name-size-slug>.
# BD Browser (past DataDome) walks the top categories, scrolls to load the section-loader batches, and reads
# product links (name+size in the slug). Reusable for ANY Bottlecapps store — just its store_id. ──
def _browser_auth():
    key = _bd_key()
    r = urllib.request.Request("https://api.brightdata.com/zone/passwords?zone=cli_browser",
                               headers={"Authorization": "Bearer " + key})
    return "brd-customer-hl_32bcfbaa-zone-cli_browser:%s" % json.loads(
        urllib.request.urlopen(r, timeout=30).read())["passwords"][0]


_BC_SIZE = re.compile(r"(\d+(?:\.\d+)?)-(ml|l|liter|oz|pk|pack|ltr)\b", re.I)


def _bc_parse(url):
    m = re.search(r"/p-(\d+)/buy-(.+?)(?:\?|$)", url)
    if not m:
        return None, None, None
    pid, slug = m.group(1), m.group(2)
    sz = _BC_SIZE.search(slug)
    return pid, slug.replace("-", " ").strip(), (sz.group(0).replace("-", " ") if sz else "")


def bottlecapps_store_id(base, key):
    """Every Bottlecapps store exposes its id in its own page (store_id = "N" or an /s-N/ link)."""
    try:
        h = _unlock(base.rstrip("/") + "/", key)
    except Exception:
        return None
    m = re.search(r'store_id\s*=\s*"?(\d{3,7})', h) or re.search(r"/s-(\d{3,7})/", h)
    return m.group(1) if m else None


def bottlecapps_catalog(base, store_id, key, max_cats=12, scrolls=8, log=print):
    from playwright.sync_api import sync_playwright
    base = base.rstrip("/")
    prods = {}
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("wss://%s@brd.superproxy.io:9222" % _browser_auth(), timeout=90000)
        try:
            ctx = b.contexts[0] if b.contexts else b.new_context()
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()

            def _settle():
                try:
                    pg.wait_for_load_state("load", timeout=20000)
                except Exception:
                    pass
                time.sleep(8)                                   # the JS nav / product grid renders after load

            def _hrefs(sel):                                    # retry — the page can redirect mid-eval
                for _t in range(3):
                    try:
                        return pg.eval_on_selector_all(sel, "els=>els.map(a=>a.href).filter(Boolean)")
                    except Exception:
                        time.sleep(3)
                return []
            pg.goto(base + "/", wait_until="domcontentloaded", timeout=120000); _settle()
            cats = sorted(set(l for l in _hrefs("a") if re.search(r"/s-%s/c-\d+/buy-[a-z0-9-]+$" % store_id, l)))[:max_cats]
            log("  [bottlecapps] store %s: %d top categories" % (store_id, len(cats)))
            for c in cats:
                try:
                    pg.goto(c, wait_until="domcontentloaded", timeout=90000)
                except Exception:
                    continue
                _settle()
                for _ in range(scrolls):
                    pg.mouse.wheel(0, 7000); time.sleep(1.3)
                hrefs = _hrefs("a[href*='/product/s-%s/']" % store_id)
                for u in set(hrefs):
                    pid, name, size = _bc_parse(u)
                    if pid and pid not in prods:
                        prods[pid] = {"name": name, "size": size, "price": None, "sku": "", "upc": "",
                                      "product_type": "", "url": u.split("?")[0]}
        finally:
            b.close()
    return list(prods.values())


# ── WooCommerce recipe — the public Store API: /wp-json/wc/store/v1/products (no auth), name + price (in
# CENTS) + sku (usually the UPC), paginated. One of the largest platforms overall (liquor AND hemp). ──
def woo_catalog(base, key, max_pages=50, log=print):
    rows = []
    for pg in range(1, max_pages + 1):
        j = None
        for _t in range(2):
            try:
                j = json.loads(_unlock("%s/wp-json/wc/store/v1/products?per_page=100&page=%d"
                                       % (base.rstrip("/"), pg), key))
                break
            except Exception:
                time.sleep(2)
        if not isinstance(j, list) or not j:
            break
        for p in j:
            pr = (p.get("prices") or {}).get("price")
            try:
                price = round(float(pr) / 100.0, 2) if pr not in (None, "") else None
            except Exception:
                price = None
            sku = (p.get("sku") or "").strip()
            rows.append({"name": _html.unescape(p.get("name") or "").strip(), "brand": "", "price": price,
                         "sku": sku, "upc": (sku if sku.isdigit() and 8 <= len(sku) <= 14 else ""),
                         "product_type": ""})
        if len(j) < 100:
            break
    return rows


_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_WIX_STORES_APP = "1380b703-ce81-ff05-f115-39571d94dfcd"
_WIX_GQL = ("query getProducts($limit:Int,$offset:Int){ catalog{ products(limit:$limit, offset:$offset, "
            "onlyVisible:true){ totalCount list{ id name brand price formattedPrice comparePrice sku "
            "isInStock productType urlPart } } } }")


def _wix_instance(base, log=print):
    """Wix mints per-app instance tokens at the UNAUTHENTICATED /_api/v1/access-tokens bootstrap (this is how
    the public storefront authorizes itself). Return (instance_token, working_site) — trying apex + www since
    the census website may be either. urllib follows the apex->www redirect, so we read the final host."""
    hosts = [base.rstrip("/")]
    m = re.match(r"(https?://)(www\.)?(.+)", base.rstrip("/"))
    if m:
        alt = m.group(1) + ("" if m.group(2) else "www.") + m.group(3)
        if alt not in hosts:
            hosts.append(alt)
    for site in hosts:
        try:
            req = urllib.request.Request(site + "/_api/v1/access-tokens", headers={"User-Agent": _UA})
            resp = urllib.request.urlopen(req, timeout=45)
            real = "%s://%s" % (urllib.parse.urlparse(resp.geturl()).scheme,
                                urllib.parse.urlparse(resp.geturl()).netloc)
            j = json.loads(resp.read().decode("utf-8", "replace"))
            app = (j.get("apps") or {}).get(_WIX_STORES_APP)
            if app and app.get("instance"):
                return app["instance"], (real or site)
        except Exception as e:
            log("  [wix] access-tokens %s: %s" % (site, str(e)[:50]))
    return None, base


def wix_catalog(base, key=None, page=100, max_products=None, log=print):
    """Wix Stores — powers a huge slice of independent liquor retail. The storefront runs on a GraphQL API
    (`catalog.products`) authorized by an app INSTANCE token that /_api/v1/access-tokens hands out
    unauthenticated. Bootstrap the token, then page catalog.products (name/brand/price/sku/stock). Full
    fields, no browser. NB: a site can have the Stores app installed but an EMPTY catalog (totalCount 0) if it
    sells through a third party instead — that's not a failure, the store just has no Wix inventory."""
    inst, site = _wix_instance(base, log=log)
    if not inst:
        log("  [wix] %s -> no Stores instance (app not installed?)" % base); return []
    ep = site + "/_api/wix-ecommerce-storefront-web/api"
    hdr = {"User-Agent": _UA, "Authorization": inst, "Content-Type": "application/json"}

    def gql(offset):
        body = json.dumps({"query": _WIX_GQL, "variables": {"limit": page, "offset": offset}}).encode()
        req = urllib.request.Request(ep, data=body, headers=hdr, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace"))

    rows, offset, total = [], 0, None
    while True:
        try:
            pq = (((gql(offset).get("data") or {}).get("catalog") or {}).get("products")) or {}
        except Exception as e:
            log("  [wix] products offset=%d: %s" % (offset, str(e)[:50])); break
        if total is None:
            total = pq.get("totalCount") or 0
            log("  [wix] %s: totalCount=%d" % (site, total))
            if not total:
                return []
        lst = pq.get("list") or []
        if not lst:
            break
        for p in lst:
            sku = (p.get("sku") or "").strip()
            rows.append({"name": _html.unescape(p.get("name") or "").strip(), "brand": (p.get("brand") or ""),
                         "price": p.get("price"), "sku": sku,
                         "upc": (sku if sku.isdigit() and 8 <= len(sku) <= 14 else ""),
                         "size_ml": _ch_ml(p.get("name")), "category": p.get("productType") or "",
                         "in_stock": p.get("isInStock")})
        offset += len(lst)
        if (max_products and offset >= max_products) or offset >= total:
            break
    log("  [wix] %s -> %d products" % (site, len(rows)))
    return rows[:max_products] if max_products else rows


# ── National signature-discovery + sweep — expand the recipes we have to their whole national footprint. For a
# LIQUOR-SPECIFIC platform (Bottlecapps), SERP its fingerprint -> store domains -> validate -> run the recipe.
# (Generic platforms — Shopify/Woo — are better discovered per-market via the Maps census.) ──
_NOISE = re.compile(r"quora|medium|crunchbase|accessnewswire|retail-today|reddit\.|youtube|facebook|linkedin|"
                    r"apple\.com|play\.google|glassdoor|indeed|bloomberg|yelp|tripadvisor|/blog|wikipedia|"
                    r"twitter|instagram|pinterest|g2\.com|capterra|owler|zoominfo", re.I)


def _ch_sitemap_products(base, key, log=print):
    """City Hive stores publish their FULL catalog in /sitemap.xml (product URLs
    /shop/product/<slug>/<product_id>?option-id=<opt>). BD Unlocker occasionally returns a
    Cloudflare interstitial instead of the XML, so retry until we get <loc> product URLs."""
    for _ in range(3):
        try:
            xml = _unlock(base.rstrip("/") + "/sitemap.xml", key)
        except Exception as e:
            log("  [cityhive] sitemap %s" % str(e)[:60]); continue
        urls = [l for l in re.findall(r"<loc>([^<]+)</loc>", xml) if "/shop/product/" in l]
        if urls:
            return urls
    return []


def _ch_parse_product(html):
    """Every City Hive product page is server-rendered for SEO — JSON-LD Product + OpenGraph meta
    carry the whole record (no session/browser). Pull name/price/size/store/image from them."""
    def meta(*keys):
        for k in keys:
            m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(k), html)
            if m:
                return _html.unescape(m.group(1))
        return None
    d = {}
    for b in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(b)
            if j.get("@type") == "Product":
                d["name"] = j.get("name"); break
        except Exception:
            pass
    title = meta("og:title"); desc = meta("og:description", "description")
    d["name"] = _html.unescape(d.get("name") or (title.split(" - ")[0].strip() if title else "") or "")
    p = meta("product:price:amount")
    try:
        d["price"] = float(p) if p else None
    except Exception:
        d["price"] = None
    d["pid"] = meta("ch:product:id"); d["image"] = meta("og:image") or ""
    d["size_ml"] = _ch_ml(title) or _ch_ml(desc)
    m = re.search(r"from .+? - (.+?) in ([^.]+)", desc or "")
    if m:
        d["store"] = m.group(1).strip(); d["store_loc"] = m.group(2).strip()
    return d


def _ch_ml(s):
    m = re.search(r"([\d.]+)\s*(ml|l|lt|ltr|liter|litre|oz)\b", str(s or ""), re.I)
    if not m:
        return None
    try:
        v = float(m.group(1)); u = m.group(2).lower()
        return round(v * (1000 if u.startswith("l") else (29.57 if u == "oz" else 1)))
    except Exception:
        return None


def cityhive_catalog(base, key=None, max_products=None, workers=8, log=print):
    """City Hive — the biggest independent-liquor platform (~2000 retailers). The widget's product API
    is session-gated (browse_categories/render.json only ever serves ~49 curated homepage items), but the
    store publishes its FULL catalog on the plain SEO surface: /sitemap.xml lists every product URL and each
    product page is server-rendered with a JSON-LD Product + OpenGraph meta (name/price/size/store/image).
    So we enumerate the sitemap and parse each page over BD Unlocker — no browser, no session, whole catalog.
    `max_products` bounds the per-store fetch (None = full); parsing is concurrent."""
    from concurrent.futures import ThreadPoolExecutor
    key = key or _bd_key()
    urls = _ch_sitemap_products(base, key, log=log)
    if not urls:
        log("  [cityhive] %s -> no sitemap product URLs (challenge or non-City-Hive store)" % base)
        return []
    total = len(urls)
    if max_products:
        urls = urls[:max_products]
    log("  [cityhive] %s: %d products in sitemap%s" %
        (base, total, "" if not max_products else " (fetching %d)" % len(urls)))

    def fetch(u):
        for _ in range(2):
            try:
                d = _ch_parse_product(_unlock(u, key))
                if d.get("name"):
                    return d, u
            except Exception:
                pass
        return None, u
    rows, done = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d, u in ex.map(fetch, urls):
            done += 1
            if done % 250 == 0:
                log("  [cityhive]   %d/%d parsed, %d products" % (done, len(urls), len(rows)))
            if not d:
                continue
            oid = _first(re.search(r"option-id=([0-9a-f]+)", u))
            rows.append(dict(name=d["name"], brand="", price=d.get("price"),
                             sku=d.get("pid") or "", upc="", size_ml=d.get("size_ml"),
                             category="", store=d.get("store") or "", image=d.get("image") or "",
                             option_id=oid or ""))
    log("  [cityhive] %s -> %d products" % (base, len(rows)))
    return rows


def discover_stores(query, pages=4, log=print):
    """SERP a platform signature -> distinct store domains (noise filtered). Market-agnostic national discovery."""
    key = _bd_key()
    found = set()
    for start in range(0, pages * 10, 10):
        try:
            j = json.loads(_unlock("https://www.google.com/search?q=%s&brd_json=1&gl=us&hl=en&start=%d"
                                   % (urllib.parse.quote(query), start), key))
        except Exception:
            continue
        for o in (j.get("organic") or []):
            l = o.get("link") or ""
            if l and not _NOISE.search(l):
                m = re.match(r"https?://[^/]+", l)
                if m:
                    found.add(m.group(0))
        time.sleep(0.3)
    return sorted(found)


def national_sweep(platform, log=print):
    """Discover every store on a platform nationally (by signature) + run its recipe -> national_<platform>_products."""
    key = _bd_key()
    sig = {"bottlecapps": '"powered by bottlecapps" OR "bottlecapps" liquor wine spirits order online',
           "cityhive": '"powered by city hive" liquor wine spirits'}
    domains = discover_stores(sig.get(platform, platform), log=log)
    log("[national] %s: %d candidate store domains" % (platform, len(domains)))
    rows, hit = [], 0
    for d in domains:
        try:
            if platform == "bottlecapps":
                sid = bottlecapps_store_id(d, key)
                items = bottlecapps_catalog(d, sid, key, max_cats=10, log=log) if sid else []
            elif platform == "shopify":
                items = shopify_catalog(d, key, log=log)
            elif platform == "woocommerce":
                items = woo_catalog(d, key, log=log)
            else:
                items = []
        except Exception as e:
            log("  [national] %-30s failed: %s" % (d[:30], str(e)[:40])); continue
        if items:
            hit += 1
        for it in items:
            b = ctx.classify_beverage(it["name"])
            rows.append(dict(store=d, platform=platform, name=it["name"], brand=it.get("brand", ""),
                             price_value=it.get("price"), sku=it.get("sku", ""), upc=it.get("upc", ""),
                             bev_category=b["category"], is_hemp=observe.is_hemp(it["name"]),
                             **dd._parse_pack(it["name"])))
        log("  [national] %-32s -> %d products" % (d.replace("https://", "")[:32], len(items)))
        if rows and hit % 3 == 0:
            warehouse.write_accumulate("national_%s_products" % platform, rows,   # checkpoint (accumulates)
                                       key=lambda r: (r.get("store"), r.get("name")))
    if rows:
        warehouse.write_accumulate("national_%s_products" % platform, rows,
                                   key=lambda r: (r.get("store"), r.get("name")))
    log("[national] %s: %d products from %d/%d stores -> national_%s_products"
        % (platform, len(rows), hit, len(domains), platform))
    return rows


def run_census(market="orlando", platforms=("Shopify", "WooCommerce", "Bottlecapps", "City Hive", "Wix"), census=None, out=None, log=print):
    """Pull every census e-commerce store on the given platform(s) -> <out> (a corroboration source +
    independent-store assortment). Dispatches per platform recipe. Default = ALL recipe-able platforms
    (was Shopify-only, which left Woo/Bottlecapps stores unscraped). BigCommerce stores are ABC → abc_catalog."""
    key = _bd_key()
    census = census or ("%s_offprem_census" % market)
    out = out or ("%s_offprem_products" % market)
    seen, stores = set(), []
    for s in warehouse.query(census,
                             "SELECT DISTINCT account, website, platform FROM t WHERE has_ecommerce = true"):
        base = re.match(r"https?://[^/]+", s["website"] or "")
        if not base or base.group(0) in seen:
            continue
        seen.add(base.group(0)); stores.append(dict(s, base=base.group(0)))
    run_id = "offprem-" + time.strftime("%Y%m%d-%H%M%S")
    rows = []
    for s in stores:
        plat = (s["platform"] or "").lower()
        if not any(p.lower() in plat for p in platforms):
            continue
        try:
            if "shopify" in plat:
                items = shopify_catalog(s["base"], key, log=log)
            elif "woocommerce" in plat:
                items = woo_catalog(s["base"], key, log=log)
            elif "bottlecapps" in plat:
                sid = bottlecapps_store_id(s["base"], key)
                items = bottlecapps_catalog(s["base"], sid, key, log=log) if sid else []
            elif "city hive" in plat or "cityhive" in plat:
                # bound per-store in a multi-store census sweep (full catalog can be thousands/store);
                # a single-store pull (run()/CLI) goes full (max_products=None).
                items = cityhive_catalog(s["base"], key, max_products=int(os.environ.get("CITYHIVE_MAX", "600")), log=log)
            elif "wix" in plat:
                items = wix_catalog(s["base"], key, log=log)
            else:
                items = []
        except Exception as e:
            log("  [off] %-26s FAILED %s" % (s["account"][:26], str(e)[:40])); continue
        for it in items:
            b = ctx.classify_beverage(it["name"])
            rows.append(dict(store=s["account"], base=s["base"], platform=s["platform"], name=it["name"],
                             brand=it["brand"], price_value=it["price"], sku=it["sku"], upc=it["upc"],
                             bev_category=b["category"], is_hemp=observe.is_hemp(it["name"]), run_id=run_id,
                             **dd._parse_pack(it["name"])))
        log("  [off] %-26s (%s) -> %d products" % (s["account"][:26], s["platform"], len(items)))
    if rows:
        # ACCUMULATE — `out` is per-market but filled by a PLATFORM-filtered subset; running for one platform
        # then another into the same table must not wipe the first platform's products.
        warehouse.write_accumulate(out, rows, key=lambda r: (r.get("account") or r.get("store"), r.get("name")))
    log("[off] %d products -> %s" % (len(rows), out))
    return rows


def run(store, base=None, platform=None, sample=None, delay=1.5, log=print):
    cfg = STORES.get(store, {})
    base = (base or cfg.get("base", "")).rstrip("/")
    platform = platform or cfg.get("platform", "bigcommerce")
    name = cfg.get("name", store)
    if not base:
        log("[off] no base URL for %s" % store); return None, 0
    if platform not in _RECIPES:
        log("[off] no recipe for platform '%s' (have: %s)" % (platform, ", ".join(_RECIPES))); return None, 0
    harvest, product = _RECIPES[platform]
    key = _bd_key()
    run_id = "%s-%s" % (store, time.strftime("%Y%m%d-%H%M%S"))

    urls = harvest(base, key, log=log)
    log("[off] %s: %d products in catalog" % (name, len(urls)))
    if sample:
        urls = urls[:sample]
    rows, miss = [], 0
    for i, u in enumerate(urls):
        try:
            it = product(u, key)
        except Exception:
            it = None
        if not it or it["price"] is None:
            miss += 1
        if not it:
            continue
        b = ctx.classify_beverage(it["name"])
        rows.append(dict(store=store, store_name=name, name=it["name"], price_value=it["price"],
                         sku=it["sku"], in_stock=it["in_stock"], url=it["url"], source=store,
                         bev_category=b["category"], beer_style=b.get("beer_style", ""),
                         is_hemp=observe.is_hemp(it["name"]), run_id=run_id, **dd._parse_pack(it["name"])))
        if (i + 1) % 25 == 0:
            log("  ...%d/%d products (%d priced)" % (i + 1, len(urls), len(rows) - 0))
        time.sleep(delay)
    degraded = rows and (miss > len(urls) * 0.4)                 # >40% missing price/name -> selector drift
    if rows:
        warehouse.write_parquet(store + "_catalog", rows)
        observe.record(store, [dict(store=store, store_id=store, product_id=r["name"][:90], brand="",
                                     name=r["name"], price=r.get("price_value"), in_stock=r.get("in_stock"),
                                     qty=None, is_hemp=r.get("is_hemp")) for r in rows])
    log("[off] %s DONE: %d products -> %s_catalog%s"
        % (name, len(rows), store, "  [DEGRADED — check selectors]" if degraded else ""))
    return run_id, len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="")
    ap.add_argument("--base", default="")
    ap.add_argument("--platform", default="")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--census", default="", help="market -> pull all its census e-commerce stores")
    ap.add_argument("--platforms", default="Shopify")
    ap.add_argument("--hemp", action="store_true", help="pull the hemp census instead of off-premise")
    ap.add_argument("--national", default="", help="platform -> discover its stores nationally + sweep")
    a = ap.parse_args()
    if a.national:
        national_sweep(a.national)
    elif a.census:
        pl = tuple(p.strip() for p in a.platforms.split(",") if p.strip())
        if a.hemp:
            run_census(a.census, platforms=pl, census="%s_hemp_census" % a.census, out="%s_hemp_products" % a.census)
        else:
            run_census(a.census, platforms=pl)
    elif a.store:
        run(a.store, base=a.base or None, platform=a.platform or None, sample=a.sample or None)
