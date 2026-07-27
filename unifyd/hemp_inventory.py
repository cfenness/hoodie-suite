#!/usr/bin/env python3
"""hemp_inventory.py — pull PER-STORE INVENTORY COUNTS for hemp beverages from Shopify hemp retailers.

The goal is inventory COUNTS, not just listings, and to PUSH each retailer until we're sure counts aren't
available. Shopify hides `inventory_quantity` in /products.json (only `available`), but the CART-ADD trick
forces the number: request a huge quantity and Shopify replies "Only N items were added … due to availability"
→ N is the exact on-hand count. Stores that oversell (add the full 9999) genuinely expose no count — we record
that as `oversell` so a source is provably exhausted, not silently skipped.

This blows the hemp-retailer list WAY out: any Shopify hemp shop becomes an inventory source. Lands
`hemp_inventory` as a dated time-series (retailer, product, variant, qty, method). Pair with Binny's (which
gives exact per-store qty natively) for the chain side. Polite, stdlib.

    python hemp_inventory.py --base https://www.nothingbuthemp.net
    python hemp_inventory.py            # all known hemp Shopify bases (orlando_hemp + offprem)
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import hemp_scan

UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36")}
FLD = ["retailer", "base", "state", "name", "brand", "variant_id", "sku", "upc", "price", "available",
       "qty", "method", "signal", "captured_at", "source"]
_ONLY = re.compile(r"only\s+(\d+)\s+item", re.I)                 # "Only N items were added … availability"


def _get(url, timeout=15):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]}),
                                  timeout=timeout).read().decode("utf-8", "replace")


def shopify_products(base, max_pages=40, log=print):
    """All /products.json products for a Shopify store (paged)."""
    out = []
    for pg in range(1, max_pages + 1):
        try:
            d = json.loads(_get("%s/products.json?limit=250&page=%d" % (base, pg)))
        except Exception as e:
            if pg == 1:
                log("  %s products.json: %s" % (base, str(e)[:50]))
            break
        prods = d.get("products") or []
        if not prods:
            break
        out.extend(prods)
        if len(prods) < 250:
            break
        time.sleep(0.3)
    return out


def cart_count(base, variant_id, timeout=15):
    """Exact on-hand via the cart-add trick. Returns (qty, method): an int + 'cart' when the store limits,
    (None,'oversell') when it adds the full request (no count exposed), (None,'err') on failure."""
    body = json.dumps({"items": [{"id": variant_id, "quantity": 9999}]}).encode()
    req = urllib.request.Request(base + "/cart/add.js", data=body, method="POST",
                                 headers={**UA, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=timeout).read()
        return None, "oversell"                                 # accepted 9999 → no on-hand limit exposed
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        m = _ONLY.search(msg)
        return (int(m.group(1)), "cart") if m else (None, "err")
    except Exception:
        return None, "err"


def harvest(base, retailer="", state="", delay=0.5, log=print):
    base = base.rstrip("/")
    prods = shopify_products(base, log=log)
    recs, hemp_n, counted = [], 0, 0
    ts = int(time.time())
    for p in prods:
        sig = hemp_scan.classify(p.get("title"), p.get("product_type") or "")
        if not sig:                                             # hemp BEVERAGES only
            continue
        hemp_n += 1
        for v in (p.get("variants") or []):
            qty, method = cart_count(base, v.get("id"))
            if method == "cart":
                counted += 1
            recs.append({"retailer": retailer or base.split("//")[-1], "base": base, "state": state,
                         "name": p.get("title"), "brand": (p.get("vendor") or ""), "variant_id": str(v.get("id")),
                         "sku": (v.get("sku") or ""), "upc": (v.get("barcode") or ""),
                         "price": v.get("price"), "available": bool(v.get("available")),
                         "qty": qty, "method": method, "signal": sig, "captured_at": ts, "source": "shopify_cart"})
            if delay:
                time.sleep(delay)
    log("  %-34s %d hemp-bev products · %d variants counted (of %d)"
        % (base.split("//")[-1][:34], hemp_n, counted, len(recs)))
    return recs


def known_bases():
    # was also reading `orlando_hemp_products` — a PHANTOM table no module produces (it silently except-passed,
    # contributing nothing). Dropped. The only real base universe is the Shopify subset of offprem_products.
    bases = set()
    try:
        for r in warehouse.query("offprem_products", "SELECT DISTINCT base b FROM t WHERE base LIKE 'http%'"):
            bases.add(r["b"].rstrip("/"))
    except Exception:
        pass
    return sorted(bases)


def run(bases=None, land=True, log=print):
    bases = bases or known_bases()
    log("[hemp_inv] %d hemp Shopify bases" % len(bases))
    allrecs = []
    for b in bases:
        try:
            allrecs.extend(harvest(b, log=log))
        except Exception as e:
            log("  %s: %s" % (b, str(e)[:60]))
    if land and allrecs:
        warehouse.write_accumulate("hemp_inventory", allrecs,
                                   key=lambda r: (r["base"], r["variant_id"]), fields=FLD)
    got = sum(1 for r in allrecs if r["method"] == "cart")
    over = len({r["base"] for r in allrecs if r["method"] == "oversell"})
    log("[hemp_inv] DONE: %d variant rows, %d with an exact count; %d bases oversell (no count)"
        % (len(allrecs), got, over))
    return allrecs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hemp-beverage per-store inventory counts (Shopify cart-add).")
    ap.add_argument("--base", default=None, help="one Shopify base (default: all known hemp bases)")
    a = ap.parse_args(argv)
    recs = run(bases=[a.base] if a.base else None)
    for r in recs[:20]:
        print("  %-30s %-34s qty=%s (%s)" % (r["retailer"][:30], (r["name"] or "")[:34], r["qty"], r["method"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
