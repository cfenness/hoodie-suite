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
import argparse, html as _html, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error

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


# ── direct-first fetch — try a free polite request, fall back to BD Unlocker only when actually blocked ──
# Most catalog surfaces (Shopify /products.json, BigCommerce sitemaps + og pages, City Hive sitemap + product
# pages) serve fine to a well-behaved direct client; routing them through BD is pure defensiveness/cost. So
# _fetch tries direct and escalates to _unlock ONLY on a real bot wall (403/429/503, Cloudflare, DataDome, or a
# connection error). FORCE_BD=1 skips direct (debug); OFFPREM_NO_BD=1 forbids the fallback (never pay BD).
_BLOCK_MARKERS = ("__cf_chl", "cf-browser-verification", "cf_chl_opt", "just a moment...",
                  "datadome", "px-captcha", "access denied", "request unsuccessful", "attention required!")
_FETCH_STATS = {"direct": 0, "bd": 0, "fail": 0}


def _looks_blocked(text):
    low = (text or "")[:4000].lower()
    return any(m in low for m in _BLOCK_MARKERS)


def _fetch(url, key=None, timeout=45, log=None):
    """Return the body via a free direct request when possible, else Bright Data Unlocker."""
    if os.environ.get("FORCE_BD") != "1":
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": _UA, "Accept": "text/html,application/json,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                t = r.read().decode("utf-8", "replace")
            if t and not _looks_blocked(t):
                _FETCH_STATS["direct"] += 1
                return t
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403, 407, 408, 409, 429, 503):   # a 404/500 won't be fixed by a proxy
                try:
                    return e.read().decode("utf-8", "replace")      # hand the body back (e.g. end-of-pagination)
                except Exception:
                    pass
        except Exception:
            pass
    if os.environ.get("OFFPREM_NO_BD") == "1":                      # forbid paid fallback — free-only mode
        _FETCH_STATS["fail"] += 1
        raise RuntimeError("blocked and OFFPREM_NO_BD=1 (no BD fallback) for %s" % url)
    _FETCH_STATS["bd"] += 1
    return _unlock(url, key or _bd_key())


def _first(m):
    return next((g for g in m.groups() if g), None) if m else None


# ── BigCommerce recipe ──────────────────────────────────────────────────────────────────────────────────
def bigcommerce_ids(base, key, max_pages=40, log=print):
    """Walk /xmlsitemap.php?type=products -> every product URL (the full catalog spine)."""
    urls = []
    for pg in range(1, max_pages + 1):
        try:
            sm = _fetch("%s/xmlsitemap.php?type=products&page=%d" % (base, pg), key)
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
    """Take EVERYTHING off the SERVER HTML (no JS): og tags + the schema.org Product JSON-LD (brand /
    description / sku / gtin / category / image). -> dict or None (self-reports drift)."""
    p = _fetch(url, key)
    name = _first(_BC_TITLE.search(p))
    if not name:
        return None
    ld = _jsonld_product(p)

    def meta(*keys):
        for k in keys:
            m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(k), p)
            if m:
                return _html.unescape(m.group(1))
        return ""
    pm = _BC_PRICE.search(p)
    offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else (ld.get("offers") or [{}])[0] if ld.get("offers") else {}
    price = (float(pm.group(1)) if pm else None) or _num(offers.get("price"))
    im = _BC_INSTOCK.search(p)
    instock = (im.group(1) == "true" or bool(im.group(2))) if im else None
    sku = _first(_BC_SKU.search(p)) or str(ld.get("sku") or "")
    gtin = str(ld.get("gtin13") or ld.get("gtin12") or ld.get("gtin") or "").strip()
    upc = next((x for x in (gtin, sku) if re.fullmatch(r"\d{8,14}", x or "")), "")
    return {"name": _html.unescape(name).strip(), "price": price, "brand": _ld_str(ld.get("brand")),
            "sku": sku, "upc": upc, "item_code": str(ld.get("productID") or ld.get("mpn") or ""),
            "in_stock": instock, "category": _ld_str(ld.get("category")),
            "description": _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ld.get("description") or meta("og:description") or ""))).strip()[:2000],
            "image": _ld_str(ld.get("image")) or meta("og:image"), "url": url,
            "raw_json": json.dumps(ld, separators=(",", ":"))[:6000] if ld else ""}


_RECIPES = {"bigcommerce": (bigcommerce_ids, bigcommerce_product)}


