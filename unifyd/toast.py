#!/usr/bin/env python3
"""toast.py — restaurant OWN menus + an outlet spine from Toast (toasttab.com), $0.

Toast is the online-ordering platform behind ~100k US restaurants. Its local sitemaps
(`toasttab.com/local/sitemaps/index.xml` → `restaurant-pages*.xml`) list every restaurant's own-ordering
page `/local/order/<name-city-slug>/r-<guid>` — the restaurant's OWN menu (name/description/price), NOT the
delivery-inflated aggregator menu. We harvest the universe ($0 — curl_cffi Safari-17 + the flat ISP pool,
the same path that beats DoorDash's Forter), land `toast_outlets` (a source layer for the outlet
pre-mastering union), and parse each menu → beverages (via cocktail_taxonomy) into `toast_beverages`,
tagged source=toast / price_basis=menu so it stays SEPARABLE from the DoorDash delivery menus for
per-source freshness judging.

    python toast.py --harvest             # refresh the outlet universe (weekly)
    python toast.py --menus --limit 60    # pull the next batch of own-menus
"""
import argparse
import html as _html
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe
import cocktail_taxonomy as ctx

SITEMAP_INDEX = "https://www.toasttab.com/local/sitemaps/index.xml"
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_ORDER = re.compile(r"/local/order/(?P<slug>[^/]+)/r-(?P<guid>[0-9a-f-]{16,})", re.I)
_NAME = re.compile(r'<span class="headerText">([^<]+)</span>')
_DESC = re.compile(r'class="itemDescription">([^<]*)</div>')
_PRICE = re.compile(r'<span class="price"[^>]*>\$([\d,]+\.\d\d)</span>')
_STATE = re.compile(r'"addressState":"([A-Z]{2})"|"state":"([A-Z]{2})"')
OUTLET_FIELDS = ["guid", "name", "slug", "url", "state", "source"]
BEV_FIELDS = ["store", "account", "name", "description", "price", "category", "is_alcoholic",
              "root", "sub", "base_spirit", "beer_style", "is_hemp", "source", "price_basis", "captured"]
ACCT_FIELDS = ["guid", "account", "state", "serves_alcohol", "n_beverages", "source", "captured"]


def _get(url, tries=4, log=print):
    """$0 fetch — curl_cffi Safari-17 through the ISP pool (rotating exit). '' after `tries` (skip, no spend)."""
    import resi
    try:
        from curl_cffi import requests as cr
    except Exception as e:
        log("  [toast] curl_cffi unavailable: %s" % str(e)[:60])
        return ""
    last = ""
    for a in range(tries):
        u = resi.isp_url() if resi.isp_enabled() else None
        px = {"http": u, "https": u} if u else None
        try:
            r = cr.get(url, impersonate="safari17_0", proxies=px, timeout=45)
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
            last = "status %s" % r.status_code
        except Exception as e:
            last = str(e)[:70]
        time.sleep(1 + a)
    log("  [toast] fetch failed after %d (%s): %s" % (tries, last, url[:70]))
    return ""


def _slug_name(slug):
    """The order-slug is '<name-tokens>-<city/neighborhood>'. We can't split name vs city perfectly, so keep
    the whole slug title-cased as the outlet name; city/state get enriched from the page during the menu pull."""
    return slug.replace("-", " ").title()[:90]


def harvest_outlets(cap=None, log=print):
    """Toast local sitemaps → toast_outlets (guid, name, slug, url). Returns the rows harvested."""
    idx = _get(SITEMAP_INDEX, log=log)
    subs = _LOC.findall(idx)
    log("[toast] %d sub-sitemaps" % len(subs))
    rows, seen = [], set()
    for si, sub in enumerate(subs):
        body = _get(sub, log=log)
        if not body:
            continue
        n = 0
        for url in _LOC.findall(body):
            m = _ORDER.search(url)
            if not m:
                continue
            guid = m.group("guid")
            if guid in seen:
                continue
            seen.add(guid)
            rows.append(dict(guid=guid, name=_slug_name(m.group("slug")), slug=m.group("slug"),
                             url=url, state="", source="toast"))
            n += 1
            if cap and n >= cap:
                break
        log("  sub %d/%d: %d outlets" % (si + 1, len(subs), n))
    if rows:
        warehouse.write_accumulate("toast_outlets", rows, key=lambda r: r["guid"], fields=OUTLET_FIELDS)
        log("[toast] landed %d outlets -> toast_outlets" % len(rows))
    else:
        log("[toast] no outlets harvested (fetch blocked?) — nothing landed")
    return rows


