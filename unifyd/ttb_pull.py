#!/usr/bin/env python3
"""ttb_pull.py — incremental TTB COLA scrape that runs OFF-MAC (Fly ephemeral), landing into ttb_cola.

The COLA scraper (ttb_cola_scraper) already works from a server container: ttbonline.gov serves an
incomplete TLS chain, so it uses verify=False and talks DIRECT (Bright Data DC zones KYC-gate .gov) — no
browser warm, no F5 dance, no Mac. Verified live from Fly: a 2-day window returned 160 COLAs, status success.
The only thing that kept TTB on the Mac was that no registry source ran the scrape + LANDED it; the app path
(cola_pull) only scraped to an in-memory preview. This is that source.

Each run scrapes the last TTB_DAYS (default 14) of the public COLA registry → a CSV on the ephemeral machine
→ ACCUMULATE into ttb_cola by TTB ID (never overwrites the ~1M-row backfill; the overlap just dedups). The
CSV columns are exactly ttb_cola_scraper.COLA_HEADER, which matches the ttb_cola schema, so no drift.

    python ttb_pull.py                 # last 14 days
    python ttb_pull.py --days 30       # wider catch-up window
"""
import argparse
import csv
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import ttb_cola_scraper as cola


def pull(days=None, chunk_days=1, detail=False, out=None, log=print):
    days = days if days is not None else int(os.environ.get("TTB_DAYS", "14"))
    today = datetime.date.today()
    frm = (today - datetime.timedelta(days=days)).strftime("%m/%d/%Y")
    to = today.strftime("%m/%d/%Y")
    out = out or os.path.join(os.environ.get("TTB_OUT", "/tmp"), "ttb_cola")
    argv = ["--from", frm, "--to", to, "--chunk-days", str(chunk_days), "--out", out, "--resume"]
    if detail:
        argv.append("--detail")
    args = cola.build_args(argv)
    cola.scrape(args, log=log)
    csv_path = os.path.join(out, "cola_labels.csv")
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8"))) if os.path.exists(csv_path) else []
    # normalize to the canonical COLA schema (== ttb_cola columns) so accumulate never drifts the table
    recs = [{h: r.get(h, "") for h in cola.COLA_HEADER} for r in rows if r.get("TTB ID")]
    if recs:
        warehouse.write_accumulate("ttb_cola", recs, key=lambda r: r["TTB ID"], fields=cola.COLA_HEADER)
        log("[ttb] %s..%s scraped %d rows → accumulated into ttb_cola" % (frm, to, len(recs)))
    else:
        log("[ttb] %s..%s scraped 0 rows — nothing to land (window empty or blocked)" % (frm, to))
    return len(recs)


DETAIL_TABLE = "ttb_cola_detail"
LABELS_TABLE = "ttb_cola_labels"
_VIEW_URL = cola.BASE + "/viewColaDetails.do"

# EXISTING table schemas (snake_case) — this producer EXTENDS these, never a parallel schema.
DETAIL_COLS = ["ttb_id", "status", "vendor_code", "serial_number", "class_type_code", "class_type_desc",
               "origin_code", "brand_name", "fanciful_name", "application_type", "for_sale_in", "net_contents",
               "wine_vintage", "grape_varietal", "alcohol_content", "formula", "approval_date",
               "qualifications", "plant_permit", "label_image_url", "other_json"]
LABEL_COLS = ["ttb_id", "image_file", "upc", "abv", "net_contents", "claims", "gov_warning", "ocr_chars",
              "front_label_url", "back_label_url", "label_urls"]
# ttb_enrich (PascalCase, validated on the current site) field -> ttb_cola_detail snake_case column
_DETAIL_MAP = {"status": "Status", "vendor_code": "Vendor Code", "serial_number": "Serial #",
               "class_type_code": "Class/Type Code", "origin_code": "Origin Code", "brand_name": "Brand Name",
               "fanciful_name": "Fanciful Name", "application_type": "Type of Application",
               "for_sale_in": "For Sale In", "net_contents": "Total Bottle Capacity",
               "wine_vintage": "Wine Vintage", "formula": "Formula", "approval_date": "Approval Date",
               "qualifications": "Qualifications"}


