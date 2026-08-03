"""overlay_detect.py — S6 of the Overlay pipeline: the DETECTOR REGISTRY.

The moat of the diagnose stage is not any single check; it is the catalog of bev-alc-specific
detectors and the discipline governing it. A detector here does not report a symptom ("bad UPC") —
it reports a ROOT CAUSE with the arithmetic shown ("your export tool ate leading zeros on 1,204
codes; we re-padded and the mod-10 check proves it") and a specific fix.

THE GOVERNANCE (OVERLAY-DESIGN §4) — three gates, all enforced here, in this order:

  1. requires — a detector whose mapped fields are absent SKIPS, silently. Never a false alarm
     from a missing column, never a scary "couldn't run" for a file that simply doesn't have ABV.
  2. precision gate — a detector reaches the customer only if its precision clears the bar:
     ≥0.98 to state as DETERMINISTIC fact, ≥0.90 to show as INFERENCE. Precision comes from one
     of two places and the difference is load-bearing:
        structural — the rule IS a proof (mod-10 arithmetic, GS1 length classes, string identity).
                     Precision is 1.0 by construction, not by claim.
        backtest   — the rule is a heuristic. Precision is whatever `overlay_precision.json` says
                     it measured on the corpus. NO MEASUREMENT ⇒ THE DETECTOR RUNS SILENTLY:
                     it accumulates stats and is invisible to users. An unmeasured heuristic is
                     exactly the thing that costs credibility in front of a CIO, so the default
                     is silence, not optimism.
  3. misfire suppression — inherited verbatim from dq.js's misfireGuard: a row-grained detector
     firing on >5% of eligible rows at flat confidence is suppressed for that file and replaced by
     ONE meta-card. Volume is never evidence. The exception is declared per detector
     (`systematic_ok`): where each hit is individually PROVEN (a check digit either passes or it
     doesn't), a whole-file pattern is the actual finding, not a sign the rule misfired.

Pure stdlib (imports `upc`, `precleanse` from this package). `python overlay_detect.py` self-tests.
"""
import json
import os
import re

import precleanse
import upc as upc_mod

REGISTRY_VERSION = 1                      # stamped on every run — a returned workbook is reproducible

_DIR = os.path.dirname(os.path.abspath(__file__))
_BANDS_FILE = os.path.join(_DIR, "overlay_bands.json")
_PRECISION_FILE = os.path.join(_DIR, "overlay_precision.json")

DETERMINISTIC = "DETERMINISTIC"
INFERENCE = "INFERENCE"

# dq.js MISFIRE_ROW_FRACTION / MISFIRE_REQUIRES_UNIFORM_CONF, same numbers on purpose.
MISFIRE_ROW_FRACTION = 0.05
MISFIRE_MIN_HITS = 3

_DET_BAR = 0.98                           # to state as fact
_INF_BAR = 0.90                           # to show as inference

_DEFAULT_BANDS = {
    "beer": [3.0, 14.0], "cider": [3.0, 12.0], "seltzer": [3.0, 9.0], "rtd": [3.0, 14.0],
    "table wine": [7.5, 17.0], "sparkling wine": [9.0, 14.0], "dessert wine": [14.0, 24.0],
    "fortified wine": [15.0, 24.0], "liqueur": [12.0, 45.0], "vermouth": [14.0, 24.0],
    "brandy": [35.0, 60.0], "gin": [37.0, 60.0], "rum": [35.0, 76.0], "tequila": [35.0, 55.0],
    "vodka": [35.0, 60.0], "whiskey": [38.0, 70.0], "bourbon": [40.0, 70.0], "rye": [40.0, 70.0],
    "scotch": [40.0, 65.0], "spirits": [15.0, 76.0],
}


