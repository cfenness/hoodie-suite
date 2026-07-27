"""menu_ingest.py — parse a DISTRIBUTOR WHOLESALE MENU file (xlsx/csv) into normalized order lines.

Distributors (Curaleaf NY is the reference — the shape they email weekly) send their menus as
spreadsheets: a title row, brand section headers, then product rows with THC%, batch, expiry, bin
size, on-hand units, wholesale Base $ and MSRP. Every distributor formats differently, so the parser
is HEURISTIC, not positional: it finds the header row by column-name synonyms, maps columns onto a
canonical line shape, and treats rows without a price/batch as brand-section context. Nothing is
guessed — a field lands only if the sheet states it; the untouched cells ride along as raw_json.

The deterministic parser is stdlib only (xlsx is just zipped XML — no openpyxl, per the scraper
standard) and pure/offline. `parse_smart()` wraps it with a Claude fallback that fires ONLY when the
deterministic pass fails or is low-confidence (few lines / most lines unpriced) — so an odd layout still
gets extracted, without adding per-menu LLM cost to menus that already parse cleanly. Lands
`distributor_menu_items` (accumulated, keyed by menu+line) — the combined catalog behind
apps/ordering.html, where a retailer builds one order across every distributor and it fans back out.

    python menu_ingest.py "7.20.26 - Curaleaf NY Menu.xlsx" --distributor "Curaleaf NY"
"""
import argparse
import csv as _csv
import datetime
import hashlib
import io
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Canonical order-line fields ← the column-name synonyms distributor sheets use. First match wins;
# specific → general. Everything unmapped is preserved per-row in raw_json.
_COLS = [
    ("product_name", ("product name", "product", "item name", "item", "description", "sku name", "name")),
    ("thc", ("thc", "thc %", "thc%", "total thc", "potency")),
    ("cbd", ("cbd", "cbd %", "cbd%")),
    ("exp_date", ("exp date", "expiration", "expiration date", "exp", "expiry", "best by")),
    ("batch", ("batch #", "batch", "batch number", "lot #", "lot", "package id", "metrc id", "metrc")),
    ("bin_size", ("bin size", "case size", "case qty", "units per case", "pack size", "case")),
    ("units", ("total units", "units", "qty available", "available", "on hand", "inventory", "qty on hand", "avail")),
    ("base_price", ("base $", "base", "wholesale $", "wholesale price", "wholesale", "unit price",
                    "price", "cost", "unit cost", "price per unit")),
    ("msrp", ("msrp $", "msrp", "retail $", "retail price", "suggested retail", "srp")),
    ("size", ("size", "weight", "net weight", "unit size")),
    ("category", ("category", "type", "product type", "form")),
    ("strain", ("strain", "strain name", "cultivar")),
]
_SKIP_ROWS = re.compile(r"^\s*(totals?|grand total|subtotal|notes?)\s*$", re.I)

# Category from the product name when the sheet has no category column — the vocabulary is small
# and stable across cannabis menus.
_CATS = [("Preroll", r"pre\s?-?roll|blunt"), ("Vape", r"cartridge|cart\b|vape|briq|cliq|pod|disposable|battery|charger"),
         ("Flower", r"flower|prepack|eighth|quarter\b|\b3\.5g\b|\b7g\b|\b14g\b|\b28g\b|smalls|popcorn"),
         ("Edible", r"gumm|edible|chew|jellies|chocolate|beverage|drink|mints?\b"),
         ("Concentrate", r"concentrate|rosin|resin|badder|budder|shatter|wax\b|diamonds?\b|rso|dab")]


