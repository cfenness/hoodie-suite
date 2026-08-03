"""overlay.py — the Overlay pipeline: upload → match → cleanse → derive → diagnose → report.

One pass over an uploaded file that returns it annotated. The stages are the design's S0–S9 and
each one lives in a module of its own; this is the orchestrator and the artifact writer.

    S0  ingest            analyze.parse_upload      (CSV/TSV/XLSX, header auto-detect, demojibake)
    S2  semantic mapping  overlay_map              their columns → master fields, values first
    S3  identity          overlay_match            five tiers, reported BY TIER
    S4  cleanse           here + precleanse/upc    original values never modified, only annotated
    S5  derive & enrich   here                     from the matched master record, with provenance
    S6  diagnose          overlay_detect           the governed detector registry
    S7  market read       overlay_market           joins to our own observations, bar-gated
    S9  return            here                     the on-screen read + the returned workbook

Two rules the orchestrator itself enforces:

  THE ORIGINAL FILE IS NEVER MODIFIED. Every cleanse writes a NEW `clean_*` column beside theirs.
  A returned workbook where we quietly "fixed" their data would be unusable as evidence, and the
  whole artifact is evidence.

  EVERY STAGE DEGRADES HONESTLY AND SAYS WHY. A stage that can't run reports its reason in the
  output ("no UPC column found → identity resolved by brand+size+name only, match confidence
  capped at INFERENCE"). "Structure doesn't matter" is a product promise, and a promise that
  fails silently is worse than one that fails out loud.
"""
import re
import time

import analyze
import overlay_detect
import overlay_map
import overlay_market
import overlay_match
import precleanse
import upc as upc_mod
import xlsx_write

PIPELINE_VERSION = 1

# Caps per tier. Beyond these, the free page returns the "this is an engagement" card — a
# qualifying signal, not an error.
CAPS = {"free": {"bytes": 25 * 1024 * 1024, "rows": 250_000},
        "suite": {"bytes": 200 * 1024 * 1024, "rows": 2_000_000}}

# S5 — what the master can add once a row is matched. `free` is what the free page shows; the rest
# are named-but-locked: the header is visible in the workbook, the values are withheld. That is the
# cleanest upgrade CTA there is, because it isn't a banner — it's their own file with columns they
# can see exist.
# NB `gtin14` is NOT here: it is DERIVED FROM THEIR OWN UPC, so it belongs to the cleanse stage
# and is written as `clean_*` output. Listing it in both places produced a workbook with two
# columns of the same name — one from cleanse, one from enrichment — which is exactly the kind of
# small wrongness that makes a reader distrust the careful parts.
ENRICH = [
    ("hoodie_category", "category", True),
    ("hoodie_brand", "brand", True),
    ("brand_owner", None, True),
    ("hoodie_size_ml", "size_ml", True),
    ("hoodie_class_type", "class_type", True),
    ("hoodie_varietal", "varietal", False),
    ("hoodie_origin", "origin", False),
    ("hoodie_supplier", "supplier", False),
    ("hoodie_container", "container", False),
    ("bottles_per_case", "pack", False),
    ("hoodie_abv", "abv", False),
    ("hoodie_image", "image", False),
]
LOCKED_VALUE = "—locked—"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── S4: cleanse ────────────────────────────────────────────────────────────────────────────────

def _to_ml(v):
    """Size in ml from whatever they wrote. Shared shape with build_product_master._to_ml; kept
    local so the pipeline doesn't drag the whole master build into a request."""
    s = str(v or "")
    m = re.search(r"([\d.]+)\s*(ml|l|lt|ltr|liter|litre|oz)\b", s, re.I)
    if m:
        try:
            n = float(m.group(1))
        except ValueError:
            return None
        u = m.group(2).lower()
        n = n * 1000 if u[0] == "l" else (n * 29.57 if u == "oz" else n)
        return round(n) if 1 <= n <= 20000 else None
    if re.fullmatch(r"\s*[\d.]+\s*", s):
        try:
            n = float(s)
        except ValueError:
            return None
        return round(n) if 1 <= n <= 20000 else None
    return None