def _load_bands():
    try:
        with open(_BANDS_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d.get("abv_bands") or _DEFAULT_BANDS, d
    except (OSError, ValueError):
        return _DEFAULT_BANDS, {"version": 0}


BANDS, BAND_DATA = _load_bands()
BANDS_VERSION = BAND_DATA.get("version", 0)


def _load_precision():
    """Measured precision per detector id, from the backtest corpus. Absent ⇒ every `backtest`-
    sourced detector stays silent, which is the designed default."""
    try:
        with open(_PRECISION_FILE, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return {k: float(v["precision"]) if isinstance(v, dict) else float(v)
                for k, v in (d.get("detectors") or {}).items()}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


# ── category resolution ────────────────────────────────────────────────────────────────────────
# Their category strings are theirs; the bands are keyed to ours. This maps the common vocabulary
# to a band key, and returns None when it can't — an unmapped category means the ABV detectors
# skip that row rather than guess a band.
_CAT_ALIAS = [
    (r"\bbourbon\b", "bourbon"), (r"\brye\b", "rye"), (r"\bscotch\b", "scotch"),
    (r"\b(whisk(e)?y|whiskies)\b", "whiskey"), (r"\btequila\b", "tequila"), (r"\bmezcal\b", "tequila"),
    (r"\bvodka\b", "vodka"), (r"\bgin\b", "gin"), (r"\brum\b", "rum"),
    (r"\b(cognac|armagnac|brandy)\b", "brandy"), (r"\b(liqueur|cordial|schnapps)\b", "liqueur"),
    (r"\bvermouth\b", "vermouth"),
    (r"\b(champagne|prosecco|cava|sparkling)\b", "sparkling wine"),
    (r"\b(port|sherry|madeira|fortified)\b", "fortified wine"),
    (r"\b(sauternes|ice ?wine|dessert wine)\b", "dessert wine"),
    (r"\b(rtd|ready to drink|canned cocktail|cocktail)\b", "rtd"),
    (r"\b(seltzer|hard seltzer)\b", "seltzer"), (r"\bcider\b", "cider"),
    (r"\b(beer|ale|lager|ipa|stout|pilsner|porter|malt)\b", "beer"),
    (r"\b(wine|red|white|ros[eé]|chardonnay|cabernet|merlot|pinot|sauvignon|riesling|zinfandel|syrah|malbec)\b",
     "table wine"),
    (r"\b(spirit|spirits|liquor)\b", "spirits"),
]
_CAT_RE = [(re.compile(p, re.I), k) for p, k in _CAT_ALIAS]


def band_for(*texts):
    """Resolve a band key from any of the category-ish strings on a row (their category, our
    matched category, the product name). Returns (key, [lo, hi]) or (None, None)."""
    for t in texts:
        if not t:
            continue
        for rx, key in _CAT_RE:
            if rx.search(str(t)):
                b = BANDS.get(key)
                if b:
                    return key, b
    return None, None


def _f(v):
    try:
        return float(str(v).strip().replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _digits(v):
    return re.sub(r"\D", "", str(v or ""))


# ── the detector contract ──────────────────────────────────────────────────────────────────────

class Detector(object):
    """One registered detector. `fn(ctx)` returns a list of hits:

        {"row": <0-based row index or None for a dataset-grained finding>,
         "evidence": {...},          # the arithmetic, shown
         "confidence": <float>}      # INFERENCE only

    Everything else — the root cause sentence, the fix, the severity — is declared once, here, so
    a finding can never be phrased differently on two runs.
    """

    def __init__(self, id, domain, requires, label, root_cause, fix, severity,
                 fn, precision_source, precision=None, travels=True, systematic_ok=False,
                 scope="row", title=None, requires_any=None):
        self.id = id
        self.domain = domain
        self.requires = list(requires)
        # `requires` is ALL-of; `requires_any` is at-least-one-of. Both exist because a detector
        # like mixed_case_grain works off whichever key the file happens to carry — demanding a
        # specific one would skip files it can genuinely diagnose.
        self.requires_any = list(requires_any or [])
        self.label = label
        self.title = title or id.replace("_", " ")
        self.root_cause = root_cause
        self.fix = fix
        self.severity = severity            # blocks_match | corrupts_analysis | cosmetic
        self.fn = fn
        self.precision_source = precision_source     # "structural" | "backtest"
        self.precision = 1.0 if precision_source == "structural" else precision
        self.travels = travels
        self.systematic_ok = systematic_ok
        self.scope = scope                  # row | dataset

    def bar(self):
        return _DET_BAR if self.label == DETERMINISTIC else _INF_BAR

    def visible(self, measured):
        """The precision gate. Structural rules are proofs and pass by construction. A heuristic
        passes only on a MEASURED number that clears its label's bar — an unmeasured one runs
        silently, which is the point of the gate."""
        p = measured.get(self.id, self.precision)
        return (p is not None) and (p >= self.bar())

    def meta(self, measured):
        p = measured.get(self.id, self.precision)
        return {"id": self.id, "domain": self.domain, "title": self.title, "label": self.label,
                "root_cause": self.root_cause, "fix": self.fix, "severity": self.severity,
                "requires": self.requires, "requires_any": self.requires_any,
                "precision": p, "precision_source": self.precision_source,
                "travels": self.travels, "scope": self.scope}


class Ctx(object):
    """What a detector sees. `rows` are MAPPED values only — a dict per input row keyed by master
    field — so a detector never touches a proprietary or PII column, by construction rather than
    by discipline."""

    def __init__(self, rows, fields, cols=None, crosswalk=None, matched=None):
        self.rows = rows
        self.fields = set(fields)
        self.cols = cols or []
        self.crosswalk = crosswalk
        self.matched = matched or []        # per-row match records from S3 (may be empty)

    def has(self, *fields):
        return all(f in self.fields for f in fields)

    def get(self, i, field, default=""):
        try:
            v = self.rows[i].get(field, default)
        except (IndexError, AttributeError):
            return default
        return default if v is None else v

    def cat_of(self, i):
        """Category text for row i — theirs first, then the MATCHED master's, then their class/name.

        The matched category is what makes the ABV detectors work on a file that carries no category
        column at all, which is the common case: the file says '80' with no context, S3 resolves the
        row to a whiskey, and only then is 80 provably proof-not-ABV."""
        m = self.matched[i] if i < len(self.matched) else None
        m = m or {}
        return (self.get(i, "category"), m.get("matched_category") or m.get("category"),
                self.get(i, "class_type"), self.get(i, "product_name"))


# ── the launch set ─────────────────────────────────────────────────────────────────────────────

def _d_upc_is_gtin(ctx):
    """THE demo finding. A column labelled UPC whose values are structurally EAN-13/GTIN-14.
    Dataset-grained: it's one fact about the column, not N facts about rows."""
    col = next((c for c in ctx.cols
                if c.get("field") in ("upc", "gtin") and c.get("label") == DETERMINISTIC), None)
    if not col:
        return []
    ev = col.get("evidence") or {}
    modal, header = ev.get("modal_len"), str(col.get("name", ""))
    if modal not in (13, 14):
        return []
    hn = re.sub(r"[^a-z]", "", header.lower())
    if "gtin" in hn or "ean" in hn:
        return []                           # the header already says so — nothing to correct
    if "upc" not in hn:
        return []                           # only a column CLAIMING to be a UPC can be corrected
    return [{"row": None, "evidence": {
        "column": header, "modal_length": modal, "codes_checked": ev.get("n"),
        "check_digit_pass_rate": ev.get("check_pass"),
        "arithmetic": "%d of %d values are %d digits and clear the GS1 mod-10 check — that is an %s, "
                      "not a 12-digit UPC-A" % (ev.get("n") or 0, ev.get("n") or 0, modal,
                                                "EAN-13" if modal == 13 else "GTIN-14")}}]


def _d_zero_strip(ctx):
    """Leading zeros eaten by the export tool. The mod-10 check is the proof: a random re-pad
    clears it ~10% of the time, so a code that clears it really did lose its zeros."""
    out = []
    for i in range(len(ctx.rows)):
        raw = _digits(ctx.get(i, "upc") or ctx.get(i, "gtin"))
        if len(raw) not in (10, 11, 7):
            continue
        padded = raw.zfill(12 if len(raw) in (10, 11) else 8)
        if upc_mod.check_digit_ok(padded):
            out.append({"row": i, "evidence": {
                "as_exported": raw, "restored": padded,
                "arithmetic": "%s → %s, and %s clears the GS1 mod-10 check" % (raw, padded, padded)}})
    return out


def _class_detector(id_, want, title, root_cause, fix, severity):
    def fn(ctx):
        out = []
        for i in range(len(ctx.rows)):
            raw = ctx.get(i, "upc") or ctx.get(i, "gtin")
            if not _digits(raw):
                continue
            st = upc_mod.classify(raw)
            if st in want:
                out.append({"row": i, "evidence": {"code": str(raw), "classification": st,
                                                   "arithmetic": _class_arithmetic(raw, st)}})
        return out
    return Detector(id_, "bev-alc", ["upc"], DETERMINISTIC, root_cause, fix, severity,
                    fn, "structural", systematic_ok=True, title=title)


def _class_arithmetic(raw, st):
    d = _digits(raw)
    if st == "bad_check":
        body = d[:-1]
        return "check digit is %s; GS1 mod-10 over %s computes %s" % (
            d[-1:], body, upc_mod.with_check_digit(body)[-1:] or "?")
    if st == "placeholder":
        return "%s is a pre-assignment dummy barcode (single repeated digit or a trivial run)" % d
    if st in ("restricted", "coupon"):
        frame = d[-12:] if len(d) >= 12 else d.zfill(12)
        return ("number system %s — %s. Locally assigned: retailers reuse it across different "
                "products, so it is not a global identity" % (
                    frame[0], {"2": "variable-weight/in-store", "4": "internal/no-format",
                               "5": "coupon"}.get(frame[0], "restricted range")))
    return d


def _d_owner_mismatch(ctx):
    """The UPC's GS1 company prefix disagrees with the brand/supplier on the row."""
    xw = ctx.crosswalk
    if not xw:
        return []
    out = []
    for i in range(len(ctx.rows)):
        code = ctx.get(i, "upc") or ctx.get(i, "gtin")
        who = ctx.get(i, "supplier") or ctx.get(i, "brand")
        if not code or not who:
            continue
        a = upc_mod.assess(code, applicant=who, crosswalk=xw)
        if a.get("owner_agrees") is False:
            out.append({"row": i, "confidence": 1.0 - a["confidence"],
                        "evidence": {"code": str(code), "row_says": str(who),
                                     "prefix_owner": a["prefix_owner"],
                                     "gs1_prefix": a["brand_key"],
                                     "arithmetic": "GS1 prefix %s is registered to %s across our COLA "
                                                   "filings; this row says %s" % (
                                                       a["brand_key"], a["prefix_owner"], who)}})
    return out


def _d_abv_proof_double(ctx):
    """Proof written into the ABV field. Proof-shaped rule, not a guess: fires ONLY when the value
    is outside the category band AND halving it lands inside. Both conditions together are what
    make this arithmetic rather than an opinion."""
    out = []
    for i in range(len(ctx.rows)):
        v = _f(ctx.get(i, "abv"))
        if v is None or v <= 0:
            continue
        key, band = band_for(*ctx.cat_of(i))
        if not band:
            continue
        lo, hi = band
        if v > hi and lo <= v / 2.0 <= hi:
            out.append({"row": i, "evidence": {
                "stated_abv": v, "category": key, "expected": [lo, hi], "corrected_abv": round(v / 2.0, 2),
                "arithmetic": "%s is outside the %s band %s–%s; %s ÷ 2 = %s, which is inside it — "
                              "that is proof, not ABV" % (v, key, lo, hi, v, round(v / 2.0, 2))}})
    return out


def _d_abv_out_of_category(ctx):
    out = []
    for i in range(len(ctx.rows)):
        v = _f(ctx.get(i, "abv"))
        if v is None or v <= 0:
            continue
        key, band = band_for(*ctx.cat_of(i))
        if not band:
            continue
        lo, hi = band
        if lo <= v <= hi:
            continue
        if v > hi and lo <= v / 2.0 <= hi:
            continue                        # that's the proof case; it has its own detector
        out.append({"row": i, "confidence": 0.7,
                    "evidence": {"stated_abv": v, "category": key, "expected": [lo, hi],
                                 "arithmetic": "%s is outside the deterministic %s band %s–%s" % (
                                     v, key, lo, hi)}})
    return out


def _sig(ctx, i):
    """The size/pack signature of a row — what makes a case different from an each."""
    return (str(ctx.get(i, "size_raw") or ctx.get(i, "size_ml") or "").strip().lower(),
            str(ctx.get(i, "pack") or "").strip().lower())


def _d_mixed_case_grain(ctx):
    """One item code carrying two size/pack signatures — the case-vs-each break. Dataset-grained
    per offending code, WITH the split named (both children are listed)."""
    key_field = "dist_item_code" if "dist_item_code" in ctx.fields else "upc"
    if key_field not in ctx.fields:
        return []
    groups = {}
    for i in range(len(ctx.rows)):
        k = str(ctx.get(i, key_field) or "").strip()
        if not k:
            continue
        groups.setdefault(k, {}).setdefault(_sig(ctx, i), []).append(i)
    out = []
    for k, sigs in groups.items():
        real = {s: rows for s, rows in sigs.items() if any(s)}
        if len(real) < 2:
            continue
        first = sorted(r[0] for r in real.values())[0]
        out.append({"row": first, "evidence": {
            "item_code": k, "signatures": [{"size": s[0], "pack": s[1], "rows": len(r)}
                                           for s, r in sorted(real.items())],
            "arithmetic": "item code %s appears under %d different size/pack signatures (%s) — "
                          "those are different sellable units sharing one code" % (
                              k, len(real), "; ".join("%s%s" % (s[0] or "?", "/" + s[1] if s[1] else "")
                                                      for s in sorted(real)))}})
    return out


def _d_size_pack_incoherence(ctx):
    """size_ml × pack disagrees with a stated total-volume column."""
    if not ctx.has("size_ml", "pack", "total_volume_ml"):
        return []
    out = []
    for i in range(len(ctx.rows)):
        ml, pk, tot = _f(ctx.get(i, "size_ml")), _f(ctx.get(i, "pack")), _f(ctx.get(i, "total_volume_ml"))
        if not (ml and pk and tot):
            continue
        expect = ml * pk
        if abs(expect - tot) / max(expect, tot) > 0.02:
            out.append({"row": i, "evidence": {
                "size_ml": ml, "pack": pk, "stated_total": tot, "computed_total": expect,
                "arithmetic": "%s ml × %s = %s, but the row states %s" % (ml, pk, expect, tot)}})
    return out


def _d_state_variant(ctx):
    """The regulatory-quirk family. Curated tribal knowledge made deterministic: state + rule +
    affected category + what to expect."""
    if "state" not in ctx.fields:
        return []
    quirks = BAND_DATA.get("state_quirks") or []
    control = set(BAND_DATA.get("control_states") or [])
    out = []
    for i in range(len(ctx.rows)):
        st = str(ctx.get(i, "state") or "").strip().upper()[:2]
        if not st:
            continue
        key, _ = band_for(*ctx.cat_of(i))
        for q in quirks:
            qs = q.get("state")
            applies = (qs == st) or (qs == "*CONTROL*" and st in control)
            if not applies:
                continue
            cats = q.get("categories") or []
            if cats and key not in cats:
                continue
            out.append({"row": i, "confidence": 0.75, "evidence": {
                "state": st, "category": key, "quirk": q.get("id"), "rule": q.get("rule"),
                "arithmetic": q.get("expect")}})
            break
    return out


def _d_mojibake(ctx):
    out = []
    for i in range(len(ctx.rows)):
        for f in ("brand", "product_name", "supplier"):
            v = ctx.get(i, f)
            if not v:
                continue
            fixed = precleanse.demojibake(str(v))
            if fixed != str(v):
                out.append({"row": i, "evidence": {
                    "field": f, "as_sent": str(v), "repaired": fixed,
                    "arithmetic": "UTF-8 read as latin-1: %r decodes to %r" % (str(v), fixed)}})
                break
    return out


_YR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def _d_vintage_in_name(ctx):
    out = []
    for i in range(len(ctx.rows)):
        nm = str(ctx.get(i, "product_name") or "")
        m = _YR.search(nm)
        if not m:
            continue
        if str(ctx.get(i, "vintage") or "").strip() == m.group(1):
            continue                        # already carried in its own column — nothing to say
        out.append({"row": i, "evidence": {
            "product_name": nm, "vintage": m.group(1),
            "arithmetic": "%r carries the vintage %s inside the name; a vintage in the name splits "
                          "one product into a new row every year" % (nm, m.group(1))}})
    return out


def _d_brand_repeated(ctx):
    if not ctx.has("brand", "product_name"):
        return []
    out = []
    for i in range(len(ctx.rows)):
        b, nm = str(ctx.get(i, "brand") or ""), str(ctx.get(i, "product_name") or "")
        kb = precleanse.nbrand(b)
        if not kb or not nm:
            continue
        toks = precleanse.nbrand(nm).split()
        bt = kb.split()
        if len(toks) > len(bt) and toks[:len(bt)] == bt:
            out.append({"row": i, "evidence": {
                "brand": b, "product_name": nm,
                "arithmetic": "the name repeats the brand at its head (%r inside %r) — the same "
                              "product reads as two levels of one hierarchy" % (b, nm)}})
    return out


REGISTRY = [
    Detector("upc_is_gtin", "bev-alc", [], DETERMINISTIC,
             "The column labelled UPC holds GTIN-14/EAN-13 values, not 12-digit UPC-A",
             "Read as GS1 canonical GTIN-14 in a new `gtin14` column; your column is left exactly as sent",
             "blocks_match", _d_upc_is_gtin, "structural", scope="dataset",
             requires_any=["upc", "gtin"], title="Your UPC column is actually GTIN"),
    Detector("zero_strip", "bev-alc", ["upc"], DETERMINISTIC,
             "The export tool ate the leading zeros on these codes",
             "Re-padded to 12 digits; accepted only where the mod-10 check proves the pad. Restored in `clean_upc`",
             "blocks_match", _d_zero_strip, "structural", systematic_ok=True,
             title="Leading zeros stripped from UPCs"),
    _class_detector("placeholder_upc", ("placeholder",), "Placeholder barcodes acting as real UPCs",
                    "A pre-assignment dummy barcode was shipped as the item's UPC",
                    "Excluded from identity — a dummy code shared across products would collapse them into one item",
                    "blocks_match"),
    _class_detector("bad_check_upc", ("bad_check",), "UPCs that fail their own check digit",
                    "The code is mistyped or truncated — its check digit does not match its body",
                    "Excluded from identity; the computed check digit is shown so the right code can be confirmed",
                    "blocks_match"),
    _class_detector("restricted_ns_upc", ("restricted", "coupon"),
                    "In-store codes used as global identifiers",
                    "A locally-assigned code (number system 2/4/5) is being used as a global product identity",
                    "Excluded from identity — retailers reuse these across different products",
                    "corrupts_analysis"),
    Detector("upc_owner_mismatch", "bev-alc", ["upc", "brand"], INFERENCE,
             "The UPC's GS1 company prefix belongs to a different company than the row names",
             "Verify the code: a borrowed or recycled UPC will match the wrong brand owner downstream",
             "corrupts_analysis", _d_owner_mismatch, "backtest",
             title="UPC belongs to a different company"),
    Detector("abv_proof_double", "bev-alc", ["abv"], DETERMINISTIC,
             "Proof was written into the ABV field",
             "Divide by 2 → the corrected ABV; move the original to a `proof` column",
             "corrupts_analysis", _d_abv_proof_double, "structural",
             title="Proof recorded as ABV"),
    Detector("abv_out_of_category", "bev-alc", ["abv"], INFERENCE,
             "The stated ABV is outside the range this category can hold",
             "Confirm against the label; an out-of-band ABV usually means a unit or a category error",
             "corrupts_analysis", _d_abv_out_of_category, "backtest",
             title="ABV outside its category band"),
    Detector("mixed_case_grain", "bev-alc", [], DETERMINISTIC,
             "One item code is carrying two different sellable units (case vs each)",
             "Split into the two children — both are named below; a 12-pack scanning as a single unit "
             "misstates volume by 12×",
             "corrupts_analysis", _d_mixed_case_grain, "structural", scope="dataset",
             requires_any=["dist_item_code", "upc"], title="One item code, two sellable units"),
    Detector("size_pack_incoherence", "generic", ["size_ml", "pack", "total_volume_ml"], DETERMINISTIC,
             "The stated total volume disagrees with size × pack",
             "Recompute the total from size × pack, or correct whichever of the three is wrong",
             "corrupts_analysis", _d_size_pack_incoherence, "structural",
             title="Size × pack ≠ stated total"),
    Detector("state_variant_sku", "bev-alc", ["state"], INFERENCE,
             "A state regulatory rule forces a state-specific SKU that this file may be conflating",
             "Check for the state-specific item; the national code is not the state code",
             "corrupts_analysis", _d_state_variant, "backtest",
             title="State rule forces a separate SKU"),
    Detector("mojibake", "generic", ["product_name"], DETERMINISTIC,
             "UTF-8 text was read as latin-1 somewhere upstream (Añejo → AÃ±ejo)",
             "Repaired in the `clean_*` column; fix the encoding on export to stop it recurring",
             "cosmetic", _d_mojibake, "structural", systematic_ok=True,
             title="Character encoding damage"),
    Detector("vintage_in_name", "bev-alc", ["product_name"], DETERMINISTIC,
             "The vintage is embedded in the product name instead of its own field",
             "Split the year into a `vintage` column so one wine stays one product across years",
             "corrupts_analysis", _d_vintage_in_name, "structural", systematic_ok=True,
             title="Vintage buried in the product name"),
    Detector("brand_repeated_in_name", "bev-alc", ["brand", "product_name"], DETERMINISTIC,
             "The brand is repeated at the head of the product name",
             "Cleansed in `clean_product_name`; the brand stays in its own column",
             "cosmetic", _d_brand_repeated, "structural", systematic_ok=True,
             title="Brand repeated inside the name"),
]

BY_ID = {d.id: d for d in REGISTRY}


# ── the runner ─────────────────────────────────────────────────────────────────────────────────

def _eligible(ctx, det):
    """Rows the detector could have fired on — the denominator the misfire guard divides by."""
    n = 0
    for i in range(len(ctx.rows)):
        if all(str(ctx.get(i, f) or "").strip() for f in det.requires):
            n += 1
    return n


def run(ctx, measured=None, log=None):
    """Run the registry over a mapped table. Returns {findings, skipped, silent, suppressed, meta}.

    A finding is:
        {id, title, label, confidence, severity, root_cause, fix, count, eligible, rows,
         evidence:[...], scope}
    """
    measured = measured if measured is not None else _load_precision()
    findings, skipped, silent, suppressed = [], [], [], []

    for det in REGISTRY:
        missing = [f for f in det.requires if f not in ctx.fields]
        if det.requires_any and not any(f in ctx.fields for f in det.requires_any):
            missing.append("one of " + "/".join(det.requires_any))
        if missing:
            # Gate 1 — SILENT skip. A file without an ABV column is not a file with a problem.
            skipped.append({"id": det.id, "missing": missing})
            continue
        try:
            hits = det.fn(ctx) or []
        except Exception as e:                       # a broken detector must never break the run
            skipped.append({"id": det.id, "error": str(e)[:120]})
            continue
        if not hits:
            continue

        elig = _eligible(ctx, det) or len(ctx.rows)
        row_hits = [h for h in hits if h.get("row") is not None]

        # Gate 3 — misfire suppression (dq.js misfireGuard, same thresholds).
        confs = {round(h.get("confidence", 1.0), 4) for h in row_hits}
        flat = len(confs) <= 1
        frac = (len(row_hits) / elig) if elig else 0.0
        misfired = (det.scope == "row" and not det.systematic_ok and len(row_hits) > MISFIRE_MIN_HITS
                    and frac > MISFIRE_ROW_FRACTION and flat)
        if misfired:
            suppressed.append({
                "id": det.id, "title": det.title, "label": INFERENCE, "severity": "cosmetic",
                "count": len(row_hits), "eligible": elig, "fraction": round(frac, 3),
                "root_cause": "This file's %s pattern is systematic — likely a convention, not %d "
                              "row-level errors. Detector withheld rather than shown wrong." % (
                                  "/".join(det.requires), len(row_hits)),
                "fix": "Tell us the convention and we encode it; nothing is changed meanwhile.",
                "scope": "dataset", "instances_withheld": len(row_hits)})
            continue

        # Gate 2 — the precision gate. An unmeasured heuristic accumulates stats and stays invisible.
        if not det.visible(measured):
            silent.append({"id": det.id, "count": len(hits), "eligible": elig,
                           "precision": measured.get(det.id, det.precision),
                           "precision_source": det.precision_source, "bar": det.bar(),
                           "why": "no measured precision on the backtest corpus"
                                  if measured.get(det.id, det.precision) is None
                                  else "measured precision below the %s bar" % det.label})
            continue

        conf = min([h.get("confidence", 1.0) for h in hits]) if det.label == INFERENCE else None
        findings.append({
            "id": det.id, "title": det.title, "label": det.label, "confidence": conf,
            "severity": det.severity, "root_cause": det.root_cause, "fix": det.fix,
            "scope": det.scope, "count": len(hits), "eligible": elig,
            "rows": [h["row"] for h in hits if h.get("row") is not None],
            "evidence": [h["evidence"] for h in hits[:25]],
            "precision": measured.get(det.id, det.precision),
        })

    findings.sort(key=lambda f: -_rank(f))
    if log:
        log("[overlay_detect] v%d: %d findings, %d silent (unmeasured/below bar), %d suppressed, "
            "%d skipped (fields absent)" % (REGISTRY_VERSION, len(findings), len(silent),
                                            len(suppressed), len(skipped)))
    return {"findings": findings, "silent": silent, "suppressed": suppressed, "skipped": skipped,
            "registry_version": REGISTRY_VERSION, "bands_version": BANDS_VERSION}


_SEV_W = {"blocks_match": 3.0, "corrupts_analysis": 2.0, "cosmetic": 1.0}


def _rank(f):
    """severity × confidence × specificity. Specificity rewards a finding that names few rows —
    'these 4 codes are wrong' outranks 'a third of your file is odd'."""
    sev = _SEV_W.get(f["severity"], 1.0)
    conf = f["confidence"] if f["confidence"] is not None else 1.0
    n, elig = f["count"], max(f["eligible"], 1)
    spec = 1.0 if f["scope"] == "dataset" else max(0.2, 1.0 - (n / elig))
    return sev * conf * spec


def catalog(measured=None):
    """The registry as data — for the provenance sheet and for /api/overlay/registry."""
    measured = measured if measured is not None else _load_precision()
    return {"registry_version": REGISTRY_VERSION, "bands_version": BANDS_VERSION,
            "detectors": [d.meta(measured) for d in REGISTRY],
            "visible": [d.id for d in REGISTRY if d.visible(measured)],
            "silent": [d.id for d in REGISTRY if not d.visible(measured)]}


def backtest(cases, log=print):
    """Measure precision per detector against a LABELLED corpus and write overlay_precision.json.

    `cases`: [{"rows": [...mapped dicts...], "fields": [...], "truth": {det_id: set(row indexes)}}]
    Precision = correct hits / all hits, pooled across cases. This is the only thing that can move
    a `backtest`-sourced detector from silent to visible — the encode playbook's gate, mechanized.
    """
    hits = {d.id: [0, 0] for d in REGISTRY}          # [correct, total]
    for case in cases:
        ctx = Ctx(case["rows"], case["fields"], cols=case.get("cols"),
                  crosswalk=case.get("crosswalk"), matched=case.get("matched"))
        truth = case.get("truth") or {}
        for det in REGISTRY:
            if any(f not in ctx.fields for f in det.requires):
                continue
            if det.requires_any and not any(f in ctx.fields for f in det.requires_any):
                continue
            try:
                got = det.fn(ctx) or []
            except Exception:
                continue
            want = set(truth.get(det.id) or ())
            for h in got:
                hits[det.id][1] += 1
                if h.get("row") is None or h["row"] in want:
                    hits[det.id][0] += 1
    out = {}
    for det in REGISTRY:
        c, t = hits[det.id]
        if t:
            out[det.id] = {"precision": round(c / t, 4), "hits": t, "correct": c}
    payload = {"registry_version": REGISTRY_VERSION, "detectors": out}
    with open(_PRECISION_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    log("[overlay_detect] backtest: measured %d detectors → %s" % (len(out), _PRECISION_FILE))
    return payload


def _selftest():
    rows = [
        # 0: proof written into ABV (80 on a bourbon; 80/2 = 40, inside 40–70)
        {"upc": "082184090565", "brand": "Jack Daniel's", "product_name": "Old No 7", "abv": "80",
         "category": "Bourbon Whiskey", "dist_item_code": "A1", "size_raw": "750 ML"},
        # 1: zero-stripped UPC
        {"upc": "36000291452", "brand": "Acme", "product_name": "Acme Gin", "abv": "40",
         "category": "Gin", "dist_item_code": "A2", "size_raw": "750 ML"},
        # 2: placeholder barcode
        {"upc": "000000000000", "brand": "Acme", "product_name": "Acme Vodka", "abv": "40",
         "category": "Vodka", "dist_item_code": "A3", "size_raw": "750 ML"},
        # 3: in-store restricted code used as a global key
        {"upc": "212345678909", "brand": "Store", "product_name": "House Red", "abv": "13",
         "category": "Table Wine", "dist_item_code": "A4", "size_raw": "750 ML"},
        # 4+5: one item code, two sellable units (case vs each)
        {"upc": "081584000228", "brand": "Kendall Jackson", "product_name": "Chardonnay 2019",
         "abv": "13.5", "category": "Table Wine", "dist_item_code": "A5", "size_raw": "750 ML"},
        {"upc": "081584000228", "brand": "Kendall Jackson", "product_name": "Chardonnay 2019",
         "abv": "13.5", "category": "Table Wine", "dist_item_code": "A5", "size_raw": "12/750 ML"},
        # 6: mojibake + brand repeated at the head of the name
        {"upc": "081753817107", "brand": "Herradura", "product_name": "Herradura AÃ±ejo",
         "abv": "40", "category": "Tequila", "dist_item_code": "A6", "size_raw": "750 ML"},
    ]
    fields = ["upc", "brand", "product_name", "abv", "category", "dist_item_code", "size_raw"]
    ctx = Ctx(rows, fields)
    res = run(ctx, measured={})
    got = {f["id"]: f for f in res["findings"]}

    # structural detectors are visible with no backtest file — the rule IS the proof
    assert "abv_proof_double" in got, res["findings"]
    ev = got["abv_proof_double"]["evidence"][0]
    assert ev["corrected_abv"] == 40.0 and "÷ 2" in ev["arithmetic"], ev
    assert got["abv_proof_double"]["label"] == DETERMINISTIC

    assert "zero_strip" in got and got["zero_strip"]["evidence"][0]["restored"] == "036000291452"
    assert "placeholder_upc" in got and "bad_check_upc" not in got, sorted(got)
    assert "restricted_ns_upc" in got, sorted(got)
    assert "mixed_case_grain" in got, sorted(got)
    assert got["mixed_case_grain"]["evidence"][0]["item_code"] == "A5"
    assert "mojibake" in got and got["mojibake"]["evidence"][0]["repaired"] == "Herradura Añejo"
    assert "brand_repeated_in_name" in got, sorted(got)
    assert "vintage_in_name" in got, sorted(got)   # "Chardonnay 2019"

    # heuristics stay SILENT without a measured precision — the gate's whole point
    assert "abv_out_of_category" not in got
    silent = {s["id"]: s for s in res["silent"]}
    assert "abv_out_of_category" not in silent or silent["abv_out_of_category"]["precision"] is None

    # requires-gate: no `state` column ⇒ the state detector skips silently, not loudly
    assert any(s["id"] == "state_variant_sku" for s in res["skipped"]), res["skipped"]

    # ── the precision gate lets a measured heuristic through, and holds a weak one back ──
    wide = [dict(r, abv="99", category="Table Wine") for r in rows[:3]]
    r2 = run(Ctx(wide, fields), measured={"abv_out_of_category": 0.95})
    assert any(f["id"] == "abv_out_of_category" for f in r2["findings"]), r2["findings"]
    r3 = run(Ctx(wide, fields), measured={"abv_out_of_category": 0.80})
    assert not any(f["id"] == "abv_out_of_category" for f in r3["findings"])
    assert any(s["id"] == "abv_out_of_category" and "below the" in s["why"] for s in r3["silent"])

    # ── misfire suppression: a heuristic over-firing at flat confidence is withheld ──
    many = [dict(upc="082184090565", brand="B", product_name="P", abv="99",
                 category="Table Wine", dist_item_code="C%d" % i, size_raw="750 ML")
            for i in range(40)]
    r4 = run(Ctx(many, fields), measured={"abv_out_of_category": 0.95})
    assert not any(f["id"] == "abv_out_of_category" for f in r4["findings"])
    sup = {s["id"]: s for s in r4["suppressed"]}
    assert "abv_out_of_category" in sup and sup["abv_out_of_category"]["instances_withheld"] == 40, sup
    assert "likely a convention" in sup["abv_out_of_category"]["root_cause"]

    # ── but a PROVEN detector is allowed to be systematic: a whole file of zero-stripped codes
    #    is the finding, not a misfire ──
    zs = [dict(upc="36000291452", brand="B", product_name="P", abv="40", category="Gin",
               dist_item_code="Z%d" % i, size_raw="750 ML") for i in range(40)]
    r5 = run(Ctx(zs, fields), measured={})
    assert any(f["id"] == "zero_strip" and f["count"] == 40 for f in r5["findings"]), r5["findings"]

    # ── the demo finding: a column CLAIMING to be UPC whose values are GTIN-14 ──
    cols = [{"name": "UPC", "field": "gtin", "label": DETERMINISTIC,
             "evidence": {"modal_len": 14, "n": 5, "check_pass": 1.0}}]
    r6 = run(Ctx([{"gtin": "00082184090565"}], ["gtin"], cols=cols), measured={})
    f6 = next(f for f in r6["findings"] if f["id"] == "upc_is_gtin")
    assert f6["scope"] == "dataset" and "EAN-13" not in f6["evidence"][0]["arithmetic"]
    assert "GTIN-14" in f6["evidence"][0]["arithmetic"], f6
    # a column already HONESTLY named gtin has nothing to correct — silence, not a finding
    cols2 = [dict(cols[0], name="GTIN14")]
    assert not any(f["id"] == "upc_is_gtin"
                   for f in run(Ctx([{"gtin": "00082184090565"}], ["gtin"], cols=cols2), {})["findings"])

    # ── owner mismatch needs the crosswalk; without it the detector is inert, not wrong ──
    xw = upc_mod.build_crosswalk([("036000291452", "ACME WINE CO")])
    mism = [{"upc": "036000291452", "brand": "Totally Different Distillery", "product_name": "X"}]
    r7 = run(Ctx(mism, ["upc", "brand", "product_name"], crosswalk=xw),
             measured={"upc_owner_mismatch": 0.97})
    f7 = next(f for f in r7["findings"] if f["id"] == "upc_owner_mismatch")
    assert f7["evidence"][0]["prefix_owner"] == "ACME WINE" and f7["label"] == INFERENCE, f7

    # ranking: a blocks_match finding on few rows outranks a broad cosmetic one
    assert _rank({"severity": "blocks_match", "confidence": None, "count": 2, "eligible": 100,
                  "scope": "row"}) > \
           _rank({"severity": "cosmetic", "confidence": None, "count": 50, "eligible": 100,
                  "scope": "row"})

    # a detector that raises must not take the run down with it
    boom = Detector("boom", "generic", [], DETERMINISTIC, "x", "y", "cosmetic",
                    lambda c: (_ for _ in ()).throw(RuntimeError("nope")), "structural")
    REGISTRY.append(boom); BY_ID["boom"] = boom
    try:
        r8 = run(Ctx(rows, fields), measured={})
        assert any(s.get("id") == "boom" and "error" in s for s in r8["skipped"]), r8["skipped"]
    finally:
        REGISTRY.remove(boom); BY_ID.pop("boom")

    print("overlay_detect.py self-test: OK (%d detectors, registry v%d, bands v%d)"
          % (len(REGISTRY), REGISTRY_VERSION, BANDS_VERSION))


if __name__ == "__main__":
    _selftest()
