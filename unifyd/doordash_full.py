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
    root = "/convenience/store/%s/category/alcohol-1024" % store
    items, outlet, seen_cat, pages = {}, None, set(), 0
    queue = [root]
    while queue and pages < max_pages:
        path = queue.pop(0)
        if path in seen_cat:
            continue
        seen_cat.add(path); pages += 1
        try:
            html = dd._unlock("https://www.doordash.com" + path, key)
        except Exception as e:
            log("  cat %s failed: %s" % (path.split("/category/")[1][:24], str(e)[:40])); continue
        blob = dd._rsc(html)
        for it in dd._parse_items(blob):
            items.setdefault(it["name"], it)
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
    for term in dd.ALCOHOL_TERMS:
        try:
            for it in dd.search_store(store, term, key):
                items.setdefault(it["name"], it)
        except Exception:
            pass
        time.sleep(0.4)
    log("  [%s] + term-search union -> %d distinct items" % (store, len(items)))
    return list(items.values()), outlet


def run(chain, stores=None, log=print):
    cfg = dd.CHAINS.get(chain, {"name": chain, "stores": []})
    stores = stores or cfg["stores"]
    if not stores:
        log("[%s] no store ids" % chain); return None, 0
    key = dd._api_key()
    run_id = "%sfull-%s" % (chain, time.strftime("%Y%m%d-%H%M%S"))
    all_rows, outlets = [], []
    for store in stores:
        items, outlet = full_catalog(store, key, log=log)
        rows = [dict(it, store=str(store), store_id=str(store), product_id=it["name"][:90],
                     price_value=dd._price_val(it.get("price", "")), source=chain,
                     is_hemp=observe.is_hemp(it["name"]), run_id=run_id, **dd._parse_pack(it["name"]))
                for it in items]
        all_rows.extend(rows)
        if outlet:
            outlet["source"] = chain; outlets.append(outlet)
        log("  [%s] store %s — %d items (FULL catalog)" % (chain, store, len(rows)))
    if all_rows:
        warehouse.write_parquet(chain + "_products_full", all_rows)
        observe.record(chain, [dict(store=r["store"], store_id=r["store_id"], product_id=r["product_id"],
                                    brand="", name=r["name"], price=r.get("price_value"),
                                    in_stock=True, qty=None, is_hemp=r.get("is_hemp")) for r in all_rows])
    if outlets:
        warehouse.write_parquet(chain + "_outlets", outlets)
    log("[%s] FULL DONE %d items across %d stores -> %s_products_full" % (chain, len(all_rows), len(stores), chain))
    return run_id, len(all_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", required=True)
    ap.add_argument("--stores", default="")
    a = ap.parse_args()
    run(a.chain, stores=[s.strip() for s in a.stores.split(",") if s.strip()] or None)
