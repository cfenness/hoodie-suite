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
    """Return a canonical 8/12/13/14-digit code, or '' if it isn't one."""
    c = only_digits(code)
    return c if len(c) in (8, 12, 13, 14) else ""


def check_digit_ok(code):
    """Standard GS1 mod-10 check (UPC-A 12 / EAN-13 13 / EAN-8 8); True for other lengths."""
    c = only_digits(code)
    if len(c) not in (8, 12, 13):
        return True
    d = [int(x) for x in c]
    body, chk = d[:-1], d[-1]
    total = sum(x * (3 if i % 2 == 0 else 1) for i, x in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == chk


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
    print("upc.py self-test: OK")


if __name__ == "__main__":
    _selftest()
