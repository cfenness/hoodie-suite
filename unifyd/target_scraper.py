"""target_scraper.py — Target bev-alc via the RedSky API, through the ban-safe Bright Data Unlocker.

Target has no login/age gate on product data, a clean JSON API, and — unusually — a REAL numeric per-store
inventory count. Two RedSky calls per term:
  • plp_search_v2                     -> products: tcin, name, price, brand, image (discovery + price)
  • product_summary_with_fulfillment  -> per-store location_available_to_promise_quantity (numeric inventory)
Fetched through the BD Unlocker (POST api.brightdata.com/request, format=raw) which defeats Target's anti-bot
and rotates IPs (Tier-5 sanctioned/managed path). Lands target_products + retail_observations, with image +
is_hemp. Store ids from the store-locator; a few markets to start.

    python target_scraper.py                      # default terms x stores
    python target_scraper.py --terms "wine,beer"  --stores "2259:20001:DC"
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

REDSKY = "https://redsky.target.com/redsky_aggregations/v1/web"
SEARCH_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"     # Target's public web search key
# (store_id, zip, state) — Target markets. Store 2259 = DC/Columbia Heights (validated).
DEFAULT_STORES = [("2259", "20001", "DC"), ("1234", "33139", "FL"), ("2088", "90013", "CA")]
DEFAULT_TERMS = ["red wine", "white wine", "cabernet sauvignon", "pinot noir", "chardonnay", "champagne",
                 "prosecco", "rose wine", "sauvignon blanc", "moscato", "beer", "ipa", "lager",
                 "mexican beer", "hard seltzer", "hard cider", "canned cocktail", "hemp drink", "thc seltzer"]


def _api_key():
    return json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]


def _unlock(url, api_key, retries=2):
    """Fetch a URL via the Bright Data Unlocker (raw) — past Target's anti-bot, rotating IPs. The Unlocker
    occasionally returns a non-JSON body (challenge/empty); retry until it looks like JSON."""
    body = json.dumps({"zone": "cli_unlocker", "url": url, "format": "raw"}).encode()
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request("https://api.brightdata.com/request", data=body,
                                     headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
        try:
            last = urllib.request.urlopen(req, timeout=70).read().decode("utf-8", "replace")
            if last.lstrip()[:1] in "{[":
                return last
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    return last


def _img(item):
    en = (item.get("enrichment") or {}).get("images") or {}
    return en.get("primary_image_url") or ""


def plp_search(term, store, zipc, api_key, offset=0, count=28):
    q = {"key": SEARCH_KEY, "channel": "WEB", "count": count, "default_purchasability_filter": "true",
         "keyword": term, "offset": offset, "page": "/s/" + term, "platform": "desktop",
         "pricing_store_id": store, "store_ids": store, "visitor_id": "0193", "zip": zipc}
    d = json.loads(_unlock("%s/plp_search_v2?%s" % (REDSKY, urllib.parse.urlencode(q)), api_key))
    prods = (((d.get("data") or {}).get("search") or {}).get("products")) or []
    out = []
    for p in prods:
        it = p.get("item") or {}; pr = p.get("price") or {}
        name = (it.get("product_description") or {}).get("title", "")
        out.append(dict(tcin=str(p.get("tcin", "")), name=name,
                        brand=(it.get("primary_brand") or {}).get("name", ""),
                        price=pr.get("current_retail"), promo=pr.get("current_retail_min"),
                        image_url=_img(it), category=(it.get("product_classification") or {}).get("product_type_name", ""),
                        is_hemp=observe.is_hemp((it.get("primary_brand") or {}).get("name"), name)))
    return out


def fulfillment_qty(tcins, store, zipc, state, api_key):
    """{tcin: available_to_promise_quantity} for a store, from product_summary_with_fulfillment."""
    q = {"key": SEARCH_KEY, "tcins": ",".join(tcins), "store_id": store, "pricing_store_id": store,
         "zip": zipc, "state": state, "channel": "WEB"}
    d = json.loads(_unlock("%s/product_summary_with_fulfillment_v1?%s" % (REDSKY, urllib.parse.urlencode(q)), api_key))
    out = {}
    for p in ((d.get("data") or {}).get("product_summaries") or []):
        so = (p.get("fulfillment") or {}).get("store_options") or []
        qty = so[0].get("location_available_to_promise_quantity") if so else None
        out[str(p.get("tcin"))] = qty
    return out


def run(stores=None, terms=None, pages=2, log=print):
    stores = stores or DEFAULT_STORES
    terms = terms or DEFAULT_TERMS
    key = _api_key()
    run_id = "tg-" + time.strftime("%Y%m%d-%H%M%S")
    rows, seen = [], set()
    for (store, zipc, state) in stores:
        got = 0
        for term in terms:
            for pg in range(pages):
                try:
                    prods = plp_search(term, store, zipc, key, offset=pg * 28)
                except Exception as e:
                    log("  plp_search(%s@%s) failed: %s" % (term, store, str(e)[:70])); break
                if not prods:
                    break
                try:
                    qty = fulfillment_qty([p["tcin"] for p in prods if p["tcin"]], store, zipc, state, key)
                except Exception:
                    qty = {}
                for p in prods:
                    k = (p["tcin"], store)
                    if k in seen or not p["tcin"]:
                        continue
                    seen.add(k)
                    q = qty.get(p["tcin"])
                    rows.append(dict(p, store=store, store_id=store, state=state, zip=zipc,
                                     qty=q, in_stock=bool(q and q > 0), on_promo=False,
                                     product_id=p["tcin"], source="target", run_id=run_id))
                    got += 1
                time.sleep(1.0)
        log("  [target] store %s (%s) — %d products" % (store, state, got))
    if rows:
        warehouse.write_parquet("target_products", rows)
        observe.record("target", [dict(store=r["store"], store_id=r["store_id"], product_id=r["tcin"],
                                        brand=r["brand"], name=r["name"], price=r["price"],
                                        in_stock=r["in_stock"], qty=r["qty"], is_hemp=r.get("is_hemp")) for r in rows])
        log("[target] DONE %d products across %d stores -> warehouse" % (len(rows), len({r["store"] for r in rows})))
    return run_id, len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terms", default="")
    ap.add_argument("--stores", default="", help="store:zip:state,comma-separated")
    ap.add_argument("--pages", type=int, default=2)
    a = ap.parse_args()
    terms = [t.strip() for t in a.terms.split(",") if t.strip()] or None
    stores = [tuple(s.split(":")) for s in a.stores.split(",") if s.count(":") == 2] or None
    run(stores=stores, terms=terms, pages=a.pages)


if __name__ == "__main__":
    main()
