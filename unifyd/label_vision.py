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


# ---- tiered engine (Phase 0): barcode + OCR/rules first, Claude only on low confidence ---------
# Same facts as extract() but NORMALIZED (abv/net as numbers) + provenance/confidence/tier. Enable
# per-run with `--engine tiered` (or CV_TIERED=1); lands to `cv_reads`, leaving the Claude-only
# `label_extract` path untouched. In Phase 2 the fine-tuned Florence-2 extractor replaces the rules
# brain behind the same seam — the tiers/gate/provenance don't change, so Tier-2 (Claude) spend just
# shrinks. See docs/PROPRIETARY-CV.md.
_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(_HERE, "cv") not in sys.path:
    sys.path.insert(0, os.path.join(_HERE, "cv"))

_TIERED_FIELDS = ["source", "sku", "src_name", "image", "product_name", "brand", "origin", "region",
                  "varietal", "vintage", "abv", "net_ml", "upc", "gov_warning", "tier", "confidence",
                  "provenance", "ts"]


def _claude_native(image_url):
    """Claude extract() -> the tiered-native, normalized schema (the escalation adapter)."""
    import trainset as _t
    d = extract(image_url) or {}
    out = {"product_name": d.get("product_name"), "brand": d.get("brand"), "origin": d.get("origin"),
           "region": d.get("region"), "varietal": d.get("varietal"), "vintage": d.get("vintage"),
           "abv": _t.norm_abv(d.get("abv")), "net_ml": _t.norm_net_ml(d.get("net_contents")),
           "upc": d.get("upc"), "gov_warning": bool(d.get("gov_warning"))}
    return {k: v for k, v in out.items() if v not in (None, "", False)}


def extract_tiered(image_url):
    """Barcode + OCR/rules, escalating to Claude only when confidence < CV_TAU. Returns the full read
    result: {fields, confidence, tier, provenance, needs_escalation, ...}."""
    import read as cv_read
    return cv_read.read(image_url, escalate=lambda _b: _claude_native(image_url))


def run_tiered(source="binnys_products", limit=200, workers=6, only_gaps=True, log=print):
    """Tiered batch read of low-confidence imaged products -> `cv_reads` (native schema + provenance).
    Mirrors run()'s gap-only selection so we still spend Tier-2 (Claude) only where we're unsure."""
    import placeholders
    cols = set(warehouse.query(source, "SELECT * FROM t LIMIT 1")[0].keys())
    gap = ["(%s IS NULL OR %s='')" % (c, c) for c in _CONF_GEO if c in cols]
    where = "image IS NOT NULL AND image<>''" + ((" AND (" + " OR ".join(gap) + ")") if (only_gaps and gap) else "")
    ph = placeholders.placeholder_images(source, image_col="image", key_col="sku", log=log)
    rows = warehouse.query(source, "SELECT DISTINCT sku, name, image FROM t WHERE %s LIMIT %d"
                           % (where, int(limit) * 3))
    rows = [r for r in rows if r["image"] not in ph][:int(limit)]
    log("[cv] %s: %d imaged products through the tiered engine (barcode+OCR first, backend=%s)"
        % (source, len(rows), _ocr_backend()))

    def one(r):
        try:
            res = extract_tiered(r["image"])
            f = res["fields"]
            rec = {k: f.get(k) for k in ("product_name", "brand", "origin", "region", "varietal",
                                         "vintage", "abv", "net_ml", "upc", "gov_warning")}
            rec.update(source=source, sku=str(r.get("sku") or ""), src_name=r.get("name") or "",
                       image=r["image"], tier=res["tier"], confidence=res["confidence"],
                       provenance=json.dumps(res["provenance"]), ts=int(time.time()))
            return rec
        except Exception as e:
            log("  [cv] %s: %s" % (str(r.get("name"))[:30], str(e)[:60]))
            return None
    out, esc = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d in ex.map(one, rows):
            if d:
                out.append(d)
                esc += 1 if d["tier"] == "claude" else 0
    if out:
        warehouse.write_accumulate("cv_reads", out, key=lambda r: (r.get("source"), r.get("sku")),
                                   fields=_TIERED_FIELDS)
    log("[cv] landed %d tiered reads -> cv_reads | escalated to Claude: %d (%.0f%%) — the rest were free"
        % (len(out), esc, 100.0 * esc / max(1, len(out))))
    return out


def _ocr_backend():
    try:
        import ocr as _o
        return _o.backend_name()
    except Exception:
        return "none"


# Vision's value is the fields it can actually READ off the label: origin(COO)/region/varietal/ABV. If the
# structured pull already has these, we're confident → skip (that's "clearly Napa Cabernet 12.5%, don't run").
# UPC is deliberately NOT in this gate — front-label shots don't show the barcode, and UPC is the structured-
# source + UPC-engine + back-label track, not front-label vision.
_CONF_GEO = tuple((os.environ.get("VISION_CONF_FIELDS") or "origin,region,varietal").split(","))


def run(source="binnys_products", limit=200, workers=6, only_gaps=True, log=print):
    """Vision-extract only LOW-CONFIDENCE imaged products — ones the structured pull left GAPS on (missing
    origin/region/varietal/abv). Products already resolved (Napa + Cabernet + 12.5%) are skipped: vision is
    per-image $$, so we only spend it where we're unsure. Placeholder images are skipped too."""
    import placeholders
    cols = set(warehouse.query(source, "SELECT * FROM t LIMIT 1")[0].keys())
    gap = ["(%s IS NULL OR %s='')" % (c, c) for c in _CONF_GEO if c in cols]
    where = "image IS NOT NULL AND image<>''" + ((" AND (" + " OR ".join(gap) + ")") if (only_gaps and gap) else "")
    ph = placeholders.placeholder_images(source, image_col="image", key_col="sku", log=log)
    rows = warehouse.query(source, "SELECT DISTINCT sku, name, image FROM t WHERE %s LIMIT %d"
                           % (where, int(limit) * 3))
    rows = [r for r in rows if r["image"] not in ph][:int(limit)]
    log("[vision] %s: %d LOW-CONFIDENCE imaged products to read (confident ones + placeholders skipped)"
        % (source, len(rows)))

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
    ap.add_argument("--engine", choices=["claude", "tiered"],
                    default=("tiered" if os.environ.get("CV_TIERED") else "claude"),
                    help="'claude' = Opus per image -> label_extract; 'tiered' = barcode+OCR/rules "
                         "first, Claude only on low confidence -> cv_reads (Phase 0)")
    a = ap.parse_args()
    (run_tiered if a.engine == "tiered" else run)(source=a.source, limit=a.limit, workers=a.workers)
