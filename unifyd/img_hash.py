#!/usr/bin/env python3
"""img_hash.py — the CHEAP twin of img_embed: perceptual hashes of product images, on pillow alone.

WHY THIS EXISTS ALONGSIDE img_embed
  `img_embed` answers "are these two photographs of the same product?" — a semantic question that
  genuinely needs CLIP, and CLIP needs torch, which this image does not ship. That dependency is why
  `img_vec` has never been populated.

  Cross-retailer asset divergence mostly asks a much cheaper question: **is this the same FILE?**
  When Kroger and Total Wine both show a product, they are usually both showing the supplier's
  syndicated JPEG, re-encoded and resized on the way through. A perceptual hash is exactly the right
  instrument for that — it survives re-encoding and scaling, and it needs nothing beyond pillow,
  which the image already carries.

THE ASYMMETRY THAT MATTERS, AND WHICH THE CALLER MUST RESPECT
  A hash MATCH is strong evidence: two images that hash within a few bits really are the same file.
  A hash SPLIT is weak evidence: the same pack photographed twice — different angle, lighting,
  background — hashes far apart. So this tier can confirm sameness confidently and can only ever
  RAISE A CANDIDATE for difference. `asset_divergence` encodes that as a separate verdict
  (`divergent_unconfirmed`) rather than letting a hash split masquerade as a packaging change.

  This is also why the well-known "every amber bottle hashes alike" problem
  ([[image-match-signal]]) does not bite here: that failure is about telling DIFFERENT products
  apart, and everything compared here is already inside one UPC.

    python img_hash.py build --source binnys_products
    python img_hash.py build --all
"""
import argparse
import io
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse  # noqa: E402

TABLE = "img_hash"
HASH_FLD = ["source", "sku", "upc", "image", "dhash", "width", "height"]
HASH_BITS = 8                              # 8x8 difference hash -> 64 bits -> 16 hex chars

# Same starting set as img_embed's DEFAULT_SOURCES, plus the larger retail catalogs — divergence
# needs BREADTH across chains more than depth in any one, since the whole signal is "who disagrees".
DEFAULT_SOURCES = ["specs_products", "abc_products", "offprem_products", "binnys_products",
                   "haskells_products", "meijer_products", "target_products", "publix_products",
                   "total_wine_products", "walmart_products", "salsify_products"]

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124"


def dhash(data, size=HASH_BITS):
    """64-bit difference hash as 16 hex chars, or None. Never a fabricated value: undecodable bytes
    and a missing pillow both return None, so an absent hash can't be mistaken for a computed one."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("L").resize((size + 1, size), Image.LANCZOS)
        px = list(im.getdata())
        bits = 0
        for row in range(size):
            for col in range(size):
                left = px[row * (size + 1) + col]
                right = px[row * (size + 1) + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return "%0*x" % (size * size // 4, bits)
    except Exception:
        return None


def hamming(a, b):
    """Bit distance between two hashes. None when either is missing — an absent hash must never
    read as distance 0, which would be a confident claim of identity."""
    if not a or not b or len(a) != len(b):
        return None
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def dims(data):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return (None, None)


def _one(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(12_000_000)
        h = dhash(data)
        w, ht = dims(data)
        del data
        return url, h, w, ht
    except Exception:
        return url, None, None, None


def hash_urls(urls, workers=12, log=print):
    """{url: (dhash, w, h)} for the ones that decoded. A URL that fails is simply absent — the
    caller lands nothing for it rather than a null row implying we looked and it had no image."""
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for url, h, w, ht in ex.map(_one, urls):
            if h:
                out[url] = (h, w, ht)
    return out


def build(source, limit=None, workers=12, land=True, log=print):
    """Hash a source's product images into `img_hash` (accumulates; skips sku already hashed).

    Mirrors `img_embed.build` deliberately — same key discovery, same resumable chunking — so the
    two tiers stay operationally interchangeable and a source can be on either or both."""
    try:
        cols = set(warehouse.query(source, "SELECT * FROM t LIMIT 1")[0].keys())
    except Exception as e:
        log("[img_hash] %s unreadable: %s" % (source, str(e)[:90]))
        return 0
    keyc = "sku" if "sku" in cols else ("tcin" if "tcin" in cols else "slug")
    upcc = "upc" if "upc" in cols else "NULL"
    rows = warehouse.query(source, "SELECT DISTINCT CAST(%s AS VARCHAR) sku, CAST(%s AS VARCHAR) upc, image "
                           "FROM t WHERE image IS NOT NULL AND image<>''%s"
                           % (keyc, upcc, (" LIMIT %d" % limit) if limit else ""))
    try:
        done = {r["sku"] for r in warehouse.query(TABLE, "SELECT sku FROM t WHERE source=?", [source])}
    except Exception:
        done = set()
    todo = [r for r in rows if r["sku"] not in done]
    log("[img_hash] %s: %d images, %d already hashed, %d to do" % (source, len(rows), len(done), len(todo)))

    landed = 0
    for i in range(0, len(todo), 512):
        chunk = todo[i:i + 512]
        got = hash_urls([r["image"] for r in chunk], workers=workers, log=log)
        recs = [{"source": source, "sku": r["sku"], "upc": r.get("upc") or "", "image": r["image"],
                 "dhash": got[r["image"]][0], "width": got[r["image"]][1], "height": got[r["image"]][2]}
                for r in chunk if r["image"] in got]
        if recs and land:
            warehouse.write_accumulate(TABLE, recs, key=lambda x: (x["source"], x["sku"]),
                                       fields=HASH_FLD, coverage=False)
        landed += len(recs)
        log("[img_hash] %s: +%d (%d/%d)" % (source, len(recs), min(i + 512, len(todo)), len(todo)))
    log("[img_hash] %s DONE: %d hashed" % (source, landed))
    return landed


def build_all(sources=None, limit=None, workers=12, log=print):
    total = 0
    for s in (sources or DEFAULT_SOURCES):
        total += build(s, limit=limit, workers=workers, log=log)
    log("[img_hash] ALL DONE: %d hashed across %d source(s)" % (total, len(sources or DEFAULT_SOURCES)))
    print('HOODIE_RESULT {"status": "success", "items_done": %d, "items_total": %d}' % (total, total))
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description="Perceptual-hash product images (the cheap divergence tier).")
    ap.add_argument("cmd", choices=["build"], nargs="?", default="build")
    ap.add_argument("--source")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args(argv)
    if a.all or not a.source:
        build_all(limit=a.limit, workers=a.workers)
    else:
        build(a.source, limit=a.limit, workers=a.workers)


if __name__ == "__main__":
    main()
