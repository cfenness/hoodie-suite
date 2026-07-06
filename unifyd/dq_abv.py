"""dq_abv.py — data-quality check: is a product's ABV plausible for its TTB class/type?

dq.js spirit (DETERMINISTIC vs INFERENCE, with severity + provenance): map the class/type to a
category, then check the ABV against that category's PLAUSIBLE band (outside = a hard flag: a
vodka at 5% or a table wine at 45% is an error) and its TYPICAL band (outside typical but inside
plausible = a soft 'unusual, review'). Catches data-entry / OCR / mis-category errors before they
reach the product master. Pure stdlib; `python dq_abv.py` self-tests.
"""
import re

# class/type description -> category. Order matters (liqueur before spirit; sparkling/fortified
# before table wine; flavored-malt before malt).
CAT_RULES = [
    (re.compile(r"liqueur|cordial|schnapps|creme|cr[eè]me|aperitif|amaro|triple sec", re.I), "liqueur"),
    (re.compile(r"whisk|bourbon|scotch|\brye\b|vodka|\bgin\b|\brum\b|tequila|mezcal|brand(y|ies)|cognac|armagnac|aquavit|absinthe|grappa|neutral spirit|distilled|spirit", re.I), "spirit"),
    (re.compile(r"dessert|fortified|\bport\b|sherry|madeira|marsala|vermouth|aperitif wine|sake fortified", re.I), "wine_fortified"),
    (re.compile(r"sparkling|champagne|prosecco|cr[eé]mant|spumante|cava", re.I), "wine_sparkling"),
    (re.compile(r"\bsake\b|sak[eé]", re.I), "sake"),
    (re.compile(r"cider|perry", re.I), "cider"),
    (re.compile(r"flavored malt|malt beverage special|seltzer|hard seltzer|flavored beer", re.I), "fmb"),
    (re.compile(r"malt|beer|\bale\b|lager|stout|porter|pilsner|\bbock\b|saison|ipa", re.I), "malt"),
    (re.compile(r"wine|grape|\bmead\b|fruit wine|table|red|white|ros[eé]|blush", re.I), "wine_table"),
]

# category -> plausible (hard error outside) / typical (soft flag outside) ABV% bands + a note.
RANGES = {
    "spirit":         {"plaus": (20, 80),   "typ": (37, 65), "note": "distilled spirit — SOI min 40% for base classes"},
    "liqueur":        {"plaus": (10, 60),   "typ": (15, 40), "note": "liqueur/cordial"},
    "wine_table":     {"plaus": (5, 16),    "typ": (8, 15),  "note": "table wine (TTB tax class ≤14%)"},
    "wine_fortified": {"plaus": (14, 25),   "typ": (16, 22), "note": "fortified/dessert wine"},
    "wine_sparkling": {"plaus": (6, 15),    "typ": (10, 13), "note": "sparkling wine"},
    "sake":           {"plaus": (10, 22),   "typ": (14, 18), "note": "sake"},
    "cider":          {"plaus": (1.5, 12),  "typ": (4, 8.5), "note": "cider/perry (≤7% = hard-cider tax class)"},
    "malt":           {"plaus": (0, 16),    "typ": (3, 10),  "note": "malt beverage / beer"},
    "fmb":            {"plaus": (2, 14),    "typ": (4, 8),   "note": "flavored malt / seltzer"},
}


def category(class_type):
    s = str(class_type or "")
    for rx, cat in CAT_RULES:
        if rx.search(s):
            return cat
    return None