_PACK = re.compile(r"\b(\d{1,2})\s*(?:pk|pack|packs|ct|count)\b|\bpack\s*of\s*(\d{1,2})\b|\b(\d{1,2})\s*[x/]\s*\d",
                   re.I)


def _pack_of(*texts):
    for t in texts:
        m = _PACK.search(str(t or ""))
        if not m:
            continue
        n = next((g for g in m.groups() if g), None)
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 2 <= n <= 48:
            return n
    return None


def _brand_canon(mapped):
    """brand_key → the canonical DISPLAY spelling within this file. Same preference order as
    precleanse.build_brand_canon (mixed-case over ALL-CAPS, then most frequent, then shortest),
    keyed on the stronger overlay brand key."""
    counts = {}
    for r in mapped:
        b = str(r.get("brand") or "").strip()
        if b:
            counts[b] = counts.get(b, 0) + 1
    groups = {}
    for b in counts:
        k = overlay_match.brand_key(b)
        if k:
            groups.setdefault(k, []).append(b)
    out = {}
    for k, variants in groups.items():
        best = sorted(variants, key=lambda b: (b.upper() == b, -counts[b], len(b)))[0]
        out[k] = precleanse.clean_name(best, None) if best.upper() == best else precleanse.unescape(best)
    return out


def cleanse(mapped, fields, log=None):
    """Add the `clean_*` values for every mapped field. Returns (clean_rows, counts).

    Nothing here writes back into `mapped` — the caller keeps their values verbatim and the clean
    values ride alongside. Every change is counted, and the counts ARE the report line
    ("brand: 2,014 of 11,300 values canonicalized; size: 96% parsed to ml; 41 unparseable").
    """
    n = len(mapped)
    clean = [{} for _ in range(n)]
    counts = {}

    def bump(key, k="changed"):
        c = counts.setdefault(key, {"eligible": 0, "changed": 0, "unparseable": 0})
        c[k] += 1

    # brand — canonicalized against the file's OWN variants, so "TITO HANDMADE" and "Tito's
    # Handmade" land on one display within the file before anything is matched or counted.
    if "brand" in fields:
        # Grouped on overlay_match.brand_key, not precleanse.nbrand: within ONE uploaded file the
        # same brand routinely appears as "TITO HANDMADE" and "TITOS HANDMADE", and those must
        # collapse to one display before anything is counted per brand.
        canon = _brand_canon(mapped)
        for i, r in enumerate(mapped):
            b = str(r.get("brand") or "").strip()
            if not b:
                continue
            bump("brand", "eligible")
            cb = canon.get(overlay_match.brand_key(b)) or precleanse.unescape(b)
            clean[i]["clean_brand"] = cb
            if cb != b:
                bump("brand")

    if "product_name" in fields:
        for i, r in enumerate(mapped):
            nm = str(r.get("product_name") or "").strip()
            if not nm:
                continue
            bump("product_name", "eligible")
            cn = precleanse.clean_name(nm, clean[i].get("clean_brand") or r.get("brand"))
            # the brand at the head of the name is repetition, not information
            b = overlay_match.brand_key(clean[i].get("clean_brand") or r.get("brand") or "")
            if b:
                drop = set()
                for t in b.split():
                    drop.update({t, t + "s", t.rstrip("s")})
                toks = cn.split()
                while toks and re.sub(r"[^a-z]", "", toks[0].lower()) in drop:
                    toks.pop(0)
                cn = " ".join(toks) or cn
            clean[i]["clean_product_name"] = cn
            if cn != nm:
                bump("product_name")

    if "upc" in fields or "gtin" in fields:
        for i, r in enumerate(mapped):
            raw = str(r.get("upc") or r.get("gtin") or "").strip()
            if not raw:
                continue
            bump("upc", "eligible")
            g14 = upc_mod.to_gtin14(raw)
            if g14:
                clean[i]["clean_upc"] = upc_mod.normalize(raw)
                clean[i]["gtin14"] = g14
                if upc_mod.normalize(raw) != re.sub(r"\D", "", raw):
                    bump("upc")                       # the heal changed it (zero-strip)
            else:
                clean[i]["clean_upc"] = ""
                bump("upc", "unparseable")

    if "size_raw" in fields or "size_ml" in fields:
        for i, r in enumerate(mapped):
            raw = r.get("size_ml") or r.get("size_raw")
            if not str(raw or "").strip():
                continue
            bump("size", "eligible")
            ml = _to_ml(raw)
            if ml:
                clean[i]["clean_size_ml"] = ml
                bump("size")
            else:
                bump("size", "unparseable")

    for i, r in enumerate(mapped):
        pk = _pack_of(r.get("pack"), r.get("size_raw"), r.get("product_name"))
        if pk:
            clean[i]["clean_pack"] = pk

    if "abv" in fields:
        for i, r in enumerate(mapped):
            v = str(r.get("abv") or "").strip()
            if not v:
                continue
            bump("abv", "eligible")
            try:
                f = float(re.sub(r"[^0-9.]", "", v))
            except ValueError:
                bump("abv", "unparseable")
                continue
            clean[i]["clean_abv"] = f

    for k, c in counts.items():
        c["parse_rate"] = round(1 - c["unparseable"] / c["eligible"], 3) if c["eligible"] else None
    if log:
        log("[overlay] cleansed: " + "; ".join(
            "%s %d/%d" % (k, c["changed"], c["eligible"]) for k, c in sorted(counts.items())))
    return clean, counts


