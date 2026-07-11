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
import argparse, html as _html, json, os, re, sys, time, urllib.request

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


def run_census(market="orlando", platforms=("Shopify",), log=print):
    """Pull every census e-commerce store on the given platform(s) -> <market>_offprem_products (a corroboration
    source + independent-store assortment). Dispatches per platform recipe."""
    key = _bd_key()
    seen, stores = set(), []
    for s in warehouse.query("%s_offprem_census" % market,
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
            items = shopify_catalog(s["base"], key, log=log) if "shopify" in plat else []
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
        warehouse.write_parquet("%s_offprem_products" % market, rows)
    log("[off] %d products -> %s_offprem_products" % (len(rows), market))
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
    a = ap.parse_args()
    if a.census:
        run_census(a.census, platforms=tuple(p.strip() for p in a.platforms.split(",") if p.strip()))
    elif a.store:
        run(a.store, base=a.base or None, platform=a.platform or None, sample=a.sample or None)
