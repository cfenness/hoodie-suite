#!/usr/bin/env python3
"""normalization_scout.py — sweeps LANDED source tables for data that isn't normalized yet.

The contract ([[append-only-versioned-master]]): landed data is NEVER rewritten. Every finding here is a
PROPOSAL for the translation layer — the src_/agg builds that read the raw tables — never an UPDATE against
the landing spot. The scout's job is discovery: the classes of un-normalized data nobody has written a
deterministic rule for yet (a zero-stripped UPC, an "N/A" masquerading as a value, a size trapped in a name).
Once a pattern is confirmed it gets codified as a deterministic rule (upc.normalize / precleanse / dict) and
the scout stops reporting it as new.

Two passes, kept honest in the dq.js spirit (DETERMINISTIC vs INFERENCE):

  1. DETERMINISTIC profiling — pure SQL/python over each registry table's fields, no LLM:
       code fields (upc/gtin/barcode): digit-length histogram; the ZERO-STRIP test (a 10/11-digit cohort
         whose check digit passes when zero-padded is PROVABLY spreadsheet-stripped — random pads pass 10%);
         upc.classify distribution (placeholder / bad_check / restricted shares).
       text fields: null-encodings ("N/A"/"--"/"unknown" as fake values), stray whitespace, mojibake
         (Ã± / â€™ artifacts), ALL-CAPS share (case harmonization candidate), numeric-stored-as-text.
       name fields: embedded size/pack rate — size parseable from the name that translation should CAPTURE
         (the dim_item grain starves without it, [[master-item-grain]]).

  2. INFERENCE (optional, --adjudicate + ANTHROPIC_API_KEY) — Claude reads the per-field PROFILES (never row
     data, so cost stays bounded) and proposes rules the deterministic battery didn't anticipate.

Findings land in `normalization_findings` (derived table, rebuilt each run) for the MDM DQ surface, and print
as a report. Usage:

    python normalization_scout.py                      # all registry tables present in the warehouse
    python normalization_scout.py --tables abc_products kroger_products
    python normalization_scout.py --adjudicate         # + Claude proposals over the profiles
"""
import argparse, collections, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import upc
import sku_match as _sku_match

CODE_COL = re.compile(r"upc|gtin|barcode|ean", re.I)
NAME_COL = re.compile(r"^(name|product_name|description|title|item_name|display_name|desc)$", re.I)
SKIP_COL = re.compile(r"raw_json|_json$|image|photo|url|href|link|^_|^id$|_id$|hash", re.I)
NULLISH = {"n/a", "na", "none", "null", "unknown", "unk", "--", "-", ".", "tbd", "?", "0", "0.0", "nan"}
MOJIBAKE = ("Ã", "â€", "�")
SIZE_IN_NAME = re.compile(r"\b\d[\d.]*\s?(?:ml|l|ltr|liter|litre|oz|pk|pack|ct)\b", re.I)
NUMERICISH = re.compile(r"^-?\d+(\.\d+)?$")


def _s(v):
    """Value → clean string. Guards the float trap: a UPC landed as DOUBLE prints '36000291452.0' and a naive
    digit-strip would APPEND the 0 — the exact class of silent corruption this scout exists to catch."""
    if v is None:
        return ""
    t = str(v)
    return t[:-2] if t.endswith(".0") and NUMERICISH.match(t) else t


def registry_tables():
    import source_registry
    seen, out = set(), []
    for s in source_registry.SOURCES:
        for t in (s.get("tables") or []):
            if t not in seen:
                seen.add(t); out.append(t)
    return out


def _sample(table, col, n):
    try:
        rows = warehouse.query(table, 'SELECT v FROM (SELECT CAST("%s" AS VARCHAR) v FROM t USING SAMPLE %d ROWS) '
                                      "WHERE v IS NOT NULL AND v <> ''" % (col, n))
        return [_s(r["v"]) for r in rows]
    except Exception:
        return []


def _finding(table, col, check, kind, cohort_n, total_n, evidence, proposal, samples=()):
    return dict(table=table, field=col, check=check, kind=kind, cohort_n=cohort_n, total_n=total_n,
                share=round(cohort_n / max(1, total_n), 4), evidence=evidence, proposal=proposal,
                samples=" | ".join(list(samples)[:5])[:300])


