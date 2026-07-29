#!/usr/bin/env python3
"""total_wine_inventory.py — REAL per-store inventory for Total Wine via its own getProduct JSON API.

The model (same as ABC FWS, but Total Wine gives EXACT unit counts + shelf position):
  1. Product universe from the published sitemap ( /p/<productId> ) — total_wine.product_urls().
     skuId = "<productId>-1" (the sitemap lists each size variant as its own /p/ URL).
  2. For a store, call the product's own API — the one its JS uses — keyed by storeId:
       GET /product/api/product/product-detail/v1/getProduct/<skuId>
           ?shoppingMethod=INSTORE_PICKUP&state=US-<ST>&attrConfig=true&storeId=<storeId>
     -> price, stockLevel[].stock (units on hand), stockMessages.digital*Quantity / digitalInStock,
        shipping qty, fulfillment, physical bay/shelf, ABV, and the geo categories
        (VARIETAL_TYPE / COUNTRY_STATE / REGION).
  3. Land per-store rows to retail_observations (exact qty, like ABC) + enrich total_wine_products
     (geo/abv/price the master reads).

robots.txt compliant: /p/ pages, the product sitemap, and /product/api/.../getProduct are ALL allowed;
/search/ is NOT (so we never use a bulk search API — the sitemap is the sanctioned product universe).

BD-minimizing by design (two fetch modes; PerimeterX binds the cookie to the IP, so warm+pull share one IP):
  • TW_FETCH=direct   — $0: warm a PX cookie in a LOCAL browser once, then requests.Session direct.
                        Use on a CLEAN IP (e.g. the Fly box). No Bright Data at all.
  • TW_FETCH=bdbrowser — pull THROUGH one live BD Browser session (page.request), reusing its IP+cookie.
                        One session serves hundreds of API calls (amortized, not per-request Unlocker).
                        Default here because this dev IP is currently PerimeterX-flagged.

    python total_wine_inventory.py --store 920 --state FL --limit 300
"""
import argparse, json, os, sys, time, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import total_wine as tw

API = ("https://www.totalwine.com/product/api/product/product-detail/v1/getProduct/%s"
       "?shoppingMethod=INSTORE_PICKUP&state=US-%s&attrConfig=true&storeId=%s")
STORE_API = "https://www.totalwine.com/product/api/store/storelocator/v1/store/%s"
FETCH = os.environ.get("TW_FETCH", "bdbrowser")


def _geo(cats):
    g = {"varietal": "", "origin": "", "region": "", "style": ""}
    for c in cats or []:
        t, n = (c.get("type") or ""), (c.get("name") or "")
        if t == "VARIETAL_TYPE": g["varietal"] = n
        elif t == "COUNTRY_STATE": g["origin"] = n
        elif t == "REGION": g["region"] = n
        elif t == "PRODUCT_TYPE": g["style"] = n
    return g


import re
_AWARD = re.compile(
    r"\b(Double Gold|Gold|Silver|Bronze|Platinum|Best in Class|\d{2,3}\s*(?:points?|pts))\b\s*[-–—]?\s*"
    r"([A-Z][A-Za-z0-9&'. ]*(?:Awards?|Competition|Challenge|Spectator|Advocate|Enthusiast|SIP))?", 0)


def _chars(d):
    """itemCharacteristics = the RNDC-style attribute vector Total Wine serves: finish/taste/style/body/varietal.
    Richer than the `categories` geo, so take it too. TASTE1/2/3 collapse into one comma-joined taste."""
    out = {"finish": "", "taste": "", "style": "", "body": ""}
    tastes = []
    for c in (d.get("itemCharacteristics") or []):
        t, v = (c.get("type") or ""), (c.get("value") or "")
        if not v:
            continue
        if t == "FINISH":
            out["finish"] = v
        elif t.startswith("TASTE"):
            tastes.append(v)
        elif t == "STYLE":
            out["style"] = v
        elif t == "BODY":
            out["body"] = v
    out["taste"] = ", ".join(tastes)
    return out


def _award(review):
    m = _AWARD.search(review or "")
    return (m.group(0).strip(" -–—") if m else "")