# TAKE EVERYTHING the retailer gives — not just name/price. The scraper's job is to lose NOTHING: capture every
# field the source exposes (flavor/varietal/region live in tags + description; item code, barcode/UPC, size/
# vintage in options, weight, stock, image) PLUS the full raw record for anything we don't map yet.
def _num(x):
    try:
        return float(x) if str(x).strip() not in ("", "None") else None
    except Exception:
        return None


def _shopify_row(p):
    v = (p.get("variants") or [{}])[0]
    sku = (v.get("sku") or "").strip()
    barcode = (v.get("barcode") or "").strip()
    upc = next((x for x in (barcode, sku) if x.isdigit() and 8 <= len(x) <= 14), "")
    tags = p.get("tags")
    tags = tags if isinstance(tags, list) else [t.strip() for t in str(tags or "").split(",") if t.strip()]
    desc = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p.get("body_html") or ""))).strip()
    opts = {o.get("name", "").lower(): (v.get("option%d" % (i + 1)) or "")
            for i, o in enumerate(p.get("options") or [])}
    return {"name": (p.get("title") or "").strip(), "brand": (p.get("vendor") or "").strip(),
            "price": _num(v.get("price")), "compare_at_price": _num(v.get("compare_at_price")),
            "sku": sku, "upc": upc, "item_code": str(p.get("id") or ""),
            "product_type": p.get("product_type") or "", "tags": ", ".join(tags),
            "description": desc[:2000], "handle": p.get("handle") or "", "variant": v.get("title") or "",
            "grams": v.get("grams"), "in_stock": v.get("available"),
            "size_opt": opts.get("size") or "", "vintage_opt": opts.get("vintage") or "",
            "image": ((p.get("images") or [{}])[0] or {}).get("src") or "",
            "raw_json": json.dumps(p, separators=(",", ":"))[:8000]}


# ── Shopify recipe — the golden path: /products.json returns the whole catalog as JSON, paginated. We now
# capture the FULL product (tags/description/options/barcode/weight/stock/image + raw), not just name/price. ──
def shopify_catalog(base, key, max_pages=25, log=print):
    rows = []
    for pg in range(1, max_pages + 1):
        j = None
        for _try in range(2):                                   # transient BD failures happen under load
            try:
                j = json.loads(_fetch("%s/products.json?limit=250&page=%d" % (base.rstrip("/"), pg), key))
                break
            except Exception:
                time.sleep(2)
        if j is None:
            break
        ps = j.get("products") or []
        if not ps:
            break
        for p in ps:
            rows.append(_shopify_row(p))
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
        h = _fetch(base.rstrip("/") + "/", key)
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
                j = json.loads(_fetch("%s/wp-json/wc/store/v1/products?per_page=100&page=%d"
                                       % (base.rstrip("/"), pg), key))
                break
            except Exception:
                time.sleep(2)
        if not isinstance(j, list) or not j:
            break
        for p in j:
            rows.append(_woo_row(p))
        if len(j) < 100:
            break
    return rows


# WooCommerce ATTRIBUTES are the prize on wine/spirit stores — "Region: Napa", "Country: France",
# "Varietal: Cabernet", "ABV: 14%" are structured there. Map the known ones to master fields; keep the rest
# (+ description, categories, tags, image, item code) + raw.
_WOO_ATTR = {"region": "region", "sub-region": "sub_region", "subregion": "sub_region", "appellation": "appellation",
             "country": "origin", "country of origin": "origin", "origin": "origin",
             "varietal": "varietal", "grape": "varietal", "grape variety": "varietal", "varietals": "varietal",
             "abv": "abv", "alcohol": "abv", "alcohol content": "abv", "vintage": "vintage", "year": "vintage",
             "bottled in": "bottled_in", "bottler": "bottled_in"}


def _woo_row(p):
    pr = (p.get("prices") or {}).get("price")
    price = None
    try:
        price = round(float(pr) / 100.0, 2) if pr not in (None, "") else None
    except Exception:
        pass
    sku = (p.get("sku") or "").strip()
    desc = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
             p.get("description") or p.get("short_description") or ""))).strip()
    cats = ", ".join(c.get("name", "") for c in (p.get("categories") or []) if c.get("name"))
    row = {"name": _html.unescape(p.get("name") or "").strip(), "brand": "", "price": price,
           "sku": sku, "upc": (sku if sku.isdigit() and 8 <= len(sku) <= 14 else ""),
           "item_code": str(p.get("id") or ""), "product_type": cats, "tags": cats,
           "description": desc[:2000], "image": ((p.get("images") or [{}])[0] or {}).get("src") or "",
           "raw_json": json.dumps(p, separators=(",", ":"))[:8000]}
    for a in (p.get("attributes") or []):
        nm = (a.get("name") or "").strip().lower()
        val = ", ".join(t.get("name", "") for t in (a.get("terms") or []) if t.get("name"))
        fld = _WOO_ATTR.get(nm)
        if fld and val and not row.get(fld):
            row[fld] = val
    return row


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
            xml = _fetch(base.rstrip("/") + "/sitemap.xml", key)
        except Exception as e:
            log("  [cityhive] sitemap %s" % str(e)[:60]); continue
        urls = [l for l in re.findall(r"<loc>([^<]+)</loc>", xml) if "/shop/product/" in l]
        if urls:
            return urls
    return []


