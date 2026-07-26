"""ttb_cola_labels.py — decode the barcode on a COLA label image.

`decode_barcode(img_bytes) -> (raw_digits, status)` reads the label's BARCODE with pyzbar and
classifies it via upc.classify, so a placeholder / invalid barcode is recognized rather than
trusted (companies routinely print a dummy barcode before a real UPC is assigned).
`extract_upc_from_label(img_bytes)` returns the code ONLY when it's a real UPC ('' otherwise).

Optional deps (only for the barcode read; the module no-ops without them):
    brew install zbar                 # macOS system lib
    pip install pyzbar pillow
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upc as _upc


def _decode(img_bytes):
    """Return the raw digits of the first readable UPC/EAN barcode, or ''."""
    if not img_bytes or len(img_bytes) < 64:
        return ""
    try:
        import io
        from PIL import Image
        from pyzbar.pyzbar import decode, ZBarSymbol
    except Exception:
        return ""            # pyzbar / Pillow / zbar not installed — no-op
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return ""
    wanted = [ZBarSymbol.EAN13, ZBarSymbol.UPCA, ZBarSymbol.UPCE, ZBarSymbol.EAN8]
    try:
        syms = decode(im, symbols=wanted)
    except Exception:
        try:
            syms = decode(im)
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


def decode_barcode(img_bytes):
    """(raw_digits, status) — status from upc.classify ('valid'|'placeholder'|'bad_check'|…)."""
    raw = _decode(img_bytes)
    return (raw, _upc.classify(raw)) if raw else ("", "none")


def extract_upc_from_label(img_bytes):
    raw, status = decode_barcode(img_bytes)
    return raw if status == "valid" else ""


# ── text OCR (Tesseract) — reads the WORDS off the label (vs the barcode). Fills claims / gov_warning /
# ocr_chars. Optional: no-ops cleanly if tesseract / pytesseract / Pillow aren't installed. ─────────────────
_CLAIM_KW = ("gluten free", "gluten-free", "organic", "estate bottled", "hand crafted", "handcrafted",
             "small batch", "kosher", "vegan", "non-gmo", "sustainably", "biodynamic", "single barrel",
             "cask strength", "barrel strength", "unfiltered", "barrel aged", "barrel-aged", "reserve",
             "limited edition", "old vine", "single origin", "grand cru", "bottled in bond", "sulfite",
             "cold filtered", "triple distilled", "gold medal", "award winning")


def read_label_text(img_bytes):
    """OCR the label image with Tesseract → {'ocr_chars', 'gov_warning', 'claims'}. Returns empties (never
    raises) if the OCR stack is missing or the image is unreadable — so the enrich pass degrades to barcode +
    ABV only. gov_warning is reliable (fixed legal boilerplate); claims = detected keywords from the raw text."""
    import re
    try:
        import io
        import pytesseract
        from PIL import Image
    except Exception:
        return {"ocr_chars": "", "gov_warning": "", "claims": ""}
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("L")     # greyscale helps OCR on label art
        txt = pytesseract.image_to_string(im)
    except Exception:
        return {"ocr_chars": "", "gov_warning": "", "claims": ""}
    flat = re.sub(r"\s+", " ", txt or "").strip()
    gw = ""
    m = re.search(r"GOVERNMENT WARNING.*?(?:machinery|health problems)[.,)]?", flat, re.I)
    if m:
        gw = m.group(0)[:600]
    elif re.search(r"surgeon general", flat, re.I):
        gw = "GOVERNMENT WARNING (detected, partial OCR)"
    claims = "|".join(sorted({k for k in _CLAIM_KW if re.search(r"\b" + re.escape(k) + r"\b", flat, re.I)}))
    return {"ocr_chars": flat[:2000], "gov_warning": gw, "claims": claims}