def scan_code_field(table, col, vals, min_cohort):
    """The UPC battery: length histogram + the zero-strip proof + classify distribution."""
    out, digits = [], [upc.only_digits(v) for v in vals if v]
    digits = [d for d in digits if d]
    if not digits:
        return out
    n = len(digits)
    by_len = collections.Counter(len(d) for d in digits)
    # zero-strip: pad the 7/10/11 cohorts and let the check digit testify
    for L, target in ((11, 12), (10, 12), (7, 8)):
        cohort = [d for d in digits if len(d) == L]
        if len(cohort) < min_cohort:
            continue
        healed = [d for d in cohort if upc.check_digit_ok(d.zfill(target))]
        rate = len(healed) / len(cohort)
        if rate >= 0.8:      # random pads pass 10% — ≥80% is proof the zeros were stripped upstream
            out.append(_finding(table, col, "zero_stripped_codes", "deterministic", len(cohort), n,
                                "%d of %d %d-digit codes pass the check digit when zero-padded to %d (%.0f%%; "
                                "chance is 10%%)" % (len(healed), len(cohort), L, target, rate * 100),
                                "heal at translation: zfill(%d) iff check digit passes — upc.normalize() now "
                                "does this; re-run the agg build" % target,
                                ("%s→%s" % (d, d.zfill(target)) for d in healed)))
        elif rate < 0.3:     # a wrong-length cohort that ISN'T zero-stripped is junk in the column
            out.append(_finding(table, col, "malformed_length_cohort", "deterministic", len(cohort), n,
                                "%d codes at %d digits but only %.0f%% heal by zero-pad — not stripped zeros, "
                                "likely a different field bleeding in" % (len(cohort), L, rate * 100),
                                "inspect source mapping; exclude cohort at translation", cohort))
    # checkless-13 (the Kroger class): a 13-digit '00'-padded cohort mostly FAILING the EAN-13 check is a
    # code body missing its check digit — provable by cross-source join lift after upc.from_checkless_13
    c13 = [d for d in digits if len(d) == 13 and d[:2] == "00"]
    explained = set()          # codes a more specific finding accounts for — kept out of the generic buckets
    if len(c13) >= min_cohort:
        bad = [d for d in c13 if not upc.check_digit_ok(d)]
        if len(bad) / len(c13) >= 0.5:
            explained.update(bad)
            out.append(_finding(table, col, "checkless_13", "deterministic", len(bad), n,
                                "%d of %d '00'-padded 13-digit codes fail the EAN-13 check (%.0f%%; a healthy "
                                "column fails ~0%%) — code body missing its check digit" %
                                (len(bad), len(c13), 100 * len(bad) / len(c13)),
                                "heal at translation with upc.from_checkless_13 (drop pad, append computed "
                                "check); verify via cross-source UPC join lift",
                                ("%s→%s" % (d, upc.from_checkless_13(d)) for d in bad)))
    rest = [d for d in digits if d not in explained]
    # restricted/in-store codes (number-system 2/4/5) are LOCALLY assigned — reused across products, never a
    # global key. classify() reports these as bad_check (its check runs first), so surface them explicitly.
    restricted = [d for d in rest if _sku_match.norm_upc(d) is None and len(d) in (12, 13)
                  and (d[-12:] if len(d) >= 12 else d.zfill(12))[0] in ("2", "4", "5")]
    if len(restricted) >= min_cohort:
        explained.update(restricted)
        out.append(_finding(table, col, "restricted_instore_codes", "deterministic", len(restricted), n,
                            "%d codes are number-system 2/4/5 (variable-weight/in-store/coupon) — locally "
                            "assigned, reused across products" % len(restricted),
                            "sku_match.norm_upc rejects these as match keys (not a global identity); ensure "
                            "any source-specific UPC use does the same", restricted))
    rest = [d for d in rest if d not in explained]
    cls = collections.Counter(upc.classify(d) for d in rest)
    for status, prop in (("placeholder", "suppress at translation — pre-assignment dummy barcodes"),
                         ("bad_check", "quarantine at translation; check for truncation/typo class"),
                         ("malformed", "inspect: lengths %s" % dict(sorted(by_len.items())))):
        k = cls.get(status, 0)
        if k >= min_cohort and k / n >= 0.02:
            out.append(_finding(table, col, "code_%s" % status, "deterministic", k, n,
                                "%d/%d (%.1f%%) classify as %s" % (k, n, 100 * k / n, status), prop,
                                (d for d in rest if upc.classify(d) == status)))
    return out


