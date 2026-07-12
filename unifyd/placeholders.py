"""placeholders.py — identify placeholder / template content so it doesn't pollute the master or waste vision.

Two kinds, both real in our data:
  1. PLACEHOLDER IMAGES — a "no image / coming soon" graphic reused across many products (City Hive puts one
     image on 87 products). The robust signal is FREQUENCY: a real photo appears on ONE product; a placeholder
     is shared by many. Plus obvious URL patterns (no-image / placeholder / default / coming-soon). We must not
     feed these to label_vision (wasted API + garbage extraction) or treat them as a real image.
  2. PLACEHOLDER / TEMPLATE DATA — demo products left in a store's catalog ("I'm a product" — the Shopify/Wix
     template default), "Default Title", dummy UPCs (all-same / 000…), $0 prices. These aren't real SKUs.
"""
import collections
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

_IMG_RE = re.compile(r"no[-_ ]?image|placeholder|coming[-_ ]?soon|default[-_ ]?(?:image|product|thumb)|"
                     r"no[-_ ]?photo|noimage|/blank\.|/default\.|/coming|comingsoon|not[-_ ]?available|/missing|"
                     r"image[-_ ]?unavailable|swatch", re.I)
_NAME_RE = re.compile(r"^(?:i'?m a product|default title|sample(?: product)?|untitled|test(?: product)?|"
                      r"product|item|new product|placeholder|tbd|n/?a|no name|example|demo)$", re.I)
_UPC_BAD = {"000000000000", "111111111111", "999999999999", "123456789012", "012345678905"}


def is_placeholder_name(name):
    n = (name or "").strip()
    return bool(n) and bool(_NAME_RE.match(n))


def is_placeholder_image_url(url):
    return bool(url) and bool(_IMG_RE.search(url))


def is_placeholder_upc(upc):
    d = re.sub(r"\D", "", str(upc or ""))
    return bool(d) and (d in _UPC_BAD or len(set(d)) == 1)


def placeholder_images(source, image_col="image", key_col="sku", min_products=8, log=print):
    """The SET of placeholder image URLs in a source: an image URL on >= min_products DISTINCT products (a real
    photo is one-per-product; a placeholder is reused), OR one matching the obvious patterns. Feed this to
    label_vision to skip them. key_col dedups store-level rows so store duplication isn't mistaken for reuse."""
    try:
        rows = warehouse.query(source, 'SELECT DISTINCT "%s" k, "%s" u FROM t WHERE "%s" IS NOT NULL AND "%s"<>\'\''
                               % (key_col, image_col, image_col, image_col))
    except Exception as e:
        log("[placeholder] %s: %s" % (source, str(e)[:60])); return set()
    cnt = collections.Counter(r["u"] for r in rows)                 # distinct-product count per image
    ph = {u for u, c in cnt.items() if c >= min_products} | {u for u in cnt if is_placeholder_image_url(u)}
    log("[placeholder] %s: %d placeholder image URLs (of %d distinct; reuse>=%d)"
        % (source, len(ph), len(cnt), min_products))
    return ph


def scan(source, log=print):
    """Report placeholder prevalence in a source (names / upcs / images)."""
    rows = warehouse.query(source, "SELECT * FROM t")
    ncol = next((c for c in rows[0] if c.lower() in ("name", "product_name")), "name") if rows else "name"
    icol = next((c for c in rows[0] if c.lower() in ("image", "image_url", "img")), None) if rows else None
    ucol = next((c for c in rows[0] if c.lower() == "upc"), None) if rows else None
    ph_img = placeholder_images(source, image_col=icol, log=log) if icol else set()
    bad_name = sum(1 for r in rows if is_placeholder_name(r.get(ncol)))
    bad_upc = sum(1 for r in rows if ucol and is_placeholder_upc(r.get(ucol)))
    on_ph_img = sum(1 for r in rows if icol and r.get(icol) in ph_img)
    log("[placeholder] %s: %d template names · %d placeholder UPCs · %d rows on a placeholder image"
        % (source, bad_name, bad_upc, on_ph_img))
    return {"template_names": bad_name, "placeholder_upcs": bad_upc, "on_placeholder_image": on_ph_img,
            "placeholder_image_urls": len(ph_img)}


if __name__ == "__main__":
    for s in (sys.argv[1:] or ["cityhive_products", "orlando_offprem_products"]):
        scan(s)
