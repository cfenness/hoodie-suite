#!/usr/bin/env python3
"""
ttb_cola_scraper.py
===================
Scrapes the TTB COLA Public Registry (Certificate of Label Approval) into a
clean dataset for the Unifyd MDM item master.

The registry (https://www.ttbonline.gov/colasonline) is a legacy Struts app with
no bulk endpoint. Records are reached through a search form, so this:
  1. searches by COMPLETED-DATE window, chunked into small ranges to stay under
     the registry's per-search result cap (so you don't silently miss records),
  2. parses each result page (header-aware, positional fallback) and follows the
     real "Next" pagination link,
  3. optionally opens each COLA detail page to enrich applicant / fanciful name /
     net contents / approval date / status,
  4. optionally OCRs the label image for the UPC (the bridge to scan & delivery),
  5. checkpoints so you can stop/resume, and emits both raw CSV and the
     dashboard-ready datasets.js + runs.json.

Dependencies:  pip install requests beautifulsoup4
OCR (optional): your existing ttb_cola_labels.extract_upc_from_label

Examples
--------
  # last 7 days, fast (summary fields only)
  python ttb_cola_scraper.py --from 06/18/2026 --to 06/25/2026

  # a full month, enriched + UPC OCR, 1 day per search chunk
  python ttb_cola_scraper.py --from 05/01/2026 --to 05/31/2026 --detail --ocr --chunk-days 1

  # dump the class/type + origin code lists the registry uses
  python ttb_cola_scraper.py --list-codes
"""
import argparse, csv, io, json, os, random, re, string, sys, time, datetime
from collections import Counter

BASE = "https://www.ttbonline.gov/colasonline"
SEARCH_PAGE   = f"{BASE}/publicSearchColasBasic.do"
SEARCH_ADV    = f"{BASE}/publicSearchColasAdvanced.do"
SEARCH_PROC   = f"{BASE}/publicSearchColasBasicProcess.do"
DETAIL_URL    = f"{BASE}/publicFormDisplay.do"
ATTACH_URL    = f"{BASE}/publicViewAttachment.do"

COLA_HEADER = ["TTB ID","Permit Number","Serial Number","Brand Name","Fanciful Name",
               "Class/Type","Origin","Applicant","Status","Completed Date","Approval Date",
               "Net Contents","UPC"]