def scan_text_field(table, col, vals, min_cohort):
    out, n = [], len(vals)
    if not n:
        return out
    num = [v for v in vals if NUMERICISH.match(v.strip())]
    num_share = len(num) / n
    # a numeric-dominant column's '0' is DATA (in_stock=0), not a null-encoding — narrow the token set
    tokens = NULLISH - {"0", "0.0", "nan"} if num_share >= 0.3 else NULLISH
    nullish = [v for v in vals if v.strip().lower() in tokens]
    if len(nullish) >= min_cohort:
        out.append(_finding(table, col, "null_encoded_values", "deterministic", len(nullish), n,
                            "%d/%d values are null-encodings (%s)" %
                            (len(nullish), n, ", ".join(sorted({v.strip().lower() for v in nullish})[:6])),
                            "translate to NULL at the agg layer so they never key/count as real values", nullish))
    ws = [v for v in vals if v != v.strip() or "  " in v]
    if len(ws) >= min_cohort:
        out.append(_finding(table, col, "stray_whitespace", "deterministic", len(ws), n,
                            "%d/%d values carry leading/trailing/double whitespace" % (len(ws), n),
                            "strip + collapse at translation (precleanse)", (repr(v) for v in ws)))
    moji = [v for v in vals if any(m in v for m in MOJIBAKE)]
    if moji:
        out.append(_finding(table, col, "mojibake", "deterministic", len(moji), n,
                            "%d/%d values contain encoding artifacts (Ã/â€/�) — añejo/rosé class" % (len(moji), n),
                            "fix decode at translation (latin-1↔utf-8 double-encode); source fetch may need charset",
                            moji))
    alpha = [v for v in vals if len(v) > 3 and any(c.isalpha() for c in v)]
    caps = [v for v in alpha if v.isupper()]
    if alpha and len(caps) / len(alpha) >= 0.6 and len(caps) >= min_cohort:
        out.append(_finding(table, col, "allcaps_field", "inference", len(caps), n,
                            "%.0f%% of values are ALL-CAPS — price-list style; cross-source joins are "
                            "case-hostile" % (100 * len(caps) / len(alpha)),
                            "case-normalize INSIDE match keys only (display keeps the landed casing)", caps))
    if n >= min_cohort and num_share >= 0.95 and not CODE_COL.search(col):
        out.append(_finding(table, col, "numeric_as_text", "deterministic", len(num), n,
                            "%.0f%% of values are numeric strings in a text column" % (100 * num_share),
                            "cast at translation; keep landed type as-is", num))
    if NAME_COL.match(col):
        sized = [v for v in vals if SIZE_IN_NAME.search(v)]
        if len(sized) >= min_cohort:
            out.append(_finding(table, col, "size_embedded_in_name", "deterministic", len(sized), n,
                                "%d/%d (%.0f%%) names embed a size/pack token" % (len(sized), n, 100 * len(sized) / n),
                                "CAPTURE size_ml/pack from the name at translation (don't just strip it) — "
                                "feeds the dim_item grain", sized))
    return out


def scan_table(table, meta, sample_n, min_cohort, log=print):
    cols = [c for c in (meta.get("fields") or []) if not SKIP_COL.search(c) or CODE_COL.search(c)]
    try:      # landed types: the text battery only applies to columns that ARE text (a DOUBLE price cast
        types = {r["column_name"]: r["column_type"] for r in warehouse.query(table, "DESCRIBE t")}
    except Exception:  # to VARCHAR for sampling must not read as "numeric stored as text")
        types = {}
    findings, profiles = [], []
    for col in cols:
        is_text = types.get(col, "VARCHAR").upper().startswith("VARCHAR")
        if not is_text and not CODE_COL.search(col):
            continue
        vals = _sample(table, col, sample_n)
        if not vals:
            continue
        f = scan_code_field(table, col, vals, min_cohort) if CODE_COL.search(col) else \
            scan_text_field(table, col, vals, min_cohort)
        findings.extend(f)
        # compact per-field profile — what the adjudicator reads (never row data)
        lens = collections.Counter(len(v) for v in vals)
        profiles.append(dict(table=table, field=col, sampled=len(vals),
                             distinct=len(set(vals)), top_lengths=dict(lens.most_common(5)),
                             examples=[v[:60] for v in vals[:3]]))
    log("[scout]   %-28s %2d fields scanned, %2d findings" % (table, len(cols), len(findings)))
    return findings, profiles