# ── cell grid readers ────────────────────────────────────────────────────────────────────────────
def _xlsx_rows(data):
    """Yield (sheet_name, rows) per worksheet; each row is a list of cell strings (A..max, gaps='')."""
    z = zipfile.ZipFile(io.BytesIO(data))
    try:
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in si.iter(_M + "t")) for si in sst]
    except KeyError:
        strings = []
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    names = [s.get("name") for s in wb.iter(_M + "sheet")]
    sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    for i, sh in enumerate(sheets):
        rows = []
        for row in ET.fromstring(z.read(sh)).iter(_M + "row"):
            cells = {}
            for c in row.iter(_M + "c"):
                v = c.find(_M + "v")
                if v is None:
                    v = c.find(_M + "is")            # inlineStr
                val = "".join(v.itertext()) if v is not None else ""
                if c.get("t") == "s" and val != "":
                    try:
                        val = strings[int(val)]
                    except Exception:
                        pass
                col = re.sub(r"\d", "", c.get("r") or "")
                cells[_col_idx(col)] = str(val)
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(j, "").strip() for j in range(width)])
            else:
                rows.append([])
        yield (names[i] if i < len(names) else sh), rows


def _col_idx(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _csv_rows(data):
    text = data.decode("utf-8-sig", "replace")
    return [[c.strip() for c in r] for r in _csv.reader(io.StringIO(text))]


# ── normalization ────────────────────────────────────────────────────────────────────────────────
def _excel_date(v):
    """Excel serial (days since 1899-12-30) or a plain date string → ISO yyyy-mm-dd (else as-is)."""
    s = str(v or "").strip()
    if re.fullmatch(r"\d{4,6}", s):
        try:
            return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(s))).isoformat()
        except Exception:
            return s
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", s)
    if m:
        mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        yy += 2000 if yy < 100 else 0
        try:
            return datetime.date(yy, mm, dd).isoformat()
        except Exception:
            return s
    return s


def _num(v):
    try:
        n = float(re.sub(r"[^0-9.\-]", "", str(v or "")) or "x")
        return n
    except Exception:
        return None


def _thc(v):
    """0.3235 → 32.35 (fraction-formatted %); 32.35 stays. Returns a % number or None."""
    n = _num(v)
    if n is None:
        return None
    return round(n * 100, 2) if 0 < n <= 1 else round(n, 2)


def _category(name):
    low = (name or "").lower()
    for cat, pat in _CATS:
        if re.search(pat, low):
            return cat
    return ""


def _size(name):
    m = re.search(r"\b([0-9][0-9.]*\s?(?:m?g|oz|ml))\b", (name or "").lower())
    return m.group(1).replace(" ", "") if m else ""


# Product-FORM words — their presence marks a header as a product-family sub-section (not a brand) and
# lets us read category/form off a header row.
_FORM_WORDS = re.compile(r"\b(packs?|pk|pre-?rolls?|prerolls?|blunts?|cartridges?|carts?|vapes?|briq2?|"
                         r"cliq|pods?|disposables?|batteries|battery|chargers?|gummies|gummy|edibles?|"
                         r"jellies|chocolates?|mints?|flower|prepack|smalls|popcorn|eighths?|quarters?|"
                         r"diamonds?|rosin|resin|badder|budder|shatter|wax|rso|capsules?|tinctures?|"
                         r"beverages?|drinks?|shots?|nano\s?bites?|squeeze|assortment|series|collection)\b", re.I)
_VARIANT_LEAD = re.compile(r"^\s*(indica|sativa|hybrid|cbd|thca?|1\s*:\s*1|[:\-–])", re.I)


def _brand_seed(text):
    """Leading brand-ish words of a header, up to the first size/form token (e.g.
    'Grassroots Diamond Preroll 5 Pack 2g' → 'Grassroots Diamond')."""
    out = []
    for w in (text or "").split():
        if _FORM_WORDS.match(w) or re.match(r"^[0-9]", w) or w in ("-", "–", "|", ":"):
            break
        out.append(w)
    return " ".join(out).strip(" -–|:") or (text or "").strip(" -–|:")


def _section_info(text):
    """Classify a header row. A header naming a size or a product form (3.5g / Preroll / Cartridge…) is a
    SUB-section (product family) that carries data; a bare name is a brand header. Returns
    {kind: 'brand'|'sub', brand_part, size, category}."""
    size = _size(text)
    cat = _category(text)
    kind = "sub" if (size or cat or _FORM_WORDS.search(text or "")) else "brand"
    return {"kind": kind, "brand_part": _brand_seed(text), "size": size, "category": cat}