def parse_menu(html):
    """Extract items from a Toast order page: each <span class=headerText> is an item name; the price and
    (optional) description follow within its block (bounded to the next item)."""
    items = []
    names = list(_NAME.finditer(html))
    for i, m in enumerate(names):
        start = m.end()
        end = names[i + 1].start() if i + 1 < len(names) else min(len(html), start + 1600)
        window = html[start:end]
        pm = _PRICE.search(window)
        dm = _DESC.search(window)
        price = None
        if pm:
            try:
                price = float(pm.group(1).replace(",", ""))
            except ValueError:
                price = None
        items.append({"name": _html.unescape(m.group(1)).strip(),
                      "description": _html.unescape(dm.group(1)).strip() if dm else "",
                      "price": price})
    return items


def _due_outlets(limit, log=print):
    """Next batch of toast_outlets not yet menu-pulled (absent from toast_menu_accounts)."""
    try:
        done = {str(r["guid"]) for r in warehouse.query("toast_menu_accounts", "SELECT DISTINCT guid FROM t")}
    except Exception:
        done = set()
    try:
        universe = warehouse.query("toast_outlets", "SELECT guid, name, url FROM t LIMIT 200000")
    except Exception:
        universe = []
    todo = [r for r in universe if str(r["guid"]) not in done]
    log("  [toast] universe=%d done=%d taking=%d" % (len(universe), len(done), min(limit, len(todo))))
    return todo[:limit]


def pull_menus(limit=None, log=print):
    """Fetch the next batch of Toast own-menus → beverages into toast_beverages (source=toast)."""
    limit = limit or int(os.environ.get("TOAST_LIMIT", "60"))
    outlets = _due_outlets(limit, log=log)
    if not outlets:
        log("[toast] no un-pulled outlets (harvest first?)")
        return 0
    today = time.strftime("%Y-%m-%d")
    bevs, accts, obs = [], [], []
    for o in outlets:
        guid, name, url = str(o["guid"]), o.get("name") or "", o.get("url") or ""
        h = _get(url, log=log)
        if not h:
            continue
        sm = _STATE.search(h)
        state = (sm.group(1) or sm.group(2)) if sm else ""
        menu = parse_menu(h)
        kept = 0
        for it in menu:
            b = ctx.classify_beverage(it["name"], it["description"])
            if not (b["is_alcoholic"] or b["category"] == "mocktail"):
                continue
            bevs.append(dict(store=guid, account=name, name=it["name"], description=it["description"][:300],
                             price=it["price"], category=b["category"], is_alcoholic=b["is_alcoholic"],
                             root=b.get("root", ""), sub=b.get("sub", ""), base_spirit=b.get("base_spirit", ""),
                             beer_style=b.get("beer_style", ""),
                             is_hemp=observe.is_hemp(it["name"], it["description"]),
                             source="toast", price_basis="menu", captured=today))
            kept += 1
        accts.append(dict(guid=guid, account=name, state=state, serves_alcohol=kept > 0,
                          n_beverages=kept, source="toast", captured=today))
        log("  [toast] %-40s [%s] — %d items, %d beverages" % (name[:40], state or "?", len(menu), kept))
    if bevs:
        warehouse.write_accumulate("toast_beverages", bevs, key=lambda r: (r["store"], r["name"]), fields=BEV_FIELDS)
    if accts:
        warehouse.write_accumulate("toast_menu_accounts", accts, key=lambda r: r["guid"], fields=ACCT_FIELDS)
    log("[toast] DONE %d beverages · %d outlets (%d serve alcohol) -> toast_beverages / toast_menu_accounts"
        % (len(bevs), len(accts), sum(1 for a in accts if a["serves_alcohol"])))
    return len(bevs)


def run(log=print):
    """Registry entrypoint: harvest the universe if empty, then pull a menu batch."""
    try:
        have = warehouse.row_count("toast_outlets")
    except Exception:
        have = 0
    if not have:
        harvest_outlets(log=log)
    return pull_menus(log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Toast restaurant own-menus + outlet spine ($0).")
    ap.add_argument("--harvest", action="store_true", help="refresh toast_outlets from the sitemaps")
    ap.add_argument("--menus", action="store_true", help="pull a batch of own-menus")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--cap", type=int, default=None, help="cap outlets/sub-sitemap (smoke)")
    a = ap.parse_args(argv)
    if a.harvest:
        harvest_outlets(cap=a.cap)
    if a.menus or not a.harvest:
        pull_menus(limit=a.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