# ── S5: derive & enrich ────────────────────────────────────────────────────────────────────────

def enrich(matches, clean, tier="free", crosswalk=None):
    """Fields added from the master via the matched Hoodie ID. Every enriched value carries
    provenance; on the free tier the non-free columns are present but withheld."""
    rows = []
    for m, c in zip(matches, clean):
        out = {}
        matched = m.get("hoodie_id") is not None or m.get("matched_display")
        for col, src, is_free in ENRICH:
            if tier == "free" and not is_free:
                out[col] = LOCKED_VALUE if matched else ""
                continue
            if col == "brand_owner":
                out[col] = _owner(c.get("clean_upc"), crosswalk)
            else:
                # read from the matched master record carried on the match — never from a
                # `matched_<field>` key that may not exist, which resolves to a silent blank column
                out[col] = (m.get("master") or {}).get(src) if src else ""
                out[col] = "" if out[col] is None else out[col]
        rows.append(out)
    return rows


def _owner(code, crosswalk):
    if not code or not crosswalk:
        return ""
    hit = crosswalk.get(upc_mod.brand_key(code))
    return (hit or {}).get("owner", "")


def enrich_columns(tier="free"):
    return [{"column": col, "locked": (tier == "free" and not is_free),
             "source": "hoodie_master", "basis": "DERIVED" if col == "brand_owner" else "OBSERVED"}
            for col, _src, is_free in ENRICH]


# ── the run ────────────────────────────────────────────────────────────────────────────────────

