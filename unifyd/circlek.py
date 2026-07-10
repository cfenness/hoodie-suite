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
    """Concatenate + unescape the Next.js RSC streamed payload (where the item list lives). We unescape
    the JS string escapes by hand (\\uXXXX, \\\", \\/, …) rather than codecs.unicode_escape, which mangles
    multi-byte UTF-8 (it was turning the '×' in pack sizes into 'Ã—')."""
    s = "".join(re.findall(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)', html))
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    return (s.replace('\\\\', '\x00').replace('\\"', '"').replace('\\/', '/')
             .replace('\\n', '\n').replace('\\t', '\t').replace('\x00', '\\'))


# Product-name convention: "<brand> <style> <Cans|Bottles> (<size> <uom> [x <count> ct])"
# e.g. "Budweiser Lager Cans (12 fl oz x 12 ct)" / "Sutter Home Pinot Grigio (187 ml × 4 ct)" / "(750 ml)".
_SIZE_RE = re.compile(r'\(([\d.]+)\s*(fl oz|oz|ml|mL|L|liter|litre)\s*(?:[x×*]\s*(\d+)\s*ct)?\s*\)', re.I)
_CONT_RE = re.compile(r'\b(Cans?|Bottles?|Carton|Tetra|Box|Keg|Pouch|Growler)\b', re.I)


def _parse_pack(name):
    """Isolate unit size, package config, and units-per-pack from the item name. Beer configuration =
    Can/Bottle; 187ml minis and multipacks get their ct count too. total_ml = size × pack when in ml/oz."""
    out = {"container": "", "unit_size": None, "size_uom": "", "pack_count": 1, "total_size": None}
    cm = _CONT_RE.search(name)
    if cm:
        out["container"] = cm.group(1).rstrip("sS").title()          # Can / Bottle / Tetra …
    sm = _SIZE_RE.search(name)
    if sm:
        try:
            out["unit_size"] = float(sm.group(1))
        except ValueError:
            pass
        out["size_uom"] = sm.group(2).lower().replace("litre", "l").replace("liter", "l")
        out["pack_count"] = int(sm.group(3)) if sm.group(3) else 1
        if out["unit_size"] is not None:
            out["total_size"] = round(out["unit_size"] * out["pack_count"], 2)
    return out


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
        out.append(dict(name=name, price=labels.get("item_price", ""), image_url=img, **_parse_pack(name)))
    return out


def _parse_outlet(html, store_id):
    """The DoorDash store page carries the physical Circle K location (schema.org LocalBusiness) —
    capture it as an outlet while we're here."""
    def g(pat, d=""):
        mm = re.search(pat, html)
        return mm.group(1) if mm else d
    street = g(r'"streetAddress":"([^"]*)"') or g(r'Circle K \(([^)]+)\)')
    return dict(store_id=str(store_id), source="circlek", chain="Circle K",
                street=street, city=g(r'"addressLocality":"([^"]*)"'),
                state=g(r'"addressRegion":"([^"]*)"'), zip=g(r'"postalCode":"([^"]*)"'),
                lat=g(r'"latitude":([-\d.]+)'), lon=g(r'"longitude":([-\d.]+)'))


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
    all_rows, outlets = [], []
    for store in stores:
        seen, outlet = {}, None
        for term in terms:
            url = "https://www.doordash.com/convenience/store/%s/search/%s?attr_src=home" % (store, urllib.parse.quote(term))
            try:
                html = _unlock(url, key)
                items = _parse_items(_rsc(html))
            except Exception as e:
                log("  %s/%s failed: %s" % (store, term, str(e)[:60])); continue
            if outlet is None and ('"latitude"' in html or "Circle K (" in html):   # capture the outlet once
                outlet = _parse_outlet(html, store)
            for it in items:
                if it["name"] not in seen:
                    seen[it["name"]] = dict(it, price_value=_price_val(it["price"]))
            time.sleep(0.6)
        if outlet:
            outlets.append(outlet)
        rows = [dict(v, store=store, store_id=store, product_id=v["name"][:90], source="circlek",
                     is_hemp=observe.is_hemp(v["name"]), run_id=run_id) for v in seen.values()]
        all_rows.extend(rows)
        loc = ("%s, %s" % (outlet["city"], outlet["state"])) if outlet and outlet.get("state") else "?"
        log("  [circlek] store %s (%s) — %d bev-alc items" % (store, loc, len(rows)))
    if all_rows:
        warehouse.write_parquet("circlek_products", all_rows)
        observe.record("circlek", [dict(store=r["store"], store_id=r["store_id"], product_id=r["product_id"],
                                        brand="", name=r["name"], price=r.get("price_value"),
                                        in_stock=True, qty=None, is_hemp=r.get("is_hemp")) for r in all_rows])
    if outlets:
        warehouse.write_parquet("circlek_outlets", outlets)
    log("[circlek] DONE %d items + %d outlets across %d stores -> circlek_products / circlek_outlets / retail_observations"
        % (len(all_rows), len(outlets), len(stores)))
    return run_id, len(all_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default="")
    ap.add_argument("--terms", default="")
    a = ap.parse_args()
    run(stores=[s.strip() for s in a.stores.split(",") if s.strip()] or None,
        terms=[t.strip() for t in a.terms.split(",") if t.strip()] or None)
