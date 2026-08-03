#!/usr/bin/env python3
"""vip_brandbuilder_sellsheets_test.py — URL-safety, UPC validation, and run() bookkeeping, no network.

Pins the exact live incident (2026-08-03): 669 of 2,427 sell sheets failed with "URL can't contain
control characters" because real filenames carry literal spaces/parens
('Crisp Apple_SRS_0_0.pdf', 'SRS-SAM-BeersOfSummerVP-2025_50352 (1)_c0m69fb2.pdf') — urllib.request
rejects a raw space outright. _safe_url() is the fix; this file proves it actually re-encodes those
exact filenames and, just as importantly, does not mangle URLs that already worked.

    python3 unifyd/vip_brandbuilder_sellsheets_test.py
"""
import os
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="bbs_sellsheets_")
    import vip_brandbuilder_sellsheets as m

    print("_safe_url(): the exact live failures now encode cleanly")
    space_url = "https://images.vtinfo.com/b_white/companies/sam/item-catalog/supplemental/Crisp Apple_SRS_0_0.pdf"
    fixed = m._safe_url(space_url)
    check("space is percent-encoded, not left raw", " " not in fixed, fixed)
    check("percent-encoding is the correct %20", "%20" in fixed, fixed)

    paren_url = ("https://images.vtinfo.com/b_white/companies/sam/item-catalog/items/"
                "SRS-SAM-BeersOfSummerVP-2025_50352 (1)_c0m69fb2.pdf")
    fixed2 = m._safe_url(paren_url)
    check("space+parens both encoded", " " not in fixed2 and "(" not in fixed2 and ")" not in fixed2, fixed2)

    print("\n_safe_url(): does not corrupt a URL with no unsafe characters")
    clean = "https://images.vtinfo.com/b_white/companies/abt/item-catalog/supplemental/ssandygator-30adcf5_zyfyh56m.pdf"
    check("a clean URL (no spaces/reserved chars) passes through byte-for-byte", m._safe_url(clean) == clean,
          m._safe_url(clean))

    print("\n_clean_upc(): only real UPC/EAN/GTIN lengths survive, never a guessed digit string")
    check("a real UPC-A (12 digits) passes", m._clean_upc("080020530909") == "080020530909")
    check("spaces in a printed barcode are stripped before length-checking",
          m._clean_upc("0 80020 53090 9") == "080020530909")
    check("an EAN-13 (13 digits) passes", m._clean_upc("1234567890123") == "1234567890123")
    check("a GTIN-14 (14 digits) passes", m._clean_upc("12345678901234") == "12345678901234")
    check("a UPC-E (8 digits) passes", m._clean_upc("12345678") == "12345678")
    check("a clearly wrong length (e.g. a partial 4-digit read) is rejected, not padded/guessed",
          m._clean_upc("1234") is None)
    check("None input never crashes, returns None", m._clean_upc(None) is None)
    check("empty string returns None, not an empty string", m._clean_upc("") is None)

    print("\nsheets_from_csv(): groups rows by sell_sheet URL, tracks distributor/product context")
    csv_path = os.path.join(tempfile.mkdtemp(prefix="bbs_csv_"), "index.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("distributor_id,product_id,product_sell_sheet\n")
        f.write("00019,P1,https://x/a.pdf\n")
        f.write("02143,P1,https://x/a.pdf\n")          # same sheet, different distributor
        f.write("00023,P2,https://x/b.png\n")
        f.write("00023,P3,\n")                          # no sell sheet — must be skipped
    idx = m.sheets_from_csv(csv_path)
    check("rows with no sell sheet are excluded", len(idx) == 2, idx.keys())
    check("a sheet shared across distributors unions both", idx["https://x/a.pdf"][0] == {"00019", "02143"},
          idx["https://x/a.pdf"])
    check("n_source_rows counts every row that referenced the sheet", idx["https://x/a.pdf"][2] == 2)

    print("\nrun(): a real hit, a correctly-empty result, and a failure are counted distinctly")
    real_extract = m.extract

    def fake_extract(url):
        if url.endswith("hit.pdf"):
            return [{"package_name": "Can", "upc": "080020530909"}, {"package_name": "Keg", "upc": None}]
        if url.endswith("empty.pdf"):
            return []                                   # a real sheet with nothing legible — not an error
        raise RuntimeError("404")

    csv_path2 = os.path.join(tempfile.mkdtemp(prefix="bbs_csv2_"), "index.csv")
    with open(csv_path2, "w", encoding="utf-8") as f:
        f.write("distributor_id,product_id,product_sell_sheet\n")
        f.write("00019,P1,https://x/hit.pdf\n")
        f.write("00023,P2,https://x/empty.pdf\n")
        f.write("00035,P3,https://x/broken.pdf\n")

    m.extract = fake_extract
    try:
        rows = m.run(csv_path2, workers=2, log=lambda *a: None)
    finally:
        m.extract = real_extract

    check("only the real hit's packages land as rows", len(rows) == 2, rows)
    check("a package with no legible UPC lands with an empty string, not dropped or fabricated",
          any(r["package_name"] == "Keg" and r["upc"] == "" for r in rows), rows)
    check("a package WITH a UPC keeps it exactly", any(r["upc"] == "080020530909" for r in rows), rows)
    check("distributor_ids/product_ids are attached from the CSV context",
          all(r["distributor_ids"] == "00019" and r["product_ids"] == "P1" for r in rows), rows)

    print("\nland(): the accumulate key prevents duplicate (sheet, package) rows across re-runs")
    n1 = m.land(rows, log=lambda *a: None)
    n2 = m.land(rows, log=lambda *a: None)              # same rows again — a re-run must not error or explode
    check("landing succeeds both times", n1 == 2 and n2 == 2, (n1, n2))
    landed = warehouse.query("vip_brandbuilder_sellsheet_packages", "SELECT * FROM t")
    check("re-landing the same rows does not duplicate (accumulate key dedupes)", len(landed) == 2, landed)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
