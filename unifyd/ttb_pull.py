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
_VIEW_URL = cola.BASE + "/viewColaDetails.do"


def _detail_fields():
    import ttb_enrich as te
    return ["TTB ID"] + te.DETAIL_FIELDS + ["UPC", "alc_content", "abv", "proof", "n_labels"]


def _enrich_one(s, tid):
    """Full per-COLA enrichment via ttb_enrich's VALIDATED (current-USWDS-site) parsers: detail fields from
    ?publicDisplaySearchBasic, alcohol + label filenames from ?publicFormDisplay, and the label-barcode UPC
    from the attachment image. Returns a dict for ttb_cola_detail. Best-effort per field (a page/label miss
    just leaves it empty), so one bad COLA never sinks the batch."""
    import ttb_enrich as te
    rec = {"TTB ID": tid}
    try:
        h2 = s.get(_VIEW_URL, params={"action": "publicDisplaySearchBasic", "ttbid": tid}, timeout=60).text
        for k, v in te.parse_detail_fields(h2).items():
            if k != "TTB ID":
                rec[k] = v
    except Exception:
        pass
    upc = ""
    try:
        h1 = s.get(_VIEW_URL, params={"action": "publicFormDisplay", "ttbid": tid}, timeout=60).text
        alc = te.parse_alcohol(h1, rec.get("Class/Type Code", ""))
        rec["alc_content"], rec["abv"], rec["proof"] = alc.get("content", ""), alc.get("abv", ""), alc.get("proof", "")
        labs = te.label_filenames(h1)
        rec["n_labels"] = len(labs)
        try:
            from ttb_cola_labels import extract_upc_from_label
            for fn in labs[:3]:
                img = s.get(cola.ATTACH_URL, params={"filename": fn, "filetype": "l"}, timeout=60).content
                u = extract_upc_from_label(img)
                if u:
                    upc = u
                    break
        except Exception:
            pass
    except Exception:
        pass
    rec["UPC"] = upc
    return rec


def enrich_pass(limit=None, workers=None, log=print):
    """DEEPEN COLA records not yet enriched — full detail fields + ABV + label-barcode UPC — CONCURRENTLY,
    landing a rich ttb_cola_detail (keyed TTB ID) joined to ttb_cola. 'Un-enriched' = a ttb_cola TTB ID absent
    from ttb_cola_detail, so it covers BOTH freshly-scraped thin rows and the ~1M backfill; bounded per run
    (TTB_ENRICH_LIMIT). $0, off-Mac (verify=False direct; UPC needs libzbar0 + pyzbar + pillow on the image)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    limit = limit if limit is not None else int(os.environ.get("TTB_ENRICH_LIMIT", "400"))
    workers = workers or int(os.environ.get("TTB_ENRICH_WORKERS", "8"))
    # Select un-enriched TTB IDs (in ttb_cola, absent from ttb_cola_detail) as a DuckDB ANTI-JOIN that returns
    # only `limit` rows — never materialize all ~1M ids in Python (that OOM'd the shared box).
    detail_rows = 0
    try:
        detail_rows = warehouse.row_count(DETAIL_TABLE)
    except Exception:
        detail_rows = 0
    if detail_rows:
        sql = ('SELECT t."TTB ID" FROM t WHERE t."TTB ID" IS NOT NULL AND t."TTB ID" NOT IN '
               "(SELECT \"TTB ID\" FROM read_parquet('%s')) LIMIT %d" % (warehouse.uri(DETAIL_TABLE), limit))
    else:
        sql = 'SELECT "TTB ID" FROM t WHERE "TTB ID" IS NOT NULL LIMIT %d' % limit
    try:
        todo = [str(r["TTB ID"]) for r in warehouse.query("ttb_cola", sql)]
    except Exception as e:
        log("[ttb-enrich] un-enriched select failed: %s" % str(e)[:100])
        return 0
    if not todo:
        log("[ttb-enrich] nothing to enrich (all COLAs already in %s)" % DETAIL_TABLE)
        return 0
    log("[ttb-enrich] %d already enriched — enriching %d this pass" % (detail_rows, len(todo)))
    s = cola.make_session()
    out, cnt, lock = [], [0], threading.Lock()

    def _work(tid):
        rec = _enrich_one(s, tid)
        with lock:
            out.append(rec)
            cnt[0] += 1
            if cnt[0] % 50 == 0:
                log("  [ttb-enrich] %d/%d" % (cnt[0], len(todo)))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_work, todo))
    fields = _detail_fields()
    recs = [{h: r.get(h, "") for h in fields} for r in out if r.get("TTB ID")]
    warehouse.write_accumulate(DETAIL_TABLE, recs, key=lambda r: r["TTB ID"], fields=fields)
    got_upc = sum(1 for r in out if r.get("UPC"))
    got_det = sum(1 for r in out if r.get("Status") or r.get("Class/Type Code"))
    got_abv = sum(1 for r in out if r.get("abv"))
    log("[ttb-enrich] enriched %d → %s (+%d UPC, +%d detail, +%d abv)"
        % (len(recs), DETAIL_TABLE, got_upc, got_det, got_abv))
    return len(recs)


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