def _is_terse(name, brand, section, sec_size, sec_cat):
    """True when a child row's text is just a variant/strain and the real product lives in the section
    header above it — so we compose the full product_name from the header + the variant."""
    if not section:
        return False
    low = (name or "").lower()
    if brand and brand.lower() in low:
        return False                                  # already carries the brand → it's a full name
    if _VARIANT_LEAD.match(name or ""):
        return True                                   # 'Indica : Triple Stack', ': Sugar Kushions', …
    if not _size(name) and (sec_size or sec_cat):
        return True                                   # no size of its own, sitting under a sized family
    return False


def _find_header(rows):
    """(row_index, {col_index: canon_field}) for the first row where ≥3 cells match column synonyms."""
    for i, row in enumerate(rows[:30]):
        hits = {}
        for j, cell in enumerate(row):
            key = re.sub(r"\s+", " ", (cell or "").strip().lower()).rstrip(":")
            if not key:
                continue
            for canon, syns in _COLS:
                if key in syns and canon not in hits.values():
                    hits[j] = canon
                    break
        if len(hits) >= 3 and "product_name" in hits.values():
            return i, hits
    return -1, {}


def parse(data, filename="menu.xlsx", distributor="", log=print):
    """Parse a menu file (bytes) → {distributor, menu_date, license, items[], warnings[]}.
    Rows without a price AND batch but with a product cell become brand-section context."""
    fn = os.path.basename(filename)
    if fn.lower().endswith((".xlsx", ".xlsm")):
        sheet_iter = list(_xlsx_rows(data))
    else:
        sheet_iter = [("csv", _csv_rows(data))]

    # menu-level metadata from the filename + the pre-header title rows
    menu_date = ""
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", fn)
    if m:
        mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        yy += 2000 if yy < 100 else 0
        try:
            menu_date = datetime.date(yy, mm, dd).isoformat()
        except Exception:
            pass

    best, warnings = None, []
    for sheet_name, rows in sheet_iter:
        hi, colmap = _find_header(rows)
        if hi < 0:
            continue
        license_no, title = "", ""
        for r in rows[:hi]:                          # title rows above the header
            for cell in r:
                if re.search(r"\b[A-Z]{2,4}-[A-Z]{3,6}-\d{2}-\d{4,}", cell):
                    license_no = re.search(r"\b[A-Z]{2,4}-[A-Z]{3,6}-\d{2,}-?\d*", cell).group(0)
                elif len(cell) > 8 and not title:
                    title = cell
        if not distributor and title:
            # "July 20th 2026 - Curaleaf NY Menu" → "Curaleaf NY"
            t = re.sub(r"^\s*\w+ \d{1,2}(st|nd|rd|th)?,? \d{4}\s*[-–]\s*", "", title)
            distributor = re.sub(r"\s*menu\s*$", "", t, flags=re.I).strip()

        # Section context carried DOWN to child rows. `sec_size`/`sec_cat` are pulled off a sub-header so
        # a size/form that lives ONLY in the header (e.g. 'Dark Heart - 3.5g') still reaches its items.
        items, brand, section, sec_size, sec_cat = [], "", "", "", ""
        for ri, row in enumerate(rows[hi + 1:]):
            get = lambda canon: next((row[j] for j, c in colmap.items() if c == canon and j < len(row)), "")
            name = get("product_name")
            if not name or _SKIP_ROWS.match(name):
                if _SKIP_ROWS.match(name or ""):
                    break                            # TOTALS = end of the item block
                continue
            base, batch, units = _num(get("base_price")), get("batch"), get("units")
            msrp, thc = _num(get("msrp")), _thc(get("thc"))
            # A row with NO price / batch / units / thc / msrp is a section or sub-header, not a sellable
            # line — regardless of how many stray cells it has (the old len(filled)<=2 test missed some).
            if not (base is not None or batch or _num(units) is not None or msrp is not None or thc is not None):
                si = _section_info(name)
                if si["kind"] == "brand":
                    brand, section, sec_size, sec_cat = si["brand_part"] or name.strip(), "", "", ""
                else:
                    section, sec_size, sec_cat = name.strip(), si["size"], si["category"]
                    if not brand and si["brand_part"]:
                        brand = si["brand_part"]     # a family header can seed the brand when none's set yet
                continue
            # Compose a terse child ('Indica : Triple Stack') into the full product using its section
            # header — that header is often the ONLY place the product/size/form is named.
            terse = _is_terse(name, brand, section, sec_size, sec_cat)
            variant = name.strip().lstrip(":-–| ").strip() if terse else ""   # 'Indica : X', ': X' → clean tail
            full_name = ((section.rstrip(" -–|:") + " - " + variant).strip(" -–|:") if (terse and section)
                         else name.strip())
            raw = {str(j): row[j] for j in range(len(row)) if row[j] and j not in colmap}
            it = {"product_name": full_name, "brand": brand, "section": section,
                  "category": get("category") or _category(name) or sec_cat,
                  "size": get("size") or _size(name) or sec_size,
                  "strain": get("strain") or variant,
                  "thc_pct": thc, "cbd_pct": _thc(get("cbd")),
                  "batch": batch, "exp_date": _excel_date(get("exp_date")),
                  "bin_size": _num(get("bin_size")), "units_available": _num(units),
                  "base_price": base, "msrp": msrp,
                  "raw_json": json.dumps(raw, separators=(",", ":")) if raw else ""}
            items.append(it)
        if items and (best is None or len(items) > len(best[1])):
            best = (sheet_name, items, license_no, title)

    if not best:
        return {"ok": False, "error": "No parseable menu found — couldn't locate a header row with a "
                "product column plus ≥2 known columns (THC / batch / price / units…).", "items": []}

    sheet_name, items, license_no, title = best
    priced = sum(1 for i in items if i["base_price"] is not None)
    if priced < len(items) * 0.5:
        warnings.append("Only %d/%d lines carry a base price — column mapping may be off for this "
                        "distributor's layout." % (priced, len(items)))
    menu_id = "%s-%s" % (re.sub(r"[^a-z0-9]+", "-", (distributor or "menu").lower()).strip("-"),
                         menu_date or hashlib.sha1(data).hexdigest()[:8])
    for n, it in enumerate(items):
        it.update(menu_id=menu_id, distributor=distributor or "Unknown", menu_date=menu_date,
                  license=license_no, source_file=fn, line=n, ts=int(time.time()))
    return {"ok": True, "menu_id": menu_id, "distributor": distributor or "Unknown",
            "menu_date": menu_date, "license": license_no, "title": title, "sheet": sheet_name,
            "items": items, "warnings": warnings}


