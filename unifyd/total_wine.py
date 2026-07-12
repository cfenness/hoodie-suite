#!/usr/bin/env python3
"""total_wine.py — Total Wine & More product catalog, scraped DIRECTLY (connId `total-wine`).

How it works, plainly (so the pieces are legible):
  1. Total Wine's site is PerimeterX-walled to a desktop browser (403), but a MOBILE Safari UA
     sails through (200). So every request here sends an iPhone UA.
  2. Their sitemap is OPEN — the product universe lives in Product-en-USD-*.xml (5,000 URLs each,
     several files → the whole catalog). We read those to get every product page URL.
  3. Each product page server-renders schema.org MICRODATA (itemProp=…) inside a big React page:
     brand · name · description · image · sku · price, plus a size string. We parse that.
  4. Snapshot {sku: {...}} and diff vs the last run (in/out + price moves) — repeatable inventory.

Caveats we know: NO UPC (Total Wine uses its own sku, e.g. 140521750-1) → it corroborates the
master by brand+name+size, not the UPC spine. Pages are ~1MB, so a full crawl is a background job;
`cap` bounds a run. PerimeterX could tighten the mobile-UA gap — stay polite (delay) and watch for 403.
stdlib only. Same (datasets,[run],movement) contract as the other owned connectors.
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request, urllib.error

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
SITEMAP_INDEX = "https://www.totalwine.com/sitemap.xml"
SNAP = "total_wine_snapshot.json"
HEADER = ["Brand", "Name", "Size", "Price", "SKU", "Category", "URL"]


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA, "Accept": "*/*",
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def product_sitemaps(log=print):
    """The Product-*.xml sub-sitemaps from the sitemap index."""
    try:
        idx = _fetch(SITEMAP_INDEX)
    except Exception as e:
        log("sitemap index failed: %s" % str(e)[:120]); return []
    subs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", idx)
    prod = [u for u in subs if re.search(r"[Pp]roduct.*\.xml$", u)]
    log("sitemap: %d sub-sitemaps, %d product sub-sitemaps" % (len(subs), len(prod)))
    return prod


def product_urls(cap, log=print):
    urls = []
    for sm in product_sitemaps(log):
        try:
            body = _fetch(sm)
        except Exception:
            continue
        found = re.findall(r"<loc>\s*(https?://www\.totalwine\.com/[^<\s]*/p/\d+)\s*</loc>", body)
        urls.extend(found)
        log("  %s → %d product URLs (%d total)" % (sm.rsplit("/", 1)[-1], len(found), len(urls)))
        if len(urls) >= cap:
            break
    return urls[:cap]


def _m(pattern, text, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def parse_product(html, url):
    """Pull the main product's microdata. The page has several productResultContainer blocks
    (the product + related items); the MAIN one's sku starts with the URL's /p/<id>."""
    pid = _m(r"/p/(\d+)", url)
    for blk in re.split(r"productResultContainer", html)[1:]:
        sku = _m(r'itemProp="sku"[^>]*content="([^"]*)"', blk)
        if not (sku and (not pid or sku.startswith(pid))):
            continue
        brand = _m(r'itemProp="brand"[^>]*>\s*<meta[^>]*itemProp="name"[^>]*content="([^"]*)"', blk, re.S)
        # the product name is the itemProp=name that follows the brand block's closing </div>
        name = _m(r'</div>\s*<meta[^>]*itemProp="name"[^>]*content="([^"]*)"', blk) \
            or _m(r'itemProp="name"[^>]*content="([^"]*)"', blk)
        desc = _m(r'itemProp="description"[^>]*content="([^"]*)"', blk)
        img = _m(r'itemProp="image"[^>]*content="([^"]*)"', blk)
        # size + price live in the surrounding page, not the microdata block
        seg = html[max(0, html.find(blk[:40]) - 400):html.find(blk[:40]) + 200] if blk[:40] in html else ""
        size = _m(r"([0-9][0-9.]*\s*(?:ml|ML|L|Liter|oz|OZ|Pack|pk))\b", seg) or _m(r"([0-9][0-9.]*\s*(?:ml|L))\b", name)
        price = _m(r'itemProp="price"[^>]*content="([0-9.]+)"', html) or _m(r'"price"\s*:\s*"?\$?([0-9]+\.[0-9]{2})', html) or _m(r"\$([0-9]+\.[0-9]{2})", html)
        cat = _m(r'itemProp="itemListElement".*?itemProp="name"[^>]*content="([^"]*)"', html, re.S)
        # TAKE EVERYTHING — Total Wine embeds a structured attributes JSON: {"attributeName":"Varietal Type",
        # "value":"..."}, Country State, Appellation, Region, Style, ABV, Taste/Body. Map the geo/varietal ones.
        attrs = {}
        for an, av in re.findall(r'"attributeName":"([^"]+)","value":"([^"]+)"', html):
            attrs.setdefault(an, av)

        def a(*names):
            return next((attrs[n] for n in names if attrs.get(n)), "")
        return {"sku": sku, "brand": brand, "name": name, "size": size, "price": price,
                "category": cat, "desc": desc, "img": img, "url": url,
                "varietal": a("Varietal Type", "Varietal"), "origin": a("Country State", "Country"),
                "region": a("Region"), "sub_region": a("Sub Region", "Sub-Region", "Sub-Appellation"),
                "appellation": a("Appellation"), "style": a("Style", "Wine Style"),
                "abv": a("ABV", "Alcohol Content"), "taste": a("Taste"), "body": a("Body")}
    return None


def pull(cap=400, delay=1.0, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    started = int(time.time() * 1000)
    urls = product_urls(cap, log)
    if not urls:
        run = _run(0, 0, "failed", ["No product URLs from sitemap — Total Wine sitemap or the mobile-UA path may have changed."], started)
        return {}, [run], {"sampled": 0, "changed": 0}
    cur, warns, blocked = {}, [], 0
    for i, u in enumerate(urls):
        try:
            html = _fetch(u)
            p = parse_product(html, u)
            if p and p.get("sku"):
                cur[p["sku"]] = p
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                blocked += 1
        except Exception:
            pass
        if delay:
            time.sleep(delay)
        if (i + 1) % 50 == 0:
            log("  fetched %d/%d (%d products, %d blocked)" % (i + 1, len(urls), len(cur), blocked))
    if blocked > len(urls) * 0.3:
        warns.append("%d/%d requests blocked (403/429) — PerimeterX may have tightened; slow down / rotate." % (blocked, len(urls)))

    prev = {}
    sp = os.path.join(state_dir, SNAP)
    if os.path.exists(sp):
        try: prev = json.load(open(sp)).get("cells", {})
        except Exception: prev = {}
    changed = sum(1 for k, v in cur.items() if k not in prev or prev[k].get("price") != v.get("price"))
    json.dump({"__ts__": started, "cells": cur}, open(sp, "w"))

    rows = [[v["brand"], v["name"], v["size"], v["price"], v["sku"], v["category"], v["url"]]
            for v in sorted(cur.values(), key=lambda x: (x["brand"], x["name"]))]
    status = "degraded" if (warns or not cur) else "success"
    ds = {"total_wine_products": {"header": HEADER, "rows": rows[:800], "total": len(rows),
                                  "_rows_full": rows}}
    run = _run(len(cur), changed, status, warns, started)
    log("total-wine: %d products (%d changed since last run), status %s" % (len(cur), changed, status))
    return ds, [run], {"sampled": len(cur), "changed": changed}


def _run(n, changed, status, warnings, started):
    return {"id": "R-TW" + hashlib.sha1(str(n).encode()).hexdigest()[:3].upper(), "connId": "total-wine",
            "startedAt": started, "finishedAt": int(time.time() * 1000), "durationMs": 0,
            "status": status, "trigger": "manual", "total": n, "degraded": status != "success",
            "warnings": warnings, "healed": [],
            "extracts": [{"id": "total_wine_products", "rows": n, "delta": changed, "status": status}]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Total Wine direct product scrape (mobile-UA + sitemap + microdata).")
    ap.add_argument("--cap", type=int, default=20); ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--out", default="./tw_out")
    a = ap.parse_args(argv)
    ds, runs, _ = pull(cap=a.cap, delay=a.delay, out=a.out, state_dir=a.out)
    r = list(ds.values())[0] if ds else {"rows": []}
    for row in r["rows"][:12]:
        print("  •", row[0], "|", row[1][:34], "|", row[2], "| $" + str(row[3]), "| sku", row[4])
    print("status:", runs[0]["status"], "| total:", runs[0]["total"])


if __name__ == "__main__":
    main()
