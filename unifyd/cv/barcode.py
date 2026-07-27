#!/usr/bin/env python3
"""cv/barcode.py — Tier 0: decode a UPC/EAN barcode from an image. Deterministic, free, no model.

The cheapest, highest-ROI vision op: if a real barcode is visible we get the product's identity
for $0 and short-circuit straight to the UPC-resolution engine (`upc.classify`) — no OCR, no LLM.
Placeholder / invalid barcodes (companies print a dummy before a real UPC is assigned) are
recognized as such rather than trusted. Generalizes the COLA-specific `ttb_cola_labels.py` to any
image bytes; that module can be repointed here later (see docs/PROPRIETARY-CV.md).

Optional deps (module no-ops without them, so callers degrade to OCR/escalation):
    brew install zbar && pip install pyzbar pillow
"""
import io
import os
import sys

_CV = os.path.dirname(os.path.abspath(__file__))
_UNIFYD = os.path.dirname(_CV)
for _p in (_CV, _UNIFYD):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import upc as _upc                      # UPC-resolution engine: classify(digits) -> status
except Exception:
    _upc = None


def decode(img_bytes):
    """(digits, status). status from upc.classify ('valid'|'placeholder'|'bad_check'|…);
    ('', 'none') when no barcode is readable; ('<digits>', 'unknown') if the classifier is absent."""
    raw = _decode_raw(img_bytes)
    if not raw:
        return ("", "none")
    return (raw, _upc.classify(raw) if _upc else "unknown")


def upc_if_valid(img_bytes):
    """The digits ONLY when the barcode is a real, valid UPC/EAN — '' otherwise."""
    d, s = decode(img_bytes)
    return d if s == "valid" else ""


def _decode_raw(img_bytes):
    """First readable UPC-A/UPC-E/EAN-13/EAN-8 barcode's digits, or ''."""
    if not img_bytes or len(img_bytes) < 64:
        return ""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode as zdecode, ZBarSymbol
    except Exception:
        return ""                            # pyzbar / Pillow / zbar not installed — no-op
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return ""
    want = [ZBarSymbol.EAN13, ZBarSymbol.UPCA, ZBarSymbol.UPCE, ZBarSymbol.EAN8]
    try:
        syms = zdecode(im, symbols=want)
    except Exception:
        try:
            syms = zdecode(im)
        except Exception:
            return ""
    for d in syms:
        try:
            data = d.data.decode("ascii", "ignore").strip()
        except Exception:
            continue
        if data.isdigit() and len(data) in (8, 12, 13, 14):
            return data
    return ""
