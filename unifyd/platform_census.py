"""platform_census.py — traverse off-premise outlets, find the ones with a website, and CAPTURE THE SYSTEM.

Not a scrape — a census. For every off-premise store it: finds the website (Google Maps), fetches it, and
detects the e-commerce PLATFORM (City Hive / Bottlecapps / BigCommerce / Shopify / WooCommerce / …) plus
whether it sells online (inventory), hemp/CBD, and bev-alc. The output maps store -> platform, so we build
catalog recipes ONE PLATFORM AT A TIME — each recipe then unlocks every store on it. (Far more local liquor
stores run e-commerce than a small sample suggests; this quantifies which systems dominate so we prioritize.)
Lands <market>_offprem_census.

    python platform_census.py --market orlando
"""
import argparse, json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import menu_site as ms            # discover_site helpers: _bd_key, _SOCIAL, _unlock
import place_coverage as pc       # _bd_maps_text (Maps website lookup)

# ordered by liquor-ecommerce relevance; first match wins
_PLATFORMS = [
    ("City Hive", r"cityhive"),
    ("Bottlecapps", r"bottlecapps"),
    ("BigCommerce", r"bigcommerce"),
    ("Shopify", r"cdn\.shopify|myshopify|Shopify\.theme"),
    ("WooCommerce", r"woocommerce|wp-content/plugins/woo"),
    ("Ecwid", r"ecwid"),
    ("Square Online", r"square-online|squareup\.com|\.square\.site"),
    ("Magento", r"magento|\bMage\.|/mage/"),
    ("Wix Stores", r"wix.{0,30}stores|wixstores|_wixCssStates"),
    ("Squarespace", r"squarespace"),
    ("Weebly", r"weebly"),
    ("Liquor-POS", r"drinkeasy|liquorpos|winepos|nimble commerce|spiritpos|bevager|liquorsplit|barcart"),
]
_ECOM = re.compile(r"add to cart|add-to-cart|/cart\b|data-product-id|\"@type\":\s*\"Product\"|itemprop=\"price\"|"
                   r"proceed to checkout|shopping cart|in stock|out of stock", re.I)
_HEMP = re.compile(r"\bhemp\b|\bcbd\b|delta[\s-]?8|delta[\s-]?9|\bthca?\b|cannabis|\bkratom\b|hemp[\s-]?derived", re.I)
_BEVALC = re.compile(r"\bliquor\b|\bwine\b|\bspirits?\b|whiske?y|bourbon|tequila|\bvodka\b|\brum\b|\bgin\b|"
                     r"champagne|\bbeer\b|\bcognac\b", re.I)


def detect(html):
    plat = next((n for n, p in _PLATFORMS if re.search(p, html, re.I)), "bespoke")
    return {"platform": plat, "has_ecommerce": bool(_ECOM.search(html)),
            "sells_hemp": bool(_HEMP.search(html)), "sells_bevalc": bool(_BEVALC.search(html))}


def census(market="orlando", near="Orlando FL", scope="retail", log=print):
    key = ms._bd_key()
    where = "type='retail'" if scope == "retail" else "1=1"
    stores = warehouse.query("%s_merchants" % market, "SELECT store_id, name, is_chain FROM t WHERE " + where)
    run_id = "census-" + time.strftime("%Y%m%d-%H%M%S")
    rows = []
    for i, s in enumerate(stores):
        site = ""
        try:
            ps = pc._bd_maps_text("%s %s" % (s["name"], near), key)
            site = (ps[0].get("link") if ps else "") or ""
        except Exception:
            pass
        rec = dict(account=s["name"], store_id=s.get("store_id", ""), is_chain=s.get("is_chain"),
                   website=site, has_website=bool(site and not ms._SOCIAL.search(site)),
                   platform="", has_ecommerce=False, sells_hemp=False, sells_bevalc=False,
                   market=market, run_id=run_id)
        if rec["has_website"]:
            try:
                rec.update(detect(ms._unlock(site, key)))
            except Exception:
                pass
        rows.append(rec)
        if (i + 1) % 20 == 0:
            warehouse.write_parquet("%s_offprem_census" % market, rows)          # checkpoint
            log("  [census] ...%d/%d" % (i + 1, len(stores)))
        time.sleep(0.5)
    warehouse.write_parquet("%s_offprem_census" % market, rows)
    from collections import Counter
    withsite = sum(1 for r in rows if r["has_website"])
    ecom = sum(1 for r in rows if r["has_ecommerce"])
    plats = Counter(r["platform"] for r in rows if r["has_ecommerce"] and r["platform"])
    log("[census] %s: %d outlets · %d with website · %d with e-commerce" % (market, len(rows), withsite, ecom))
    log("[census] PLATFORMS (e-commerce stores): %s" % dict(plats.most_common()))
    return rows


_HEMP_TERMS = ["cbd store", "smoke shop", "vape shop", "hemp store", "cbd dispensary", "delta 8 store", "hemp dispensary"]


def census_hemp(market="orlando", near="Orlando FL", log=print):
    """The HEMP vertical — a separate retail world (smoke/CBD/vape/hemp shops) the alcohol sweep never touches.
    Discover them via Google Maps, then capture the SAME system census: platform + has_ecommerce + sells_hemp +
    sells_bevalc. Lands <market>_hemp_census. Feeds the hemp-bev interest + the recipe-first build."""
    import place_coverage as pc
    key = ms._bd_key()
    seen = {}
    for term in _HEMP_TERMS:
        try:
            for p in pc._bd_maps_text("%s %s" % (term, near), key):
                fid = p.get("fid")
                if fid and fid not in seen:
                    seen[fid] = {"name": p.get("title"), "website": p.get("link") or "",
                                 "category": p.get("category")}
        except Exception:
            pass
    log("[hemp] discovered %d distinct hemp/CBD/smoke retailers" % len(seen))
    rows, run_id = [], "hemp-" + time.strftime("%Y%m%d-%H%M%S")
    for i, (fid, s) in enumerate(seen.items()):
        site = s["website"]
        rec = dict(account=s["name"], google_fid=fid, website=site,
                   has_website=bool(site and not ms._SOCIAL.search(site)),
                   platform="", has_ecommerce=False, sells_hemp=False, sells_bevalc=False,
                   market=market, run_id=run_id)
        if rec["has_website"]:
            try:
                rec.update(detect(ms._unlock(site, key)))
            except Exception:
                pass
        rows.append(rec)
        if (i + 1) % 20 == 0:
            warehouse.write_parquet("%s_hemp_census" % market, rows)
            log("  [hemp] ...%d/%d" % (i + 1, len(seen)))
        time.sleep(0.4)
    warehouse.write_parquet("%s_hemp_census" % market, rows)
    from collections import Counter
    ecom = sum(1 for r in rows if r["has_ecommerce"])
    plats = Counter(r["platform"] for r in rows if r["has_ecommerce"] and r["platform"])
    log("[hemp] %s: %d retailers · %d with website · %d with e-commerce · platforms %s"
        % (market, len(rows), sum(1 for r in rows if r["has_website"]), ecom, dict(plats.most_common())))
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="orlando")
    ap.add_argument("--scope", default="retail", choices=["retail", "all", "hemp"])
    a = ap.parse_args()
    (census_hemp if a.scope == "hemp" else census)(a.market)
