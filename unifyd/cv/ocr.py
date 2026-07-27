#!/usr/bin/env python3
"""cv/ocr.py — Tier 1 text: read the characters off a label image. Self-hosted, ~$0, pluggable.

Backends (lazy, optional — `read()` degrades to ('', 0.0) if none is installed, so the tiered
engine still runs on barcode + escalation):
    - 'paddle'    PaddleOCR    (best on curved/stylized labels; `pip install paddleocr paddlepaddle`)
    - 'tesseract' pytesseract  (lighter; needs the tesseract binary + `pip install pytesseract`)
    - 'none'      disabled

Choose with env CV_OCR_BACKEND=paddle|tesseract|auto|none (default 'auto' = paddle→tesseract).

IMPORTANT (deploy hygiene): the heavy OCR/vision models run on the **Mac or a worker**, NOT in the
slim Fly web image — keep these deps out of `unifyd/requirements.txt`. The web tier calls the
tiered engine only for low-volume, on-demand reads or escalates; batch label-reading runs locally.
"""
import io
import os

_BACKEND = None            # cached (name, callable)


def _load():
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    want = (os.environ.get("CV_OCR_BACKEND") or "auto").lower()
    order = {"auto": ["paddle", "tesseract"], "paddle": ["paddle"],
             "tesseract": ["tesseract"], "none": []}.get(want, ["paddle", "tesseract"])
    for name in order:
        fn = _try_paddle() if name == "paddle" else _try_tesseract()
        if fn:
            _BACKEND = (name, fn)
            return _BACKEND
    _BACKEND = ("none", None)
    return _BACKEND


def backend_name():
    return _load()[0]


def read(img_bytes):
    """(text, mean_confidence 0..1). ('', 0.0) when no backend is available or nothing is read."""
    name, fn = _load()
    if not fn or not img_bytes:
        return ("", 0.0)
    try:
        return fn(img_bytes)
    except Exception:
        return ("", 0.0)


def _try_paddle():
    try:
        from paddleocr import PaddleOCR
    except Exception:
        return None
    eng = {}

    def run(b):
        if "o" not in eng:
            eng["o"] = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        import numpy as np
        from PIL import Image
        im = np.array(Image.open(io.BytesIO(b)).convert("RGB"))
        res = eng["o"].ocr(im, cls=True) or []
        lines, confs = [], []
        for page in res:
            for det in (page or []):
                try:
                    txt, cf = det[1][0], float(det[1][1])
                except Exception:
                    continue
                lines.append(txt)
                confs.append(cf)
        return ("\n".join(lines), (sum(confs) / len(confs)) if confs else 0.0)
    return run


def _try_tesseract():
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return None

    def run(b):
        import pytesseract
        from PIL import Image
        im = Image.open(io.BytesIO(b)).convert("RGB")
        data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)
        words, confs = [], []
        for w, c in zip(data.get("text", []), data.get("conf", [])):
            try:
                c = float(c)
            except (TypeError, ValueError):
                c = -1
            if w and w.strip() and c >= 0:
                words.append(w.strip())
                confs.append(c / 100.0)
        return (" ".join(words), (sum(confs) / len(confs)) if confs else 0.0)
    return run