def _jsonld_product(html):
    """The schema.org Product JSON-LD off a server-rendered page — the richest single structured blob
    (name/brand/description/sku/gtin/category/offers). Shared by BigCommerce + City Hive."""
    for b in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(b)
        except Exception:
            continue
        for d in (j if isinstance(j, list) else [j]):
            t = d.get("@type") if isinstance(d, dict) else None
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                return d
    return {}


def _ld_str(v):
    if isinstance(v, dict):
        return v.get("name") or ""
    if isinstance(v, list):
        return _ld_str(v[0]) if v else ""
    return v or ""


def _ch_parse_product(html):
    """Every City Hive product page is server-rendered for SEO — take EVERYTHING off the JSON-LD Product +
    OpenGraph meta (name/brand/price/size/category/description/upc/store/image), not just name+price."""
    def meta(*keys):
        for k in keys:
            m = re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % re.escape(k), html)
            if m:
                return _html.unescape(m.group(1))
        return None
    ld = _jsonld_product(html)
    d = {}
    title = meta("og:title"); desc = meta("og:description", "description")
    d["name"] = _html.unescape(ld.get("name") or (title.split(" - ")[0].strip() if title else "") or "")
    p = meta("product:price:amount")
    try:
        d["price"] = float(p) if p else _num(_ld_str((ld.get("offers") or {}).get("price") if isinstance(ld.get("offers"), dict) else None))
    except Exception:
        d["price"] = None
    d["pid"] = meta("ch:product:id"); d["image"] = _ld_str(ld.get("image")) or meta("og:image") or ""
    d["size_ml"] = _ch_ml(title) or _ch_ml(desc)
    d["brand"] = _ld_str(ld.get("brand"))
    d["category"] = _ld_str(ld.get("category")) or (meta("product:category") or "")
    gtin = str(ld.get("gtin13") or ld.get("gtin12") or ld.get("gtin") or "").strip()
    d["upc"] = gtin if re.fullmatch(r"\d{8,14}", gtin) else ""
    d["sku"] = str(ld.get("sku") or "")
    d["description"] = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", ld.get("description") or ""))).strip()[:2000]
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
                d = _ch_parse_product(_fetch(u, key))
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
            rows.append(dict(name=d["name"], brand=d.get("brand") or "", price=d.get("price"),
                             sku=d.get("sku") or d.get("pid") or "", upc=d.get("upc") or "",
                             size_ml=d.get("size_ml"), category=d.get("category") or "",
                             description=d.get("description") or "", store=d.get("store") or "",
                             image=d.get("image") or "", option_id=oid or ""))
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
    import runlog
    _rl = runlog.start("offprem-%s" % market, total=len(stores))     # register in the Active Runs board
    rows = []
    for si, s in enumerate(stores):
        _rl.progress(si + 1)
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
        # optional rich fields the enriched recipes capture — carried through so NOTHING the retailer gives is
        # lost (flavor/varietal in tags+description, item code, weight, stock, image, and the full raw record).
        _extra = ("tags", "description", "item_code", "product_type", "compare_at_price", "grams", "in_stock",
                  "image", "size_opt", "vintage_opt", "abv", "vintage", "origin", "bottled_in", "region",
                  "sub_region", "appellation", "varietal", "raw_json")
        for it in items:
            b = ctx.classify_beverage(it["name"])
            rec = dict(store=s["account"], base=s["base"], platform=s["platform"], name=it["name"],
                       brand=it.get("brand", ""), price_value=it.get("price"), sku=it.get("sku", ""),
                       upc=it.get("upc", ""), size_ml=it.get("size_ml"),
                       bev_category=b["category"], is_hemp=observe.is_hemp(it["name"]), run_id=run_id,
                       **dd._parse_pack(it["name"]))
            for k in _extra:
                rec[k] = it.get(k)          # always set the key (None default) → consistent table schema
            rows.append(rec)
        log("  [off] %-26s (%s) -> %d products" % (s["account"][:26], s["platform"], len(items)))
    if rows:
        # ACCUMULATE — `out` is per-market but filled by a PLATFORM-filtered subset; running for one platform
        # then another into the same table must not wipe the first platform's products.
        warehouse.write_accumulate(out, rows, key=lambda r: (r.get("account") or r.get("store"), r.get("name")))
    _rl.finish("done", note="%d products from %d stores" % (len(rows), len(stores)))
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


