"""label_vision.py — read a product/label image with Claude vision and extract EVERYTHING on it.

The label is the source of truth for the fields no catalog structures: country of ORIGIN (the source of the
juice — "Product of Barbados", "Distilled in Scotland" — NOT the bottler), where it was bottled, UPC/barcode,
ABV, appellation/region/sub-region, grape varietal, vintage, net contents, the government warning. We now have
images across every retail source + the TTB label scans, so this turns those pixels into master fields.

Structured output is FORCED via a tool call (the model must return the schema). COO vs bottled_in is called out
explicitly in the prompt because that distinction matters for sourced rum/whiskey. Anything not clearly visible
is null — we never guess. Results land in `label_extract`; the master + Steward page consume them, and the
Steward corrections become the ground-truth feedback loop.

    python label_vision.py --source binnys_products --limit 50
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

MODEL = os.environ.get("LABEL_VISION_MODEL", "claude-opus-4-8")

_FIELDS = {
    "product_name": "the product name as printed",
    "brand": "the brand / producer name",
    "origin": "COUNTRY OF ORIGIN = where the product/juice is actually FROM (e.g. 'Product of Barbados', "
              "'Distilled in Scotland', the appellation's country) — NOT where it was bottled",
    "bottled_in": "where it was BOTTLED if stated separately (e.g. 'Bottled by X in Kentucky')",
    "region": "wine/spirit region (e.g. Bordeaux, Napa Valley, Speyside)",
    "sub_region": "sub-region if finer than region",
    "appellation": "the appellation / AVA / AOC / DOCG (e.g. Pauillac, Rutherford, Barolo)",
    "varietal": "grape varietal(s) or the spirit type (e.g. Cabernet Sauvignon, Single Malt)",
    "vintage": "vintage year if present, else 'NV' if labeled non-vintage",
    "abv": "alcohol by volume as a number with % (e.g. '13.5%')",
    "upc": "the UPC/EAN barcode digits if a barcode number is visible",
    "net_contents": "net contents / size as printed (e.g. '750 mL')",
    "gov_warning": "true if the US GOVERNMENT WARNING text is present, else false",
}
_TOOL = {"name": "label", "description": "Return the fields read from the alcohol label. Use null for anything "
         "NOT clearly visible — never guess.",
         "input_schema": {"type": "object",
                          "properties": {k: {"type": ["string", "boolean", "null"], "description": v}
                                         for k, v in _FIELDS.items()},
                          "required": list(_FIELDS)}}
_PROMPT = ("Read this alcohol product label/photo and extract every field via the `label` tool. Only report what "
           "is CLEARLY visible — null otherwise, never guess. Critically: `origin` is the country the product is "
           "FROM (source of the juice), which is NOT necessarily where it was bottled — put the bottling place in "
           "`bottled_in`.")

_client = None


def _cl():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def _image_block(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=30).read()
    ct = "image/png" if url.lower().split("?")[0].endswith(".png") else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": ct,
                                        "data": base64.b64encode(data).decode()}}


def extract(image_url):
    """-> dict of label fields (null where not visible). Raises on fetch/API failure (caller handles)."""
    msg = _cl().messages.create(
        model=MODEL, max_tokens=1024, tools=[_TOOL], tool_choice={"type": "tool", "name": "label"},
        messages=[{"role": "user", "content": [_image_block(image_url), {"type": "text", "text": _PROMPT}]}])
    for b in msg.content:
        if b.type == "tool_use":
            return {k: (v if v != "" else None) for k, v in (b.input or {}).items()}
    return {}


def run(source="binnys_products", limit=200, workers=6, log=print):
    """Pull products WITH a REAL image that still lack COO/appellation, vision-extract, land to label_extract.
    Placeholder images (no-image / coming-soon graphics reused across many products) are skipped — feeding them
    to vision wastes API and returns garbage."""
    import placeholders
    ph = placeholders.placeholder_images(source, image_col="image", key_col="sku", log=log)
    rows = warehouse.query(source, "SELECT DISTINCT sku, name, image FROM t "
                           "WHERE image IS NOT NULL AND image<>'' LIMIT %d" % (int(limit) * 3))
    rows = [r for r in rows if r["image"] not in ph][:int(limit)]
    log("[vision] %s: %d imaged products to read (placeholders skipped)" % (source, len(rows)))

    def one(r):
        try:
            d = extract(r["image"])
            d.update(source=source, sku=str(r.get("sku") or ""), src_name=r.get("name") or "",
                     image=r["image"], ts=int(time.time()))
            return d
        except Exception as e:
            log("  [vision] %s: %s" % (str(r.get("name"))[:30], str(e)[:60]))
            return None
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d in ex.map(one, rows):
            if d:
                out.append(d)
                if len(out) % 25 == 0:
                    log("  [vision] %d/%d extracted" % (len(out), len(rows)))
    if out:
        warehouse.write_accumulate("label_extract", out, key=lambda r: (r.get("source"), r.get("sku")))
    log("[vision] landed %d label extractions -> label_extract" % len(out))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="binnys_products")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    run(source=a.source, limit=a.limit, workers=a.workers)
