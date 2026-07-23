#!/usr/bin/env python3
"""meijer.py — Meijer bev-alc catalog via the OPEN storefront GraphQL (digital.meijer.com/graphql).

No auth, no cookie, no anti-bot: the `productSearch` query is public — POST with the header
`x-meijer-gql-query: search` and a storeId, and it returns per-store product records with UPC,
alcohol flag, price (base/customer/promo), and inventory stockStatus. Validated 2026-07-23:
`wine` @ store 265 → 2415 items, 200 OK, no token.

We crawl the alcohol terms per store, page through the cursor connection, keep only isAlcohol
items, and land `meijer_products` (keyed upc|storeId) + the retail_observations time-series.
Stdlib-only (urllib); curl_cffi (Chrome-JA3) is used when present but the endpoint is open either way.

    python meijer.py --stores 265 --terms wine,beer,spirits
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

GQL = "https://digital.meijer.com/graphql/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/150.0.0.0 Safari/537.36")
# alcohol search terms — the storefront has no single "alcohol" filter exposed here, so we sweep the
# category's common terms and dedupe by (upc, store). isAlcohol gates out non-alc that a term drags in.
TERMS = ["wine", "beer", "spirits", "liquor", "whiskey", "bourbon", "tequila", "vodka", "rum", "gin",
         "seltzer", "champagne", "cognac", "brandy", "scotch", "hard cider"]
# Meijer store numbers (a starter set spanning the footprint; extend via a store-locator later).
DEFAULT_STORES = [265]

_QUERY = """query ProductSearchQuery($args: SearchArgsInput!, $context: ProductQueryContextInput!,
  $storeId: Int!, $includeThirdParty: Boolean!, $first: Int, $after: String, $sort: ProductSortInput,
  $filters: [ProductFilterInput!], $userIdentity: UserIdentityInput) {
  productSearch(args: $args, context: $context, filters: $filters, sort: $sort, userIdentity: $userIdentity) {
    __typename
    ... on ProductsResult {
      resultCounts { totalCount }
      itemConnection(first: $first, after: $after) {
        items { __typename ... on ProductExtended {
          productName upc isAlcohol productId soldByUnitDescription unitOfMeasureQuantity
          storeSpecificProductDetails(storeId: $storeId) {
            productStore { basePrice customerPrice isOnSale promotionPrice priceText savingsDescription }
            productStoreInventory { stockStatus }
            promotions { displayText } }
          thirdPartyOffers @include(if: $includeThirdParty) { sellerName }
        } }
        totalCount
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}"""


def _post(store_id, term, after=None, first=52, timeout=30):
    """One page of productSearch for a term at a store. Returns the parsed JSON (or raises)."""
    import uuid
    body = {
        "query": _QUERY,
        "variables": {
            "args": {"searchType": "SearchTerm", "searchValue": term, "includeThirdParty": False,
                     "adUnitId": str(uuid.uuid4())},
            "context": {"storeId": store_id, "constructorSessionId": 1, "userSegments": ["web"]},
            "userIdentity": {"constructorClientId": str(uuid.uuid4())},
            "storeId": store_id, "includeThirdParty": False,
            "sort": {"by": "relevance", "order": "descending"}, "filters": [],
            "first": first, "after": after},
    }
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "*/*", "User-Agent": UA,
               "Origin": "https://www.meijer.com", "Referer": "https://www.meijer.com/",
               "x-meijer-gql-query": "search"}
    try:                                        # curl_cffi (Chrome JA3) if available; endpoint is open regardless
        from curl_cffi import requests as _cr
        r = _cr.post(GQL, data=data, headers=headers, impersonate="chrome", timeout=timeout)
        return json.loads(r.text)
    except ImportError:
        req = urllib.request.Request(GQL, data=data, headers=headers)
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))


def _flatten(item, store_id):
    """One ProductExtended → a flat meijer_products record (alcohol only)."""
    if not item or not item.get("isAlcohol"):
        return None
    sd = item.get("storeSpecificProductDetails") or {}
    ps = sd.get("productStore") or {}
    inv = sd.get("productStoreInventory") or {}
    promos = "; ".join(p.get("displayText", "") for p in (sd.get("promotions") or []) if p.get("displayText"))
    return {"store_id": str(store_id), "product_id": str(item.get("productId") or ""),
            "upc": str(item.get("upc") or ""), "name": item.get("productName") or "",
            "size": item.get("unitOfMeasureQuantity"), "uom": item.get("soldByUnitDescription") or "",
            "base_price": ps.get("basePrice"), "price": ps.get("customerPrice"),
            "promo_price": ps.get("promotionPrice"), "on_sale": bool(ps.get("isOnSale")),
            "price_text": ps.get("priceText") or "", "savings": ps.get("savingsDescription") or "",
            "promo": promos, "stock_status": (inv.get("stockStatus") or ""),
            "in_stock": (inv.get("stockStatus") or "").upper() in ("IN_STOCK", "INSTOCK", "LOW_STOCK", ""),
            "source": "meijer"}


def crawl_store(store_id, terms=None, max_pages=40, delay=0.4, log=print):
    """All alcohol items for one store across the terms, deduped by (upc|product_id)."""
    terms = terms or TERMS
    seen, rows = set(), []
    for term in terms:
        after, pages = None, 0
        while pages < max_pages:
            try:
                j = _post(store_id, term, after=after)
            except Exception as e:
                log("  [meijer] %s '%s' page %d: %s" % (store_id, term, pages, str(e)[:80])); break
            ps = ((j.get("data") or {}).get("productSearch") or {})
            conn = ps.get("itemConnection") or {}
            items = conn.get("items") or []
            for it in items:
                r = _flatten(it, store_id)
                if not r:
                    continue
                k = r["upc"] or r["product_id"]
                if k and k not in seen:
                    seen.add(k); rows.append(r)
            pi = conn.get("pageInfo") or {}
            pages += 1
            if not pi.get("hasNextPage"):
                break
            after = pi.get("endCursor")
            time.sleep(delay)
        log("  [meijer] store %s '%s': %d unique alcohol items so far" % (store_id, term, len(rows)))
    return rows


_FIELDS = ["store_id", "product_id", "upc", "name", "size", "uom", "base_price", "price", "promo_price",
           "on_sale", "price_text", "savings", "promo", "stock_status", "in_stock", "source"]


def pull(stores=None, terms=None, log=print):
    """Crawl each store's alcohol catalog → land meijer_products (upc|store) + observations. Returns count."""
    stores = stores or DEFAULT_STORES
    all_rows = []
    for s in stores:
        rows = crawl_store(s, terms=terms, log=log)
        all_rows += rows
        log("[meijer] store %s: %d alcohol items" % (s, len(rows)))
    if not all_rows:                                   # never clobber a good catalog with an empty crawl
        log("[meijer] 0 rows — NOT writing"); return 0
    warehouse.write_accumulate("meijer_products", all_rows,
                               key=lambda r: "%s|%s" % (r.get("upc") or r.get("product_id"), r.get("store_id")),
                               fields=_FIELDS)
    try:
        observe.record("meijer", [{"source": "meijer", "store_id": r["store_id"], "store": "Meijer %s" % r["store_id"],
                                   "product_id": r["product_id"], "upc": r["upc"], "name": r["name"],
                                   "price": r["price"], "on_promo": bool(r["on_sale"]), "in_stock": bool(r["in_stock"]),
                                   "stock_level": r["stock_status"], "is_hemp": False}
                                  for r in all_rows if r.get("price") is not None], log=log)
    except Exception as e:
        log("[meijer] observe skipped: %s" % str(e)[:80])
    log("[meijer] landed %d alcohol items across %d store(s)" % (len(all_rows), len(stores)))
    return len(all_rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Meijer bev-alc catalog via the open storefront GraphQL.")
    ap.add_argument("--stores", default="", help="comma-separated Meijer store numbers (default 265)")
    ap.add_argument("--terms", default="", help="comma-separated search terms (default: the alcohol sweep)")
    a = ap.parse_args(argv)
    stores = [int(x) for x in a.stores.split(",") if x.strip()] or None
    terms = [t.strip() for t in a.terms.split(",") if t.strip()] or None
    n = pull(stores=stores, terms=terms)
    print("landed", n)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
