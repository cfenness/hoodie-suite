"""upc.py — UPC/EAN validation, classification, and a self-built company crosswalk.

Two halves, kept honest in the dq.js spirit (every signal is DETERMINISTIC or INFERENCE):

  DETERMINISTIC — structure you can prove from the code alone:
    normalize / check_digit_ok / classify → 'valid' | 'placeholder' | 'bad_check'
    | 'restricted' (variable-weight / internal) | 'coupon' | 'malformed' | 'empty'.
    Companies routinely print placeholder barcodes (all-zeros, dummy sequences) before a
    real UPC is assigned; these are caught here so they never masquerade as real data.

  INFERENCE — the ownable part: a UPC's leading digits are the GS1 company prefix (the brand
    owner). COLA hands us UPC + applicant TOGETHER on every filing, so build_crosswalk() learns
    a prefix -> brand-owner map from ground-truth filings — a bev-alc UPC↔owner registry we OWN
    (generic UPC DBs are food-skewed and don't know who filed the label). assess() then checks
    "does this UPC's prefix agree with the applicant who filed it?" and scores confidence.

Pure stdlib. `python upc.py` runs the self-test.
"""
import re

_DIG = re.compile(r"\D+")


def only_digits(s):
    return _DIG.sub("", str(s or ""))


def normalize(code):
    """Return a canonical 8/12/13/14-digit code, or '' if it isn't one.

    ZERO-STRIP HEAL: spreadsheets/CSV exports eat leading zeros, so a real UPC-A lands as
    10–11 digits and used to be discarded as malformed. Pad back to 12 (or 7→EAN-8) and accept
    ONLY if the padded code passes the mod-10 check — a random pad passes just 10% of the time,
    so the check digit is the proof the zeros were really stripped. This is a TRANSLATION-layer
    heal: callers keep the raw landed value; the healed code lives in the normalized/agg tables."""
    c = only_digits(code)
    if len(c) in (8, 12, 13, 14):
        return c
    if len(c) in (10, 11):                     # zero-stripped UPC-A
        p = c.zfill(12)
        if check_digit_ok(p):
            return p
    if len(c) == 7:                            # zero-stripped EAN-8
        p = c.zfill(8)
        if check_digit_ok(p):
            return p
    return ""


