#!/usr/bin/env python3
"""winebow.py — Winebow importer/distributor brand portfolio (the supplier/importer tier).

Winebow is a major US wine + spirits IMPORTER/DISTRIBUTOR. Its public /our-brands page lists every brand it
carries (~1,394), and each card links to the BRAND'S OWN website. Two things we don't otherwise have:
  1. The IMPORTER→BRAND mapping — a real supplier-tier grouping (all these brands reach US shelves through
     Winebow), the deterministic supplier signal the plant/vendor codes couldn't give us.
  2. The producer WEBSITE per brand — feeds producer_site.py deep enrichment (cask/mash-bill/tasting/awards).

Drupal view, server-rendered (no JS needed), plain `?page=N` pagination at 12 cards/page. Lands
`winebow_brands` (brand, website, logo, importer, country, product_type). stdlib only, polite. This is the
first of the importer-portfolio class — the same shape works for other importers (add their base + card regex).
"""
import argparse
import html as _html
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

BASE = "https://www.winebow.com/our-brands"
IMPORTER = "Winebow"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
FLD = ["brand", "website", "logo", "importer", "country", "product_type", "source"]
_TITLE = re.compile(r'styles_brand_card__title[^>]*>([^<]+)<')
_HREF = re.compile(r'href="([^"]+)"')
_IMG = re.compile(r'<img[^>]*\bsrc="([^"]+)"', re.I)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def parse_page(h):
    """One entry per brand card (every brand, even the ~few with no linked website). The website is taken ONLY
    when the title sits INSIDE an open <a> (no </a> between the anchor and the title) — so a link-less brand
    gets an empty website rather than inheriting the previous card's link."""
    out = []
    for m in _TITLE.finditer(h):
        brand = _html.unescape(m.group(1)).strip()
        if not brand:
            continue
        seg = h[:m.start()]
        ai = seg.rfind("<a ")
        website = ""
        if ai >= 0 and "</a>" not in seg[ai:]:           # the title is enclosed by this still-open anchor
            hm = _HREF.search(seg[ai:])
            if hm:
                website = _html.unescape(hm.group(1)).strip()
        logo = ""
        for im in _IMG.finditer(seg):                    # last image before the title = this card's logo
            logo = _html.unescape(im.group(1))
        out.append({"brand": brand, "website": website, "logo": logo, "importer": IMPORTER,
                    "country": "", "product_type": "", "source": "winebow"})
    return out


def pull(max_pages=200, delay=0.5, log=print):
    seen, out = set(), []
    for pg in range(max_pages):
        try:
            h = _get("%s?page=%d" % (BASE, pg))
        except Exception as e:
            log("[winebow] page %d: %s" % (pg, str(e)[:70])); break
        cards = parse_page(h)
        if not cards:
            log("[winebow] page %d empty — done" % pg); break
        new = 0
        for c in cards:
            k = c["brand"].lower()
            if k in seen:
                continue
            seen.add(k); out.append(c); new += 1
        log("[winebow] page %d: +%d brands (%d total)" % (pg, new, len(out)))
        if new == 0:                                     # pagination looped back to the start
            break
        if delay:
            time.sleep(delay)
    if out:
        warehouse.write_parquet("winebow_brands", out, fields=FLD)
    log("[winebow] DONE: %d distinct brands -> winebow_brands (importer=%s)" % (len(out), IMPORTER))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Winebow importer brand-portfolio scraper.")
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--delay", type=float, default=0.5)
    a = ap.parse_args(argv)
    out = pull(max_pages=a.max_pages, delay=a.delay)
    for c in out[:15]:
        print("  %-32s %s" % (c["brand"][:32], c["website"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
