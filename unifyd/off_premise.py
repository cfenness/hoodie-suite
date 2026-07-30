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
import raw_capture
import doordash as dd                # reuse _parse_pack (container/size)
import cocktail_taxonomy as ctx      # bev_category / beer_style

# store registry — {slug: {name, base, platform}}. Broudy's (bottlecapps/magento) + Goody Goody (bespoke)
# await their own recipes; the flagged large-format Orlando/Tampa independents get added as discovered.
STORES = {
    "haskells": {"name": "Haskell's", "base": "https://www.haskells.com", "platform": "bigcommerce"},
}
# Shopify brand/retailer domains run through the Shopify recipe (/products.json is open → $0). Migrated from
# the retired standalone shopify_scraper.py so Shopify lives inside the census sweep, not as a parallel source.
# National retailer discovery is the Maps census (run_census) + optional OFFPREM_SERP signature sweep; this
# seed is the known-brand floor. SHOPIFY_DOMAINS env extends/overrides it.
SHOPIFY_SEED = [d.strip() for d in os.environ.get(
    "SHOPIFY_DOMAINS", "drinkbrez.com,drinkcann.com,cornbreadhemp.com,hopwtr.com,drinkolipop.com"
).split(",") if d.strip()]

_BC_TITLE = re.compile(r'"og:title"[^>]*content="([^"]+)"|property="og:title"[^>]*content="([^"]+)"', re.I)
_BC_PRICE = re.compile(r'(?:og:price:amount|product:price:amount)"[^>]*content="([\d.]+)"', re.I)
_BC_SKU = re.compile(r'itemprop="sku"[^>]*content="([^"]+)"|"sku":\s*"([^"]+)"|data-product-id="(\d+)"', re.I)
_BC_INSTOCK = re.compile(r'"instock"\s*:\s*(true|false)|product:availability"[^>]*content="(instock|in stock)"', re.I)


def _bd_key():
    """BRIGHTDATA_API_KEY env, else the `bdata login` credential store (all platforms), else None.
    This is a direct-first, no-BD connector: the key is only needed for the optional Unlocker
    fallback, so a missing key must NOT crash the run. Delegates to brightdata._key(), which
    resolves the macOS / XDG / Linux ~/.config / Windows cred paths — the old hardcoded macOS
    path raised FileNotFoundError on the Linux CI runner, killing the whole census before a
    single fetch (health finding: offprem-census run-failed)."""
    import brightdata
    return brightdata._key()


def _unlock(url, key):
    if not key:
        raise RuntimeError("Bright Data fallback needed for %s but no key "
                           "(set BRIGHTDATA_API_KEY or run `bdata login`)" % url)
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
def bigcommerce_ids(base, key, max_pages=400, log=print):
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


def _shopify_rows(p):
    """One row PER VARIANT — a product sold in 750ml + 1.75L (or by vintage) is multiple purchasable SKUs at
    different prices/barcodes; taking only variants[0] silently dropped every size but the first. Each variant
    carries the shared product context (tags/description/type) so nothing is lost."""
    tags = p.get("tags")
    tags = tags if isinstance(tags, list) else [t.strip() for t in str(tags or "").split(",") if t.strip()]
    desc = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p.get("body_html") or ""))).strip()
    opt_names = [o.get("name", "").lower() for o in (p.get("options") or [])]
    img = ((p.get("images") or [{}])[0] or {}).get("src") or ""
    rows = []
    for v in (p.get("variants") or [{}]):
        sku = (v.get("sku") or "").strip()
        barcode = (v.get("barcode") or "").strip()
        upc = next((x for x in (barcode, sku) if x.isdigit() and 8 <= len(x) <= 14), "")
        opts = {opt_names[i]: (v.get("option%d" % (i + 1)) or "") for i in range(len(opt_names))}
        vt = v.get("title") or ""
        # size is often in the variant TITLE ("750ml"), not a "Size" option — capture it either way
        size_opt = opts.get("size") or opts.get("volume") or (vt if re.search(r"\d\s*(ml|l|oz|pk|pack|liter)\b", vt, re.I) else "")
        rows.append({"name": (p.get("title") or "").strip(), "brand": (p.get("vendor") or "").strip(),
                     "price": _num(v.get("price")), "compare_at_price": _num(v.get("compare_at_price")),
                     "sku": sku, "upc": upc, "barcode": barcode, "item_code": str(v.get("id") or p.get("id") or ""),
                     "product_type": p.get("product_type") or "", "tags": ", ".join(tags),
                     "description": desc[:2000], "handle": p.get("handle") or "", "variant": v.get("title") or "",
                     "grams": v.get("grams"), "in_stock": v.get("available"),
                     "size_opt": size_opt, "vintage_opt": opts.get("vintage") or "",
                     "image": ((v.get("featured_image") or {}) or {}).get("src") or img,
                     "raw_json": json.dumps(dict(p, variants=[v]), separators=(",", ":"))[:8000]})
    return rows