# ── Claude fallback — the LLM safety net for menu layouts the heuristic can't map ──────────────────
# The deterministic parser above covers the common shapes cheaply and offline. But "every distributor
# formats differently", so when it FAILS (no header row found) or comes back LOW-CONFIDENCE (few lines,
# or most lines missing a price), we hand the raw grid to Claude to extract the same normalized lines —
# the "Claude where unsure" pattern (cf. menu_site / label_vision), gated on ANTHROPIC_API_KEY and only
# on the low-confidence path so it never adds per-menu cost to a menu that already parsed cleanly.
_MENU_MODEL = os.environ.get("MENU_LLM_MODEL", "claude-opus-4-8")
_LINE_FIELDS = {
    "product_name": "the FULL product name — if the row itself is only a strain/variant, compose it with "
                    "the brand/size/form from the section header above it (that header is often the only "
                    "place the product is named)",
    "brand": "brand / producer", "category": "Flower / Vape / Preroll / Edible / Concentrate if inferable",
    "size": "unit size e.g. 3.5g, 1g, 750ml", "strain": "strain / cultivar if present",
    "thc_pct": "THC as a percent number (e.g. 28.5), null if absent",
    "cbd_pct": "CBD percent number, null if absent", "batch": "batch / lot / METRC id",
    "exp_date": "expiration date as printed", "bin_size": "units per case (number)",
    "units_available": "quantity available (number)", "base_price": "WHOLESALE unit price (number)",
    "msrp": "suggested retail price (number)"}
