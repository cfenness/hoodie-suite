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
import base64, json, os, re, time, urllib.request

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


if __name__ == "__main__":
    print("bottle_dims — import classify_bottle / fetch_image; run the demo via the harness.")