def check_digit_ok(code):
    """Standard GS1 mod-10 check (UPC-A 12 / EAN-13 13 / EAN-8 8); True for other lengths."""
    c = only_digits(code)
    if len(c) not in (8, 12, 13):
        return True
    d = [int(x) for x in c]
    body, chk = d[:-1], d[-1]
    total = sum(x * (3 if i % 2 == 0 else 1) for i, x in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == chk


def with_check_digit(body):
    """Append the GS1 mod-10 check digit to a 7/11/12/13-digit code BODY (EAN-8/UPC-A/EAN-13/GTIN-14)."""
    b = only_digits(body)
    if len(b) not in (7, 11, 12, 13):
        return ""
    total = sum(x * (3 if i % 2 == 0 else 1) for i, x in enumerate(int(c) for c in reversed(b)))
    return b + str((10 - total % 10) % 10)


def from_checkless_13(code):
    """Kroger-class heal: their API 'upc' is the UPC-A BODY (no check digit) zero-padded to 13 digits —
    ~90% fail the EAN-13 check as-is; healed codes join other sources' UPCs (0 → 1,347 join lift, proven).
    Shape-gated: 13 digits, '00' pad, failing as-is → drop the pad, append the computed check. Returns ''
    when it isn't that shape. (The ~10% that pass the EAN-13 check by coincidence are indistinguishable
    without cross-source evidence and are left alone.) TRANSLATION-layer only — landed data stays raw."""
    d = only_digits(code)
    if len(d) == 13 and d[:2] == "00" and not check_digit_ok(d):
        return with_check_digit(d[2:])
    return ""


def is_placeholder(code):
    """All-same-digit or a trivial ascending/descending run — the pre-assignment dummy barcodes."""
    c = only_digits(code)
    if not c:
        return False
    if len(set(c)) <= 1:
        return True
    asc = "01234567890123"
    return c in asc or c in asc[::-1]


def classify(code):
    """Deterministic status of a code. Order matters: empty < malformed < placeholder <
    bad_check < restricted/coupon < valid."""
    raw = only_digits(code)
    if not raw:
        return "empty"
    c = normalize(raw)
    if not c:
        return "malformed"
    if is_placeholder(c):
        return "placeholder"
    if not check_digit_ok(c):
        return "bad_check"
    # number-system digit restrictions (UPC-A, or EAN-13 that is a zero-padded UPC-A)
    ns = c[0] if len(c) == 12 else (c[1] if len(c) == 13 and c[0] == "0" else "")
    if ns in ("2", "4"):
        return "restricted"        # 2 = variable-weight/in-store, 4 = internal/no-format
    if ns == "5":
        return "coupon"
    # EAN restricted ranges (020–029 / 040–049 / 200–299 assigned to internal/coupon use)
    if len(c) == 13 and (c[:2] in ("02", "04") or c[0] == "2"):
        return "restricted"
    return "valid"


def is_real(code):
    return classify(code) == "valid"


def brand_key(code):
    """The leading digits used to cluster codes by likely owner. GS1 company-prefix length is
    variable (6–10) and not derivable from the code alone, so this drops the trailing item-ref +
    check digit as a heuristic — good enough to cluster same-owner products; the crosswalk's
    majority vote tolerates the slack. (GEPIR could refine exact prefix length later.)"""
    c = normalize(code)
    if not c or len(c) < 8:
        return ""
    return c[:-5]


def norm_owner(name):
    n = re.sub(r"[^A-Za-z0-9 &]+", " ", str(name or "")).upper()
    n = re.sub(r"\b(INC|LLC|LTD|CO|CORP|COMPANY|CORPORATION|LP|LLP|CELLARS|WINERY|WINERIES|"
               r"DISTILLERY|BREWING|BREWERY|IMPORTS?|BEVERAGES?|SPIRITS?|VINEYARDS?)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def build_crosswalk(pairs):
    """pairs: iterable of (upc, applicant). Learn brand_key -> owner from VALID codes only.
    Returns {brand_key: {owner, n, distinct, top:[(owner,count)…]}} with the majority owner."""
    from collections import Counter, defaultdict
    tally = defaultdict(Counter)
    for upc, applicant in pairs:
        if not is_real(upc):
            continue
        k = brand_key(upc)
        o = norm_owner(applicant)
        if k and o:
            tally[k][o] += 1
    out = {}
    for k, c in tally.items():
        owner, n = c.most_common(1)[0]
        out[k] = {"owner": owner, "n": sum(c.values()), "distinct": len(c),
                  "top": c.most_common(5)}
    return out


def assess(code, applicant=None, crosswalk=None):
    """Full assessment. Deterministic fields always; inference fields only when a crosswalk is
    supplied. Confidence: real+owner-agrees→0.95, real+no-crosswalk→0.6, real+owner-disagrees→0.35,
    non-valid→0."""
    raw = only_digits(code)
    status = classify(raw)
    c = normalize(raw)
    real = status == "valid"
    prov = ["deterministic:" + status]
    out = {"input": str(code or ""), "upc": c if real else "",
           "status": status, "brand_key": brand_key(c) if real else "",
           "prefix_owner": None, "owner_agrees": None,
           "confidence": 0.6 if real else 0.0, "provenance": prov}
    if real and crosswalk is not None:
        hit = crosswalk.get(brand_key(c))
        if hit:
            out["prefix_owner"] = hit["owner"]
            prov.append("inference:prefix_owner(%s,n=%s)" % (hit["owner"], hit["n"]))
            if applicant is not None and norm_owner(applicant):
                agrees = _owner_match(norm_owner(applicant), hit["owner"])
                out["owner_agrees"] = agrees
                out["confidence"] = 0.95 if agrees else 0.35
                prov.append("inference:owner_%s" % ("agrees" if agrees else "MISMATCH"))
    return out


# ── GS1 2D codes (QR / DataMatrix) — the "Sunrise 2027" carrier ────────────────────────────────────────────
# GS1's Sunrise 2027 pushes retail POS to scan 2D codes. It does NOT replace the GTIN: the same
# 12/13/14-digit number stays embedded, as AI (01). So a 2D code is a new CARRIER, not a new identity —
# everything above (normalize/classify/brand_key/assess) still applies once the GTIN is extracted.
#
# GRAIN WARNING (load-bearing): only AI (01) is ITEM-grain. (10) batch/lot, (17) expiry and (21) serial
# identify a PHYSICAL UNIT, not a product — they must never be written onto an item/SKU row. parse_2d()
# therefore returns the gtin separately from `ais`, so a caller can't casually flatten them together.
_AI_NAME = {"01": "gtin", "10": "batch_lot", "11": "production_date", "13": "packaging_date",
            "15": "best_before", "17": "expiry", "21": "serial", "22": "consumer_variant",
            "240": "additional_id", "30": "count", "414": "party_gln", "422": "country_of_origin"}
# AIs whose value is a FIXED length (so an unparenthesized/FNC1 stream can be walked). Everything else is
# variable-length and needs a separator, which is exactly why we don't guess at raw FNC1 streams below.
_AI_FIXED = {"00": 18, "01": 14, "02": 14, "11": 6, "12": 6, "13": 6, "15": 6, "16": 6, "17": 6, "20": 2}
_EL_RE = re.compile(r"\((\d{2,4})\)([^(]*)")


def parse_element_string(s):
    """Parse a PARENTHESIZED GS1 element string — '(01)00614141123452(10)LOT(21)SER' — to {ai: value}.

    Also walks an unparenthesized stream while every AI it meets is fixed-length; it STOPS at the first
    variable-length AI rather than guessing, because without an FNC1 separator the boundary is genuinely
    ambiguous and a guess would silently invent a batch code."""
    s = str(s or "").strip()
    if not s:
        return {}
    if "(" in s:
        return {m.group(1): m.group(2).strip() for m in _EL_RE.finditer(s) if m.group(2).strip()}
    out, i = {}, 0
    while i + 2 <= len(s):
        ai = s[i:i + 2]
        n = _AI_FIXED.get(ai)
        if n is None:
            break                                  # variable-length without a separator → stop, don't guess
        val = s[i + 2:i + 2 + n]
        if len(val) < n:
            break
        out[ai] = val
        i += 2 + n
    return out


def parse_digital_link(uri):
    """Parse a GS1 Digital Link URI to {ai: value}.

    Shape: https://host/01/09521234543213/10/LOT/21/SER?17=270101
    Path segments are AI/value pairs; query params carry further AIs. Alphabetic aliases ('gtin',
    'lot', 'ser', 'cpv') are accepted because GS1 permits them in the human-readable form."""
    s = str(uri or "").strip()
    if "/" not in s:
        return {}
    alias = {"gtin": "01", "itip": "8006", "cpv": "22", "lot": "10", "ser": "21"}
    path, _, query = s.partition("?")
    path = path.split("://", 1)[-1]
    segs = [p for p in path.split("/")[1:] if p]        # drop the host
    out = {}
    for i in range(len(segs) - 1):
        key = alias.get(segs[i].lower(), segs[i])
        if key.isdigit() and 2 <= len(key) <= 4:
            out[key] = segs[i + 1]
    for pair in query.split("&"):
        k, _, v = pair.partition("=")
        k = alias.get(k.lower(), k)
        if v and k.isdigit() and 2 <= len(k) <= 4:
            out[k] = v
    return out


def to_gtin14(code):
    """Resolve ANY GS1 item code to its canonical GTIN-14 — the cross-encoding identity.

    UPC-A(12) / EAN-13(13) / EAN-8(8) / GTIN-14 are the same number in different encodings, so GS1's
    canonical form is simply zero-padded to 14. Doing this automatically is what lets a 1D UPC and a
    2D code carrying a GTIN-14 land on the SAME item instead of looking like two products. Matches
    normalize._upc_norm's existing convention. '' only when the code isn't a usable GS1 code at all."""
    c = normalize(code) or only_digits(code)
    if not c or not (8 <= len(c) <= 14):
        return ""
    return c.zfill(14)


GS1_CANONICAL_HOST = "https://id.gs1.org"


def digital_link(code, domain=GS1_CANONICAL_HOST, ais=None):
    """DERIVE the GS1 Digital Link for a GTIN. The inverse of parse_2d.

    This is the high-yield direction: a Digital Link is a URI TEMPLATE over the identifier
    (`<domain>/01/<gtin14>`), not data that has to be obtained from anywhere — so every valid GTIN we
    already hold can be given its 2D payload deterministically, at zero cost and full coverage,
    instead of waiting to scrape one off a label that probably doesn't carry a 2D code yet.

    `domain` defaults to GS1's canonical resolver. What is NOT derivable is the brand owner's OWN
    resolver domain (they register it with GS1) — that requires actually resolving the canonical URI,
    which is a separate, genuinely-new-information step. Pass `domain` when a brand's resolver is known.

    `ais` optionally appends instance-grain AIs ({'10': lot, '21': serial}) in GS1's specified order —
    only meaningful for a physical unit, never for an item row. Returns '' for a non-valid GTIN, so a
    placeholder or bad-check code can never be minted into an official-looking URI."""
    if classify(code) != "valid":
        return ""
    g = to_gtin14(code)
    if not g:
        return ""
    url = "%s/01/%s" % (str(domain or GS1_CANONICAL_HOST).rstrip("/"), g)
    for ai in ("22", "10", "21"):                  # GS1's qualifier order: CPV → lot → serial
        v = (ais or {}).get(ai)
        if v:
            url += "/%s/%s" % (ai, v)
    return url


def parse_2d(payload):
    """Decode a 2D barcode payload. CAPTURES EVERYTHING — nothing read off the code is thrown away.

    Returns {raw, format, ais, gtin_raw, gtin, gtin14, gtin_status, digital_link}:
      raw        the payload exactly as scanned, always kept verbatim
      ais        EVERY application identifier found, including (01) and any AI we don't have a name
                 for (kept under its numeric AI) — an unrecognized AI is still data
      gtin_raw   the item code as read, kept even when it fails validation
      gtin       the same code once validated ('' if placeholder/bad-check)
      gtin14     the GS1-canonical GTIN-14, resolved automatically
      gtin_status  why gtin is or isn't populated

    The instance-grain AIs (batch/lot, expiry, serial) are returned for capture; what they must NOT do
    is get written onto an item/SKU ROW, which is a modelling decision at the master, not a reason to
    drop them on the floor here."""
    s = str(payload or "").strip()
    if not s:
        return {"raw": "", "format": "", "ais": {}, "gtin_raw": "", "gtin": "", "gtin14": "",
                "gtin_status": "none", "digital_link": ""}
    is_dl = s.lower().startswith(("http://", "https://"))
    ais = parse_digital_link(s) if is_dl else parse_element_string(s)
    if not ais and s.isdigit():                        # a bare number in a QR is just a GTIN
        ais = {"01": s}
    raw_gtin = ais.get("01", "")                       # NOT popped — (01) stays in the captured AIs
    status = classify(raw_gtin) if raw_gtin else "none"
    return {"raw": s,
            "format": "digital_link" if is_dl else ("element_string" if ais else ""),
            "ais": {_AI_NAME.get(k, k): v for k, v in ais.items()},
            "gtin_raw": raw_gtin,
            "gtin": normalize(raw_gtin) if status == "valid" else "",
            "gtin14": to_gtin14(raw_gtin) if status == "valid" else "",
            "gtin_status": status,
            "digital_link": s if is_dl else ""}


def _owner_match(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a.split()), set(b.split())
    inter = sa & sb
    return len(inter) >= 1 and len(inter) / max(1, min(len(sa), len(sb))) >= 0.5


def _selftest():
    assert classify("000000000000") == "placeholder"
    assert classify("111111111111") == "placeholder"
    assert classify("123456789012") == "placeholder"
    assert classify("036000291452") == "valid"          # real UPC-A
    assert classify("036000291453") == "bad_check"
    assert classify("2123456789012"[:12]) in ("restricted", "bad_check")  # ns=2 variable weight
    assert classify("") == "empty"
    assert classify("12ab") == "malformed"
    # zero-strip heal: Excel ate the leading zero(s) → pad back iff the check digit proves it
    assert normalize("36000291452") == "036000291452"    # 11-digit → healed UPC-A
    assert classify("36000291452") == "valid"
    assert normalize("3456789017") == "003456789017"     # 10-digit (two zeros stripped) → healed
    assert classify("3456789017") == "valid"
    assert normalize("36000291453") == ""                # padded check digit FAILS → stays malformed
    assert classify("36000291453") == "malformed"
    assert brand_key("36000291452") == "0360002"         # healed code clusters with its owner
    # checkless-13 heal (Kroger class): body zero-padded to 13, no check digit
    assert with_check_digit("03600029145") == "036000291452"
    assert from_checkless_13("0008068600140") == "080686001409"   # pad dropped, check appended
    assert classify(from_checkless_13("0008068600140")) == "valid"
    assert from_checkless_13("036000291452") == ""       # real UPC-A → not that shape, untouched
    assert from_checkless_13("0002150001340") == ""      # passes EAN-13 as-is → left alone
    assert brand_key("036000291452") == "0360002"
    xw = build_crosswalk([("036000291452", "ACME WINE CO"), ("036000291469", "Acme Wine Company"),
                          ("036000291476", "ACME WINE, LLC")])
    assert xw["0360002"]["owner"] == "ACME WINE"
    a1 = assess("036000291452", applicant="ACME WINE CO", crosswalk=xw)
    assert a1["owner_agrees"] is True and a1["confidence"] == 0.95, a1
    a2 = assess("036000291452", applicant="TOTALLY DIFFERENT DISTILLERY", crosswalk=xw)
    assert a2["owner_agrees"] is False and a2["confidence"] == 0.35, a2
    a3 = assess("000000000000", applicant="ACME WINE CO", crosswalk=xw)
    assert a3["status"] == "placeholder" and a3["upc"] == "" and a3["confidence"] == 0.0

    # ── GS1 canonical resolution: every encoding of the same number → one GTIN-14 ──
    assert to_gtin14("036000291452") == "00036000291452"      # UPC-A
    assert to_gtin14("0036000291452") == "00036000291452"     # EAN-13 (zero-padded UPC-A)
    assert to_gtin14("00036000291452") == "00036000291452"    # already GTIN-14
    assert to_gtin14("36000291452") == "00036000291452"       # zero-stripped → healed, then resolved
    assert to_gtin14("") == "" and to_gtin14("abc") == ""

    # ── DERIVE the Digital Link from a GTIN we already hold — round-trips through parse_2d ──
    assert digital_link("036000291452") == "https://id.gs1.org/01/00036000291452"
    assert digital_link("0036000291452") == digital_link("036000291452")   # encoding-independent
    assert digital_link("036000291452", domain="https://id.brand.com/") == \
        "https://id.brand.com/01/00036000291452"
    assert digital_link("036000291452", ais={"10": "L1", "21": "S9"}) == \
        "https://id.gs1.org/01/00036000291452/10/L1/21/S9"
    assert parse_2d(digital_link("036000291452"))["gtin14"] == "00036000291452"   # derive → parse
    assert digital_link("000000000000") == ""      # a placeholder is never minted into a real URI
    assert digital_link("36000291453") == "" and digital_link("") == ""

    # ── GS1 2D (Sunrise 2027): capture EVERYTHING; the GTIN survives the carrier change ──
    dl = parse_2d("https://example.com/01/00036000291452/10/LOT42/21/SER7?17=270101")
    assert dl["format"] == "digital_link", dl
    assert dl["raw"].startswith("https://"), dl               # payload kept verbatim
    assert dl["gtin"] == "00036000291452" and dl["gtin14"] == "00036000291452", dl
    # every AI captured, INCLUDING (01) — nothing read off the code is discarded
    assert dl["ais"] == {"gtin": "00036000291452", "batch_lot": "LOT42",
                         "serial": "SER7", "expiry": "270101"}, dl
    el = parse_2d("(01)00036000291452(10)LOT42(21)SER7")
    assert el["format"] == "element_string" and el["gtin14"] == "00036000291452", el
    assert el["ais"]["batch_lot"] == "LOT42" and el["ais"]["serial"] == "SER7", el
    # an AI we have no name for is still kept, under its numeric AI
    assert parse_2d("(01)00036000291452(91)INTERNAL")["ais"]["91"] == "INTERNAL"
    # a 2D code carrying a plain UPC-A resolves through the existing engine AND to GS1 canonical form
    bare = parse_2d("036000291452")
    assert bare["gtin"] == "036000291452" and bare["gtin_status"] == "valid", bare
    assert bare["gtin14"] == "00036000291452", bare           # 1D and 2D land on the SAME item
    assert brand_key(bare["gtin"]) == "0360002"               # → same owner crosswalk as the 1D path
    # a placeholder printed into a QR is caught — but the raw code is still RETAINED, not dropped
    ph = parse_2d("https://example.com/01/00000000000000")
    assert ph["gtin"] == "" and ph["gtin14"] == "" and ph["gtin_status"] == "placeholder", ph
    assert ph["gtin_raw"] == "00000000000000" and ph["raw"], ph
    # alphabetic Digital Link aliases
    assert parse_digital_link("https://ex.com/gtin/00036000291452/lot/L1")["10"] == "L1"
    # unparenthesized stream: walk fixed-length AIs, then STOP rather than invent a lot code
    assert parse_element_string("010003600029145217270101") == {"01": "00036000291452", "17": "270101"}
    assert parse_element_string("010003600029145210LOT") == {"01": "00036000291452"}
    assert parse_2d("")["gtin"] == "" and parse_2d(None)["format"] == ""
    print("upc.py self-test: OK")


if __name__ == "__main__":
    _selftest()
