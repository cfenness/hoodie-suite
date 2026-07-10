"""ca_abc.py — California ABC statewide license export -> ca_outlets (the CA outlet spine).

California is a LICENSE state (no state stores / no control-state price book), so the value is the
outlet map: the ABC's daily export lists every licensed alcohol premise statewide (~129k), refreshed
daily, with license type + premises address + county + census tract. The download is WAF-gated, so it
needs a realistic browser header set + referer (the Unlocker returns 0 bytes for this binary).

Off-sale package RETAIL = License Type 20 (beer & wine) + 21 (general); on-sale = 41/47/48.
`Lic or App` = LIC (active license) | APP (pending application).

    python ca_abc.py            # download + land ca_outlets
"""
import csv, io, os, sys, urllib.request, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

URL = "https://www.abc.ca.gov/wp-content/uploads/DailyExport-CSV.zip"
HDRS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/zip,application/octet-stream,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.abc.ca.gov/licensing/licensing-reports/",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
}
OFF_SALE_RETAIL = {"20", "21"}     # beer/wine + general package (off-premise retail)


def fetch(log=print):
    b = urllib.request.urlopen(urllib.request.Request(URL, headers=HDRS), timeout=240).read()
    if b[:2] != b"PK":
        raise RuntimeError("CA ABC: not a zip (%d bytes) — WAF block? try refreshing headers/referer" % len(b))
    z = zipfile.ZipFile(io.BytesIO(b))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    lines = z.read(name).decode("utf-8-sig", "replace").splitlines()
    rdr = list(csv.reader(lines[1:]))          # line 0 = "Updated ..." title; line 1 = header
    header = [h.strip() for h in rdr[0]]
    recs = [{header[i]: (r[i].strip() if i < len(r) else "") for i in range(len(header))}
            for r in rdr[1:] if any((c or "").strip() for c in r)]
    log("CA ABC: %d licenses parsed" % len(recs))
    return header, recs


def run(log=print):
    header, recs = fetch(log=log)
    res = warehouse.write_parquet("ca_outlets", recs, fields=header)   # fields=header -> keep leading zeros
    off = sum(1 for r in recs if r.get("License Type") in OFF_SALE_RETAIL and r.get("Lic or App") == "LIC")
    log("ca_outlets: %s rows -> %s  (%s active off-sale retail)"
        % (format(res["rows"], ","), res["uri"], format(off, ",")))
    return {"name": "ca_outlets", "rows": res["rows"], "uri": res["uri"], "off_sale_retail": off}


if __name__ == "__main__":
    run()
