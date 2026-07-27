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


_1D = ("EAN13", "UPCA", "UPCE", "EAN8")
_2D = ("QRCODE", "DATAMATRIX")


def decoder_status():
    """Which barcode decoders are actually LIVE → {'pyzbar': str, 'pylibdmtx': str, 'symbologies': [...]}.

    _symbols() deliberately swallows import errors so a partial install degrades instead of failing —
    which means a missing decoder is otherwise INVISIBLE and we'd silently under-read labels forever.
    (That is exactly what happened: pylibdmtx imported fine locally but died on the image with
    "No module named 'distutils'" — Python 3.12 removed it and pylibdmtx imports it — while
    libdmtx.so.0 loaded fine. QR-only coverage, reported as 2D coverage.) Call this to state the real
    capability rather than assume it."""
    st = {"pyzbar": "", "pylibdmtx": "", "symbologies": []}
    try:
        from pyzbar.pyzbar import ZBarSymbol
        have = [n for n in _1D + _2D if getattr(ZBarSymbol, n, None) is not None]
        st["pyzbar"] = "ok"
        st["symbologies"] += have
    except Exception as e:
        st["pyzbar"] = "%s: %s" % (type(e).__name__, str(e)[:80])
    try:
        from pylibdmtx.pylibdmtx import decode as _dm       # noqa: F401
        st["pylibdmtx"] = "ok"
        st["symbologies"].append("DATAMATRIX")
    except Exception as e:
        st["pylibdmtx"] = "%s: %s" % (type(e).__name__, str(e)[:80])
    return st


def _symbols(img_bytes):
    """[(symbol_type, payload)] for every readable 1D *and* 2D symbol on the label, or [].

    TWO decoders, because no single one covers GS1's 2D pair: **zbar does not implement DataMatrix**
    (its ZBarSymbol enum has no DATAMATRIX member), so pyzbar gives us the 1D symbologies + QR, and
    pylibdmtx (libdmtx) gives us GS1 DataMatrix. Each is independently optional and no-ops when its
    system lib is absent, so a partial install degrades to fewer symbol types rather than failing."""
    if not img_bytes or len(img_bytes) < 64:
        return []
    try:
        import io
        from PIL import Image
    except Exception:
        return []            # Pillow absent — nothing can be decoded
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception:
        return []
    out = []

    try:                                     # ── pyzbar: 1D + QR ──
        from pyzbar.pyzbar import decode, ZBarSymbol
        wanted = [s for s in (getattr(ZBarSymbol, n, None) for n in _1D + _2D) if s is not None]
        try:
            syms = decode(im, symbols=wanted) if wanted else decode(im)
        except Exception:
            syms = decode(im)
        for d in syms:
            try:
                data = d.data.decode("utf-8", "ignore").strip()
            except Exception:
                continue
            if data:
                out.append((getattr(d, "type", "") or "", data))
    except Exception:
        pass                                 # pyzbar / libzbar0 missing — fall through to DataMatrix

    try:                                     # ── pylibdmtx: GS1 DataMatrix (zbar can't) ──
        from pylibdmtx.pylibdmtx import decode as dm_decode
        seen = {p for _, p in out}
        # timeout so a busy label can't stall the enrich pass; max_count keeps it bounded.
        for d in dm_decode(im, timeout=5000, max_count=4):
            try:
                data = d.data.decode("utf-8", "ignore").strip()
            except Exception:
                continue
            if data and data not in seen:
                seen.add(data)
                out.append(("DATAMATRIX", data))
    except Exception:
        pass                                 # pylibdmtx / libdmtx0b missing — QR-only coverage
    return out


def _decode(img_bytes):
    """Return the raw digits of the first readable UPC/EAN (1D) barcode, or ''. Unchanged behaviour."""
    for typ, data in _symbols(img_bytes):
        if typ in _1D and data.isdigit() and len(data) in (8, 12, 13, 14):
            return data
    return ""


def decode_any(img_bytes):
    """Read EVERY carrier off a label and keep all of it.

    GS1's Sunrise 2027 moves retail POS to 2D codes (QR carrying a GS1 Digital Link, or DataMatrix),
    but the GTIN stays embedded as AI (01) — a 2D code is a new carrier for an identifier we already
    model, not a new identity. `gtin14` resolves whatever encoding was scanned to the GS1 canonical
    form, so a 1D UPC-A and a 2D GTIN-14 land on the SAME item instead of looking like two products.

    Returns {symbols, code_type, payload, gtin, gtin_raw, gtin14, status, digital_link, ais}:
      symbols   EVERY symbol decoded, as [{type, payload}] — a label can carry a 1D barcode AND a QR,
                and both are retained verbatim rather than the first match winning and the rest being
                dropped. `code_type`/`payload` just name whichever symbol resolved the identifier.
      ais       every application identifier found across the 2D codes, captured as read.
    1D is preferred to RESOLVE the identifier (long-proven path); 2D fills in when no 1D is readable.
    """
    syms = _symbols(img_bytes)
    out = {"symbols": [{"type": t, "payload": d} for t, d in syms],
           "code_type": "", "payload": "", "gtin": "", "gtin_raw": "", "gtin14": "",
           "status": "none", "digital_link": "", "ais": {}}
    if not syms:
        return out
    # capture every 2D payload's AIs + the first Digital Link, independent of what resolves the GTIN
    for typ, data in syms:
        if typ in _2D:
            p = _upc.parse_2d(data)
            for k, v in p["ais"].items():
                out["ais"].setdefault(k, v)
            if p["digital_link"] and not out["digital_link"]:
                out["digital_link"] = p["digital_link"]
    for typ, data in syms:                       # 1D first — the proven path
        if typ in _1D and data.isdigit() and len(data) in (8, 12, 13, 14):
            st = _upc.classify(data)
            out.update({"code_type": typ, "payload": data, "gtin_raw": data, "status": st,
                        "gtin": data if st == "valid" else "",
                        "gtin14": _upc.to_gtin14(data) if st == "valid" else ""})
            return out
    for typ, data in syms:                       # then 2D — extract the embedded GTIN
        if typ in _2D:
            p = _upc.parse_2d(data)
            out.update({"code_type": typ, "payload": data, "gtin": p["gtin"],
                        "gtin_raw": p["gtin_raw"], "gtin14": p["gtin14"], "status": p["gtin_status"]})
            return out
    # symbols were read but none carried an identifier — still return them, never silently empty
    out["code_type"], out["payload"] = syms[0][0], syms[0][1]
    return out


def decode_barcode(img_bytes):
    """(raw_digits, status) — status from upc.classify ('valid'|'placeholder'|'bad_check'|…).

    Now also resolves the GTIN out of a 2D code when the label has no readable 1D barcode."""
    r = decode_any(img_bytes)
    if r["code_type"] in _1D:
        return (r["payload"], r["status"])
    if r["gtin"]:
        return (r["gtin"], r["status"])
    return ("", r["status"] if r["code_type"] else "none")


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