# ── Shopify recipe — the golden path: /products.json returns the whole catalog as JSON, paginated. We now
# capture the FULL product (tags/description/options/barcode/weight/stock/image + raw), not just name/price. ──
def shopify_catalog(base, key, max_pages=400, log=print):
    rows = []
    for pg in range(1, max_pages + 1):                          # loop stops at the true last page; cap is a backstop
        j = None
        for _try in range(2):                                   # transient failures happen under load
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
            rows.extend(_shopify_rows(p))                       # one row PER VARIANT
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
    import browser_warm
    sync_playwright = browser_warm.sync_playwright_api()   # patchright on the image; NEVER import playwright
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
def woo_catalog(base, key, max_pages=400, log=print):
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
        variable = []
        for p in j:
            if p.get("type") == "variable" and p.get("variations"):
                variable.append(p)                          # expand after the page (per-variation fetch, concurrent)
            else:
                rows.append(_woo_row(p))
        if variable:
            rows.extend(_woo_expand_variations(base, key, variable, log=log))
        if len(j) < 100:
            break
    return rows


def _woo_expand_variations(base, key, parents, log=print, workers=8):
    """A variable Woo product (e.g. a wine in 750ml/1.5L) exposes only variation IDs — each variation is its OWN
    product with its own price/sku. Fetch them (concurrently) so every size lands as a distinct row instead of one
    price-range row. Size comes from the parent's variations[].attributes."""
    from concurrent.futures import ThreadPoolExecutor
    jobs = []
    for p in parents:
        base_row = _woo_row(p)
        for var in (p.get("variations") or []):
            if isinstance(var, dict) and var.get("id"):
                jobs.append((base_row, var))

    def fetch(job):
        base_row, var = job
        try:
            v = json.loads(_fetch("%s/wp-json/wc/store/v1/products/%d" % (base.rstrip("/"), var["id"]), key))
        except Exception:
            return None
        size = "; ".join(str(a.get("value") or "") for a in (var.get("attributes") or []) if a.get("value"))
        pr = (v.get("prices") or {}).get("price")
        try:
            price = round(float(pr) / 100.0, 2) if pr not in (None, "") else base_row.get("price")
        except Exception:
            price = base_row.get("price")
        vsku = (v.get("sku") or "").strip()
        r = dict(base_row)
        r.update(price=price, sku=vsku or base_row.get("sku", ""),
                 upc=(vsku if vsku.isdigit() and 8 <= len(vsku) <= 14 else base_row.get("upc", "")),
                 item_code=str(var["id"]), variant=size, size_opt=size,
                 size_ml=_ch_ml(size) or base_row.get("size_ml"),
                 in_stock=v.get("is_in_stock"))
        return r
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(fetch, jobs):
            if r:
                out.append(r)
    if jobs:
        log("  [woo] expanded %d variable products -> %d variation rows" % (len(parents), len(out)))
    return out


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
    nm = _html.unescape(p.get("name") or "").strip()
    row = {"name": nm, "brand": "", "price": price,
           "sku": sku, "upc": (sku if sku.isdigit() and 8 <= len(sku) <= 14 else ""),
           "item_code": str(p.get("id") or ""), "product_type": cats, "tags": cats,
           "size_ml": _ch_ml(nm),                          # Woo has no size field — parse it from the name
           "description": desc[:2000], "image": ((p.get("images") or [{}])[0] or {}).get("src") or "",
           "raw_json": json.dumps(p, separators=(",", ":"))[:8000]}
    for a in (p.get("attributes") or []):
        nm = (a.get("name") or "").strip().lower()
        val = ", ".join(t.get("name", "") for t in (a.get("terms") or []) if t.get("name"))
        if nm in ("brand", "brands", "producer", "winery", "distillery", "vendor") and val and not row["brand"]:
            row["brand"] = val                                  # Woo hides brand in an attribute/taxonomy, not a field
        fld = _WOO_ATTR.get(nm)
        if fld and val and not row.get(fld):
            row[fld] = val
    return row


