#!/usr/bin/env python3
"""cv/rules.py — Tier 1 brain (Phase 0): deterministic field extraction from OCR text.

This is the *placeholder* brain that reads structured facts off OCR text with regex + the shared
normalizers, so Phase 0 already covers the easy, regular bev-alc patterns (ABV, net contents, the
government warning, a printed barcode number, simple origin phrasing) for $0. In Phase 2 the fine-
tuned Florence-2 extractor replaces THIS function behind the same `read()` seam — the tier
structure, confidence gate, and provenance don't change. Never guesses: a field it can't match
stays absent (the escalation path fills it). Reuses `cv/trainset.py` normalizers so a value means
the same thing whether it came from the registry or from pixels.
"""
import os
import re
import sys

_CV = os.path.dirname(os.path.abspath(__file__))
_UNIFYD = os.path.dirname(_CV)
for _p in (_CV, _UNIFYD):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import trainset  # norm_abv, norm_net_ml  (shared, deterministic)

_GOV = re.compile(r"government\s+warning", re.I)
_ABV = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:alc|abv|by\s*vol|vol)", re.I)
_ABV2 = re.compile(r"alc(?:ohol)?\.?\s*(?:by\s*vol\.?\s*)?[:=]?\s*(\d{1,2}(?:\.\d+)?)\s*%?", re.I)
_NET = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|milliliters?|millilitres?|cl|l|liters?|litres?|fl\.?\s*oz|oz)\b", re.I)
_ORIGIN = re.compile(r"(?:product of|distilled in|produce of|imported from|made in)\s+"
                     r"([A-Z][A-Za-z .'&-]{2,28})", re.I)


def extract(text):
    """OCR text -> {field: value} for the fields we can read deterministically. Absent = not found."""
    t = text or ""
    flat = re.sub(r"[^0-9]", "", t)
    out = {}

    m = _ABV.search(t) or _ABV2.search(t)
    if m:
        v = trainset.norm_abv(m.group(0))
        if v is not None:
            out["abv"] = v

    m = _NET.search(t)
    if m:
        v = trainset.norm_net_ml(m.group(0))
        if v is not None:
            out["net_ml"] = v

    m = re.search(r"(\d{12,13})", flat)          # a printed UPC/EAN number in the text
    if m:
        out["upc"] = m.group(1)

    m = _ORIGIN.search(t)
    if m:
        out["origin"] = re.sub(r"\s+", " ", m.group(1)).strip(" .")

    if _GOV.search(t):
        out["gov_warning"] = True

    return out


if __name__ == "__main__":       # tiny self-check
    sample = ("KENTUCKY STRAIGHT BOURBON WHISKEY\nDISTILLED IN KENTUCKY\n"
              "ALC. 45% BY VOL\n750 mL\nGOVERNMENT WARNING: (1) According to the Surgeon General...")
    print(extract(sample))
