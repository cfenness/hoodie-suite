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
    # Cloud-runnable: prefer the env var (Fly / GitHub Actions secret), fall back to the local BD CLI
    # credentials file (macOS dev box). Lets the scheduled cloud runner pull Target without ~/Library.
    k = os.environ.get("BRIGHTDATA_API_KEY")
    if k:
        return k.strip()
    for p in ("~/Library/Application Support/brightdata-cli/credentials.json",   # macOS
              "~/.config/brightdata-cli/credentials.json"):                       # linux
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return json.load(open(p))["api_key"]
    raise RuntimeError("no Bright Data API key (set BRIGHTDATA_API_KEY or run `bdata login`)")


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


# ~90 metro zips spread across Target's footprint — nearby_stores(within=75mi) off these + dedup covers
# most of the ~1,950 stores. The result (target_stores) is the spine for per-store inventory by state.
STORE_ZIPS = [
    "35203", "85004", "85701", "72201", "90012", "92101", "94103", "95814", "93721", "92801", "80202",
    "80903", "06103", "19801", "20001", "33139", "32801", "33602", "32202", "33401", "30303", "31401",
    "96813", "83702", "60601", "62701", "46204", "50309", "66603", "40202", "70112", "04101", "21201",
    "02108", "48226", "49503", "55401", "55901", "39201", "63101", "64106", "59601", "68102", "89101",
    "89501", "03301", "07102", "87102", "10001", "14202", "13202", "28202", "27601", "58102", "44113",
    "43215", "45202", "73102", "97204", "19103", "15222", "02903", "29201", "29401", "57104", "37203",
    "38103", "77002", "75201", "78205", "79901", "76102", "84101", "05401", "23219", "22201", "98101",
    "99201", "25301", "53202", "53703", "82001"]


def nearby_stores(zipc, api_key, within=100, limit=20):   # RedSky caps limit at 20
    q = {"key": SEARCH_KEY, "limit": limit, "within": within, "place": zipc, "channel": "WEB"}
    d = json.loads(_unlock("%s/nearby_stores_v1?%s" % (REDSKY, urllib.parse.urlencode(q)), api_key))
    out = []
    for s in ((((d.get("data") or {}).get("nearby_stores") or {}).get("stores")) or []):
        if s.get("is_test_location"):
            continue
        a = s.get("mailing_address") or {}; g = s.get("geographic_specifications") or {}
        out.append(dict(store_id=str(s.get("store_id", "")), name=s.get("location_name", ""),
                        city=a.get("city", ""), state=a.get("region", ""), zip=a.get("postal_code", ""),
                        address=a.get("address_line1", ""), phone=s.get("main_voice_phone_number", ""),
                        lat=g.get("latitude"), lon=g.get("longitude")))
    return out


def enumerate_stores(zips=None, log=print):
    """Enumerate Target stores nationwide via the store locator; land target_stores (dedup by store_id)."""
    key = _api_key(); zips = zips or STORE_ZIPS
    seen = {}
    for z in zips:
        try:
            for s in nearby_stores(z, key):
                if s["store_id"]:
                    seen[s["store_id"]] = s
        except Exception as e:
            log("  store zip %s failed: %s" % (z, str(e)[:60]))
        time.sleep(0.7)
    rows = list(seen.values())
    from collections import Counter
    by_state = Counter(r["state"] for r in rows)
    warehouse.write_parquet("target_stores", rows)
    log("[target] %d distinct stores across %d states -> target_stores (top: %s)"
        % (len(rows), len(by_state), dict(by_state.most_common(6))))
    return len(rows)


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


def catalog(seeds, terms, api_key, pages=2, log=print):
    """Discover the bev-alc product catalog (tcin->product) once, from a few seed stores across regions
    (Target's assortment + price is largely national). Cheap; the per-store part is inventory."""
    prods = {}
    for (store, zipc, state) in seeds:
        for term in terms:
            for pg in range(pages):
                try:
                    for p in plp_search(term, store, zipc, api_key, offset=pg * 28):
                        if p["tcin"]:
                            prods.setdefault(p["tcin"], dict(p, source="target"))
                except Exception:
                    pass
                time.sleep(0.6)
    log("  [target] catalog: %d distinct bev-alc products" % len(prods))
    return prods