_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_WIX_STORES_APP = "1380b703-ce81-ff05-f115-39571d94dfcd"
# FULL field set (description/image/options/attributes/discount) — falls back to the minimal query if a Wix site's
# schema rejects it, so we always get SOMETHING but capture everything where supported.
# field set validated live against the Wix storefront GraphQL: description/ribbon/discount/comparePrice/options and
# productItems{...} (per-VARIANT price/sku/inventory) are all valid; media{}/additionalInfoSections{}/variantId are
# NOT. productItems is the key: it exposes each size variant as a distinct item with its own price + sku.
_WIX_GQL_FULL = ("query getProducts($limit:Int,$offset:Int){ catalog{ products(limit:$limit, offset:$offset, "
                 "onlyVisible:true){ totalCount list{ id name brand description ribbon price formattedPrice "
                 "comparePrice discountedPrice sku isInStock productType urlPart "
                 "options{ id title selections{ id value } } "
                 "productItems{ id optionsSelections price formattedPrice comparePrice sku isVisible "
                 "inventory{ status quantity } } } } } }")
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

    def gql(query, offset):
        body = json.dumps({"query": query, "variables": {"limit": page, "offset": offset}}).encode()
        req = urllib.request.Request(ep, data=body, headers=hdr, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace"))

    # probe the FULL query once; if this site's schema rejects it, fall back to the minimal field set
    q = _WIX_GQL_FULL
    try:
        probe = gql(_WIX_GQL_FULL, 0)
        if probe.get("errors") or not (((probe.get("data") or {}).get("catalog") or {}).get("products")):
            q = _WIX_GQL
    except Exception:
        q = _WIX_GQL

    rows, offset, total = [], 0, None
    while True:
        try:
            pq = (((gql(q, offset).get("data") or {}).get("catalog") or {}).get("products")) or {}
        except Exception as e:
            log("  [wix] products offset=%d: %s" % (offset, str(e)[:50])); break
        if total is None:
            total = pq.get("totalCount") or 0
            log("  [wix] %s: totalCount=%d (fields=%s)" % (site, total, "full" if q is _WIX_GQL_FULL else "min"))
            if not total:
                return []
        lst = pq.get("list") or []
        if not lst:
            break
        for p in lst:
            desc = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p.get("description") or ""))).strip()
            nm = _html.unescape(p.get("name") or "").strip()
            opts_txt = "; ".join("%s: %s" % ((o.get("title") or ""),
                                             ", ".join(s.get("value", "") for s in (o.get("selections") or [])))
                                 for o in (p.get("options") or []) if isinstance(o, dict))
            # selection-id -> value, so a productItem's optionsSelections (ids) resolve to size labels ("750ml")
            sel = {s.get("id"): s.get("value") for o in (p.get("options") or []) for s in (o.get("selections") or [])}
            base_row = {"name": nm, "brand": (p.get("brand") or ""),
                        "compare_at_price": p.get("comparePrice"),
                        "category": p.get("productType") or "", "ribbon": p.get("ribbon") or "",
                        "options": opts_txt[:400], "description": desc[:2000],
                        "raw_json": json.dumps(p, separators=(",", ":"))[:8000]}
            pitems = p.get("productItems") or []
            if len(pitems) > 1:
                # a real Wix variant product → one DISTINCT ITEM per size, each with its own price + sku + stock
                for pi in pitems:
                    size = " / ".join(str(sel.get(i) or i) for i in (pi.get("optionsSelections") or []))
                    psku = (pi.get("sku") or "").strip() or (p.get("sku") or "").strip()
                    inv = pi.get("inventory") or {}
                    rows.append(dict(base_row, name=("%s %s" % (nm, size)).strip() if size else nm,
                                     price=pi.get("price") if pi.get("price") is not None else p.get("price"),
                                     sku=psku, upc=(psku if psku.isdigit() and 8 <= len(psku) <= 14 else ""),
                                     item_code=str(pi.get("id") or ""), variant=size,
                                     size_opt=size or opts_txt[:120], size_ml=_ch_ml(size) or _ch_ml(nm),
                                     in_stock=(inv.get("status") == "IN_STOCK") if inv.get("status") else pi.get("isVisible")))
            else:
                pi = pitems[0] if pitems else {}
                sku = ((pi.get("sku") or "").strip() or (p.get("sku") or "").strip())
                rows.append(dict(base_row,
                                 price=(pi.get("price") if pi.get("price") is not None else (p.get("discountedPrice") or p.get("price"))),
                                 sku=sku, upc=(sku if sku.isdigit() and 8 <= len(sku) <= 14 else ""),
                                 item_code=str(pi.get("id") or p.get("id") or ""),
                                 size_ml=_ch_ml(nm) or _ch_ml(opts_txt), size_opt=opts_txt[:120],
                                 in_stock=p.get("isInStock")))
        offset += len(lst)
        if (max_products and offset >= max_products) or offset >= total:
            break
    log("  [wix] %s -> %d products" % (site, len(rows)))
    return rows[:max_products] if max_products else rows