_MENU_TOOL = {
    "name": "menu_lines",
    "description": "The sellable product lines from a distributor wholesale menu. One entry per ORDERABLE "
                   "product; skip section/brand headers, totals and blank rows, but carry any brand/size/"
                   "form named only in a header down onto the rows beneath it. Report only what the sheet "
                   "states — null for anything absent, never guess.",
    "input_schema": {"type": "object", "properties": {
        "distributor": {"type": ["string", "null"], "description": "distributor / company name if stated"},
        "license": {"type": ["string", "null"], "description": "license number if present"},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {k: {"type": (["number", "null"] if k.endswith(("_pct", "_size", "_available"))
                                        or k in ("base_price", "msrp") else ["string", "null"]),
                               "description": v} for k, v in _LINE_FIELDS.items()},
            "required": ["product_name"]}}},
        "required": ["items"]}}


def _grid_text(rows, cap=400):
    """Render the sheet as pipe-delimited rows for the model — compact, structure-preserving."""
    out = []
    for r in rows[:cap]:
        if any((c or "").strip() for c in r):
            out.append(" | ".join((c or "").strip() for c in r).rstrip(" |"))
    return "\n".join(out)


def _llm_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()) and os.environ.get("MENU_NO_LLM") != "1"


def claude_parse(data, filename="menu.xlsx", distributor="", log=print):
    """Extract normalized menu lines with Claude (forced tool call). Returns the same
    {ok, items, distributor, license, ...} shape as parse(), with method='claude'. Raises on API error."""
    fn = os.path.basename(filename)
    if fn.lower().endswith((".xlsx", ".xlsm")):
        sheets = list(_xlsx_rows(data))
    else:
        sheets = [("csv", _csv_rows(data))]
    grid = max((_grid_text(rows) for _, rows in sheets), key=len, default="")
    if not grid.strip():
        return {"ok": False, "error": "empty sheet", "items": []}
    import anthropic
    msg = anthropic.Anthropic().messages.create(
        model=_MENU_MODEL, max_tokens=8000, tools=[_MENU_TOOL],
        tool_choice={"type": "tool", "name": "menu_lines"},
        messages=[{"role": "user", "content":
                   "Extract every orderable line from this distributor wholesale menu via the `menu_lines` "
                   "tool. Carry brand/size/form from section headers down onto the rows beneath them.\n\n"
                   + grid}])
    payload = next((b.input for b in msg.content if b.type == "tool_use"), None) or {}
    raw_items = payload.get("items") or []
    dist = distributor or (payload.get("distributor") or "").strip()
    items = []
    for it in raw_items:
        name = (it.get("product_name") or "").strip()
        if not name:
            continue
        items.append({"product_name": name, "brand": (it.get("brand") or "").strip(), "section": "",
                      "category": (it.get("category") or "").strip() or _category(name),
                      "size": (it.get("size") or "").strip() or _size(name),
                      "strain": (it.get("strain") or "").strip(),
                      "thc_pct": _thc(it.get("thc_pct")), "cbd_pct": _thc(it.get("cbd_pct")),
                      "batch": (it.get("batch") or "").strip(), "exp_date": _excel_date(it.get("exp_date")),
                      "bin_size": _num(it.get("bin_size")), "units_available": _num(it.get("units_available")),
                      "base_price": _num(it.get("base_price")), "msrp": _num(it.get("msrp")), "raw_json": ""})
    log("[menu_ingest] claude fallback extracted %d lines" % len(items))
    return _finalize(items, data, fn, dist, (payload.get("license") or "").strip(),
                     title="", sheet="claude", warnings=[], method="claude")


