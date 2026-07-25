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


def run(log=print):
    return pull(log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Incremental TTB COLA scrape → ttb_cola ($0, off-Mac).")
    ap.add_argument("--days", type=int, default=None, help="lookback window in days (default TTB_DAYS or 14)")
    ap.add_argument("--chunk-days", type=int, default=1)
    ap.add_argument("--detail", action="store_true", help="open each COLA detail page (slow; adds UPC/dates)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    n = pull(days=a.days, chunk_days=a.chunk_days, detail=a.detail, out=a.out)
    print("landed %d COLAs into ttb_cola" % n)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