def _dig_wix(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d if isinstance(d, str) else None


# ── National signature-discovery + sweep — expand the recipes we have to their whole national footprint. For a
# LIQUOR-SPECIFIC platform (Bottlecapps), SERP its fingerprint -> store domains -> validate -> run the recipe.
# (Generic platforms — Shopify/Woo — are better discovered per-market via the Maps census.) ──
_NOISE = re.compile(r"quora|medium|crunchbase|accessnewswire|retail-today|reddit\.|youtube|facebook|linkedin|"
                    r"apple\.com|play\.google|glassdoor|indeed|bloomberg|yelp|tripadvisor|/blog|wikipedia|"
                    r"twitter|instagram|pinterest|g2\.com|capterra|owler|zoominfo", re.I)


def _ch_sitemap_products(base, key, log=print):
    """City Hive stores publish their FULL catalog as a product sitemap of URLs shaped
    /shop/product/<slug>/<product_id>?option-id=<opt>. The path varies by store — some use /sitemap.xml,
    others a Google product sitemap at /googlesitemapxml.xml (Top Ten Liquors: 6,407 products there while
    /sitemap.xml holds only category URLs) — so try both, plus any Sitemap: line in robots.txt. BD Unlocker
    occasionally returns a Cloudflare interstitial instead of the XML, so retry each candidate."""
    base = base.rstrip("/")
    cands = [base + "/sitemap.xml", base + "/googlesitemapxml.xml"]
    try:                                                    # honor robots.txt Sitemap: directives
        robots = _fetch(base + "/robots.txt", key)
        for sm in re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", robots):
            if sm not in cands:
                cands.append(sm.strip())
    except Exception:
        pass
    for url in cands:
        for _ in range(3):
            try:
                xml = _fetch(url, key)
            except Exception as e:
                log("  [cityhive] sitemap %s %s" % (url, str(e)[:50])); continue
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
                             image=d.get("image") or "", option_id=oid or "",
                             raw_json=json.dumps(d, separators=(",", ":"))[:8000]))
    log("  [cityhive] %s -> %d products" % (base, len(rows)))
    return rows


