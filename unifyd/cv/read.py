#!/usr/bin/env python3
"""cv/read.py — the tiered vision engine: the permanent seam every reader goes through.

    Tier 0  barcode.decode          → valid UPC short-circuits to the UPC engine (free)
    Tier 1  ocr.read + rules.extract → structured facts off the pixels (free, self-hosted)
    gate    confidence < τ ?         → Tier 2 escalate to `escalate(img_bytes)` (Claude), else stop

Every field carries provenance ('barcode' | 'ocr_rules' | 'claude') and the result carries an
overall confidence + which tier answered. In Phase 2 the fine-tuned Florence-2 extractor replaces
`rules.extract` behind this exact interface — the tiers, gate, and provenance are unchanged, so the
cost curve bends (Tier-2's share shrinks as the model improves) without any caller changing.

    read(image, escalate=claude_fn) -> {fields, confidence, tier, provenance, needs_escalation}

`image` is bytes or a URL (str). `escalate` is optional: a callable(img_bytes)->{field:value}
(wire the existing label_vision.extract here). Nothing is written here — the caller lands results
with provenance, per the never-overwrite rule.
"""
import os
import sys
import urllib.request

_CV = os.path.dirname(os.path.abspath(__file__))
_UNIFYD = os.path.dirname(_CV)
for _p in (_CV, _UNIFYD):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import barcode
import ocr
import rules

# the label-read fields whose coverage drives the confidence gate (gov_warning is a bonus, not gated)
WANT = ("abv", "net_ml", "upc", "origin")
TAU = float(os.environ.get("CV_TAU", "0.6"))


def _fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _confidence(fields, ocr_conf):
    """Heuristic for Phase 0 (rules): coverage of WANT, floored by a valid barcode, scaled by OCR
    quality. Phase 2's model emits a calibrated confidence and this is replaced."""
    have = sum(1 for k in WANT if fields.get(k) not in (None, "", False))
    cov = have / len(WANT)
    base = 0.15 + 0.70 * cov
    if fields.get("upc"):
        base = max(base, 0.55)                      # a real barcode is strong identity evidence
    if ocr_conf:
        base *= (0.6 + 0.4 * min(1.0, ocr_conf / 0.6))
    else:
        base *= 0.85
    return round(min(0.95, base), 3)


def read(image, escalate=None, tau=None, ocr_backend=None):
    tau = TAU if tau is None else tau
    img = image if isinstance(image, (bytes, bytearray)) else _fetch(image)
    fields, prov = {}, {}

    # Tier 0 — barcode
    digits, status = barcode.decode(img)
    if status == "valid":
        fields["upc"] = digits
        prov["upc"] = "barcode"

    # Tier 1 — OCR + rules
    text, ocr_conf = ocr.read(img)
    for k, v in rules.extract(text).items():
        if k not in fields and v not in (None, "", False):
            fields[k] = v
            prov[k] = "ocr_rules"

    conf = _confidence(fields, ocr_conf)
    needs = conf < tau
    tier = "barcode+ocr" if text else ("barcode" if fields.get("upc") else "none")

    # Tier 2 — escalate to Claude ONLY when we're not confident (and only if a path is wired)
    if needs and escalate is not None:
        try:
            cl = escalate(img) or {}
        except Exception:
            cl = {}
        for k, v in cl.items():
            if v not in (None, "", False) and (k not in fields or prov.get(k) == "ocr_rules"):
                fields[k] = v
                prov[k] = "claude"
        if cl:
            conf = max(conf, 0.9)
            tier = "claude"
            needs = False

    return {"fields": fields, "confidence": conf, "tier": tier,
            "provenance": prov, "needs_escalation": needs, "ocr_conf": round(ocr_conf, 3),
            "ocr_backend": ocr.backend_name()}