# canonical set of platforms run_census() can dispatch — KEEP IN SYNC with the dispatch in run_census().
# value = the recipe fn(s) that handle it. BigCommerce is proven via ABC's abc_catalog, not run_census.
RECIPE_PLATFORMS = {"bigcommerce": "abc_catalog / bigcommerce_ids", "shopify": "shopify_catalog",
                    "woocommerce": "woo_catalog", "bottlecapps": "bottlecapps_catalog",
                    "wix": "wix_catalog", "city hive": "cityhive_catalog", "cityhive": "cityhive_catalog"}


def _has_recipe(platform):
    p = (platform or "").lower()
    return any(k in p for k in RECIPE_PLATFORMS)


def recipe_gap(log=print):
    """Standing recipe-coverage report — the 'systems we've found that we can't (yet) crawl' view.

    Scans every *_census table in the warehouse, tallies e-commerce stores by detected PLATFORM, and marks each:
      • proven          — a recipe is registered AND it has yielded products (appears in a *_products table)
      • recipe-unproven — a recipe is registered but hasn't produced anything yet (needs validating/fixing)
      • bespoke         — no platform detected; each store is its own custom job (low priority)
      • NO RECIPE       — a real named platform we have no recipe for -> BUILD priority
    Ranks by store count so the biggest un-crawlable systems float to the top = what to build/fix next.
    """
    from collections import defaultdict
    ds = warehouse.list_datasets()
    have = {d["name"]: d for d in ds}
    census_tbls = [d["name"] for d in ds if d["name"].endswith("_census") and d.get("rows")]
    prod_tbls = [d["name"] for d in ds if d["name"].endswith("_products") and d.get("rows")]
    counts, markets = defaultdict(int), defaultdict(set)
    for t in census_tbls:
        try:
            for r in warehouse.query(t, "SELECT platform, count(*) c FROM t WHERE has_ecommerce = true GROUP BY platform"):
                if r["platform"]:
                    counts[r["platform"]] += r["c"]; markets[r["platform"]].add(t.replace("_offprem_census", "").replace("_hemp_census", ""))
        except Exception:
            pass
    proven = set()
    for t in prod_tbls:
        try:
            for r in warehouse.query(t, "SELECT DISTINCT platform FROM t WHERE platform IS NOT NULL"):
                proven.add((r["platform"] or "").lower())
        except Exception:
            pass
    if have.get("abc_catalog", {}).get("rows"):
        proven.add("bigcommerce")                         # BigCommerce proves via ABC's abc_catalog
    out = []
    for plat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        pl = plat.lower()
        registered = _has_recipe(plat)
        is_proven = registered and any(k in pl or pl in k for k in proven)
        status = ("proven" if is_proven else "recipe-unproven") if registered else \
                 ("bespoke" if "bespoke" in pl else "NO RECIPE")
        out.append({"platform": plat, "stores": n, "markets": sorted(markets[plat]), "status": status})
    log("[recipe-gap] %d census tables · %d platforms" % (len(census_tbls), len(out)))
    for r in out:
        log("  %-16s %5d stores  %-16s %s" % (r["platform"], r["stores"], r["status"], ",".join(r["markets"])))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", action="store_true", help="print the standing recipe-coverage gap report")
    ap.add_argument("--store", default="")
    ap.add_argument("--base", default="")
    ap.add_argument("--platform", default="")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--census", default="", help="market -> pull all its census e-commerce stores")
    ap.add_argument("--platforms", default="Shopify")
    ap.add_argument("--hemp", action="store_true", help="pull the hemp census instead of off-premise")
    ap.add_argument("--national", default="", help="platform -> discover its stores nationally + sweep")
    a = ap.parse_args()
    if a.gap:
        recipe_gap()
    elif a.national:
        national_sweep(a.national)
    elif a.census:
        pl = tuple(p.strip() for p in a.platforms.split(",") if p.strip())
        if a.hemp:
            run_census(a.census, platforms=pl, census="%s_hemp_census" % a.census, out="%s_hemp_products" % a.census)
        else:
            run_census(a.census, platforms=pl)
    elif a.store:
        run(a.store, base=a.base or None, platform=a.platform or None, sample=a.sample or None)
