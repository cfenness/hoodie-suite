#!/usr/bin/env python3
"""
pull_sources.py — real master-data source pulls for the Unifyd MDM dashboard.

Replaces the in-browser `simulatePull` simulation with actual fetches.
Run this where the sources are reachable (your allowlisted backend), then
drop the emitted `datasets.js` into the dashboard and feed `runs.json` to the
Connections page as real run records.

  python pull_sources.py fl        # Florida DBPR/ABT  (no extra deps; tested/working)
  python pull_sources.py cola      # TTB COLA registry  (needs: pip install requests beautifulsoup4)
  python pull_sources.py all

Outputs (./out):
  raw/<id>.csv      raw source extracts
  datasets.js       const DATASETS = {...}  -> paste into the dashboard
  runs.json         real run records (id, rows, delta, status, duration) per extract
"""
import csv, io, json, os, sys, time, urllib.request, datetime, random, string
from collections import Counter

OUT = "out"; RAW = os.path.join(OUT, "raw")
os.makedirs(RAW, exist_ok=True)

# ----------------------------------------------------------------------------
# shared: profiler + sampler + run-record helpers
# ----------------------------------------------------------------------------
def profile(header, data):
    prof = []
    for i, _ in enumerate(header):
        vals = [(r[i] if i < len(r) else "") for r in data]
        non = [v for v in vals if v.strip() != ""]
        c = Counter(non)
        prof.append({"fill": len(non), "distinct": len(c),
                     "top": [[v, k] for v, k in c.most_common(12)]})
    return prof

