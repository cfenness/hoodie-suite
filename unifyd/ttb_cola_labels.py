"""ttb_cola_labels.py — read a UPC off a COLA label image.

`extract_upc_from_label(img_bytes)` decodes the label's BARCODE (a UPC on a label is a
barcode, not text — barcode decode beats text OCR for this). Uses pyzbar; returns '' when
the dependency is missing or no readable barcode is present, so enrichment degrades
gracefully instead of failing.

Optional deps (only needed if you pass --ocr to ttb_enrich.py):
    brew install zbar                 # macOS system lib
    pip install pyzbar pillow
"""


def extract_upc_from_label(img_bytes):
    if not img_bytes or len(img_bytes) < 64:
        return ""
    try:
        import io
        from PIL import Image
        from pyzbar.pyzbar import decode, ZBarSymbol
    except Exception:
        return ""            # pyzbar/Pillow (or the zbar lib) not installed — no-op
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
