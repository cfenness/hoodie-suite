"""overlay_map.py — S2 of the Overlay pipeline: THEIR columns → the master schema.

The promise on the page is "structure doesn't matter", and this module is what makes that true.
It answers one question per uploaded column: *what is this, really?* — and it answers it from the
VALUES first, the header second, because a header is a label and values are evidence.

Two labels, never blurred (the dq.js governing rule, applied to mapping):

  DETERMINISTIC — the values prove it. A column whose entries pass GS1 structural classification
    IS an identifier column no matter what the header says; a column that parses on the size
    grammar IS a size. This is how "your UPC field is actually GTIN" gets caught — the header just
    gets corrected. Stated as fact.
  INFERENCE(conf) — the header is the only evidence (an empty column, a free-text column, an
    opaque code like ITM_DSC_2). Confidence-scored, shown as a suggestion, overridable.

Every column lands in exactly one of four buckets, and the three-way count of the last three is a
headline stat of the whole run ("of your 140 fields: 100 cleansed, 20 derived, 20 yours"):

  mapped       → a master field; S4 cleanses it, S3 may match on it
  derivable    → we don't have it mapped but the master can supply it from the matched item
  proprietary  → THEIR measurement. Named honestly and left alone — the honest boundary is
                 load-bearing: "those are yours, we don't touch them" is what makes the rest
                 believable.
  pii          → identity-shaped. Excluded from every downstream stage AND from telemetry,
                 and disclosed ("3 columns look like customer data; we didn't read them").

Pure stdlib. `python overlay_map.py` runs the self-test.
"""
import re
import statistics

import upc as upc_mod

# ── the master fields an uploaded column can land on ───────────────────────────────────────────
# A subset of build_product_master.FIELDS: the ones a distributor/retailer export actually carries.
# Order matters only for display.
MASTER_FIELDS = [
    "brand", "product_name", "upc", "gtin", "dist_item_code", "size_ml", "size_raw", "pack",
    "abv", "proof", "category", "class_type", "varietal", "container", "supplier", "price",
    "vintage", "origin", "state", "description",
]

# Fields the master can SUPPLY once a row is matched (S5 enrichment). An unmapped column whose
# header names one of these is "derivable" — we can fill it, so it isn't a gap.
DERIVABLE_FIELDS = [
    "category", "class_type", "varietal", "origin", "country", "region", "appellation",
    "container", "supplier", "brand_owner", "gtin14", "bottles_per_case", "abv", "image",
    "size_ml", "style", "vintage", "bottled_in",
]

# ── header lexicon ─────────────────────────────────────────────────────────────────────────────
# Synonyms observed across real distributor/retailer exports. Matched on a normalized header
# (lowercase, non-alphanumerics → space) as a whole-token subsequence, so "ITEM UPC #" hits `upc`
# but "upcharge" does not.
_HEADER_LEX = {
    "upc":            ["upc", "upc code", "upc a", "upca", "barcode", "bar code", "scan code",
                       "retail upc", "consumer upc", "unit upc", "upc number"],
    "gtin":           ["gtin", "gtin 14", "gtin14", "ean", "ean 13", "global trade item number"],
    "dist_item_code": ["item code", "item number", "item no", "item id", "item", "sku", "sku code",
                       "product code", "product number", "dist item code", "distributor item",
                       "vendor item", "material number", "article number", "stock code", "part number"],
    "brand":          ["brand", "brand name", "brand desc", "brand description", "label", "marca"],
    "product_name":   ["product", "product name", "description", "item description", "desc",
                       "product description", "long description", "name", "item name", "short desc",
                       "prod desc", "itm dsc"],
    "size_raw":       ["size", "pack size", "size desc", "unit size", "volume", "bottle size",
                       "container size", "size description", "format"],
    "pack":           ["pack", "case pack", "units per case", "bottles per case", "pack qty",
                       "btl per case", "upc per case", "cs pack", "count"],
    "abv":            ["abv", "alcohol", "alc", "alcohol by volume", "alc content", "alcohol percent",
                       "alc vol", "alcohol pct"],
    "proof":          ["proof", "pf"],
    "category":       ["category", "class", "type", "product type", "dept", "department",
                       "segment", "beverage type", "major category", "product class"],
    "class_type":     ["class type", "sub category", "subcategory", "sub class", "sub type"],
    "varietal":       ["varietal", "variety", "grape", "grape variety", "style"],
    "container":      ["container", "package", "package type", "pkg", "pkg type", "material"],
    "supplier":       ["supplier", "vendor", "producer", "winery", "distillery", "importer",
                       "manufacturer", "supplier name", "vendor name"],
    "price":          ["price", "unit price", "case price", "bottle price", "cost", "list price",
                       "srp", "msrp", "retail price", "front line price", "flp", "net price"],
    "vintage":        ["vintage", "year", "vintage year"],
    "origin":         ["origin", "country", "country of origin", "appellation", "region"],
    "state":          ["state", "st", "state code"],
    "description":    ["notes", "comments", "tasting notes", "remarks"],
}