def _abv(v):
    m = re.search(r"([\d.]+)", str(v or ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def check_abv(class_type, abv):
    """Return {abv, category, status, severity, expected, note}. status ∈ ok | no_abv |
    unknown_category | implausible | out_of_range | atypical. severity ∈ high | low | none."""
    a = _abv(abv)
    cat = category(class_type)
    out = {"abv": a, "category": cat, "status": "ok", "severity": "none", "expected": None, "note": ""}
    if a is None:
        out.update(status="no_abv", note="no ABV captured")
        return out
    if a <= 0 or a >= 100:
        out.update(status="implausible", severity="high", note="ABV %g is physically impossible" % a)
        return out                                          # DETERMINISTIC
    if not cat:
        out.update(status="unknown_category", note="class/type not mapped to a category")
        return out
    r = RANGES[cat]; lo, hi = r["plaus"]; tlo, thi = r["typ"]; out["note"] = r["note"]
    if a < lo or a > hi:
        out.update(status="out_of_range", severity="high", expected=r["plaus"],
                   note="%g%% outside plausible %g–%g%% for %s" % (a, lo, hi, r["note"]))
    elif a < tlo or a > thi:
        out.update(status="atypical", severity="low", expected=r["typ"],
                   note="%g%% atypical for %s (usually %g–%g%%)" % (a, r["note"], tlo, thi))
    return out


def check_rows(rows, cls_key="Class/Type Code", abv_key="abv"):
    """Run over enriched rows; return {findings, summary}. Findings = non-ok, worst first."""
    findings, counts = [], {}
    for r in rows:
        v = check_abv(r.get(cls_key) or r.get("Class/Type") or "", r.get(abv_key))
        counts[v["status"]] = counts.get(v["status"], 0) + 1
        if v["status"] in ("out_of_range", "implausible", "atypical"):
            findings.append({"ttbid": r.get("TTB ID", ""), "brand": r.get("Brand Name", ""),
                             "class_type": r.get(cls_key, ""), **v})
    sev = {"high": 0, "low": 1, "none": 2}
    findings.sort(key=lambda f: sev.get(f["severity"], 3))
    checkable = sum(counts.get(k, 0) for k in ("ok", "out_of_range", "implausible", "atypical"))
    flagged = counts.get("out_of_range", 0) + counts.get("implausible", 0)
    return {"findings": findings,
            "summary": {"rows": len(rows), "checkable": checkable, "ok": counts.get("ok", 0),
                        "flags_high": flagged, "flags_low": counts.get("atypical", 0),
                        "no_abv": counts.get("no_abv", 0), "unknown_category": counts.get("unknown_category", 0),
                        "flag_rate": round(flagged / max(1, checkable), 3)}}


def _selftest():
    assert check_abv("VODKA", "40")["status"] == "ok"
    assert check_abv("VODKA", "5")["status"] == "out_of_range"           # way below spirit floor
    assert check_abv("BOURBON WHISKY", "45% ABV")["status"] == "ok"
    assert check_abv("BOURBON WHISKY", "95")["status"] == "out_of_range"
    assert check_abv("TABLE RED WINE", "45")["status"] == "out_of_range"
    assert check_abv("TABLE RED WINE", "13")["status"] == "ok"
    assert check_abv("MALT BEVERAGE", "6")["status"] == "ok"
    assert check_abv("WHISKY", "35")["status"] == "atypical"             # plausible but below typical
    assert check_abv("VODKA", "150")["status"] == "implausible"
    assert check_abv("OTHER GIN", "")["status"] == "no_abv"
    assert check_abv("MYSTERY CLASS", "40")["status"] == "unknown_category"
    assert check_abv("PORT", "19")["status"] == "ok" and check_abv("PORT", "19")["category"] == "wine_fortified"
    rows = [{"TTB ID": "1", "Brand Name": "Bad Vodka", "Class/Type Code": "VODKA", "abv": "5"},
            {"TTB ID": "2", "Brand Name": "Good Bourbon", "Class/Type Code": "BOURBON WHISKY", "abv": "45"},
            {"TTB ID": "3", "Brand Name": "Weird Wine", "Class/Type Code": "TABLE RED WINE", "abv": "17"}]
    res = check_rows(rows)
    # Bad Vodka (5%) + Weird Wine (17% > table-wine 16% max) both flag high; Good Bourbon ok
    assert res["summary"]["flags_high"] == 2 and res["summary"]["ok"] == 1, res["summary"]
    assert res["findings"][0]["severity"] == "high", res["findings"]
    print("dq_abv self-test: OK —", res["summary"])


if __name__ == "__main__":
    _selftest()