# Named national/multi-store City Hive chains we track BY NAME (not via a market census) — e.g. the liquor
# chains a hemp-data vendor bills for. Both publish their full catalog on the SEO surface, so cityhive_catalog
# covers them; we just want them as a first-class, recurring pull. (chain, base, states).
CITYHIVE_CHAINS = [
    ("Top Ten Liquors", "https://www.toptenliquors.com", "MN"),    # 6,407 products; has a THC-drink category
    ("Liquor Barn", "https://www.liquorbarn.com", "KY"),           # Cloudflare-fronted — needs the BD Unlocker path
]
_CH_CHAIN_FLD = ["chain", "store", "base", "state", "name", "brand", "price", "sku", "upc", "size_ml",
                 "category", "is_hemp", "description", "image", "option_id", "captured_at", "source"]


def pull_cityhive_chains(chains=None, max_products=None, land=True, log=print):
    """Pull the named City Hive liquor chains (Top Ten, Liquor Barn) via the SEO-surface catalog recipe and
    land `cityhive_chain_products` (accumulating snapshot, hemp-flagged). Grabs the WHOLE catalog (bev-alc
    first) and flags hemp/THC — THC drinks carry the dose in the name (e.g. '10mg THC'), so hemp_scan/observe
    catch them. No per-store inventory count (the widget API is session-walled), but full catalog + price."""
    chains = chains or CITYHIVE_CHAINS
    key = _bd_key()
    ts = int(time.time())
    allrows = []
    for name, base, state in chains:
        try:
            items = cityhive_catalog(base, key, max_products=max_products, log=log)
        except Exception as e:
            log("  [cityhive-chain] %s FAILED %s" % (name, str(e)[:50])); continue
        for it in items:
            allrows.append(dict(chain=name, store=it.get("store") or name, base=base, state=state,
                                name=it["name"], brand=it.get("brand", ""), price=it.get("price"),
                                sku=it.get("sku", ""), upc=it.get("upc", ""), size_ml=it.get("size_ml"),
                                category=it.get("category", ""),
                                is_hemp=observe.is_hemp(it["name"], it.get("category", "")),
                                description=it.get("description", ""), image=it.get("image", ""),
                                option_id=it.get("option_id", ""), captured_at=ts, source="cityhive"))
    if land and allrows:
        warehouse.write_accumulate("cityhive_chain_products", allrows,
                                   key=lambda r: (r["base"], r["sku"] or r["name"]), fields=_CH_CHAIN_FLD)
    hemp = sum(1 for r in allrows if r["is_hemp"])
    log("[cityhive-chain] %d products across %d chains -> cityhive_chain_products (%d hemp)"
        % (len(allrows), len(chains), hemp))
    return allrows


# ── Squarespace Commerce recipe — the crack: Squarespace exposes EVERYTHING as JSON. sitemap.xml lists every
# product URL (they all contain `/p/`), and appending `?format=json` to any product URL returns the full item
# (title, variants[{sku, priceMoney, salePriceMoney, qtyInStock, optionValues}], assetUrl image, body). No API
# key, no browser — universal across any Squarespace store regardless of its shop path. Per-product fetch, so
# bounded by max_products in a multi-store census sweep. ──
def _sqsp_money(v, item):
    m = (v.get("salePriceMoney") if v.get("onSale") else None) or v.get("priceMoney") \
        or item.get("salePriceMoney") or item.get("priceMoney") or {}
    try:
        return float(m.get("value")) if m.get("value") not in (None, "") else None
    except Exception:
        return None


