#!/usr/bin/env python3
"""run_sources.py — run every source from the registry, VERIFY it landed, and log the outcome.

The fix for silently-dropped progress: for each source we snapshot its table row-count BEFORE and AFTER the run,
so a job that "succeeds" but doesn't move the count is flagged (`no-change`), and a job that errors is `failed`
with the error text + when. Results land in `source_runs`, which the Data Console reads → every source shows its
last run, status, delta, and error. Headless sources run in parallel; Mac (anti-bot browser) sources run strictly
one-at-a-time (they degrade under contention).

  python run_sources.py                 # daily pass (all enabled daily sources)
  python run_sources.py --cadence weekly
  python run_sources.py --only walmart,kroger,publix
  python run_sources.py --headless-only          # skip the Mac/browser sources
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_registry as reg

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
_TIMEOUT = {"headless": 5400, "creds": 5400, "mac": 14400}   # 1.5h headless, 4h Mac (browser sweeps are long)


def _rows(table):
    """Current row count of one table via its Parquet FOOTER (cheap) — None if the table doesn't exist yet."""
    import warehouse
    import pyarrow.parquet as pq
    try:
        u = warehouse.uri(table)
        if u.startswith("s3://"):
            md = pq.read_metadata(u[5:], filesystem=warehouse._s3fs())
        else:
            md = pq.read_metadata(u)
        return md.num_rows
    except Exception:
        return None


def _counts(tables):
    return {t: _rows(t) for t in tables}


def run_one(source, log=print):
    """Run one source in a subprocess, measure before/after row counts, classify the outcome."""
    sid = source["id"]
    before = _counts(source["tables"])
    t0 = time.time()
    code = ("import sys; sys.path.insert(0, %r); import kroger_api; kroger_api._load_creds(); %s"
            % (HERE, source["code"]))
    if source["klass"] == "mac":
        for lk in glob.glob(os.path.expanduser("~/.hoodie_browser_profiles/*/Singleton*")):
            try:
                os.remove(lk)
            except Exception:
                pass
    status, error = "ok", ""
    try:
        r = subprocess.run([PY, "-c", code], cwd=HERE, timeout=_TIMEOUT.get(source["klass"], 5400),
                           capture_output=True, text=True)
        if r.returncode != 0:
            status = "failed"
            error = (r.stderr or r.stdout or "").strip().splitlines()[-1][:300] if (r.stderr or r.stdout) else "nonzero exit"
    except subprocess.TimeoutExpired:
        status, error = "timeout", "exceeded %ds" % _TIMEOUT.get(source["klass"], 5400)
    except Exception as e:
        status, error = "failed", str(e)[:300]
    after = _counts(source["tables"])
    dur = round(time.time() - t0, 1)

    b = sum(v for v in before.values() if v) or 0
    a = sum(v for v in after.values() if v) or 0
    delta = a - b
    # VERIFY LANDING: a clean exit that didn't move any row count is suspicious — surface it, don't call it success.
    if status == "ok" and delta <= 0 and a == b:
        status = "no-change"
    rec = dict(run_id="%s-%d" % (sid, int(t0)), source=sid, label=source["label"], klass=source["klass"],
               ts_start=int(t0), ts_end=int(time.time()), duration_s=dur, status=status,
               rows_before=b, rows_after=a, delta=delta, tables=",".join(source["tables"]),
               error=error, host=os.uname().nodename[:40])
    log("  %-16s %-9s Δ%-10s %5ss %s" % (sid, status, ("+%d" % delta if delta else "0"), dur,
                                         ("| " + error) if error else ""))
    return rec


SR_FIELDS = ["run_id", "source", "label", "klass", "ts_start", "ts_end", "duration_s", "status",
             "rows_before", "rows_after", "delta", "tables", "error", "host"]


def _land_runs(records, log=print):
    import warehouse
    if not records:
        return
    warehouse.write_accumulate("source_runs", records, key=lambda r: r["run_id"], fields=SR_FIELDS)
    ok = sum(1 for r in records if r["status"] == "ok")
    bad = [r["source"] for r in records if r["status"] in ("failed", "timeout", "no-change")]
    log("[run_sources] %d run, %d ok -> source_runs%s" % (len(records), ok, ("  FAILED/NO-CHANGE: " + ", ".join(bad)) if bad else ""))


def run_all(cadence=None, only=None, headless_only=False, mac_only=False, workers=6, log=print):
    """Run the enabled sources: headless/creds in PARALLEL, then Mac (browser) sources SEQUENTIALLY."""
    src = [s for s in reg.SOURCES if s.get("enabled")]
    if only:
        want = set(only)
        src = [s for s in src if s["id"] in want]
    if cadence:
        src = [s for s in src if s.get("cadence") == cadence or cadence == "all"]
    headless = [s for s in src if s["klass"] in ("headless", "creds")]
    mac = [s for s in src if s["klass"] == "mac"]
    records = []

    if not mac_only and headless:
        log("[run_sources] %d headless sources (parallel x%d) …" % (len(headless), workers))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            records += list(ex.map(lambda s: run_one(s, log=log), headless))
        _land_runs(records, log=log)                       # land partial early so the console shows progress

    if not headless_only and mac:
        log("[run_sources] %d Mac/browser sources (SEQUENTIAL — anti-bot) …" % len(mac))
        for s in mac:
            rec = run_one(s, log=log)
            records.append(rec)
            _land_runs([rec], log=log)                      # land each immediately

    log("[run_sources] DONE — %d sources" % len(records))
    return records


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run all sources, verify landing, log outcomes.")
    ap.add_argument("--cadence", default="daily", help="daily | weekly | all")
    ap.add_argument("--only", default="", help="comma-separated source ids")
    ap.add_argument("--headless-only", action="store_true")
    ap.add_argument("--mac-only", action="store_true")
    a = ap.parse_args(argv)
    only = [x.strip() for x in a.only.split(",") if x.strip()] or None
    run_all(cadence=a.cadence, only=only, headless_only=a.headless_only, mac_only=a.mac_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