def run(filename, raw, tier="free", mode="consumer", master=None, obs=None, crosswalk=None,
        log=None, distributor=None):
    """The whole pipeline. Returns the result dict the UI renders and the workbook is built from.

    `master` / `obs` are injected so the pipeline is testable end-to-end with no warehouse; in
    production the caller passes `overlay_match.WarehouseMaster()` and `overlay_market.WarehouseObs()`.
    """
    t0 = time.time()
    caps = CAPS.get(tier, CAPS["free"])
    stages = []

    if raw is not None and len(raw) > caps["bytes"]:
        return {"error": "too-large", "engagement": True,
                "detail": "This file is %d MB; the self-serve page handles up to %d MB. Files this "
                          "size are an engagement — we'd rather do it properly than truncate it."
                          % (len(raw) // (1024 * 1024), caps["bytes"] // (1024 * 1024))}

    # ── S0 ingest ──
    try:
        parsed = analyze.parse_upload(filename or "upload.csv", raw)
    except Exception as e:
        return {"error": "parse-failed", "detail": str(e)[:200]}
    header, rows = parsed.get("header") or [], parsed.get("rows") or []
    if not header or not rows:
        return {"error": "parse-failed",
                "detail": "couldn't find a header row and data — the first populated row should be "
                          "your column names"}
    truncated = 0
    if len(rows) > caps["rows"]:
        truncated = len(rows) - caps["rows"]
        rows = rows[:caps["rows"]]
    stages.append({"stage": "ingest", "rows": len(rows), "columns": len(header),
                   "header_row": parsed.get("header_row"), "sheet": parsed.get("sheet"),
                   "truncated": truncated})

    # ── S2 mapping ──
    cols = overlay_map.map_columns(header, rows)
    fmap = overlay_map.field_index(cols)
    msum = overlay_map.summary(cols)
    fields = sorted(fmap)
    stages.append({"stage": "map", **msum})

    # the mapped view — master field → their value, per row. PII columns are absent by construction.
    mapped = [{f: (r[ci] if ci < len(r) else "") for f, ci in fmap.items()} for r in rows]

    # ── S3 identity ──
    master = master if master is not None else overlay_match.WarehouseMaster()
    # `distributor` pins Tier 3. Unset, the resolver infers it from the file — a bare item code is
    # ambiguous across books (36% of keys collide on the live crosswalk), so this is what makes
    # "we matched on your own item numbers" true rather than aspirational.
    mres = overlay_match.resolve(mapped, fields, master, log=log, distributor=distributor)
    matches, agg = mres["matches"], mres["aggregate"]
    stages.append({"stage": "match", **{k: v for k, v in agg.items() if k != "master_status"}})

    # ── S4 cleanse ──
    clean, counts = cleanse(mapped, set(fields), log=log)
    stages.append({"stage": "cleanse", "fields": counts})

    # ── S5 derive ──
    enriched = enrich(matches, clean, tier=tier, crosswalk=crosswalk)
    stages.append({"stage": "derive", "columns": enrich_columns(tier)})

    # ── S6 diagnose ──
    ctx = overlay_detect.Ctx(mapped, fields, cols=cols, crosswalk=crosswalk, matched=matches)
    diag = overlay_detect.run(ctx, log=log)
    stages.append({"stage": "diagnose", "findings": len(diag["findings"]),
                   "suppressed": len(diag["suppressed"]), "silent": len(diag["silent"]),
                   "skipped": len(diag["skipped"])})

    # ── S7/S8 market read ──
    obs = obs if obs is not None else overlay_market.WarehouseObs()
    market = overlay_market.report(mapped, fields, matches, obs, mode=mode, log=log)
    stages.append({"stage": "market", "blocks": len(market["blocks"]),
                   "withheld": len(market["withheld"])})

    # per-row finding tags — what makes Sheet 1's flagged cells possible
    tags = [[] for _ in rows]
    for f in diag["findings"]:
        for ri in f.get("rows") or []:
            if 0 <= ri < len(tags):
                tags[ri].append(f["id"])

    elapsed = round(time.time() - t0, 2)
    return {
        "ok": True,
        "run": {"at": _now(), "filename": filename, "tier": tier, "mode": mode,
                "pipeline_version": PIPELINE_VERSION,
                "registry_version": diag["registry_version"], "bands_version": diag["bands_version"],
                "seconds": elapsed},
        "parse": {"header": header, "header_row": parsed.get("header_row"),
                  "sheet": parsed.get("sheet"), "row_count": len(rows), "truncated": truncated},
        "columns": cols, "field_summary": msum, "field_index": fmap,
        "match": agg, "matches": matches,
        "cleanse": counts, "clean": clean,
        "enrich": {"columns": enrich_columns(tier), "rows": enriched},
        "diagnose": diag, "market": market,
        "tags": tags, "stages": stages,
        "boundary": boundary(cols, msum),
        "_rows": rows,
    }


def boundary(cols, msum):
    """The honest boundary, stated as a sentence and as a list. This is what makes the rest
    believable, so it is part of the output contract, not a footnote — and it reads as a sentence
    a person wrote, because a line about trust that is visibly machine-assembled undercuts itself."""
    mine = [c["name"] for c in cols if c["bucket"] == "proprietary"]
    pii = [c["name"] for c in cols if c["bucket"] == "pii"]
    parts = ["Of your %d columns: %d cleansed against our master, %d we can derive, %s"
             % (msum["columns"], msum["cleansed"], msum["derived"],
                "1 is yours." if msum["yours"] == 1 else "%d are yours." % msum["yours"])]
    if mine:
        parts.append("That one looks like your own measurement — we don't touch it."
                     if len(mine) == 1 else
                     "Those %d look like your own measurements — we don't touch them." % len(mine))
    if pii:
        parts.append("One more looks like customer data; we didn't read it." if len(pii) == 1 else
                     "%d more look like customer data; we didn't read them." % len(pii))
    return {"statement": " ".join(parts), "yours": mine, "not_read": pii}


# ── S9: the returned workbook ──────────────────────────────────────────────────────────────────

def workbook(result, master_build=None, quality=None, xwalk=None):
    """The five-sheet workbook — engineered to be forwarded internally, because that forward is
    the KPI. Sheet order is the reading order: their data, the field map, the findings, the join
    report, the trust sheet."""
    wb = xlsx_write.Workbook()
    rows = result.get("_rows") or []
    header = result["parse"]["header"]
    cols = result["columns"]
    clean = result["clean"]
    enr = result["enrich"]["rows"]
    matches = result["matches"]
    tags = result["tags"]

    # ── Sheet 1 — their data, annotated. Column order preserved, originals untouched. ──
    s1 = wb.sheet("Your data, annotated")
    clean_cols = sorted({k for c in clean for k in c})
    enr_cols = [c["column"] for c in result["enrich"]["columns"]]
    s1.header(list(header) + ["hoodie_id", "match_tier", "match_method", "match_confidence",
                              "matched_display"] + clean_cols + enr_cols + ["findings"])
    flag_idx = {c["index"] for c in cols if c["bucket"] == "pii"}
    for i, r in enumerate(rows):
        m = matches[i] if i < len(matches) else {}
        vals = list(r) + [
            m.get("hoodie_id"), m.get("match_tier"), m.get("match_method") or (m.get("why") or ""),
            m.get("match_confidence"), m.get("matched_display") or "",
        ]
        vals += [clean[i].get(k, "") for k in clean_cols]
        vals += [enr[i].get(k, "") for k in enr_cols]
        vals.append(", ".join(tags[i]) if i < len(tags) else "")
        fills = {}
        if tags[i]:
            fills[len(header) + 4] = "flag"
        for ci in flag_idx:
            fills[ci] = "note"
        for j, c in enumerate(result["enrich"]["columns"]):
            if c["locked"]:
                fills[len(header) + 5 + len(clean_cols) + j] = "locked"
        s1.row(vals, fills=fills)

    # ── Sheet 2 — the field map. The 100/20/20 summary, as a table they can audit. ──
    s2 = wb.sheet("Field map")
    s2.header(["Your column", "We read it as", "How", "Confidence", "Bucket", "Fill rate",
               "Distinct", "Note"], widths={0: 28, 1: 20, 7: 60})
    for c in cols:
        s2.row([c["name"], c.get("field") or "", c.get("label") or "",
                c.get("confidence"), c["bucket"], c.get("fill_rate"), c.get("distinct"),
                c.get("note") or ""],
               fills={4: {"mapped": "good", "pii": "note", "proprietary": None,
                          "derivable": "note"}.get(c["bucket"])} if c["bucket"] != "proprietary" else None)
    s2.row([])
    s2.row(["SUMMARY", result["boundary"]["statement"]])

    # ── Sheet 3 — findings: root cause, evidence, the arithmetic, the fix. ──
    s3 = wb.sheet("Findings")
    s3.header(["Finding", "How we know", "Severity", "Rows", "Root cause", "Fix", "Evidence"],
              widths={0: 30, 4: 50, 5: 50, 6: 70})
    for f in result["diagnose"]["findings"]:
        for ev in (f["evidence"] or [{}]):
            s3.row([f["title"], f["label"] + (" (%.2f)" % f["confidence"] if f["confidence"] else ""),
                    f["severity"], f["count"], f["root_cause"], f["fix"],
                    ev.get("arithmetic") or _kv(ev)],
                   fills={2: "flag" if f["severity"] == "blocks_match" else None})
    for s in result["diagnose"]["suppressed"]:
        s3.row([s["title"], "WITHHELD", s["severity"], s["count"], s["root_cause"], s["fix"], ""])
    if not result["diagnose"]["findings"] and not result["diagnose"]["suppressed"]:
        s3.row(["No findings", "—", "—", 0,
                "Every detector that could run on this file's fields found nothing.", "", ""])

    # ── Sheet 4 — the join report. Only blocks that cleared their bar. ──
    s4 = wb.sheet("What the join shows")
    s4.header(["Block", "What we can say", "n", "Geography", "Freshness", "Method"],
              widths={1: 60, 5: 60})
    for b in result["market"]["blocks"]:
        s4.row([b["title"], b["claim"], b["n"], b["geography"], b["freshness"], b["method"]])
        for d in (b.get("detail") or [])[:8]:
            s4.row(["", "    " + _kv(d)])
    if result["market"]["withheld"]:
        s4.row([])
        s4.row(["Not shown", "and why — a block below its bar is absent, never padded"])
        for w in result["market"]["withheld"]:
            s4.row([w["id"], w["why"]])

    # ── Sheet 5 — provenance. The trust sheet. ──
    s5 = wb.sheet("Provenance")
    s5.header(["Item", "Value"], widths={0: 34, 1: 90})
    run = result["run"]
    facts = [
        ("Run", run["at"]),
        ("File", run["filename"]),
        ("Pipeline version", run["pipeline_version"]),
        ("Detector registry version", run["registry_version"]),
        ("Category-band data version", run["bands_version"]),
        ("Master build", master_build or "see /api/overlay/provenance"),
        ("Wall clock (seconds)", run["seconds"]),
        ("Rows read", result["parse"]["row_count"]),
        ("Match — deterministic (tiers 1-3)", "%s%%" % result["match"]["deterministic_pct"]),
        ("Match — inference (tier 4)", "%s%%" % result["match"]["inference_pct"]),
        ("Match — unmatched (tier 5)", "%s%%" % result["match"]["unmatched_pct"]),
    ]
    dn = result["match"].get("distributor")
    if dn:
        facts.append(("Distributor (Tier-3 scope)",
                      "%s — %s" % (dn.get("distributor_id") or "not identified", dn.get("basis"))))
    for w in result["match"].get("why") or []:
        facts.append(("  unmatched: %s" % w["reason"], w["rows"]))
    if quality:
        facts.append(("Matcher precision / recall (measured)", _kv(quality)))
    if xwalk:
        facts.append(("Tier-3 crosswalk coverage", _kv(xwalk)))
    for t, st in (result["match"].get("master_status") or {}).items():
        facts.append(("Master table: %s" % t,
                      "available" if st.get("available") else "UNAVAILABLE — %s" % st.get("why", "?")))
    facts.append(("Detectors withheld (unmeasured or below bar)",
                  ", ".join(s["id"] for s in result["diagnose"]["silent"]) or "none"))
    facts.append(("Detectors skipped (fields absent)",
                  ", ".join(s["id"] for s in result["diagnose"]["skipped"]) or "none"))
    facts.append(("The honest boundary", result["boundary"]["statement"]))
    if result["boundary"]["yours"]:
        facts.append(("Your own columns (untouched)", ", ".join(result["boundary"]["yours"])))
    if result["boundary"]["not_read"]:
        facts.append(("Columns we did not read", ", ".join(result["boundary"]["not_read"])))
    if run["tier"] == "free":
        facts.append(("Retention", "This file was processed in memory and deleted with the "
                                   "response. No row data was stored."))
    for k, v in facts:
        s5.row([k, v])
    return wb.tobytes()


def _kv(d):
    if not isinstance(d, dict):
        return str(d)
    return "; ".join("%s=%s" % (k, v) for k, v in d.items() if not str(k).startswith("_"))


def _selftest():
    csv = ("ITM_CD,ITM_DSC_2,BRAND,UPC,SIZE,ALC%,FLP,RNDC_VELOCITY_IDX,Buyer Email\n"
           "100234,TITOS HANDMADE TEXAS VODKA,TITO HANDMADE,619947000020,750 ML,40,$21.99,0.884,a@b.com\n"
           "100235,TITO HANDMADE TEXAS VODKA,TITOS HANDMADE,36000291452,1.75 L,40,$34.99,1.220,c@d.com\n"
           "100236,JACK DANIELS OLD NO 7,JACK DANIELS,082184090565,750 ML,80,$27.49,0.410,e@f.com\n"
           "100237,KENDALL JACKSON CHARDONNAY 2019,KENDALL JACKSON,000000000000,750 ML,13.5,$14.99,2.010,g@h.com\n"
           "100238,HERRADURA AÃ±EJO,HERRADURA,081753817107,750 ML,40,$59.99,0.150,i@j.com\n")
    master = overlay_match.MemoryMaster(
        items={619947000020: {"brand": "Tito's Handmade", "product_name": "Texas Vodka",
                              "category": "Vodka", "size_ml": 750},
               36000291452: {"brand": "Acme", "product_name": "Acme Gin", "category": "Gin",
                             "size_ml": 1750},
               82184090565: {"brand": "Jack Daniel's", "product_name": "Old No. 7",
                             "category": "Whiskey", "size_ml": 750}})
    res = run("book.csv", csv.encode("utf-8"), tier="free",
              master=master, obs=overlay_market.MemoryObs())
    assert res.get("ok"), res

    # S2 — the three-way count, and the honest boundary sentence
    fs = res["field_summary"]
    assert fs["cleansed"] >= 6 and fs["yours"] >= 1 and fs["not_read"] == 1, fs
    assert "we don't touch it" in res["boundary"]["statement"], res["boundary"]
    assert "didn't read it" in res["boundary"]["statement"], res["boundary"]
    assert res["boundary"]["not_read"] == ["Buyer Email"], res["boundary"]

    # S3 — tiers reported separately; the zero-strip row lands on tier 2, not tier 1
    m = res["match"]
    assert m["tiers"]["1"] == 2 and m["tiers"]["2"] == 1, m["tiers"]
    assert m["deterministic"] == 3 and m["unmatched"] == 2, m
    assert res["matches"][1]["match_method"] == "upc_zero_strip_heal", res["matches"][1]
    # the placeholder barcode never becomes an identity, and the reason is stated
    assert res["matches"][3]["hoodie_id"] is None and "placeholder" in res["matches"][3]["why"]

    # S4 — originals untouched, clean values alongside
    assert res["_rows"][0][3] == "619947000020"                     # their value, verbatim
    assert res["clean"][0]["gtin14"] == "00619947000020", res["clean"][0]
    assert res["clean"][1]["clean_upc"] == "036000291452", res["clean"][1]   # the heal, in clean_*
    assert res["clean"][0]["clean_size_ml"] == 750 and res["clean"][1]["clean_size_ml"] == 1750
    # brand canonicalized within the file: two spellings collapse to one display
    assert res["clean"][0]["clean_brand"] == res["clean"][1]["clean_brand"], res["clean"][:2]
    # the brand is removed from the head of the product name, not from their column
    assert "Titos" not in res["clean"][0]["clean_product_name"], res["clean"][0]

    # S5 — free tier: five visible enrichment columns, the rest present but withheld
    ec = res["enrich"]["columns"]
    assert sum(1 for c in ec if not c["locked"]) == 5, ec
    assert res["enrich"]["rows"][0]["hoodie_category"] == "Vodka"
    assert res["enrich"]["rows"][0]["hoodie_size_ml"] == 750, res["enrich"]["rows"][0]
    # no enrichment column may share a name with a cleanse column — that is a duplicate header
    assert not ({c["column"] for c in ec} & {k for row in res["clean"] for k in row}), ec
    assert res["enrich"]["rows"][0]["hoodie_varietal"] == LOCKED_VALUE, res["enrich"]["rows"][0]

    # S6 — the findings that should fire on this file, do
    got = {f["id"] for f in res["diagnose"]["findings"]}
    assert {"zero_strip", "placeholder_upc", "abv_proof_double", "mojibake"} <= got, got
    proof = next(f for f in res["diagnose"]["findings"] if f["id"] == "abv_proof_double")
    assert proof["evidence"][0]["corrected_abv"] == 40.0, proof

    # S7 — only 3 of 5 rows resolved, which is under the report's own floor: the whole market
    # section withholds, in one sentence, rather than rendering a block built on three rows.
    assert res["market"]["blocks"] == [], res["market"]["blocks"]
    assert "isn't enough here" in res["market"]["withheld"][0]["why"], res["market"]["withheld"]

    # ── the workbook: five sheets, opens as valid xlsx, and never contains the PII column values ──
    data = workbook(res, master_build="2026-08-02", quality={"precision": 0.99, "recall": 0.94})
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        wbx = z.read("xl/workbook.xml").decode()
        for name in ("Your data, annotated", "Field map", "Findings", "What the join shows",
                     "Provenance"):
            assert 'name="%s"' % name in wbx, name
        s5 = z.read("xl/worksheets/sheet5.xml").decode()
        assert "Detector registry version" in s5 and "No row data was stored" in s5
        assert "precision=0.99" in s5, "measured P/R must be cited, not asserted"
        s3 = z.read("xl/worksheets/sheet3.xml").decode()
        assert "Proof was written into the ABV field" in s3, "root cause, not symptom"
        assert "÷ 2" in s3, "the arithmetic must be shown"
    # Sheet 1 DOES carry their original values (it's their file back) including the PII column —
    # the guarantee is that we never READ it into a stage, and the field map says so.
    pii_col = next(c for c in res["columns"] if c["bucket"] == "pii")
    assert pii_col["sample"] == [] and pii_col["field"] is None

    # ── caps: an oversized file is a qualifying signal, not an error ──
    big = run("big.csv", b"x" * (CAPS["free"]["bytes"] + 1), master=master,
              obs=overlay_market.MemoryObs())
    assert big.get("engagement") and "engagement" in big["detail"], big

    # ── a file with no identifier at all still runs, and says what it cost ──
    plain = run("p.csv", b"Brand,Product,Size\nTito's Handmade,Texas Vodka,750 ml\n",
                master=master, obs=overlay_market.MemoryObs())
    assert plain["ok"] and plain["match"]["capped_at_inference"] is True
    assert "capped at INFERENCE" in plain["match"]["capped_note"]

    # ── a file that isn't a table at all fails with a sentence, not a stack trace ──
    junk = run("j.csv", b"", master=master, obs=overlay_market.MemoryObs())
    assert junk.get("error") == "parse-failed" and "column names" in junk["detail"], junk

    print("overlay.py self-test: OK (%d rows, %s%% deterministic, %d findings, %d-byte workbook)"
          % (res["parse"]["row_count"], m["deterministic_pct"],
             len(res["diagnose"]["findings"]), len(data)))


if __name__ == "__main__":
    _selftest()
