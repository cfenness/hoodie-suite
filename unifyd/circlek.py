"""circlek.py — Circle K beverage-alcohol via DoorDash convenience storefronts.

Circle K has no product catalog of its own (circlek.com is marketing); its per-store bev-alc menu
(beer / wine / spirits / seltzers + price) lives on DoorDash. DoorDash convenience is a Next.js RSC app
behind Forter + a 21+ age gate — the Bright Data Unlocker defeats both, and the SEARCH pages
SERVER-RENDER the item list into the RSC payload (no login, no headless browser needed per store).

We fetch /convenience/store/<id>/search/<term> for each alcohol term, parse the item `quality_labels`
(item_name + item_price + image) out of the RSC, dedup per store, and land circlek_products +
dated retail_observations (store, product, price). Store ids are DoorDash convenience store ids.

    python circlek.py                          # seed stores x alcohol terms
    python circlek.py --stores 1696295,1695349 --terms "beer,wine"
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

# alcohol coverage — one search per term (mirrors Target's term-list approach); dedup collapses overlap
ALCOHOL_TERMS = ["beer", "wine", "seltzer", "liquor", "vodka", "whiskey", "tequila", "rum",
                 "malt beverage", "hard cider", "champagne", "canned cocktail", "wine spritzer"]
DEFAULT_STORES = ["1696295", "1695349"]        # Circle K DoorDash convenience store ids (seed; expandable)
_ITEM_RE = re.compile(r'"(?:quality_labels|accessibility_labels)":\[(.*?)\]', re.S)  # DoorDash ships both variants
_LBL_RE = re.compile(r'"label":"(\w+)","priority_level":"[^"]*","value":"((?:[^"\\]|\\.)*)"')


def _api_key():
    k = os.environ.get("BRIGHTDATA_API_KEY")
    if k:
        return k.strip()
    return json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]


def _unlock(url, key, retries=2):
    body = json.dumps({"zone": "cli_unlocker", "url": url, "format": "raw"}).encode()
    last = ""
    for a in range(retries + 1):
        try:
            req = urllib.request.Request("https://api.brightdata.com/request", data=body,
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            last = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
            if "__next_f" in last:
                return last
        except Exception:
            pass
        time.sleep(2 + a * 2)
    return last


def _rsc(html):
    """Concatenate + unescape the Next.js RSC streamed payload (where the item list lives)."""
    pays = re.findall(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)', html)
    return "".join(pays).encode().decode("unicode_escape", "ignore")


def _parse_items(blob):
    out = []
    for m in _ITEM_RE.finditer(blob):
        labels = dict(_LBL_RE.findall(m.group(1)))
        name = labels.get("item_name")
        if not name:
            continue
        img = ""
        im = re.search(r'"image":\{"remote":\{"uri":"([^"\\]+)"', blob[m.end():m.end() + 500])
        if im:
            img = im.group(1)
        out.append({"name": name, "price": labels.get("item_price", ""), "image_url": img})
    return out


def search_store(store, term, key):
    url = "https://www.doordash.com/convenience/store/%s/search/%s?attr_src=home" % (store, urllib.parse.quote(term))
    return _parse_items(_rsc(_unlock(url, key)))


def _price_val(p):
    if p and p.startswith("$"):
        try:
            return float(p[1:].replace(",", ""))
        except ValueError:
            return None
    return None


def run(stores=None, terms=None, log=print):
    stores = stores or DEFAULT_STORES
    terms = terms or ALCOHOL_TERMS
    key = _api_key()
    run_id = "ck-" + time.strftime("%Y%m%d-%H%M%S")
    all_rows = []
    for store in stores:
        seen = {}
        for term in terms:
            try:
                items = search_store(store, term, key)
            except Exception as e:
                log("  %s/%s failed: %s" % (store, term, str(e)[:60])); continue
            for it in items:
                if it["name"] not in seen:
                    seen[it["name"]] = dict(it, price_value=_price_val(it["price"]))
            time.sleep(0.6)
        rows = [dict(v, store=store, store_id=store, product_id=v["name"][:90], source="circlek",
                     is_hemp=observe.is_hemp(v["name"]), run_id=run_id) for v in seen.values()]
        all_rows.extend(rows)
        log("  [circlek] store %s — %d bev-alc items" % (store, len(rows)))
    if all_rows:
        warehouse.write_parquet("circlek_products", all_rows)
        observe.record("circlek", [dict(store=r["store"], store_id=r["store_id"], product_id=r["product_id"],
                                        brand="", name=r["name"], price=r.get("price_value"),
                                        in_stock=True, qty=None, is_hemp=r.get("is_hemp")) for r in all_rows])
        log("[circlek] DONE %d items across %d stores -> circlek_products + retail_observations"
            % (len(all_rows), len(stores)))
    return run_id, len(all_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default="")
    ap.add_argument("--terms", default="")
    a = ap.parse_args()
    run(stores=[s.strip() for s in a.stores.split(",") if s.strip()] or None,
        terms=[t.strip() for t in a.terms.split(",") if t.strip()] or None)
