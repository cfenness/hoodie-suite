#!/usr/bin/env python3
"""ue_ids.py — UberEats store ids come in TWO ENCODINGS of the same value. Convert between them.

THE BUG THIS EXPLAINS (measured 2026-08-03). 208,624 UberEats stores had landed observations, and
only 15 of them could be matched to the store universe. It looked like a catastrophic capture gap:
"~188k observed stores were never landed as outlets". It was nothing of the kind.

UberEats' store URLs carry the store id **base64url-encoded**, not hex-dashed:

    https://www.ubereats.com/store/the-throwback-710-amsterdam-avenue/3GYoBDgAU6-me98dDz_kSw
                                                                     ^^^^^^^^^^^^^^^^^^^^^^
    3GYoBDgAU6-me98dDz_kSw  ->  dc662804-3800-53af-a67b-df1d0f3fe44b

`ue_sitemap` harvests that 22-character token into `store_uuid`; the catalog crawl and the BFF report
the canonical hex-dashed form. Same identifier, two spellings, and every join between them missed.

How it was caught: the UUID *version nibble*. A canonical UUID always has '4' or '5' at position 15.
Observations showed exactly that (5: 124,711 / 4: 83,888) while the sitemap column's 15th character
was uniformly random across the whole base64 alphabet ('F', 'W', 'P', 'Y', 'd' … ~12,000 each) —
the signature of an encoded blob, not a UUID.

Verified at scale: all 755,032 sitemap tokens decode to well-formed UUIDs (100%), and 208,599 of
208,624 observation stores (100.0%) then match. Nothing needs re-scraping.

This is a LOSSLESS ENCODING, not a heuristic — 22 base64url chars are exactly 16 bytes — so a match
made this way is as strong as an equality match, and callers should treat it as confidence 1.0.
"""
import base64
import re
import uuid

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def token_to_uuid(tok):
    """'3GYoBDgAU6-me98dDz_kSw' -> 'dc662804-3800-53af-a67b-df1d0f3fe44b'. None if not a token."""
    if not tok:
        return None
    t = str(tok).strip()
    if not TOKEN_RE.match(t):
        return None
    try:
        return str(uuid.UUID(bytes=base64.urlsafe_b64decode(t + "==")))
    except Exception:
        return None


def uuid_to_token(u):
    """'dc662804-3800-53af-a67b-df1d0f3fe44b' -> '3GYoBDgAU6-me98dDz_kSw'. None if not a UUID."""
    if not u:
        return None
    s = str(u).strip()
    if not UUID_RE.match(s):
        return None
    try:
        return base64.urlsafe_b64encode(uuid.UUID(s).bytes).decode("ascii").rstrip("=")
    except Exception:
        return None


def canonical(v):
    """Whichever encoding comes in, return the canonical hex-dashed UUID (or None)."""
    if not v:
        return None
    s = str(v).strip()
    if UUID_RE.match(s):
        return s.lower()
    return token_to_uuid(s)


# ── SQL forms, for joins that must stay in DuckDB rather than round-trip through Python ──────────
def sql_token_to_uuid(col):
    """SQL decoding a base64url token column to a canonical UUID. Mirrors token_to_uuid()."""
    b = ("HEX(FROM_BASE64(REPLACE(REPLACE(CAST(%s AS VARCHAR),'-','+'),'_','/') || '=='))" % col)
    return ("CASE WHEN LENGTH(CAST(%s AS VARCHAR)) = 22 THEN LOWER("
            "SUBSTR(%s,1,8) || '-' || SUBSTR(%s,9,4) || '-' || SUBSTR(%s,13,4) || '-' || "
            "SUBSTR(%s,17,4) || '-' || SUBSTR(%s,21,12)) END" % (col, b, b, b, b, b))


def sql_canonical(col):
    """SQL returning the canonical UUID whichever encoding `col` holds."""
    return ("CASE WHEN REGEXP_MATCHES(CAST(%s AS VARCHAR), "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') "
            "THEN LOWER(CAST(%s AS VARCHAR)) ELSE %s END" % (col, col, sql_token_to_uuid(col)))


if __name__ == "__main__":
    import sys
    for v in sys.argv[1:]:
        print("%-40s -> %s" % (v, canonical(v)))
