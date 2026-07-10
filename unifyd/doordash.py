"""doordash.py — generic beverage-alcohol connector for ANY DoorDash storefront.

DoorDash convenience/retail stores are a Next.js RSC app behind Forter + a 21+ age gate. The Bright
Data Unlocker defeats both, and the /convenience/store/<id>/search/<term> pages SERVER-RENDER the item
list into the RSC payload — so one recipe pulls per-store bev-alc (name, price, pack/size/config) with
NO login and NO headless browser per store. It also captures the physical outlet (schema.org address +
geo) while it's there. Proven on Circle K; the same shape covers CVS, Total Wine, Albertsons, 7-Eleven,
Walgreens — each is just a CHAINS entry (chain name + DoorDash store ids).

    python doordash.py --chain cvs
    python doordash.py --chain totalwine --stores 1862062
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

ALCOHOL_TERMS = ["beer", "wine", "seltzer", "liquor", "vodka", "whiskey", "tequila", "rum",
                 "bourbon", "gin", "malt beverage", "hard cider", "champagne", "prosecco",
                 "canned cocktail", "wine spritzer"]

# chain key -> {name, stores[]}. Store ids are DoorDash store ids (discover via the site search by market).
CHAINS = {
    "circlek":   {"name": "Circle K",          "stores": ["1696295", "1695349"]},
    "cvs":       {"name": "CVS",               "stores": ["1235440"]},
    "totalwine": {"name": "Total Wine & More", "stores": ["1862062"]},
    "albertsons":{"name": "Albertsons",        "stores": ["1473954"]},   # Denver = Safeway banner
    "seveneleven":{"name": "7-Eleven",         "stores": []},
    "walgreens": {"name": "Walgreens",         "stores": []},
}

_ITEM_RE = re.compile(r'"(?:quality_labels|accessibility_labels)":\[(.*?)\]', re.S)   # DoorDash ships both
_LBL_RE = re.compile(r'"label":"(\w+)","priority_level":"[^"]*","value":"((?:[^"\\]|\\.)*)"')
_SIZE_RE = re.compile(r'\(([\d.]+)\s*(fl oz|oz|ml|mL|L|liter|litre)\s*(?:[x×*]\s*(\d+)\s*ct)?\s*\)', re.I)
_CONT_RE = re.compile(r'\b(Cans?|Bottles?|Carton|Tetra|Box|Keg|Pouch|Growler)\b', re.I)


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
    """Concatenate + hand-unescape the Next.js RSC streamed payload (unicode_escape mangles UTF-8 '×')."""
    s = "".join(re.findall(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)', html))
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    return (s.replace('\\\\', '\x00').replace('\\"', '"').replace('\\/', '/')
             .replace('\\n', '\n').replace('\\t', '\t').replace('\x00', '\\'))


def _parse_pack(name):
    out = {"container": "", "unit_size": None, "size_uom": "", "pack_count": 1, "total_size": None}
    cm = _CONT_RE.search(name)
    if cm:
        out["container"] = cm.group(1).rstrip("sS").title()
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
        im = re.search(r'"image":\{"remote":\{"uri":"([^"\\]+)"', blob[m.end():m.end() + 500])
        out.append(dict(name=name, price=labels.get("item_price", ""), image_url=im.group(1) if im else "",
                        **_parse_pack(name)))
    return out


def _parse_outlet(html, store_id, chain):
    def g(pat, d=""):
        mm = re.search(pat, html)
        return mm.group(1) if mm else d
    street = g(r'"streetAddress":"([^"]*)"') or g(re.escape(chain) + r' \(([^)]+)\)')
    return dict(store_id=str(store_id), source=chain, chain=chain, street=street,
                city=g(r'"addressLocality":"([^"]*)"'), state=g(r'"addressRegion":"([^"]*)"'),
                zip=g(r'"postalCode":"([^"]*)"'), lat=g(r'"latitude":([-\d.]+)'), lon=g(r'"longitude":([-\d.]+)'))


def _price_val(p):
    if p and p.startswith("$"):
        try:
            return float(p[1:].replace(",", ""))
        except ValueError:
            return None
    return None


def run(chain, stores=None, terms=None, log=print):
    cfg = CHAINS.get(chain, {"name": chain, "stores": []})
    stores = stores or cfg["stores"]
    if not stores:
        log("[%s] no store ids — discover them via the DoorDash site search first" % chain); return None, 0
    terms = terms or ALCOHOL_TERMS
    key = _api_key()
    run_id = "%s-%s" % (chain, time.strftime("%Y%m%d-%H%M%S"))
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
            if outlet is None and ('"latitude"' in html or (cfg["name"] + " (") in html):
                outlet = _parse_outlet(html, store, cfg["name"])
            for it in items:
                if it["name"] not in seen:
                    seen[it["name"]] = dict(it, price_value=_price_val(it["price"]))
            time.sleep(0.6)
        if outlet:
            outlets.append(outlet)
        rows = [dict(v, store=store, store_id=store, product_id=v["name"][:90], source=chain,
                     is_hemp=observe.is_hemp(v["name"]), run_id=run_id) for v in seen.values()]
        all_rows.extend(rows)
        loc = ("%s, %s" % (outlet["city"], outlet["state"])) if outlet and outlet.get("state") else "?"
        log("  [%s] store %s (%s) — %d bev-alc items" % (chain, store, loc, len(rows)))
    if all_rows:
        warehouse.write_parquet(chain + "_products", all_rows)
        observe.record(chain, [dict(store=r["store"], store_id=r["store_id"], product_id=r["product_id"],
                                    brand="", name=r["name"], price=r.get("price_value"),
                                    in_stock=True, qty=None, is_hemp=r.get("is_hemp")) for r in all_rows])
    if outlets:
        warehouse.write_parquet(chain + "_outlets", outlets)
    log("[%s] DONE %d items + %d outlets across %d stores -> %s_products / %s_outlets / retail_observations"
        % (chain, len(all_rows), len(outlets), len(stores), chain, chain))
    return run_id, len(all_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", required=True, help="chain key: " + ", ".join(CHAINS))
    ap.add_argument("--stores", default="")
    ap.add_argument("--terms", default="")
    a = ap.parse_args()
    run(a.chain, stores=[s.strip() for s in a.stores.split(",") if s.strip()] or None,
        terms=[t.strip() for t in a.terms.split(",") if t.strip()] or None)
