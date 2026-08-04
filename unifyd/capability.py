#!/usr/bin/env python3
"""capability.py — make QUIET DEGRADES loud.

THE BUG CLASS THIS EXISTS FOR
  Optional dependencies are imported inside `try: … except Exception: return []` so a partial install
  degrades instead of crashing. That is the right runtime behaviour and the wrong reporting behaviour:
  the run still "succeeds", the rows still land, and the missing capability is INVISIBLE. You cannot
  tell a run that read no DataMatrix from a run where no DataMatrix existed.

  Caught live 2026-07-27: `pylibdmtx` was installed and `libdmtx.so.0` loaded, but the import died on
  the image with "No module named 'distutils'" (Python 3.12 removed it; pylibdmtx imports it). Every
  label decoded QR-only, reported as full 2D coverage, and nothing anywhere said so. An AST sweep then
  found 29 more sites with the same shape across 18 modules.

THE FIX
  Sources DECLARE the optional libraries they need (`caps=[…]` in source_registry, the exact mirror of
  the proven `requires=[env]` → "no-creds" convention). `run_sources` probes them and, when one is
  missing, the run is marked **degraded** with a warning naming what silently stopped working. The
  library stays optional — nothing is skipped and no data is lost — but the degradation is now on the
  record instead of in nobody's head.

  `python capability.py` prints this host's capability table.
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# name -> (import target, what it enables, what SILENTLY degrades without it)
PROBES = {
    "pillow":     ("PIL.Image", "load label/product images",
                   "NO image decoding at all — every barcode, 2D code and OCR read returns empty"),
    "pyzbar":     ("pyzbar.pyzbar", "decode 1D barcodes + QR",
                   "no UPC off label art and no QR — labels look like they carry no barcode"),
    "pylibdmtx":  ("pylibdmtx.pylibdmtx", "decode GS1 DataMatrix (zbar cannot)",
                   "GS1 2D coverage silently drops to QR-ONLY"),
    "pytesseract": ("pytesseract", "OCR label text",
                    "no gov_warning / claims / ocr_chars — reads as 'the label had no text'"),
    "pypdf":      ("pypdf", "extract text from PDF press releases (DAM facts)",
                   "PDF press releases contribute NO facts — launch dates/prices/markets stay at "
                   "folder-year precision and it reads as 'the release said nothing'"),
    "curl_cffi":  ("curl_cffi", "browser-TLS-fingerprint HTTP",
                   "anti-bot fetches fall back to plain urllib and get blocked or thin results"),
    "pyarrow":    ("pyarrow", "Parquet read/write + dataset listing",
                   "dataset listing and Parquet footer stats come back EMPTY, so drift checks pass vacuously"),
    "patchright": ("patchright.sync_api", "stealth headful browser",
                   "headful (klass=mac) sources cannot drive a real browser"),
    "anthropic":  ("anthropic", "Claude-backed extraction / analysis",
                   "AI fallbacks silently do nothing; deterministic-only results reported as complete"),
    "bs4":        ("bs4", "lenient HTML parsing",
                   "HTML parsers fall back to regex or return nothing"),
    "certifi":    ("certifi", "current CA bundle",
                   "TLS verification may fail on hosts with an incomplete chain"),
}

_CACHE = {}


def probe(name):
    """{'ok': bool, 'detail': str} for one capability. Cached — imports are not free."""
    if name in _CACHE:
        return _CACHE[name]
    target = (PROBES.get(name) or (name, "", ""))[0]
    try:
        importlib.import_module(target)
        out = {"ok": True, "detail": "ok"}
    except Exception as e:
        out = {"ok": False, "detail": "%s: %s" % (type(e).__name__, str(e)[:120])}
    _CACHE[name] = out
    return out


def status(names=None):
    """{name: {'ok','detail','enables','degrades'}} for `names` (default: every known capability)."""
    out = {}
    for n in (names if names is not None else PROBES):
        p = probe(n)
        _, enables, degrades = PROBES.get(n, (n, "", ""))
        out[n] = {"ok": p["ok"], "detail": p["detail"], "enables": enables, "degrades": degrades}
    return out


def missing(names):
    """The subset of `names` that is NOT importable on this host."""
    return [n for n in (names or []) if not probe(n)["ok"]]


def warnings_for(names):
    """Human warning lines for whatever is missing — what stopped working, not just what's absent.
    Empty list when everything is present, so a caller can use it directly as a degraded-check."""
    out = []
    for n in missing(names):
        _, _, degrades = PROBES.get(n, (n, "", "unknown effect"))
        out.append("capability %r missing (%s) — %s" % (n, probe(n)["detail"], degrades or "unknown effect"))
    return out


def main():
    st = status()
    ok = sum(1 for v in st.values() if v["ok"])
    print("capability table for %s  (%d/%d present)\n" % (os.uname().nodename[:40], ok, len(st)))
    for n, v in sorted(st.items(), key=lambda kv: (kv[1]["ok"], kv[0])):
        print("  %-12s %-8s %s" % (n, "OK" if v["ok"] else "MISSING", v["detail"] if not v["ok"] else v["enables"]))
        if not v["ok"]:
            print("  %-12s %-8s ↳ silently degrades: %s" % ("", "", v["degrades"]))
    return 0 if all(v["ok"] for v in st.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
