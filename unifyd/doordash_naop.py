"""doordash_naop.py — on-premise (NAOP) beverage-alcohol from DoorDash RESTAURANT menus.

Restaurants carry per-item DESCRIPTIONS (cocktail components, the base brand poured) that retail lacks.
The menu is server-rendered as MenuPageItem objects (name, description, displayPrice) in the RSC payload —
Unlocker-fetchable, no browser, no account. We parse the whole menu, run every item through
cocktail_taxonomy.classify_beverage (which scans drinks AND desserts), keep the beverages (alcoholic +
mocktails), and land naop_beverages: account, item, category (cocktail/mocktail/beer/dessert), root/
sub-family/base_spirit or beer_style, and price — TAGGED delivery-inflated (restaurants mark up on DD).

    python doordash_naop.py --stores 122020        # Applebee's (Orlando)
"""
import argparse, html as _html, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import doordash as dd
import cocktail_taxonomy as ctx
import cuisine as cui

# seed on-premise accounts (DoorDash restaurant store ids) — expandable via the SERP/setLocation discovery
DEFAULT_STORES = ["122020"]                 # Applebee's Grill & Bar (Orlando)
_ITEM_RE = re.compile(r'\{"__typename":"MenuPageItem".*?(?=\{"__typename":"MenuPageItem"|\}\])', re.S)
# FOOD guard — spirit/beer words show up in food names ("Bourbon Street Steak", "Whisky Bacon Burger",
# beer-cheese fries, sour-cream nachos). Drop items that are clearly a dish, not a drink. (Boozy DESSERTS —
# bananas foster, bread pudding, tiramisu — are NOT in this list, so they still come through.)
_FOOD = re.compile(
    r"steak|burger|chicken|nacho|\bfries\b|quesadilla|sandwich|pasta|salad|wings?|\bdip\b|waffle|sausage|"
    r"shrimp|\bbacon\b|pizza|taco|\bbowl\b|wrap|\bribs\b|combo|platter|sampler|\bbites\b|tenders|mozzarella|"
    r"onion ring|salmon|\bfish\b|sirloin|fajita|burrito|enchilada|\bsub\b|\bmelt\b|hoagie|\bsoup\b|\bchili\b|"
    r"mac (?:and|&) cheese|quesa|flatbread|appetizer|\bentr[eé]e|\bwrap\b|dumpling|spring roll|potstick|"
    r"pretzel|\bdip\b|cheese bites|garlic bread|breadstick|calamari|guacamole|sliders?|"
    r"nigiri|sashimi|\bmaki\b|hand ?roll|\bpoke\b|\bdon\b|ceviche|\bgyoza\b|edamame|\bramen\b|udon|"
    r"katsu|teriyaki|tempura|\bcurry\b|\bpho\b|dumpling", re.I)   # sushi/Asian dishes ("Sake Nigiri" = salmon)


def parse_menu(blob):
    """Every MenuPageItem -> {name, description, price}. Price display is doubled ('$$12.69') → clean."""
    out = []
    for o in _ITEM_RE.findall(blob):
        nm = re.search(r'"name":"((?:[^"\\]|\\.)*)"', o)
        if not nm or not nm.group(1).strip():
            continue
        ds = re.search(r'"description":"((?:[^"\\]|\\.)*)"', o)
        pr = re.search(r'"displayPrice":"\$*([\d.]+)"', o)
        out.append({"name": nm.group(1).strip(), "description": (ds.group(1).strip() if ds else ""),
                    "price": (float(pr.group(1)) if pr else None)})
    return out


def _restaurant_name(html_text):
    t = (re.search(r"<title>Order (.+?) (?:Menu|- [A-Z])", html_text) or
         re.search(r"<title>([^<|]+)", html_text) or [None, ""])[1]
    return _html.unescape(t).split(" - ")[0].replace("Order ", "").strip()[:60]