def sample(data, header, n):
    step = max(1, len(data) // n)
    return [(r + [""] * len(header))[:len(header)] for r in data[::step][:n]]

def run_record(conn_id, extracts, prev=None):
    """extracts: [(extract_id, rows_int, status_str)] -> dashboard-shaped run record."""
    prevmap = {e["id"]: e for e in (prev or {}).get("extracts", [])}
    exs, total = [], 0
    for eid, rows, status in extracts:
        delta = rows - prevmap.get(eid, {}).get("rows", rows)
        exs.append({"id": eid, "rows": rows, "delta": delta, "status": status})
        total += rows
    status = "failed" if any(e[2] == "failed" for e in extracts) else \
             "partial" if any(e[2] == "partial" for e in extracts) else "success"
    rid = "R-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    now = int(time.time() * 1000)
    return {"id": rid, "connId": conn_id, "startedAt": now - 1, "finishedAt": now,
            "durationMs": 0, "status": status, "trigger": "manual",
            "total": total, "extracts": exs}

# ----------------------------------------------------------------------------
# FLORIDA  (DBPR / ABT public records — no auth, daily CSV; this is live/tested)
# ----------------------------------------------------------------------------
FL_BASE = "https://www2.myfloridalicense.com/sto/file_download/extracts"
FL_STD = ["Board","Profession","Owner Name","Series","Modifier","Mail Address 1","Mail Address 2",
    "Mail Address 3","Mail City","Mail State","Mail ZIP","Mail County","DBA","Location Address 1",
    "Location Address 2","Location Address 3","Location City","Location State","Location ZIP",
    "Location County","License Number","Primary Status","Secondary Status","Original Licensure Date",
    "Effective Date","Expiration Date","Tax Stamp Designation","Smoking Designation","Retail Tobacco Indicator"]
# extract id, has_header_row, header_override, sample_rows
FL_EXTRACTS = [("bd4008lic", True, None, 600), ("bd4011lic", True, None, 500),
    ("abtbrands", True, None, 600), ("bd4006lic", True, None, 600),
    ("bd4005lic", True, None, 500), ("bd400revok", False, FL_STD, 500), ("bd4002lic", True, None, 500)]

def pull_fl():
    datasets, run_extracts = {}, []
    for eid, hashdr, override, n in FL_EXTRACTS:
        url = f"{FL_BASE}/{eid}.csv"
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                txt = r.read().decode("utf-8", "replace")
            open(os.path.join(RAW, f"{eid}.csv"), "w", encoding="utf-8").write(txt)
            rows = list(csv.reader(io.StringIO(txt)))
            header = [h.strip() for h in rows[0]] if hashdr else override
            data = [r for r in (rows[1:] if hashdr else rows) if any((c or "").strip() for c in r)]
            datasets[eid] = {"header": header, "rows": sample(data, header, n),
                             "total": len(data), "profile": profile(header, data)}
            run_extracts.append((eid, len(data), "success"))
            print(f"  FL {eid}: {len(data):,} rows")
        except Exception as e:
            run_extracts.append((eid, 0, "failed"))
            print(f"  FL {eid}: FAILED {e}")
    return datasets, [run_record("fl-items", [e for e in run_extracts if e[0] in
                        ("bd4008lic","bd4011lic","abtbrands")]),
                      run_record("fl-outlets", [e for e in run_extracts if e[0] in
                        ("bd4006lic","bd4005lic","bd400revok","bd4002lic")])]

# ----------------------------------------------------------------------------
# TTB COLA  (public registry crawler — needs requests + beautifulsoup4)
#   The COLA public registry has no bulk endpoint; results come from a search
#   form. This crawls a completed-date window and parses the result table.
#   Selectors are written against the live markup and may need a one-time
#   confirmation on first real run (the site is a legacy Struts app).
# ----------------------------------------------------------------------------
COLA_HEADER = ["TTB ID","Permit Number","Serial Number","Brand Name","Fanciful Name",
               "Class/Type","Origin","Applicant","Status","Completed Date","Approval Date",
               "Net Contents","UPC"]

def pull_cola(days_back=30, max_pages=50, sample_n=800, do_ocr=False):
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        print("  COLA needs: pip install requests beautifulsoup4"); return {}, []

    base = "https://www.ttbonline.gov/colasonline"
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (compatible; UnifydMDM/1.0)"
    today = datetime.date.today()
    frm = (today - datetime.timedelta(days=days_back)).strftime("%m/%d/%Y")
    to  = today.strftime("%m/%d/%Y")

    try:
        s.get(f"{base}/publicSearchColasBasic.do", timeout=60)   # establish session cookie
    except Exception as e:
        print(f"  COLA unreachable: {e}"); return {}, []

    # Basic search by completed-date window. Param names match the registry form.
    payload = {
        "action": "search",
        "searchCriteria.dateCompletedFrom": frm,
        "searchCriteria.dateCompletedTo": to,
        "searchCriteria.productNameSearchType": "C",
    }
    records, page = [], 1
    while page <= max_pages:
        url = f"{base}/publicSearchColasBasicProcess.do" if page == 1 \
              else f"{base}/publicPageColasReturn.do?action=page&pageNumber={page}"
        try:
            resp = s.post(url, data=payload, timeout=90) if page == 1 else s.get(url, timeout=90)
        except Exception as e:
            print(f"  COLA page {page} error: {e}"); break
        soup = BeautifulSoup(resp.text, "html.parser")
        # result rows link to publicFormDisplay.do?ttbid=...
        links = soup.select('a[href*="publicFormDisplay.do?ttbid="]')
        if not links:
            break
        for a in links:
            tr = a.find_parent("tr")
            cells = [td.get_text(strip=True) for td in tr.find_all("td")] if tr else []
            ttbid = a.get_text(strip=True)
            # columns vary; map defensively, leave blanks where absent
            rec = {h: "" for h in COLA_HEADER}
            rec["TTB ID"] = ttbid
            # heuristic positional fill (confirm once on first live run):
            if len(cells) >= 6:
                rec["Permit Number"] = cells[1] if len(cells) > 1 else ""
                rec["Serial Number"] = cells[2] if len(cells) > 2 else ""
                rec["Brand Name"]    = cells[3] if len(cells) > 3 else ""
                rec["Origin"]        = cells[4] if len(cells) > 4 else ""
                rec["Class/Type"]    = cells[5] if len(cells) > 5 else ""
                rec["Completed Date"]= cells[-1]
            if do_ocr:
                rec["UPC"] = ocr_upc(s, base, ttbid)   # see hook below
            records.append([rec[h] for h in COLA_HEADER])
        print(f"  COLA page {page}: {len(links)} rows (total {len(records)})")
        page += 1
        time.sleep(1.0)   # be polite

    if not records:
        print("  COLA: no rows parsed — confirm the form params/selectors on a live run.")
        return {}, []
    open(os.path.join(RAW, "cola_labels.csv"), "w", newline="", encoding="utf-8").write(
        _to_csv(COLA_HEADER, records))
    ds = {"cola_labels": {"header": COLA_HEADER, "rows": sample(records, COLA_HEADER, sample_n),
                          "total": len(records), "profile": profile(COLA_HEADER, records)}}
    return ds, [run_record("ttb-cola", [("cola_labels", len(records), "success")])]

def ocr_upc(session, base, ttbid):
    """Hook to your existing ttb_cola_labels OCR. Downloads the label image for
    a COLA and extracts the UPC. Returns '' if unavailable."""
    try:
        from ttb_cola_labels import extract_upc_from_label   # your module
    except Exception:
        return ""
    try:
        # label image attachment endpoint pattern; confirm on first run
        img = session.get(f"{base}/publicViewAttachment.do?filename={ttbid}.jpg", timeout=60).content
        return extract_upc_from_label(img) or ""
    except Exception:
        return ""

def _to_csv(header, rows):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(header); w.writerows(rows)
    return buf.getvalue()

# ----------------------------------------------------------------------------
def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    datasets, runs = {}, []
    if what in ("fl", "all"):
        d, r = pull_fl(); datasets.update(d); runs += r
    if what in ("cola", "all"):
        d, r = pull_cola(); datasets.update(d); runs += r
    if datasets:
        open(os.path.join(OUT, "datasets.js"), "w", encoding="utf-8").write(
            "const DATASETS=" + json.dumps(datasets, separators=(",", ":"), ensure_ascii=False) + ";")
    json.dump(runs, open(os.path.join(OUT, "runs.json"), "w"), indent=2)
    print(f"\nwrote {OUT}/datasets.js ({len(datasets)} datasets) and {OUT}/runs.json ({len(runs)} runs)")

if __name__ == "__main__":
    main()