# PII header hints. A column matching one of these AND profiling as identity-shaped is excluded.
_PII_HEADER = ["email", "e mail", "phone", "mobile", "cell", "fax", "ssn", "social security",
               "contact", "contact name", "customer name", "buyer", "buyer name", "rep",
               "rep name", "salesperson", "sales rep", "first name", "last name", "full name",
               "address", "street", "addr", "attn", "dob", "date of birth"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^\+?\d?[\s.\-(]*\d{3}[\s.\-)]*\d{3}[\s.\-]*\d{4}$")
_CURRENCY_RE = re.compile(r"^\s*[-(]?\s*[$€£]\s*[\d,]+(\.\d{1,2})?\s*\)?\s*$")
_NUM_RE = re.compile(r"^\s*[-(]?\s*[$€£]?\s*[\d,]*\.?\d+\s*[%)]?\s*$")
_SIZE_RE = re.compile(r"(\d[\d.,]*)\s*(ml|mls|l|lt|ltr|liter|litre|litres|liters|cl|oz|fl oz|gal|g|mg)\b", re.I)
_PACK_RE = re.compile(r"^\s*\d{1,3}\s*(pk|pack|ct|count|/\s*cs|per case)?\s*$", re.I)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


# Distributor exports are written in abbreviations (ITM_DSC_2, PRD_CD, ALC_PCT). Expanding them
# token-wise before the synonym match is what lets one lexicon cover both "Item Description" and
# "ITM_DSC" without either a per-customer mapping file or a model call. Still header evidence, so
# still INFERENCE — an expanded abbreviation is a better guess, not a proof.
_ABBREV = {
    "itm": "item", "it": "item", "prd": "product", "prod": "product", "cd": "code", "cde": "code",
    "dsc": "description", "desc": "description", "descr": "description", "dscr": "description",
    "no": "number", "num": "number", "nbr": "number", "qty": "quantity", "amt": "amount",
    "pkg": "package", "pk": "pack", "cs": "case", "btl": "bottle", "sz": "size", "vol": "volume",
    "alc": "alcohol", "pct": "percent", "mfr": "manufacturer", "vend": "vendor", "sup": "supplier",
    "cat": "category", "sub": "sub", "nm": "name", "dept": "department", "ctn": "container",
    "brnd": "brand", "br": "brand", "unt": "unit", "prc": "price", "cst": "cost",
}


def _norm_header(h):
    """Lowercase, split on non-alphanumerics, expand known abbreviations, drop trailing position
    digits ('ITM_DSC_2' → 'item description'). The digit drop matters: exports number their repeated
    description columns, and the number is a position, not a different meaning."""
    toks = [t for t in re.split(r"[^a-z0-9]+", str(h or "").lower()) if t]
    while len(toks) > 1 and toks[-1].isdigit():
        toks.pop()
    return " ".join(_ABBREV.get(t, t) for t in toks)


def _num(v):
    """Parse a spreadsheet number: strips $ , % and accounting parens. None if it isn't one."""
    s = str(v or "").strip()
    if not s or not _NUM_RE.match(s):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace("€", "").replace("£", "").replace(",", "").replace("%", "").strip()
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def _digits(v):
    return re.sub(r"\D", "", str(v or ""))


# ── value evidence: what a column's CONTENTS prove ─────────────────────────────────────────────

def _identifier_evidence(vals):
    """Is this a GS1 identifier column, and which encoding? Returns None or a dict.

    The deterministic core of the whole mapping stage. We do NOT require the codes to be *valid* —
    a column of bad-check digits is still obviously an identifier column, and saying so is the
    point (that's a finding, not a mapping failure). What we require is GS1 SHAPE: digits-only,
    length in the GS1 family, and enough of them clearing the check digit that the column can't be
    a coincidence of numeric codes.
    """
    if not vals:
        return None
    shaped = [_digits(v) for v in vals]
    shaped = [d for d in shaped if d]
    if len(shaped) < max(3, 0.5 * len(vals)):
        return None
    fam = [d for d in shaped if 8 <= len(d) <= 14]
    if len(fam) / len(shaped) < 0.8:
        return None
    ok = sum(1 for d in fam if upc_mod.check_digit_ok(d))
    # A random 12-digit number clears mod-10 ~10% of the time. Requiring a clear majority means a
    # column of internal item numbers that merely LOOKS UPC-shaped can't be mistaken for one.
    if ok / len(fam) < 0.6:
        return None
    lens = [len(d) for d in fam]
    modal = statistics.mode(lens) if lens else 0
    field = "gtin" if modal in (13, 14) else "upc"
    return {"field": field, "modal_len": modal, "check_pass": round(ok / len(fam), 3),
            "n": len(fam), "share": round(len(fam) / len(shaped), 3)}


def _size_evidence(vals):
    """Values carrying an explicit volume unit ('750ml', '1.75 L', '12 OZ'). Unit token required —
    a bare number is not evidence of a size, it's evidence of a number."""
    if not vals:
        return None
    hit = sum(1 for v in vals if _SIZE_RE.search(str(v)))
    if hit / len(vals) < 0.7:
        return None
    return {"field": "size_raw", "parse_rate": round(hit / len(vals), 3), "n": hit}


# The bev-alc ABV bands: beer/RTD/wine sit low, spirits sit high, and almost nothing lives between.
# Used ONLY to decide whether a header-less numeric column is unmistakably an ABV — the gap in the
# middle is the discriminator a price column can't fake (prices scatter, ABVs cluster).
_ABV_BANDS = ((3.0, 17.0), (35.0, 60.0))


def _abv_evidence(vals, header_hint):
    """Numeric and inside a plausible ABV band. Deliberately conservative: numbers alone are
    ambiguous (a 5.0 could be a price), so a header hint is required unless the values cluster
    inside the bev-alc bands tightly enough that nothing else produces that shape."""
    if any(_CURRENCY_RE.match(str(v)) for v in vals):
        return None                                   # a currency mark settles it — that's a price
    nums = [n for n in (_num(v) for v in vals) if n is not None]
    if len(nums) < max(3, 0.6 * len(vals)):
        return None
    inband = [n for n in nums if 0 < n <= 100]
    if len(inband) / len(nums) < 0.9:
        return None
    med = statistics.median(inband)
    if not (3 <= med <= 70):
        return None
    if not header_hint:
        clustered = sum(1 for n in inband if any(lo <= n <= hi for lo, hi in _ABV_BANDS))
        if clustered / len(inband) < 0.9 or max(inband) > 70:
            return None
    return {"field": "abv", "median": round(med, 2), "n": len(inband)}


def _price_evidence(vals, header_hint):
    """Currency-marked values are deterministic. Bare 2-decimal numbers need the header."""
    if not vals:
        return None
    marked = sum(1 for v in vals if _CURRENCY_RE.match(str(v)))
    if marked / len(vals) >= 0.7:
        return {"field": "price", "currency_marked": True, "n": marked}
    if not header_hint:
        return None
    nums = [n for n in (_num(v) for v in vals) if n is not None]
    if len(nums) >= max(3, 0.7 * len(vals)) and all(n >= 0 for n in nums):
        return {"field": "price", "currency_marked": False, "n": len(nums)}
    return None


def _vintage_evidence(vals):
    if not vals:
        return None
    hit = sum(1 for v in vals if _YEAR_RE.match(str(v).strip()))
    if hit / len(vals) < 0.9:
        return None
    return {"field": "vintage", "n": hit}


def _pii_evidence(vals):
    """Identity-shaped VALUES — emails and phone numbers are unambiguous and need no header."""
    if not vals:
        return None
    em = sum(1 for v in vals if _EMAIL_RE.match(str(v).strip()))
    if em / len(vals) >= 0.5:
        return {"kind": "email", "n": em}
    ph = sum(1 for v in vals if _PHONE_RE.match(str(v).strip()))
    if ph / len(vals) >= 0.7:
        return {"kind": "phone", "n": ph}
    return None


# ── role inference (measure vs dimension) ──────────────────────────────────────────────────────

def _role(vals, n_rows):
    """measure | dimension | identifier | empty. Mirrors dq.js's calibrated read so the server and
    the browser agree on what a column is."""
    if not vals:
        return "empty"
    nums = [n for n in (_num(v) for v in vals) if n is not None]
    numeric = len(nums) / len(vals) >= 0.85
    distinct = len(set(str(v).strip() for v in vals))
    if distinct >= 0.9 * len(vals) and len(vals) >= 20:
        return "identifier" if not numeric else "measure"
    if numeric:
        # A numeric column with FEW distinct values is a code, not a quantity. The threshold has to
        # scale down on small files: a 7-row sample with 7 distinct margins is obviously a measure,
        # and a fixed floor of 8 would call it a dimension.
        return "measure" if distinct >= min(8, max(3, 0.5 * len(vals))) else "dimension"
    return "dimension"


# ── the mapper ─────────────────────────────────────────────────────────────────────────────────

def _header_match(header):
    """Best master field for a header, by longest matching synonym. Returns (field, confidence)."""
    h = _norm_header(header)
    if not h:
        return None, 0.0
    best, best_len = None, 0
    for field, syns in _HEADER_LEX.items():
        for s in syns:
            if h == s:
                return field, 0.9
            # whole-token containment, so "upcharge" never matches "upc"
            if re.search(r"(^|\s)%s($|\s)" % re.escape(s), h) and len(s) > best_len:
                best, best_len = field, len(s)
    return (best, 0.7) if best else (None, 0.0)


def _is_pii_header(header):
    h = _norm_header(header)
    return any(re.search(r"(^|\s)%s($|\s)" % re.escape(p), h) for p in _PII_HEADER)


def map_columns(header, rows, sample=400):
    """Map every uploaded column. Returns a list of column records, one per input column:

        {index, name, role, bucket, field, label, confidence, evidence, note, fill_rate,
         distinct, sample}

    bucket ∈ mapped | derivable | proprietary | pii | empty
    label  ∈ DETERMINISTIC | INFERENCE | (absent for non-mapped)

    Nothing here reads more than `sample` rows per column — the mapping is a read of the column's
    character, not of the customer's data, and keeping the window small is also what keeps the
    whole pass inside its wall-clock budget.
    """
    header = list(header or [])
    rows = rows or []
    n_rows = len(rows)
    win = rows[:sample]
    out = []
    claimed = {}                      # master field → index of the column that won it

    for ci, name in enumerate(header):
        col = [(r[ci] if ci < len(r) else "") for r in win]
        vals = [v for v in col if str(v).strip() != ""]
        full_nonblank = sum(1 for r in rows if ci < len(r) and str(r[ci]).strip() != "")
        rec = {"index": ci, "name": name,
               "fill_rate": round(full_nonblank / n_rows, 3) if n_rows else 0.0,
               "distinct": len(set(str(v).strip() for v in vals)),
               "sample": [str(v) for v in vals[:3]]}

        # 1. PII gate — runs FIRST and short-circuits. A column we won't read is a column we don't
        #    profile further, don't map, and don't put in telemetry.
        pv = _pii_evidence(vals)
        if pv or (_is_pii_header(name) and _role(vals, n_rows) in ("dimension", "identifier")):
            rec.update(role="pii", bucket="pii", field=None, label=None, confidence=None,
                       evidence={"kind": (pv or {}).get("kind", "header")},
                       note="looks like customer data — excluded from every stage, not read")
            # Nothing profiled about this column is published either. We looked at the SHAPE long
            # enough to recognise it and then stopped; publishing its fill rate and cardinality
            # would quietly contradict the sentence we just wrote.
            rec["sample"], rec["fill_rate"], rec["distinct"] = [], None, None
            out.append(rec)
            continue

        rec["role"] = _role(vals, n_rows)
        hfield, hconf = _header_match(name)
        hint = hfield
        if not vals:
            # An empty column named for something the master carries is not a dead column — it is
            # a column we can FILL, which is exactly what "derived" means in the three-way count.
            derivable = _norm_header(name).replace(" ", "_") in DERIVABLE_FIELDS or \
                (hfield in DERIVABLE_FIELDS)
            rec.update(bucket="derivable" if derivable else "empty",
                       field=hfield if derivable else None, label=None, confidence=None,
                       evidence=None,
                       note="you have the column but no values — the master can supply it for "
                            "matched rows" if derivable else "empty in every row we read")
            out.append(rec)
            continue

        # 2. Value evidence — DETERMINISTIC, and it OVERRIDES the header. This is the stage that
        #    catches a GTIN sitting in a column called UPC.
        # Order = strength of proof. A currency mark or a GS1 check digit settles a column on its
        # own; ABV only after price, because a price column and an ABV column are both "small
        # numbers" and the currency mark is the thing that tells them apart.
        ev = (_identifier_evidence(vals)
              or _size_evidence(vals)
              or _price_evidence(vals, hint == "price")
              or _abv_evidence(vals, hint in ("abv", "proof"))
              or (_vintage_evidence(vals) if hint == "vintage" else None))

        if ev:
            field = ev["field"]
            # An identifier column whose header says "item code" but whose values are GS1-valid is
            # BOTH: it carries the retail code AND it is their item number. Prefer the GS1 read
            # (it's the higher-tier match key) and record the header's claim in the note.
            note = None
            if hfield and hfield != field:
                note = "header says %r; the values are %s" % (name, field)
            rec.update(bucket="mapped", field=field, label="DETERMINISTIC", confidence=1.0,
                       evidence=ev, note=note)
            out.append(rec)
            continue

        # 3. Header evidence — INFERENCE. Suggestion, confidence-scored, overridable.
        if hfield:
            rec.update(bucket="mapped", field=hfield, label="INFERENCE", confidence=hconf,
                       evidence={"basis": "header"},
                       note="mapped from the column name — no value-level proof")
            out.append(rec)
            continue

        # 4. Unmapped: derivable (the master carries it) vs proprietary (their measurement).
        if _norm_header(name).replace(" ", "_") in DERIVABLE_FIELDS:
            rec.update(bucket="derivable", field=_norm_header(name).replace(" ", "_"),
                       label=None, confidence=None, evidence=None,
                       note="the master can supply this for matched rows")
        elif rec["role"] == "measure":
            rec.update(bucket="proprietary", field=None, label=None, confidence=None, evidence=None,
                       note="appears to be your own measurement — we don't touch it")
        else:
            rec.update(bucket="proprietary", field=None, label=None, confidence=None, evidence=None,
                       note="not a field we hold — left exactly as you sent it")
        out.append(rec)

    # 5. Resolve duplicate claims on the same master field. Deterministic beats inference; then
    #    higher fill. The loser keeps its read but is demoted so S3/S4 never see two "brand"s.
    for rec in out:
        f = rec.get("field")
        if rec["bucket"] != "mapped" or not f:
            continue
        prev = claimed.get(f)
        if prev is None:
            claimed[f] = rec["index"]
            continue
        a, b = out[prev], rec
        keep, drop = ((a, b) if (a["label"] == "DETERMINISTIC", a["fill_rate"]) >=
                      (b["label"] == "DETERMINISTIC", b["fill_rate"]) else (b, a))
        claimed[f] = keep["index"]
        drop.update(bucket="proprietary", field=None, label=None, confidence=None,
                    note="a second column also read as %s; %r is the one we used" % (f, keep["name"]))
    return out


def summary(cols):
    """The headline three-way count, plus what's missing. Feeds Sheet 2 and the on-screen read."""
    b = {}
    for c in cols:
        b[c["bucket"]] = b.get(c["bucket"], 0) + 1
    mapped = [c for c in cols if c["bucket"] == "mapped"]
    return {
        "columns": len(cols),
        "cleansed": b.get("mapped", 0),
        "derived": b.get("derivable", 0),
        "yours": b.get("proprietary", 0),
        "not_read": b.get("pii", 0),
        "empty": b.get("empty", 0),
        "deterministic": sum(1 for c in mapped if c["label"] == "DETERMINISTIC"),
        "inference": sum(1 for c in mapped if c["label"] == "INFERENCE"),
        "fields": sorted({c["field"] for c in mapped}),
    }


def field_index(cols):
    """{master field: column index} for the columns that won their field."""
    return {c["field"]: c["index"] for c in cols if c["bucket"] == "mapped" and c.get("field")}


def _selftest():
    hdr = ["ITM_CD", "ITM_DSC_2", "BRAND", "UPC", "SIZE", "ALC%", "FLP", "RNDC_VELOCITY_IDX",
           "Buyer Email", "Notes"]
    rows = [
        ["100234", "TITOS HANDMADE VODKA", "TITOS", "619947000020", "750 ML", "40", "$21.99", "0.884", "a@b.com", ""],
        ["100235", "TITOS HANDMADE VODKA", "TITOS", "619947000037", "1.75 L", "40", "$34.99", "1.220", "c@d.com", ""],
        ["100236", "JACK DANIELS BLACK", "JACK DANIELS", "082184090565", "750 ML", "40", "$27.49", "0.410", "e@f.com", ""],
        ["100237", "KENDALL JACKSON CHARD", "KENDALL JACKSON", "081584000228", "750 ML", "13.5", "$14.99", "2.010", "g@h.com", ""],
        ["100238", "VEUVE CLICQUOT BRUT", "VEUVE CLICQUOT", "081753817107", "750 ML", "12", "$59.99", "0.150", "i@j.com", ""],
    ]
    cols = map_columns(hdr, rows)
    by = {c["name"]: c for c in cols}

    # values prove the identifier, deterministically
    assert by["UPC"]["bucket"] == "mapped" and by["UPC"]["field"] == "upc", by["UPC"]
    assert by["UPC"]["label"] == "DETERMINISTIC", by["UPC"]
    # size grammar parses → deterministic, header agrees
    assert by["SIZE"]["field"] == "size_raw" and by["SIZE"]["label"] == "DETERMINISTIC", by["SIZE"]
    # ABV: header hint + plausible band
    assert by["ALC%"]["field"] == "abv", by["ALC%"]
    # currency-marked → deterministic price even though "FLP" is an opaque header
    assert by["FLP"]["field"] == "price" and by["FLP"]["label"] == "DETERMINISTIC", by["FLP"]
    # abbreviated headers resolve through the expansion, and stay INFERENCE (a better guess, not proof)
    assert by["ITM_CD"]["field"] == "dist_item_code" and by["ITM_CD"]["label"] == "INFERENCE", by["ITM_CD"]
    assert by["ITM_DSC_2"]["field"] == "product_name", by["ITM_DSC_2"]      # ITM_DSC_2 → item description
    assert by["BRAND"]["field"] == "brand" and by["BRAND"]["label"] == "INFERENCE", by["BRAND"]
    # their measure, named honestly and left alone
    assert by["RNDC_VELOCITY_IDX"]["bucket"] == "proprietary", by["RNDC_VELOCITY_IDX"]
    # PII: detected on values, excluded, and NOT echoed back
    assert by["Buyer Email"]["bucket"] == "pii" and by["Buyer Email"]["sample"] == [], by["Buyer Email"]
    # a column empty in every row is 'empty', not a failed mapping
    assert by["Notes"]["bucket"] == "empty", by["Notes"]

    s = summary(cols)
    assert s["cleansed"] == 7 and s["yours"] == 1 and s["not_read"] == 1 and s["empty"] == 1, s
    assert s["deterministic"] == 4 and s["inference"] == 3, s

    # ── the demo finding: a column LABELLED upc that actually holds GTIN-14 ──
    g = map_columns(["UPC"], [["00619947000020"], ["00619947000037"], ["00082184090565"],
                              ["00081584000228"], ["00081753817107"]])[0]
    assert g["field"] == "gtin" and g["label"] == "DETERMINISTIC", g
    assert "the values are gtin" in (g["note"] or ""), g

    # a numeric item-code column that merely LOOKS UPC-shaped must not be mistaken for one
    fake = map_columns(["LINE"], [[str(1000000000000 + i)] for i in range(20)])[0]
    assert fake["field"] != "upc", fake

    # "upcharge" must not match the `upc` synonym
    assert _header_match("Upcharge")[0] != "upc"
    # header-only mapping is INFERENCE, never fact
    only = map_columns(["Supplier"], [["Sazerac"], ["Diageo"], ["Beam"]])[0]
    assert only["label"] == "INFERENCE" and only["field"] == "supplier", only

    # two columns claiming the same field: deterministic wins, the other is demoted (not deleted)
    dup = map_columns(["upc", "barcode"],
                      [["619947000020", "619947000020"], ["082184090565", "082184090565"],
                       ["081584000228", "081584000228"], ["081753817107", "081753817107"]])
    assert sum(1 for c in dup if c.get("field") == "upc") == 1, dup
    assert any(c["bucket"] == "proprietary" and "is the one we used" in (c["note"] or "") for c in dup), dup

    # phone-shaped values are PII on values alone, no header needed
    ph = map_columns(["col_9"], [["407-555-0134"], ["(407) 555-0199"], ["4075550122"]])[0]
    assert ph["bucket"] == "pii", ph

    print("overlay_map.py self-test: OK")


if __name__ == "__main__":
    _selftest()