def parse_product(d, store_id, store_name=""):
    """getProduct JSON -> a flat observation+enrichment record with EVERYTHING Total Wine serves. None if not a
    product. NOTE: Total Wine's getProduct exposes NO UPC field — upc stays '' by design, not an oversight."""
    if not isinstance(d, dict) or not d.get("skuId"):
        return None
    sm = d.get("stockMessages") or {}
    stock = (d.get("stockLevel") or [{}])
    st0 = stock[0] or {}
    units = st0.get("stock")
    if units is None:
        units = sm.get("digitalStoreQuantity")
    price = None
    for p in (d.get("price") or []):
        if isinstance(p, dict) and p.get("price") is not None:
            price = p.get("price"); break
    in_stock = bool(sm.get("digitalInStock") or sm.get("shippingInStock")) and not d.get("unavailableAtStore")
    g = _geo(d.get("categories"))
    ch = _chars(d)
    brand = d.get("brand") or {}
    imgs = d.get("images") or []
    review = d.get("review") or ""
    badges = [b.get("badgeCode") for b in (d.get("merchBadges") or []) if isinstance(b, dict)]
    return {"store_id": str(store_id), "store": store_name or ("Total Wine #%s" % store_id),
            "product_id": d.get("skuId"), "sku": d.get("skuId"), "upc": "",
            "brand": (brand.get("name") if isinstance(brand, dict) else "") or "",
            "brand_id": (brand.get("id") if isinstance(brand, dict) else "") or "",
            "name": d.get("name") or "", "price": price, "price_type": (d.get("price") or [{}])[0].get("type", ""),
            "in_stock": in_stock, "qty": units,
            "store_qty": sm.get("digitalStoreQuantity"), "shipping_qty": sm.get("shippingStoreQuantity"),
            "purchase_limit": st0.get("purchaseLimit"),
            "stock_level": ("OOS" if not in_stock else ("LIMITED" if sm.get("digitalLimitedStock") else "IN")),
            "size": d.get("packageDescription") or "", "abv": d.get("alcoholPercentage"),
            "bay": d.get("bay") or "", "shelf": d.get("shelf") or "", "aisle": d.get("location") or "",
            "raw_location": d.get("rawLocation") or "",              # full multi-location planogram (lisaInfo)
            "finish": ch["finish"], "taste": ch["taste"], "body": ch["body"],
            "tasting_notes": review[:800], "award": _award(review),
            "pairings": ", ".join(o for pc in (d.get("pairingsConfig") or []) for o in (pc.get("options") or [])),
            "rating": d.get("customerAverageRating"), "reviews": d.get("customerReviewsCount"),
            "department": d.get("department") or "", "direct_ship": d.get("directType") or "",
            "is_new": "new" in badges,
            "url": d.get("productUrl") or d.get("canonicalUrl") or "",
            "image": (imgs[0].get("url") or imgs[0].get("imageUrl") if imgs and isinstance(imgs[0], dict) else "") or "",
            "is_hemp": observe.is_hemp(d.get("name") or "", ch["style"] or g.get("style") or ""),
            "raw_json": json.dumps(d, separators=(",", ":"))[:6000],
            "style": ch["style"] or g["style"], "varietal": g["varietal"], "origin": g["origin"], "region": g["region"]}


def _skuids_from_sitemap(limit, log):
    urls = tw.product_urls(int(limit) * 2 if limit else 200000, log=lambda *a: None)
    ids = []
    for u in urls:
        m = u.rstrip("/").rsplit("/p/", 1)
        if len(m) == 2 and m[1].isdigit():
            ids.append(m[1] + "-1")
    seen, out = set(), []
    for s in ids:
        if s not in seen:
            seen.add(s); out.append(s)
    return out[:int(limit)] if limit else out


def _store_meta(fetch_json, store_id):
    try:
        d = fetch_json(STORE_API % store_id)
        st = (d.get("state") or d.get("stateCode") or "")[:2].upper()
        nm = ("Total Wine - %s" % d.get("city")) if d.get("city") else ""
        return (st or None), nm
    except Exception:
        return None, ""


# fetch IN-PAGE: a same-origin fetch() from the page's own JS carries every cookie + header the app sends
# (page.request does NOT replicate the PX/session context, so it 403s). One warmed session serves the batch.
_FETCH_JS = ("async (url) => { try { const r = await fetch(url, {headers:{'accept':'application/json, text/plain, */*'}});"
             " if(!r.ok) return {ok:false, s:r.status}; return {ok:true, d: await r.json()}; }"
             " catch(e){ return {ok:false, e:String(e)}; } }")


def _pull_bdbrowser(store_id, state, skuids, log):
    """Pull THROUGH one live BD Browser session — an in-page fetch() reuses the session's IP + warmed PX
    cookie, so every getProduct returns JSON. One session serves the whole batch (amortized BD)."""
    import menu_site as ms
    import browser_warm
    sync_playwright = browser_warm.sync_playwright_api()   # patchright on the image; NEVER import playwright
    auth = ms._browser_auth()
    warm = "https://www.totalwine.com/wine/red-wine/cabernet-sauvignon/caymus-cabernet/p/223968750?s=%s" % store_id
    rows = []
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("wss://%s@brd.superproxy.io:9222" % auth, timeout=90000)
        try:
            ctx = b.contexts[0] if b.contexts else b.new_context()
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.goto(warm, wait_until="domcontentloaded", timeout=60000); time.sleep(6)   # warm PX
            st = state
            if not st:
                try:
                    r = pg.evaluate(_FETCH_JS, STORE_API % store_id)
                    st = ((r.get("d") or {}).get("state") or "US")[:2].upper() if r.get("ok") else "US"
                except Exception:
                    st = "US"
            blocked = 0
            for i, sk in enumerate(skuids):
                try:
                    r = pg.evaluate(_FETCH_JS, API % (sk, st, store_id))
                    if r.get("ok"):
                        rec = parse_product(r.get("d"), store_id)
                        if rec:
                            rows.append(rec)
                    elif r.get("s") in (403, 429):
                        blocked += 1
                except Exception:
                    pass
                if (i + 1) % 50 == 0:
                    log("  [tw-inv] %d/%d pulled (%d products, %d blocked)" % (i + 1, len(skuids), len(rows), blocked))
                time.sleep(0.25)
        finally:
            b.close()
    return rows


