#!/usr/bin/env python3
"""trader_joes.py — Trader Joe's bev-alc catalog via the OPEN storefront GraphQL (traderjoes.com/api/graphql).

No auth, no anti-bot: the `SearchProducts` query is public. Store list comes from TJ's Brandify locator
(public appkey). TJ is private-label so items carry a SKU (no UPC) + retail_price + size + country_of_origin
+ the "Wine, Beer & Liquor" category. Validated 2026-07-23: search "wine" @ store 768 → 767 wine items, 200.

We enumerate stores near a set of seed zips, then per store sweep the alcohol terms, page the result set,
keep only "Wine, Beer & Liquor" items, and land `trader_joes_products` (sku|store) + observations.
Stdlib-only (urllib); curl_cffi used if present. Note: not every TJ sells alcohol (state law) — those return
zero alcohol items, which is correct, not a failure.

    python trader_joes.py --stores 768 --terms wine,beer
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

GQL = "https://www.traderjoes.com/api/graphql"
LOCATOR = "https://alphaapi.brandify.com/rest/locatorsearch"
LOCATOR_KEY = "8BC3433A-60FC-11E3-991D-B2EE0C70A832"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/150.0.0.0 Safari/537.36")
ALCOHOL_CAT = "wine, beer & liquor"                        # category_hierarchy name that marks a bev-alc item
TERMS = ["wine", "beer", "spirits", "liquor", "sparkling", "champagne", "sake", "cider", "hard seltzer",
         "vodka", "whiskey", "tequila", "rum", "gin"]
# seed zips spanning TJ alcohol markets (CA/TX/FL/NY/IL/…); the locator returns the nearest storeCodes.
SEED_ZIPS = ["90064", "94301", "10001", "60614", "33139", "78701", "98101", "85016", "80202", "20009"]

_SEARCH_QUERY = ("query SearchProducts($search: String, $pageSize: Int, $currentPage: Int, "
                 "$storeCode: String = \"768\", $availability: String = \"1\", $published: String = \"1\") { "
                 "products(search: $search, filter: {store_code: {eq: $storeCode}, published: {eq: $published}, "
                 "availability: {match: $availability}}, pageSize: $pageSize, currentPage: $currentPage) { "
                 "items { sku item_title retail_price sales_size sales_uom_description country_of_origin "
                 "availability category_hierarchy { name } price_range { minimum_price { final_price { value } } } } "
                 "total_count page_info { current_page total_pages } } }")


def _post(url, body, headers, timeout=30):
    try:
        from curl_cffi import requests as _cr
        return json.loads(_cr.post(url, data=json.dumps(body).encode(), headers=headers,
                                   impersonate="chrome", timeout=timeout).text)
    except ImportError:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))


def stores_near(zip_code, limit=6, log=print):
    """Brandify locator → [storeCode] near a zip (skips warehouses/non-retail)."""
    body = {"request": {"appkey": LOCATOR_KEY, "formdata": {
        "geoip": False, "dataview": "store_default", "limit": limit,
        "geolocs": {"geoloc": [{"addressline": zip_code, "country": "US", "latitude": "", "longitude": ""}]},
        "searchradius": "100", "where": {"warehouse": {"distinctfrom": "1"}}, "false": "0"}}}
    try:
        j = _post(LOCATOR, body, {"Content-Type": "application/json;charset=UTF-8", "User-Agent": UA,
                                  "Origin": "https://www.traderjoes.com", "Referer": "https://www.traderjoes.com/"})
        return [str(c.get("clientkey")) for c in ((j.get("response") or {}).get("collection") or []) if c.get("clientkey")]
    except Exception as e:
        log("[tj] locator %s: %s" % (zip_code, str(e)[:80])); return []


def _is_alcohol(item):
    return any((c.get("name") or "").strip().lower() == ALCOHOL_CAT for c in (item.get("category_hierarchy") or []))


def _flatten(item, store):
    pr = (((item.get("price_range") or {}).get("minimum_price") or {}).get("final_price") or {}).get("value")
    return {"store_id": str(store), "sku": str(item.get("sku") or ""), "name": item.get("item_title") or "",
            "price": pr if pr is not None else _num(item.get("retail_price")),
            "retail_price": _num(item.get("retail_price")),
            "size": item.get("sales_size"), "uom": item.get("sales_uom_description") or "",
            "country_of_origin": item.get("country_of_origin") or "",
            "in_stock": str(item.get("availability") or "") == "1", "source": "trader_joes"}


def _num(v):
    try:
        return float(str(v).replace("$", "").replace(",", ""))
    except Exception:
        return None


def crawl_store(store, terms=None, max_pages=40, delay=0.4, log=print):
    terms = terms or TERMS
    seen, rows = set(), []
    for term in terms:
        page = 1
        while page <= max_pages:
            body = {"operationName": "SearchProducts",
                    "variables": {"storeCode": str(store), "availability": "1", "published": "1",
                                  "search": term, "currentPage": page, "pageSize": 60},
                    "query": _SEARCH_QUERY}
            try:
                j = _post(GQL, body, {"Content-Type": "application/json", "Accept": "*/*", "User-Agent": UA,
                                      "Origin": "https://www.traderjoes.com",
                                      "Referer": "https://www.traderjoes.com/home/search?q=%s" % term})
            except Exception as e:
                log("[tj] %s '%s' p%d: %s" % (store, term, page, str(e)[:70])); break
            prod = (j.get("data") or {}).get("products") or {}
            items = prod.get("items") or []
            for it in items:
                if not _is_alcohol(it):
                    continue
                k = it.get("sku")
                if k and k not in seen:
                    seen.add(k); rows.append(_flatten(it, store))
            pi = prod.get("page_info") or {}
            if page >= (pi.get("total_pages") or 1):
                break
            page += 1
            time.sleep(delay)
        log("[tj] store %s '%s': %d alcohol items so far" % (store, term, len(rows)))
    return rows


_FIELDS = ["store_id", "sku", "name", "price", "retail_price", "size", "uom", "country_of_origin",
           "in_stock", "source"]


def pull(stores=None, terms=None, seed_zips=None, log=print):
    """Enumerate stores (or use `stores`), sweep each store's alcohol catalog → land trader_joes_products
    (sku|store) + observations. Returns count."""
    if not stores:
        stores, seen = [], set()
        for z in (seed_zips or SEED_ZIPS):
            for s in stores_near(z, log=log):
                if s not in seen:
                    seen.add(s); stores.append(s)
        log("[tj] %d stores enumerated across %d seed zips" % (len(stores), len(seed_zips or SEED_ZIPS)))
    all_rows = []
    for s in stores:
        rows = crawl_store(s, terms=terms, log=log)
        all_rows += rows
        log("[tj] store %s: %d alcohol items" % (s, len(rows)))
    if not all_rows:
        log("[tj] 0 rows — NOT writing"); return 0
    warehouse.write_accumulate("trader_joes_products", all_rows,
                               key=lambda r: "%s|%s" % (r.get("sku"), r.get("store_id")), fields=_FIELDS)
    try:
        observe.record("trader_joes", [{"source": "trader_joes", "store_id": r["store_id"],
                                        "store": "Trader Joe's %s" % r["store_id"], "product_id": r["sku"],
                                        "upc": "", "name": r["name"], "price": r["price"], "on_promo": False,
                                        "in_stock": bool(r["in_stock"]), "is_hemp": False}
                                       for r in all_rows if r.get("price") is not None], log=log)
    except Exception as e:
        log("[tj] observe skipped: %s" % str(e)[:80])
    log("[tj] landed %d alcohol items across %d store(s)" % (len(all_rows), len(stores)))
    return len(all_rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Trader Joe's bev-alc via the open storefront GraphQL.")
    ap.add_argument("--stores", default="", help="comma-separated TJ storeCodes (default: enumerate seed zips)")
    ap.add_argument("--terms", default="", help="comma-separated search terms (default: the alcohol sweep)")
    a = ap.parse_args(argv)
    stores = [x.strip() for x in a.stores.split(",") if x.strip()] or None
    terms = [t.strip() for t in a.terms.split(",") if t.strip()] or None
    n = pull(stores=stores, terms=terms)
    print("landed", n)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