# ---------------------------------------------------------------- session
def make_session():
    import requests
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:
        from requests.packages.urllib3.util.retry import Retry
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; UnifydMDM-COLA/1.0; +data-pipeline)",
        "Accept": "text/html,application/xhtml+xml",
    })
    retry = Retry(total=4, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

def soupify(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")

# ---------------------------------------------------------------- code lists
def list_codes(s):
    """Scrape class/type and origin option codes from the advanced search form."""
    html = s.get(SEARCH_ADV, timeout=60).text
    soup = soupify(html)
    out = {}
    for sel in soup.find_all("select"):
        name = sel.get("name", "")
        if not any(k in name.lower() for k in ("class", "origin", "type")):
            continue
        opts = [(o.get("value", "").strip(), o.get_text(strip=True))
                for o in sel.find_all("option") if o.get("value", "").strip()]
        if opts:
            out[name] = opts
    return out

# ---------------------------------------------------------------- search
def daterange_chunks(d_from, d_to, chunk_days):
    cur = d_from
    while cur <= d_to:
        end = min(cur + datetime.timedelta(days=chunk_days - 1), d_to)
        yield cur, end
        cur = end + datetime.timedelta(days=1)

def search_payload(frm, to, args):
    p = {
        "action": "search",
        "searchCriteria.dateCompletedFrom": frm.strftime("%m/%d/%Y"),
        "searchCriteria.dateCompletedTo":   to.strftime("%m/%d/%Y"),
        "searchCriteria.dateCompletedSearchType": "0",   # between
    }
    if args.product:
        p["searchCriteria.productName"] = args.product
        p["searchCriteria.productNameSearchType"] = "C"  # contains
    if args.origin:
        p["searchCriteria.originCode"] = args.origin
    if args.class_from:
        p["searchCriteria.classTypeFrom"] = args.class_from
    if args.class_to:
        p["searchCriteria.classTypeTo"] = args.class_to
    return p

def find_results_table(soup):
    """Pick the table that holds COLA rows — the INNERMOST table directly containing a
    result link (carrying ttbid=). Content-based, so it survives URL/path changes, and
    innermost so we don't grab an outer layout wrapper (which throws off the header row)."""
    a = soup.find("a", href=re.compile(r"ttbid=", re.I))
    return a.find_parent("table") if a else None

def header_map(table):
    """Map known column names -> index, from a header row if present."""
    head = table.find("tr")
    if not head:
        return {}
    cells = [c.get_text(" ", strip=True).lower() for c in head.find_all(["th", "td"])]
    idx = {}
    for i, c in enumerate(cells):
        if "ttb" in c and "id" in c:        idx["ttbid"] = i
        elif "permit" in c or "plant" in c: idx["permit"] = i
        elif "serial" in c:                 idx["serial"] = i
        elif "complet" in c:                idx["completed"] = i
        elif "brand" in c:                  idx["brand"] = i
        elif "fanciful" in c:               idx["fanciful"] = i
        elif "origin" in c:                 idx["origin"] = i
        elif "class" in c or "type" in c:   idx["classtype"] = i
    return idx

# Columns parse_results reads positionally; if the page header doesn't name one of
# these, we fall back to a fixed position AND flag it (drift) so a layout change can't
# silently produce wrong data. (TTB ID comes from the row link, not a column.)
EXPECTED_COLS = ["permit", "serial", "completed", "brand", "origin", "classtype"]

def parse_results(html):
    soup = soupify(html)
    table = find_results_table(soup)
    if not table:
        return [], None, {"table": False, "header": False, "rows": 0, "matched": [], "fallback": EXPECTED_COLS}
    hm = header_map(table)
    recs = []
    # Match on the stable identifier (ttbid=) rather than a specific page name, so a
    # URL path change (e.g. publicFormDisplay.do -> viewColaDetails.do) doesn't break us.
    for a in table.find_all("a", href=re.compile(r"ttbid=", re.I)):
        ttbid = a.get_text(strip=True) or re.search(r"ttbid=(\w+)", a["href"], re.I).group(1)
        tr = a.find_parent("tr")
        tds = tr.find_all("td") if tr else []
        cells = [td.get_text(" ", strip=True) for td in tds]
        def col(key, pos):
            i = hm.get(key, pos)
            return cells[i] if 0 <= i < len(cells) else ""
        rec = {h: "" for h in COLA_HEADER}
        rec.update({
            "TTB ID": ttbid,
            "Permit Number": col("permit", 1),
            "Serial Number": col("serial", 2),
            "Completed Date": col("completed", 3),
            "Brand Name": col("brand", 4),
            "Origin": col("origin", 5),
            "Class/Type": col("classtype", 6),
        })
        recs.append(rec)
    # find next-page link (literal href) if present
    nxt = soup.find("a", string=re.compile(r"next", re.I)) \
          or soup.find("a", href=re.compile(r"(action=page|pageNumber=)", re.I))
    next_href = nxt["href"] if nxt and nxt.get("href") else None
    if next_href and next_href.startswith("/"):
        next_href = "https://www.ttbonline.gov" + next_href
    elif next_href and not next_href.startswith("http"):
        next_href = f"{BASE}/{next_href.lstrip('/')}"
    filled = sum(1 for r in recs if any(str(r.get(h, "")).strip()
                 for h in ("Brand Name", "Origin", "Class/Type", "Permit Number")))
    diag = {"table": True, "header": bool(hm), "rows": len(recs), "filled": filled,
            "table_rows": len(table.find_all("tr")),
            "matched": [k for k in EXPECTED_COLS if k in hm],
            "fallback": [k for k in EXPECTED_COLS if k not in hm]}
    return recs, next_href, diag

def search_chunk(s, frm, to, args, log, diags=None):
    """Yield records for one date chunk, paginating until exhausted.
    Appends each page's parse diagnostics to `diags` (for drift / self-reporting)."""
    resp = s.post(SEARCH_PROC, data=search_payload(frm, to, args), timeout=120)
    page = 1
    while True:
        recs, next_href, diag = parse_results(resp.text)
        if diags is not None:
            diags.append(diag)
        if not recs and page == 1:
            log(f"    {frm:%m/%d}-{to:%m/%d} p{page}: 0 rows "
                f"(empty window, or confirm form params on first run)")
        for r in recs:
            yield r
        if recs:
            log(f"    {frm:%m/%d}-{to:%m/%d} p{page}: {len(recs)} rows")
        if not next_href or page >= args.max_pages:
            break
        time.sleep(args.delay)
        resp = s.get(next_href, timeout=120)
        page += 1
    # cap warning: if a single chunk returns a suspiciously round max, shrink chunk
    return

# ---------------------------------------------------------------- detail page
def parse_detail(html):
    """Pull richer fields from a COLA detail page via label->value scanning."""
    soup = soupify(html)
    pairs = {}
    for td in soup.find_all(["td", "th"]):
        label = td.get_text(" ", strip=True).rstrip(":").lower()
        if not label:
            continue
        val_cell = td.find_next_sibling(["td", "th"])
        if val_cell:
            pairs[label] = val_cell.get_text(" ", strip=True)
    def pick(*keys):
        for k in keys:
            for lab, v in pairs.items():
                if k in lab:
                    return v
        return ""
    return {
        "Fanciful Name": pick("fanciful name"),
        "Applicant":     pick("applicant", "vendor", "plant registry/basic permit"),
        "Class/Type":    pick("class/type"),
        "Origin":        pick("origin"),
        "Net Contents":  pick("net contents"),
        "Approval Date": pick("approval date", "date issued"),
        "Status":        pick("status of ttb id", "status"),
    }

def enrich(s, rec, args, log):
    try:
        html = s.get(DETAIL_URL, params={"action": "publicFormDisplay", "ttbid": rec["TTB ID"]},
                     timeout=90).text
        for k, v in parse_detail(html).items():
            if v and not rec.get(k):
                rec[k] = v
    except Exception as e:
        log(f"      detail {rec['TTB ID']}: {e}")
    if args.ocr:
        rec["UPC"] = ocr_upc(s, rec["TTB ID"]) or rec.get("UPC", "")
    return rec

def ocr_upc(s, ttbid):
    """Hook into your ttb_cola_labels OCR. Returns '' if unavailable."""
    try:
        from ttb_cola_labels import extract_upc_from_label
    except Exception:
        return ""
    try:
        img = s.get(ATTACH_URL, params={"filename": f"{ttbid}.jpg"}, timeout=90).content
        return extract_upc_from_label(img) or ""
    except Exception:
        return ""

# ---------------------------------------------------------------- profiling / output
def profile(header, rows):
    prof = []
    for i, _ in enumerate(header):
        non = [r[i] for r in rows if i < len(r) and r[i].strip()]
        c = Counter(non)
        prof.append({"fill": len(non), "distinct": len(c),
                     "top": [[v, k] for v, k in c.most_common(12)]})
    return prof

def sample(rows, header, n):
    step = max(1, len(rows) // n)
    return [(r + [""] * len(header))[:len(header)] for r in rows[::step][:n]]

def run_record(total, status="success", warnings=None):
    rid = "R-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    now = int(time.time() * 1000)
    return {"id": rid, "connId": "ttb-cola", "startedAt": now - 1, "finishedAt": now,
            "durationMs": 0, "status": status, "trigger": "manual", "total": total,
            "degraded": status == "degraded", "warnings": warnings or [],
            "extracts": [{"id": "cola_labels", "rows": total, "delta": 0, "status": status}]}

# ---------------------------------------------------------------- orchestration
def scrape(args, log=print):
    s = make_session()
    s.get(SEARCH_PAGE, timeout=60)   # establish session cookie

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "cola_labels.csv")
    seen = set()
    if args.resume and os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add(row.get("TTB ID", ""))
        log(f"resume: {len(seen)} TTB IDs already captured")

    new_file = not (args.resume and os.path.exists(csv_path))
    fout = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.writer(fout)
    if new_file:
        writer.writerow(COLA_HEADER)

    d_from = datetime.datetime.strptime(args.date_from, "%m/%d/%Y").date()
    d_to   = datetime.datetime.strptime(args.date_to, "%m/%d/%Y").date()
    records, n_new = [], 0
    diags = []   # per-page parse diagnostics, for drift / self-reporting

    for frm, to in daterange_chunks(d_from, d_to, args.chunk_days):
        for rec in search_chunk(s, frm, to, args, log, diags):
            if rec["TTB ID"] in seen:
                continue
            seen.add(rec["TTB ID"])
            if args.detail or args.ocr:
                enrich(s, rec, args, log)
                time.sleep(args.delay)
            row = [rec.get(h, "") for h in COLA_HEADER]
            writer.writerow(row); fout.flush()
            records.append(row); n_new += 1
        time.sleep(args.delay)
    fout.close()
    log(f"\ncaptured {n_new} new COLAs ({len(seen)} total in file)")

    # build dashboard payloads from the full file (not just this run)
    all_rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        r = csv.reader(f); next(r, None)
        all_rows = [row for row in r if any(c.strip() for c in row)]
    datasets = {"cola_labels": {"header": COLA_HEADER,
                                "rows": sample(all_rows, COLA_HEADER, args.sample),
                                "total": len(all_rows),
                                "profile": profile(COLA_HEADER, all_rows)}}
    open(os.path.join(args.out, "datasets.js"), "w", encoding="utf-8").write(
        "const DATASETS=" + json.dumps(datasets, separators=(",", ":"), ensure_ascii=False) + ";")
    # self-reporting: turn parse diagnostics into warnings + a degraded status so a
    # silent layout change surfaces as "needs review" instead of bad data.
    warnings = []
    if diags and not any(d.get("table") for d in diags):
        warnings.append("No results table found on any page — the search form or page layout may have changed.")
    # populated table(s) but nothing extracted -> the row link/selector likely changed
    extracted = sum(d.get("rows", 0) for d in diags)
    data_rows = sum(max(0, d.get("table_rows", 0) - 1) for d in diags if d.get("table"))
    if data_rows > 0 and extracted == 0:
        warnings.append("Results table present but 0 records extracted — the row link/selector may have changed.")
    if extracted > 0 and sum(d.get("filled", 0) for d in diags) == 0:
        warnings.append("Rows extracted but their fields are empty — the column mapping may be off.")
    drift_cols = sorted({c for d in diags if d.get("rows") for c in d.get("fallback", [])})
    if drift_cols:
        warnings.append("Header drift: column(s) " + ", ".join(drift_cols)
                        + " not recognized in the page header — used positional fallback; verify these fields.")
    status = "degraded" if warnings else ("success" if n_new or all_rows else "failed")
    if warnings:
        for w in warnings:
            log("⚠ " + w)
    runs = [run_record(len(all_rows), status, warnings)]
    json.dump(runs, open(os.path.join(args.out, "runs.json"), "w"), indent=2)
    log(f"wrote {args.out}/cola_labels.csv, datasets.js, runs.json")
    return datasets, runs, all_rows

# ---------------------------------------------------------------- cli
def build_args(argv=None):
    today = datetime.date.today()
    wk = today - datetime.timedelta(days=7)
    ap = argparse.ArgumentParser(description="Scrape the TTB COLA public registry.")
    ap.add_argument("--from", dest="date_from", default=wk.strftime("%m/%d/%Y"))
    ap.add_argument("--to",   dest="date_to",   default=today.strftime("%m/%d/%Y"))
    ap.add_argument("--chunk-days", type=int, default=1)
    ap.add_argument("--product", default="")
    ap.add_argument("--origin", default="")
    ap.add_argument("--class-from", dest="class_from", default="")
    ap.add_argument("--class-to", dest="class_to", default="")
    ap.add_argument("--detail", action="store_true", help="open each COLA detail page to enrich")
    ap.add_argument("--ocr", action="store_true", help="OCR label image for UPC (needs ttb_cola_labels)")
    ap.add_argument("--resume", action="store_true", help="skip TTB IDs already in the output CSV")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--max-pages", type=int, default=200)
    ap.add_argument("--sample", type=int, default=800)
    ap.add_argument("--out", default="cola_out")
    ap.add_argument("--list-codes", action="store_true")
    return ap.parse_args(argv)

def main():
    args = build_args()
    s = make_session()
    if args.list_codes:
        try:
            for name, opts in list_codes(s).items():
                print(f"\n== {name} ==")
                for code, label in opts[:60]:
                    print(f"  {code:<8} {label}")
        except Exception as e:
            print(f"could not load codes (TTB reachable?): {e}")
        return
    scrape(args)

if __name__ == "__main__":
    main()