def _pull_direct(store_id, state, skuids, log):
    """$0 path: warm a PX cookie in a LOCAL browser once, then requests-style direct (same IP). Use on a
    clean IP (the Fly box). BD-free."""
    import browser_warm
    sync_playwright = browser_warm.sync_playwright_api()   # patchright on the image; NEVER import playwright
    warm = "https://www.totalwine.com/wine/red-wine/cabernet-sauvignon/caymus-cabernet/p/223968750?s=%s" % store_id
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(warm, wait_until="domcontentloaded", timeout=60000); time.sleep(5)
        cookies = ctx.cookies(); ua = pg.evaluate("navigator.userAgent")
        b.close()
    cookie = "; ".join("%s=%s" % (c["name"], c["value"]) for c in cookies)
    st = state or "US"
    rows = []
    for i, sk in enumerate(skuids):
        try:
            req = urllib.request.Request(API % (sk, st, store_id), headers={
                "User-Agent": ua, "Accept": "application/json, text/plain, */*",
                "Referer": warm, "Cookie": cookie, "Accept-Language": "en-US,en;q=0.9"})
            d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
            rec = parse_product(d, store_id)
            if rec:
                rows.append(rec)
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            log("  [tw-inv] %d/%d pulled (%d products)" % (i + 1, len(skuids), len(rows)))
        time.sleep(0.4)
    return rows


def enrich_rows(rows):
    """parse_product records -> total_wine_products enrichment records (FULL field set incl. finish/taste/award/
    pairings/planogram + raw_json). Shared by pull_store and total_wine_full._land so both land the same schema."""
    def _s(v):
        return "" if v is None else str(v)
    return [{"sku": _s(r["sku"]), "name": _s(r["name"]), "brand": _s(r["brand"]), "brand_id": _s(r["brand_id"]),
             "size": _s(r["size"]), "price": r["price"], "abv": _s(r["abv"]), "varietal": _s(r["varietal"]),
             "origin": _s(r["origin"]), "region": _s(r["region"]), "style": _s(r["style"]), "category": _s(r["style"]),
             "finish": _s(r["finish"]), "taste": _s(r["taste"]), "body": _s(r["body"]),
             "tasting_notes": _s(r["tasting_notes"]), "award": _s(r["award"]), "pairings": _s(r["pairings"]),
             "rating": r["rating"], "reviews": r["reviews"], "purchase_limit": r["purchase_limit"],
             "store_qty": r["store_qty"], "direct_ship": _s(r["direct_ship"]), "department": _s(r["department"]),
             "url": _s(r["url"]), "image": _s(r["image"]), "raw_location": _s(r["raw_location"]),
             "raw_json": _s(r["raw_json"]), "store_id": _s(r["store_id"])} for r in rows]


def pull_store(store_id, state=None, limit=300, log=print):
    log("[tw-inv] store %s: building product universe from sitemap…" % store_id)
    skuids = _skuids_from_sitemap(limit, log)
    log("[tw-inv] store %s: %d products to check (fetch=%s)" % (store_id, len(skuids), FETCH))
    pull = _pull_direct if FETCH == "direct" else _pull_bdbrowser
    rows = pull(store_id, (state or "").upper() or None, skuids, log)
    if not rows:
        log("[tw-inv] store %s: 0 products (PX block? check IP / cookie)" % store_id); return []
    n_obs = observe.record("total-wine", rows, log=log)
    in_stock = sum(1 for r in rows if r["in_stock"])
    tot_units = sum((r["qty"] or 0) for r in rows)
    # enrich the master's total_wine_products with geo/abv/price (accumulate, keyed by sku)
    # coerce to match the existing total_wine_products schema (abv is a string there from the catalog crawl;
    # price stays float) so write_accumulate doesn't hit a column type conflict.
    enr = enrich_rows(rows)
    try:
        warehouse.write_accumulate("total_wine_products", enr, key=lambda r: r["sku"])
    except Exception as e:
        log("[tw-inv] enrich land failed: %s" % str(e)[:80])
    log("[tw-inv] store %s DONE: %d products · %d in-stock · %d total units · %d observations"
        % (store_id, len(rows), in_stock, tot_units, n_obs))
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, help="Total Wine storeId (e.g. 920 = Orlando Millenia Plaza)")
    ap.add_argument("--state", default="", help="2-letter state (e.g. FL); auto-derived if omitted")
    ap.add_argument("--limit", type=int, default=300)
    a = ap.parse_args()
    pull_store(a.store, state=a.state, limit=a.limit)