def adjudicate(findings, profiles, log=print):
    """INFERENCE pass: Claude reads the profiles + deterministic findings and proposes what the battery
    missed. Profiles only — the LLM never sees row data, so cost stays bounded."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("[scout] --adjudicate skipped: no ANTHROPIC_API_KEY")
        return []
    import anthropic          # lazy — only when the pass is on
    client = anthropic.Anthropic()
    prompt = (
        "You are auditing per-field PROFILES of scraped bev-alc retail tables for normalization gaps that a "
        "deterministic battery already ran over (its findings included). Landed data is never rewritten; every "
        "fix is a translation-layer rule. Propose ONLY new, concrete, codifiable rules (regex/cast/dict) the "
        "battery missed — no restatements, no per-row fixes. Reply as a JSON array of "
        '{"table","field","check","evidence","proposal"} and nothing else. Empty array if nothing new.\n\n'
        "DETERMINISTIC FINDINGS:\n%s\n\nPROFILES:\n%s"
        % (json.dumps([{k: f[k] for k in ("table", "field", "check", "evidence")} for f in findings], indent=0),
           json.dumps(profiles, indent=0, default=str)))
    msg = client.messages.create(model=os.environ.get("SCOUT_MODEL", "claude-sonnet-5"),
                                 max_tokens=4000, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    out = []
    for p in (json.loads(m.group(0)) if m else []):
        out.append(_finding(p.get("table", "?"), p.get("field", "?"), "claude:" + p.get("check", "proposal"),
                            "inference", 0, 0, p.get("evidence", ""), p.get("proposal", "")))
    log("[scout] adjudication: %d proposals" % len(out))
    return out


def run(tables=None, sample_n=20000, min_cohort=25, do_adjudicate=False, write=True, log=print):
    have = {d["name"]: d for d in warehouse.list_datasets()}
    want = tables or [t for t in registry_tables() if t in have]
    missing = [t for t in (tables or []) if t not in have]
    if missing:
        log("[scout] not in warehouse: %s" % ", ".join(missing))
    findings, profiles = [], []
    for t in want:
        if t not in have:
            continue
        f, p = scan_table(t, have[t], sample_n, min_cohort, log)
        findings.extend(f); profiles.extend(p)
    if do_adjudicate:
        findings.extend(adjudicate(findings, profiles, log))
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for f in findings:
        f["found_at"] = ts
    findings.sort(key=lambda f: (-f["share"], f["table"]))
    for f in findings:
        log("[scout] %-13s %-24s %-24s %6d/%-7d %s" %
            (f["kind"], f["table"], f["field"] + ":" + f["check"], f["cohort_n"], f["total_n"], f["evidence"]))
    if write:
        warehouse.write_parquet("normalization_findings", findings,
                                ["table", "field", "check", "kind", "cohort_n", "total_n", "share",
                                 "evidence", "proposal", "samples", "found_at"], allow_empty=True)
        log("[scout] wrote %d findings → normalization_findings" % len(findings))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description="find landed data that isn't normalized (read-only; proposals only)")
    ap.add_argument("--tables", nargs="*", help="scope to these tables (default: registry tables in the warehouse)")
    ap.add_argument("--sample", type=int, default=20000, help="values sampled per field")
    ap.add_argument("--min-cohort", type=int, default=25, help="smallest cohort worth reporting")
    ap.add_argument("--adjudicate", action="store_true", help="Claude pass over the profiles (ANTHROPIC_API_KEY)")
    ap.add_argument("--no-write", action="store_true", help="print only; skip normalization_findings")
    a = ap.parse_args(argv)
    run(tables=a.tables, sample_n=a.sample, min_cohort=a.min_cohort,
        do_adjudicate=a.adjudicate, write=not a.no_write)


if __name__ == "__main__":
    main()