def _menu_date(fn):
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", fn)
    if not m:
        return ""
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    yy += 2000 if yy < 100 else 0
    try:
        return datetime.date(yy, mm, dd).isoformat()
    except Exception:
        return ""


def _finalize(items, data, fn, distributor, license_no, title, sheet, warnings, method):
    """Stamp menu-level metadata onto items and build the result dict — shared by parse() and claude_parse()."""
    menu_date = _menu_date(fn)
    menu_id = "%s-%s" % (re.sub(r"[^a-z0-9]+", "-", (distributor or "menu").lower()).strip("-"),
                         menu_date or hashlib.sha1(data).hexdigest()[:8])
    for n, it in enumerate(items):
        it.update(menu_id=menu_id, distributor=distributor or "Unknown", menu_date=menu_date,
                  license=license_no, source_file=fn, line=n, ts=int(time.time()))
    return {"ok": bool(items), "menu_id": menu_id, "distributor": distributor or "Unknown",
            "menu_date": menu_date, "license": license_no, "title": title, "sheet": sheet,
            "items": items, "warnings": warnings, "method": method}


def parse_smart(data, filename="menu.xlsx", distributor="", use_llm=True, log=print):
    """Deterministic parse first; fall back to Claude only when that fails or looks low-confidence
    (few lines, or most lines missing a price). Returns the same shape + a `method` field."""
    det = parse(data, filename, distributor, log)
    det["method"] = "deterministic"
    items = det.get("items") or []
    priced = sum(1 for i in items if i.get("base_price") is not None)
    low_conf = (not det.get("ok")) or len(items) < 3 or (items and priced < len(items) * 0.5)
    if not (use_llm and low_conf and _llm_available()):
        return det
    log("[menu_ingest] deterministic parse low-confidence (%d lines, %d priced) — trying Claude" %
        (len(items), priced))
    try:
        llm = claude_parse(data, filename, distributor, log)
    except Exception as e:
        det.setdefault("warnings", []).append("Claude fallback failed: %s" % str(e)[:120])
        return det
    # keep whichever recovered more usable (priced) lines
    llm_priced = sum(1 for i in (llm.get("items") or []) if i.get("base_price") is not None)
    if llm.get("ok") and llm_priced >= priced and len(llm["items"]) >= len(items):
        llm.setdefault("warnings", []).append("Parsed by Claude fallback (deterministic parse was low-confidence).")
        return llm
    return det


def land(parsed):
    """Persist a parsed menu → distributor_menu_items. A re-upload of the same menu_id replaces its
    lines (accumulate keyed by menu+line), so a corrected menu simply supersedes."""
    import warehouse
    items = parsed.get("items") or []
    if items:
        warehouse.write_accumulate("distributor_menu_items", items,
                                   key=lambda r: (r["menu_id"], r["line"]))
    return len(items)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Parse a distributor wholesale menu (xlsx/csv).")
    ap.add_argument("path")
    ap.add_argument("--distributor", default="")
    ap.add_argument("--land", action="store_true", help="persist to distributor_menu_items")
    ap.add_argument("--no-llm", action="store_true", help="deterministic only (no Claude fallback)")
    a = ap.parse_args(argv)
    p = parse_smart(open(a.path, "rb").read(), filename=a.path, distributor=a.distributor, use_llm=not a.no_llm)
    if not p.get("ok"):
        print("ERROR:", p.get("error")); return
    print("distributor: %s  ·  date: %s  ·  license: %s  ·  %d lines (via %s)"
          % (p["distributor"], p["menu_date"], p["license"], len(p["items"]), p.get("method", "?")))
    for it in p["items"][:10]:
        print("  • %-52s %-14s thc %-6s $%-7s msrp $%-6s units %s"
              % (it["product_name"][:52], (it["brand"] or "")[:14], it["thc_pct"],
                 it["base_price"], it["msrp"], it["units_available"]))
    for w in p["warnings"]:
        print("WARN:", w)
    if a.land:
        print("landed %d -> distributor_menu_items" % land(p))


if __name__ == "__main__":
    main()
