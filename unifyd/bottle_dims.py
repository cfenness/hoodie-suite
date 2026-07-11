"""bottle_dims.py — bev-alc bottle physical-dimension enrichment from FREE/public sources only.

The hard part of sourcing dims free is MATCHING a SKU to a glass-manufacturer STOCK MOLD; the numbers then come
from the maker's public spec. A bottle SHOT (which we already collect from DoorDash / Shopify / retail / COLA
labels) makes shape a READ, not a guess. Discipline: only return dims on a real stock-mold match; a proprietary/
custom bottle (vision-detected) is flagged MANUAL, never guessed. Fits the 3-layer prep model:
    deterministic (exact stock match) -> AI cluster (fuzzy shape / visual similarity) -> human gate (proprietary).
Every field carries {value, source, confidence, ts}. Sources: glass-supplier catalogs (Berlin Packaging,
TricorBraun) for the numbers; vision for shape + stock-vs-proprietary; GS1 Verify anchors identity (separate).
No GDSN / licensed data.
"""
import base64, json, os, re, time, urllib.parse, urllib.request

SHAPE_CLASSES = ["Bordeaux", "Burgundy", "Claret", "Champagne/Sparkling", "Spirits Round", "Cognac/Brandy",
                 "Flask/Flint", "Hock/Alsace", "Decanter", "Proprietary/Custom", "Other"]


def _anthropic(content, model="claude-opus-4-8", max_tokens=400):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=90).read())
    return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")


def _media_type(data, url=""):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    u = url.lower()
    return "image/png" if ".png" in u else ("image/webp" if ".webp" in u else "image/jpeg")


def fetch_image(url, bd_key=None):
    """Direct fetch; fall back to Bright Data Unlocker if the CDN blocks."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = urllib.request.urlopen(req, timeout=25).read()
        if d and len(d) > 800:
            return d
    except Exception:
        pass
    if bd_key:
        try:
            body = json.dumps({"zone": "cli_unlocker", "url": url, "format": "raw"}).encode()
            r = urllib.request.Request("https://api.brightdata.com/request", data=body,
                                       headers={"Authorization": "Bearer " + bd_key, "Content-Type": "application/json"})
            return urllib.request.urlopen(r, timeout=40).read()
        except Exception:
            return None
    return None


def classify_bottle(image_bytes, name="", url=""):
    """Vision read of a bottle SHOT -> {shape_class, is_proprietary, confidence, reason}. This is the MATCHER
    (reads shape), NOT a measurer — absolute dims never come from a photo. Opt-in on ANTHROPIC_API_KEY."""
    if not image_bytes:
        return None
    content = [{"type": "image", "source": {"type": "base64", "media_type": _media_type(image_bytes, url),
                                            "data": base64.b64encode(image_bytes).decode()}},
               {"type": "text", "text":
                "Product photo of a beverage-alcohol bottle (\"%s\"). Judge the GLASS BOTTLE shape only, ignore "
                "the label/liquid. Return ONLY JSON with ALL four keys: {\"shape_class\": one of %s, "
                "\"is_proprietary\": true if a CUSTOM/branded mold rather than a standard stock shape, "
                "\"confidence\": a REQUIRED number 0.0-1.0, \"reason\": short phrase}." % (name, SHAPE_CLASSES)}]
    try:
        return json.loads(re.search(r"\{.*\}", _anthropic(content), re.S).group(0))
    except Exception:
        return None


def _field(value, source, confidence):
    return {"value": value, "source": source, "confidence": confidence, "ts": None}


# ── Glass-catalog spec parser (Berlin Packaging) — the SOURCE of the numbers for a matched stock mold ──
def _unlock(url, bd_key, dataf=None):
    body = {"zone": "cli_unlocker", "url": url, "format": "raw"}
    if dataf:
        body["data_format"] = dataf
    r = urllib.request.Request("https://api.brightdata.com/request", data=json.dumps(body).encode(),
                               headers={"Authorization": "Bearer " + bd_key, "Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=60).read().decode("utf-8", "replace")


_IN_MM = 25.4
_OZ_G = 28.3495


def _to_mm(s):
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*(mm|cm|in|inch|\")", s, re.I)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).lower()
    return round(v * (_IN_MM if u.startswith(("in", '"')) else (10 if u == "cm" else 1)), 1)


def _to_ml(s):
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*(ml|l|oz)", s, re.I)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).lower()
    return round(v * (1000 if u == "l" else (_OZ_G if u == "oz" else 1)), 0)


def _to_g(s):
    if not s:
        return None
    m = re.search(r"([\d.]+)\s*(g|gram|oz|lb)", s, re.I)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2).lower()
    return round(v * (_OZ_G if u == "oz" else (453.6 if u == "lb" else 1)), 0)


def parse_glass_specs(html):
    """Berlin Packaging marks specs as data-custom-field-name/-value pairs -> normalized dims (mm, mL, g)."""
    fields = dict(re.findall(r'data-custom-field-name="([^"]+)"\s+data-custom-field-value="([^"]*)"', html or ""))

    def g(*names):
        for n in names:
            for k, v in fields.items():
                if n.lower() in k.lower() and (v or "").strip():
                    return v.strip()
        return None
    # capacity: prefer a field VALUE stated in mL (the oz variant is named "Std Capacity in Oz.")
    cap = next((v for k, v in fields.items() if "capacity" in k.lower() and re.search(r"\d\s*ml\b", v, re.I)), None) \
        or next((v for v in fields.values() if re.fullmatch(r"\s*\d+\s*ml\s*", v or "", re.I)), None)
    # weight: some pages bury "Weight: 560 grams" in a mis-named field -> scan all values
    wt = next((v for v in fields.values() if re.search(r"[\d.]+\s*(?:g|gram)s?\b", v or "", re.I)), None) or g("Weight")
    cp = g("Case Pack", "Case Quantity", "Master Case", "Pack") or ""
    return {"height_mm": _to_mm(g("Height", "Overall Height")),
            "diameter_mm": _to_mm(g("Diameter", "Base Diameter", "Body Diameter")),
            "capacity_ml": _to_ml(cap),
            "finish": g("Neck Finish", "Finish Type", "Closure"),
            "weight_g": _to_g(wt),
            "case_pack": (int(re.search(r"\d+", cp).group(0)) if re.search(r"\d", cp) else None),
            "_source_fields": len(fields)}


# supplier catalogs by domain — Berlin Packaging first (clean custom-field markup)
_CATALOGS = ["berlinpackaging.com", "tricorbraun.com"]


def glass_catalog_specs(size, shape, bd_key, log=print):
    """Find a stock bottle matching size+shape in a glass catalog -> its published specs. Google-scoped to a
    catalog domain, then parse. Returns (specs, source_url) or (None, None) — no match -> caller flags manual."""
    q = "%s %s bottle site:berlinpackaging.com" % (size, shape)
    try:
        j = json.loads(_unlock("https://www.google.com/search?q=%s&brd_json=1&gl=us&hl=en"
                               % urllib.parse.quote(q), bd_key))
    except Exception:
        return None, None
    for o in (j.get("organic") or []):
        u = o.get("link") or ""
        if "berlinpackaging.com" in u and re.search(r"-\d{3,}-[kK]/?(?:\?|$)", u):
            try:
                specs = parse_glass_specs(_unlock(u, bd_key))
                if specs.get("height_mm") or specs.get("weight_g"):
                    return specs, u
            except Exception:
                pass
    return None, None


if __name__ == "__main__":
    print("bottle_dims — import classify_bottle / fetch_image; run the demo via the harness.")