def squarespace_product(url, key):
    """One Squarespace product URL -> a row per purchasable variant (size options become separate variants)."""
    try:
        # direct-first (?format=json is a public endpoint, no bot wall) — BD only if actually blocked
        j = json.loads(_fetch(url + ("&" if "?" in url else "?") + "format=json", key))
    except Exception:
        return []
    item = j.get("item") or ((j.get("items") or [{}])[0])
    if not isinstance(item, dict) or not item.get("title"):
        return []
    title = _html.unescape(item.get("title") or "").strip()
    img = item.get("assetUrl") or ""
    desc = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item.get("excerpt") or item.get("body") or ""))).strip()[:2000]
    variants = item.get("variants") or [{}]
    out = []
    for v in variants:
        # size/option variants (e.g. "750ml" / "1.5L") ride optionValues -> fold into the name for size parsing
        opt = " ".join(str(o.get("value") or "") for o in (v.get("optionValues") or []) if isinstance(o, dict)).strip()
        name = ("%s %s" % (title, opt)).strip() if opt and opt.lower() not in title.lower() else title
        sku = (v.get("sku") or "").strip()
        qty = v.get("qtyInStock")
        out.append({"name": name, "brand": "", "price": _sqsp_money(v, item),
                    "sku": sku, "upc": (sku if sku.isdigit() and 8 <= len(sku) <= 14 else ""),
                    "size_ml": _ch_ml(name), "in_stock": bool(v.get("unlimited") or (qty or 0) > 0),
                    "qty": (None if v.get("unlimited") else qty), "image": img, "description": desc,
                    "product_type": str(item.get("productType") or ""), "url": item.get("fullUrl") or url,
                    "raw_json": json.dumps(item)[:12000]})
    return out


