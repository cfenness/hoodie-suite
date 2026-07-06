"""ttb_cola_labels.py — read a UPC off a COLA label image.

`extract_upc_from_label(img_bytes)` decodes the label's BARCODE (a UPC on a label is a
barcode, not text — barcode decode beats text OCR for this). Uses pyzbar; returns '' when
the dependency is missing or no readable barcode is present, so enrichment degrades
gracefully instead of failing.

Optional deps (only needed if you pass --ocr to ttb_enrich.py):
    brew install zbar                 # macOS system lib
    pip install pyzbar pillow
"""


def _check_digit_ok(code):
    """Standard mod-10 check for UPC-A(12)/EAN-13(13)/EAN-8(8): weight 3,1,… from the right."""
    if len(code) not in (8, 12, 13):
        return True                       # UPC-E etc. — don't reject on check digit
    d = [int(c) for c in code]
    body, chk = d[:-1], d[-1]
    total = sum(x * (3 if i % 2 == 0 else 1) for i, x in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == chk


def _plausible_upc(code):
    """Reject the placeholder barcodes companies print before a real UPC is assigned —
    all-zeros, all-same-digit, trivial sequences — so they don't masquerade as real data."""
    if not code or not code.isdigit():
        return False
    if len(set(code)) <= 1:               # 000000000000, 111111111111, …
        return False
    asc = "01234567890123"
    if code in asc or code in asc[::-1]:  # 123456789012 / 210987654321 style dummies
        return False
    return _check_digit_ok(code)


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
        if data.isdigit() and len(data) in (8, 12, 13, 14) and _plausible_upc(data):
            return data
    return ""