def _due_stores(limit, log=print):
    """The next batch of DoorDash store-ids to menu-fetch, drawn from the doordash_stores UNIVERSE (harvested
    $0 by doordash_sitemap) minus the ones we've already captured (in naop_accounts). Each run advances
    national on-premise coverage a bounded chunk; accumulate over runs → full. Falls back to the seed store
    if the universe hasn't been harvested yet."""
    try:
        done = {str(r["store"]) for r in warehouse.query("naop_accounts", "SELECT DISTINCT store FROM t")}
    except Exception:
        done = set()
    try:
        universe = warehouse.query("doordash_stores",
                                   "SELECT store_id FROM t WHERE store_id IS NOT NULL LIMIT 300000")
    except Exception:
        universe = []
    todo = [str(r["store_id"]) for r in universe if str(r["store_id"]) not in done]
    if not todo:
        log("  [naop] doordash_stores empty/all-done — falling back to seed store")
        return list(DEFAULT_STORES)
    log("  [naop] universe=%d done=%d taking=%d" % (len(universe), len(done), min(limit, len(todo))))
    return todo[:limit]


def run(stores=None, limit=None, log=print):
    limit = limit or int(os.environ.get("NAOP_LIMIT", "60"))     # menu pages are ~30s each through the ISP pool
    stores = stores or _due_stores(limit, log=log)
    key = dd._api_key()
    run_id = "naop-" + time.strftime("%Y%m%d-%H%M%S")
    rows, accounts, unsure = [], [], []
    for sid in stores:
        try:
            h = dd._unlock("https://www.doordash.com/store/%s" % sid, key)
        except Exception as e:
            log("  store %s failed: %s" % (sid, str(e)[:50])); continue
        rest = _restaurant_name(h)
        cz, czs, csrc = cui.classify(rest, h)                               # servesCuisine off the page
        menu = parse_menu(dd._rsc(h))
        kept = 0
        for it in menu:
            if _FOOD.search(it["name"]):                                    # it's a dish, not a drink
                continue
            b = ctx.classify_beverage(it["name"], it["description"])
            if not (b["is_alcoholic"] or b["category"] == "mocktail"):     # keep alcoholic + mocktails
                continue
            rows.append(dict(store=str(sid), account=rest, cuisine=cz, cuisines="|".join(czs),
                             name=it["name"], description=it["description"][:300], price=it["price"],
                             price_basis="doordash_delivery", category=b["category"],
                             is_alcoholic=b["is_alcoholic"], root=b.get("root", ""), sub=b.get("sub", ""),
                             base_spirit=b.get("base_spirit", ""), beer_style=b.get("beer_style", ""),
                             is_hemp=observe.is_hemp(it["name"], it["description"]), run_id=run_id))
            kept += 1
        accounts.append(dict(store=str(sid), account=rest, cuisine=cz, cuisines="|".join(czs),
                             cuisine_source=csrc, serves_alcohol=kept > 0, n_beverages=kept, run_id=run_id))
        if not cz:
            unsure.append(rest)
        log("  [naop] %-34s (%s) [%s] — %d menu items, %d beverages" % (rest, sid, cz or "?", len(menu), kept))
    if unsure:                                                             # Claude fallback for the nameless
        guess = cui.claude_cuisine(unsure)
        for coll in (rows, accounts):
            for r in coll:
                if not r["cuisine"] and r["account"] in guess:
                    r["cuisine"] = guess[r["account"]]
                    if "cuisine_source" in r:
                        r["cuisine_source"] = "claude"
        if guess:
            log("  [naop] Claude cuisine filled %d/%d unsure accounts" % (len(guess), len(unsure)))
    # ACCUMULATE (naop_* are GLOBAL, not per-market) — merge this sweep into the existing tables so a run
    # for one metro doesn't wipe another's on-premise menus. Dedup beverages by (account, name), accounts by account.
    def _merge(name, new_rows, keyf):
        try:
            existing = warehouse.query(name, "SELECT * FROM t")
        except Exception:
            existing = []
        idx = {keyf(r): r for r in existing}
        for r in new_rows:
            idx[keyf(r)] = r
        return list(idx.values())
    if rows:
        warehouse.write_parquet("naop_beverages", _merge("naop_beverages", rows, lambda r: (r.get("account"), r.get("name"))))
    if accounts:
        warehouse.write_parquet("naop_accounts", _merge("naop_accounts", accounts, lambda r: r.get("account")))
        log("[naop] DONE %d beverages · %d accounts (%d serve alcohol) -> naop_beverages / naop_accounts"
            % (len(rows), len(accounts), sum(1 for a in accounts if a["serves_alcohol"])))
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default="")
    a = ap.parse_args()
    run(stores=[s.strip() for s in a.stores.split(",") if s.strip()] or None)