def run_national(log=print, workers=12, batch=24, limit=None):
    """FULL NATIONAL: national catalog once, then per-store inventory across every target_stores store,
    concurrently. Lands target_products (catalog) + retail_observations (dated per-store inventory).
    Incremental (re-writes the growing observation partition every ~100 stores) so it's restart-safe.
    `limit` caps to a state-spread SAMPLE of stores — the cheap daily cadence (full national = limit=None)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    key = _api_key()
    seeds = [("2259", "20001", "DC"), ("3269", "33139", "FL"), ("2088", "90013", "CA"),
             ("1375", "55401", "MN"), ("77", "77002", "TX")]
    prods = catalog(seeds, DEFAULT_TERMS, key, log=log)
    tcins = [t for t in prods if t]
    if not tcins:
        log("  [target] no catalog — aborting"); return 0
    warehouse.write_parquet("target_products", [prods[t] for t in tcins])
    stores = warehouse.query("target_stores", "SELECT store_id, zip, state FROM t")
    if limit and len(stores) > limit:
        buckets = {}                                   # round-robin by state → even national spread, cheap
        for s in sorted(stores, key=lambda r: (r["state"], str(r["store_id"]))):
            buckets.setdefault(s["state"], []).append(s)
        picked, order, row = [], list(buckets.values()), 0
        while len(picked) < limit:
            progressed = False
            for b in order:
                if row < len(b):
                    picked.append(b[row]); progressed = True
                    if len(picked) >= limit:
                        break
            if not progressed:
                break
            row += 1
        stores = picked
        log("  [target] SAMPLE %d stores across %d states (daily cadence; full = --national)"
            % (len(stores), len(buckets)))
    log("  [target] national inventory: %d products x %d stores" % (len(tcins), len(stores)))
    obs, lock, done = [], threading.Lock(), [0]
    abort = threading.Event()        # trip on systemic failure (credits exhausted / IP-blocked)
    fails_in_row = [0]               # consecutive fully-failed stores
    ABORT_AFTER = 20

    def _one(st):
        if abort.is_set():
            return
        store, zipc, state = str(st["store_id"]), st["zip"], st["state"]
        rows, ok_batches = [], 0
        for j in range(0, len(tcins), batch):
            if abort.is_set():
                return
            b = tcins[j:j + batch]
            try:
                qty = fulfillment_qty(b, store, zipc, state, key)
            except Exception:
                continue             # call failed — SKIP (never record a false zero for a dead call)
            ok_batches += 1
            for tc in b:
                q = qty.get(tc); p = prods[tc]
                rows.append(dict(store=store, store_id=store, product_id=tc, brand=p["brand"],
                                 name=p["name"], price=p["price"], on_promo=False,
                                 in_stock=bool(q and q > 0), qty=q, is_hemp=p["is_hemp"]))
        with lock:
            if ok_batches == 0:      # every batch for this store failed → systemic, not a real empty
                fails_in_row[0] += 1
                if fails_in_row[0] >= ABORT_AFTER and not abort.is_set():
                    abort.set()
                    log("  [target] ABORT — %d stores in a row fully failed (credits exhausted / blocked?). "
                        "Stopping so we don't poison the warehouse with false zeros. %d obs kept from %d stores."
                        % (fails_in_row[0], len(obs), done[0]))
                return
            fails_in_row[0] = 0
            obs.extend(rows); done[0] += 1
            if done[0] % 100 == 0:
                observe.record("target", obs, log=lambda *a: None)   # incremental, restart-safe
                log("  [target] %d/%d stores · %d observations" % (done[0], len(stores), len(obs)))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_one, stores))
    if obs:
        observe.record("target", obs, log=log)
    tag = "ABORTED (partial — see warning above)" if abort.is_set() else "DONE"
    log("[target] NATIONAL %s: %d products x %d stores covered = %d per-store observations -> warehouse"
        % (tag, len(tcins), done[0], len(obs)))
    return len(obs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--national", action="store_true", help="full national per-store inventory sweep")
    ap.add_argument("--terms", default="")
    ap.add_argument("--stores", default="", help="store:zip:state,comma-separated")
    ap.add_argument("--pages", type=int, default=2)
    a = ap.parse_args()
    if a.national:
        run_national()
        return
    terms = [t.strip() for t in a.terms.split(",") if t.strip()] or None
    stores = [tuple(s.split(":")) for s in a.stores.split(",") if s.count(":") == 2] or None
    run(stores=stores, terms=terms, pages=a.pages)


if __name__ == "__main__":
    main()