def squarespace_catalog(base, key=None, max_products=None, workers=6, log=print):
    """Every product on a Squarespace store: sitemap.xml -> /p/ URLs -> per-product ?format=json (concurrent)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    key = key or _bd_key()
    base = base.rstrip("/")
    try:
        sm = _fetch(base + "/sitemap.xml", key)        # direct-first; sitemap is public
    except Exception as e:
        log("  [sqsp] %s sitemap: %s" % (base, str(e)[:50])); return []
    urls = [u for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", sm or "") if "/p/" in u]
    # de-dupe, keep order; cap for a multi-store sweep
    seen, prod = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); prod.append(u)
    if max_products:
        prod = prod[:max_products]
    if not prod:
        log("  [sqsp] %s: no /p/ products in sitemap (Squarespace Commerce not in use?)" % base); return []
    rows, lock = [], threading.Lock()

    def w(u):
        r = squarespace_product(u, key)
        if r:
            with lock:
                rows.extend(r)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(w, prod))
    log("  [sqsp] %s -> %d products (%d variants)" % (base, len(prod), len(rows)))
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
    """Run a platform's recipe across its national footprint -> national_<platform>_products.

    Domains = a curated SEED (always; catalog endpoints like Shopify /products.json are open → $0) PLUS
    optional SERP signature discovery (discover_stores → Bright Data, METERED) gated behind OFFPREM_SERP=1,
    so the default run costs nothing. This is where the retired standalone shopify_scraper.py folded in:
    Shopify is now part of the census sweep, seeded from SHOPIFY_SEED, not a parallel source."""
    key = _bd_key()
    sig = {"bottlecapps": '"powered by bottlecapps" OR "bottlecapps" liquor wine spirits order online',
           "cityhive": '"powered by city hive" liquor wine spirits',
           "shopify": '"powered by shopify" liquor OR wine OR beer OR spirits order online'}
    seed = {"shopify": SHOPIFY_SEED}.get(platform, [])
    serp_on = os.environ.get("OFFPREM_SERP") == "1"
    serp = discover_stores(sig.get(platform, platform), log=log) if serp_on else []
    domains = sorted(dict.fromkeys(
        [(d if d.startswith("http") else "https://" + d) for d in seed] + serp))
    log("[national] %s: %d store domains (%d seed + %d serp%s)"
        % (platform, len(domains), len(seed), len(serp), "" if serp_on else "; SERP off — $0"))
    rows, hit = [], 0
    for d in domains:
        try:
            if platform == "bottlecapps":
                import bottlecapps                        # full-capture module supersedes the shallow recipe
                items = bottlecapps.pull_store(d, log=log)
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
                             size_opt=it.get("size_opt", ""), item_code=it.get("item_code", ""),
                             bev_category=b["category"], is_hemp=observe.is_hemp(it["name"]),
                             **dd._parse_pack(it["name"])))
        log("  [national] %-32s -> %d products" % (d.replace("https://", "")[:32], len(items)))
        _nkey = lambda r: (r.get("store"), r.get("sku") or r.get("item_code")
                           or ((r.get("name") or "") + "|" + str(r.get("size_opt") or "")))
        if rows and hit % 3 == 0:
            warehouse.write_accumulate("national_%s_products" % platform, rows, key=_nkey)   # checkpoint
    if rows:
        warehouse.write_accumulate("national_%s_products" % platform, rows,
                                   key=lambda r: (r.get("store"), r.get("sku") or r.get("item_code")
                                                  or ((r.get("name") or "") + "|" + str(r.get("size_opt") or ""))))
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
                import bottlecapps                        # full-capture module (patchright, all cats, price+UPC)
                items = bottlecapps.pull_store(s["base"], log=log)
            elif "city hive" in plat or "cityhive" in plat:
                # default FULL (capture everything); CITYHIVE_MAX bounds it only for a quick market census.
                items = cityhive_catalog(s["base"], key, max_products=(int(os.environ.get("CITYHIVE_MAX", "0")) or None), log=log)
            elif "wix" in plat:
                items = wix_catalog(s["base"], key, log=log)
            elif "squarespace" in plat:
                items = squarespace_catalog(s["base"], key, max_products=(int(os.environ.get("SQSP_MAX", "0")) or None), log=log)
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
        # DON'T LAND raw_json IN THE ACCUMULATE PATH. write_accumulate merges by rewriting the WHOLE
        # table — read every existing row, drop the replaced ones, append the batch — so a fat raw_json
        # column (each up to 6-12KB, per off_premise's own JSON-LD/product dumps) is re-read and
        # re-written on EVERY flush, forever, and gets more expensive as the catalog succeeds. This is
        # the exact "payloads are events" mistake raw_capture.py exists to fix (its docstring names the
        # identical UberEats OOM this pattern caused). A payload is an event — what a source said at this
        # moment — not a mutable product attribute, so it goes to the append-only raw_payloads table
        # instead, where it's still fully recoverable, just never re-read on a merge.
        raw_capture.record("off-premise", time.strftime("%Y-%m-%d"), "%s_%s" % (market, run_id),
                           [{"entity_id": r.get("sku") or r.get("name"), "parent_id": r.get("base"),
                             "raw_json": r.get("raw_json")} for r in rows], log=log)
        lean = [{k: v for k, v in r.items() if k != "raw_json"} for r in rows]
        # ACCUMULATE — `out` is per-market but filled by a PLATFORM-filtered subset; running for one platform
        # then another into the same table must not wipe the first platform's products.
        # variant-safe identity: a product's sizes are distinct SKUs — key on sku/variant-id/size, NOT bare name,
        # or per-variant rows would collapse back to one.
        warehouse.write_accumulate(out, lean, key=lambda r: (
            (r.get("account") or r.get("store")),
            r.get("sku") or r.get("item_code") or ((r.get("name") or "") + "|" + str(r.get("size_opt") or r.get("size_ml") or ""))))
        # feed the FACTS: independent-retailer price + in/out over time. unique part per market -> no clobber.
        obs = [dict(store=r.get("account") or r.get("store"), store_id=r.get("base") or r.get("account") or "",
                    product_id=(r.get("sku") or (r.get("name") or "")[:90]), upc=r.get("upc") or "",
                    brand=r.get("brand") or "", name=r.get("name") or "", price=r.get("price_value"),
                    in_stock=r.get("in_stock"), is_hemp=r.get("is_hemp")) for r in rows]
        observe.record("offprem", obs, part="%s_offprem_%s" % (time.strftime("%Y-%m-%d"), market))
        # feed the MASTER: one consolidated catalog table (in _CFG) so independent products join the master.
        warehouse.write_accumulate("offprem_products", lean,
                                   key=lambda r: ((r.get("base") or ""),
                                                  r.get("sku") or r.get("item_code")
                                                  or ((r.get("name") or "") + "|" + str(r.get("size_opt") or r.get("size_ml") or ""))))
    _rl.finish("done", note="%d products from %d stores" % (len(rows), len(stores)))
    log("[off] %d products -> %s (+ facts + offprem_products)" % (len(rows), out))
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
                    "wix": "wix_catalog", "city hive": "cityhive_catalog", "cityhive": "cityhive_catalog",
                    "squarespace": "squarespace_catalog"}


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