def _enrich_one(s, tid, class_type_desc=""):
    """Enrich ONE COLA into (detail_row, label_row) matching the EXISTING snake_case ttb_cola_detail /
    ttb_cola_labels schemas, via ttb_enrich's validated parsers. label_row is None if the COLA exposes no
    label. Best-effort per field so one bad COLA never sinks the batch. (grape_varietal/plant_permit and the
    text-OCR label fields — claims/gov_warning/ocr_chars — are left empty: the current stack decodes the
    barcode UPC + reads ABV, not full text OCR.)"""
    import json
    import ttb_enrich as te
    df, alc, labs, upc = {}, {"content": "", "abv": ""}, [], ""
    try:
        h2 = s.get(_VIEW_URL, params={"action": "publicDisplaySearchBasic", "ttbid": tid}, timeout=45).text
        df = te.parse_detail_fields(h2) or {}
    except Exception:
        pass
    try:
        h1 = s.get(_VIEW_URL, params={"action": "publicFormDisplay", "ttbid": tid}, timeout=45).text
        alc = te.parse_alcohol(h1, df.get("Class/Type Code", ""))
        labs = te.label_filenames(h1)
        try:
            from ttb_cola_labels import extract_upc_from_label
            for fn in labs[:2]:
                img = s.get(cola.ATTACH_URL, params={"filename": fn, "filetype": "l"}, timeout=45).content
                u = extract_upc_from_label(img)
                if u:
                    upc = u
                    break
        except Exception:
            pass
    except Exception:
        pass
    detail = {c: "" for c in DETAIL_COLS}
    detail["ttb_id"] = tid
    detail["class_type_desc"] = class_type_desc                      # ttb_cola's Class/Type (search field)
    detail["alcohol_content"] = alc.get("content", "")
    detail["label_image_url"] = labs[0] if labs else ""
    for sc, pc in _DETAIL_MAP.items():
        detail[sc] = df.get(pc, "") or ""
    extra = {k: v for k, v in df.items() if k not in _DETAIL_MAP.values() and k != "TTB ID" and v}
    detail["other_json"] = json.dumps(extra, separators=(",", ":")) if extra else ""
    label = None
    if labs or upc:
        urls = [cola.ATTACH_URL + "?filename=" + fn + "&filetype=l" for fn in labs]
        label = {c: "" for c in LABEL_COLS}
        label.update(ttb_id=tid, image_file=(labs[0] if labs else ""), upc=upc, abv=alc.get("abv", ""),
                     net_contents=df.get("Total Bottle Capacity", "") or "",
                     front_label_url=(urls[0] if urls else ""),
                     back_label_url=(urls[1] if len(urls) > 1 else ""), label_urls="|".join(urls))
    return detail, label


def enrich_pass(limit=None, workers=None, log=print):
    """OFF-MAC producer for the detail + label/UPC layers — EXTENDS the existing ttb_cola_detail and
    ttb_cola_labels tables (accumulate by ttb_id, never clobber) with COLAs not yet detailed. Un-enriched =
    a ttb_cola id absent from ttb_cola_detail (DuckDB anti-join, memory-safe). Concurrent but GENTLE on the
    .gov site (few workers). $0, verify=False direct; UPC needs libzbar0+pyzbar+pillow (in the image)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    limit = limit if limit is not None else int(os.environ.get("TTB_ENRICH_LIMIT", "300"))
    workers = workers or int(os.environ.get("TTB_ENRICH_WORKERS", "4"))
    detail_rows = 0
    try:
        detail_rows = warehouse.row_count(DETAIL_TABLE)
    except Exception:
        detail_rows = 0
    if detail_rows:
        sql = ('SELECT t."TTB ID" tid, t."Class/Type" ctd FROM t WHERE t."TTB ID" IS NOT NULL AND t."TTB ID" '
               "NOT IN (SELECT ttb_id FROM read_parquet('%s')) LIMIT %d" % (warehouse.uri(DETAIL_TABLE), limit))
    else:
        sql = 'SELECT "TTB ID" tid, "Class/Type" ctd FROM t WHERE "TTB ID" IS NOT NULL LIMIT %d' % limit
    try:
        todo = [(str(r["tid"]), r.get("ctd") or "") for r in warehouse.query("ttb_cola", sql)]
    except Exception as e:
        log("[ttb-enrich] select failed: %s" % str(e)[:100])
        return 0
    if not todo:
        log("[ttb-enrich] nothing to enrich (all ttb_cola COLAs already in ttb_cola_detail)")
        return 0
    log("[ttb-enrich] %d already detailed — enriching %d this pass (workers=%d)" % (detail_rows, len(todo), workers))
    s = cola.make_session()
    details, labels, cnt, lock = [], [], [0], threading.Lock()

    def _work(item):
        tid, ctd = item
        d, l = _enrich_one(s, tid, ctd)
        with lock:
            details.append(d)
            if l is not None:
                labels.append(l)
            cnt[0] += 1
            if cnt[0] % 50 == 0:
                log("  [ttb-enrich] %d/%d" % (cnt[0], len(todo)))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_work, todo))
    if details:
        warehouse.write_accumulate(DETAIL_TABLE, details, key=lambda r: r["ttb_id"], fields=DETAIL_COLS)
    if labels:
        warehouse.write_accumulate(LABELS_TABLE, labels, key=lambda r: r["ttb_id"], fields=LABEL_COLS)
    got_det = sum(1 for d in details if d.get("status"))
    got_upc = sum(1 for l in labels if l.get("upc"))
    got_abv = sum(1 for l in labels if l.get("abv"))
    log("[ttb-enrich] +%d detail (%d w/ status) → ttb_cola_detail · +%d labels (%d UPC, %d ABV) → ttb_cola_labels"
        % (len(details), got_det, len(labels), got_upc, got_abv))
    return len(details)


def run(log=print):
    return pull(log=log)


def run_enrich(log=print):
    return enrich_pass(log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Incremental TTB COLA scrape → ttb_cola ($0, off-Mac).")
    ap.add_argument("--days", type=int, default=None, help="lookback window in days (default TTB_DAYS or 14)")
    ap.add_argument("--chunk-days", type=int, default=1)
    ap.add_argument("--detail", action="store_true", help="open each COLA detail page (slow; adds UPC/dates)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--enrich", action="store_true", help="deepen un-enriched ttb_cola rows (detail + label UPC)")
    ap.add_argument("--limit", type=int, default=None, help="--enrich: rows per pass")
    a = ap.parse_args(argv)
    if a.enrich:
        n = enrich_pass(limit=a.limit)
        print("enriched %d ttb_cola rows" % n)
        return 0 if n else 1
    n = pull(days=a.days, chunk_days=a.chunk_days, detail=a.detail, out=a.out)
    print("landed %d COLAs into ttb_cola" % n)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
