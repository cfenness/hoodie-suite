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
  python run_sources.py --due                    # SLO dispatcher: run ONLY what's past its interval

--due is the near-real-time dispatcher (NRT-PLAN.md §3): each source is due when its last attempt
in `source_runs` is older than its `interval_h` (registry; default daily=24h, weekly=168h). Because
source_runs is the SHARED warehouse ledger, due-ness is host-global — a source the cloud runner just
landed shows fresh to the Mac tick and is skipped, so hosts dedupe through the ledger instead of a
schedule matrix. Fire it often (launchd/cron every 30min); a lock file makes overlapping passes no-op.
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
_TIMEOUT = {"headless": 5400, "creds": 5400, "mac": 14400,   # 1.5h headless, 4h Mac (browser sweeps are long)
            "build": 10800}                                  # 3h for master builds (dim_sku chain)


def _rows(table):
    """Current row count of one table — layout-aware via warehouse.row_count (single-file footer,
    or the bucket manifest for v2 tables). None if the table doesn't exist yet."""
    import warehouse
    return warehouse.row_count(table) or None


def _counts(tables):
    return {t: _rows(t) for t in tables}


def _interval_h(source):
    """Refresh interval in hours: per-source `interval_h` override, else cadence default."""
    return float(source.get("interval_h") or (168 if source.get("cadence") == "weekly" else 24))


def ledger_last():
    """Per-source (last_attempt_ts, last_ok_end) unioned from BOTH ledgers: the append-only
    `source_runs_log` partitions (authoritative going forward) and the legacy `source_runs` table
    (history). Two dicts: {source: max ts_start} and {source: max ts_end where status='ok'}."""
    import warehouse
    last, last_ok = {}, {}
    sql = ("SELECT source, MAX(ts_start) AS ts, "
           "MAX(CASE WHEN status='ok' THEN ts_end END) AS ok_ts FROM t GROUP BY source")
    for fn, name in ((warehouse.query, "source_runs"), (warehouse.query_parts, "source_runs_log")):
        try:
            for r in fn(name, sql):
                sid = r["source"]
                last[sid] = max(last.get(sid, 0), float(r["ts"] or 0))
                if r.get("ok_ts"):
                    last_ok[sid] = max(last_ok.get(sid, 0), float(r["ok_ts"]))
        except Exception:
            pass
    return last, last_ok


def due_sources(now=None, grace=0.98):
    """The enabled sources whose interval has lapsed — last attempt (ts_start of ANY status in
    the ledger) older than interval_h * grace. The 2% grace keeps a fixed tick (e.g. a 24h-interval
    source checked every 30min) from slipping a full tick each day. Never-run sources are due."""
    now = now or time.time()
    last, _ = ledger_last()
    return [s for s in reg.SOURCES if s.get("enabled")
            and now - last.get(s["id"], 0) >= _interval_h(s) * 3600 * grace]


def due_builds(now=None):
    """Derived master builds due (NRT-PLAN §4/Phase 3): an upstream source landed NEW rows
    (status 'ok' — delta moved, not just 'current') since the build's last attempt, and the
    build's min gap (interval_h) has passed. Same ledger, so the master lags any landing by at
    most one dispatcher cycle without ever rebuilding when nothing changed."""
    now = now or time.time()
    last_attempt, last_ok = ledger_last()
    if not last_ok:
        return []                        # nothing has ever landed -> nothing to build
    out = []
    for b in getattr(reg, "BUILDS", []):
        if not b.get("enabled"):
            continue
        mine = last_attempt.get(b["id"], 0)
        if now - mine < float(b.get("interval_h") or 6) * 3600:
            continue
        ups = b.get("after") or [s["id"] for s in reg.SOURCES if s.get("enabled")]
        if max((last_ok.get(u, 0) for u in ups), default=0) > mine:
            out.append(b)
    return out


def mac_window_open(now=None):
    """True when the Mac browser window is open (default 20:00–08:00 local, env MAC_HOURS='20-8').
    The anti-bot sources need a QUIET Mac — Cloudflare/DataDome/Incapsula degrade under daytime
    browser contention (why the old schedule ran at 03:00) — so daytime --due ticks stay
    headless-only and the browser sweeps catch up when the window opens. Explicit --mac-only
    bypasses this (a human asking is the override)."""
    spec = os.environ.get("MAC_HOURS", "20-8")
    try:
        start, end = (int(x) for x in spec.split("-"))
    except Exception:
        start, end = 20, 8
    h = time.localtime(now or time.time()).tm_hour
    return (start <= h or h < end) if start > end else (start <= h < end)


def _acquire_lock():
    """One dispatcher pass at a time (fcntl, non-blocking): a 30-min tick that fires while a 4-hour
    Mac browser sweep is still running must no-op, not stack a second Chrome. Returns the held file
    (keep a reference — GC releases the lock) or None if another pass holds it."""
    import fcntl
    path = os.path.join(HERE, "agent_state", "run_sources.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lf = open(path, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lf.close()
        return None
    lf.write("%d\n" % os.getpid())
    lf.flush()
    return lf


def run_one(source, log=print):
    """Run one source in a subprocess, measure before/after row counts, classify the outcome."""
    sid = source["id"]
    t0 = time.time()
    # Skip-with-reason: a source gated on credentials we don't have must report honestly ("no-creds"),
    # NOT run and fail — otherwise it wastes a slot and (pre-guard) risked clobbering a good table with empty.
    missing = [v for v in source.get("requires", []) if not os.environ.get(v)]
    if missing:
        a = sum(v for v in _counts(source["tables"]).values() if v) or 0
        log("  %-16s %-9s %s" % (sid, "no-creds", "| missing " + ", ".join(missing)))
        return dict(run_id="%s-%d" % (sid, int(t0)), source=sid, label=source["label"], klass=source["klass"],
                    ts_start=int(t0), ts_end=int(time.time()), duration_s=0.0, status="no-creds",
                    rows_before=a, rows_after=a, delta=0, tables=",".join(source["tables"]),
                    error="missing env: " + ", ".join(missing), host=os.uname().nodename[:40])
    before = _counts(source["tables"])
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
            # Keep the CRASH SITE, not just the message: the last traceback 'File "…", line N' frame plus
            # the final line. One stripped message line ("utf-8 codec can't decode…") left the specs crash
            # unlocatable; the frame makes the next intermittent failure a pinpointed diagnosis.
            out = (r.stderr or r.stdout or "").strip()
            if out:
                lines = out.splitlines()
                frames = [l.strip() for l in lines if l.strip().startswith('File "')]
                error = (" | ".join(frames[-1:] + [lines[-1]]))[:300]
            else:
                error = "nonzero exit"
    except subprocess.TimeoutExpired:
        status, error = "timeout", "exceeded %ds" % _TIMEOUT.get(source["klass"], 5400)
    except Exception as e:
        status, error = "failed", str(e)[:300]
    after = _counts(source["tables"])
    dur = round(time.time() - t0, 1)

    b = sum(v for v in before.values() if v) or 0
    a = sum(v for v in after.values() if v) or 0
    delta = a - b
    # VERIFY LANDING, honestly: a clean run that added rows = ok. Added none but the table HAS data = "current"
    # (a stable re-pull — e.g. a 55k catalog with nothing new — is fine, not a failure). Added none and the table
    # is EMPTY = "empty" (genuinely broken — nothing was ever captured). Only "empty" and errors are real problems.
    if status == "ok" and delta <= 0:
        status = "current" if a > 0 else "empty"
    rec = dict(run_id="%s-%d" % (sid, int(t0)), source=sid, label=source["label"], klass=source["klass"],
               ts_start=int(t0), ts_end=int(time.time()), duration_s=dur, status=status,
               rows_before=b, rows_after=a, delta=delta, tables=",".join(source["tables"]),
               error=error, host=os.uname().nodename[:40])
    log("  %-16s %-9s Δ%-10s %5ss %s" % (sid, status, ("%+d" % delta if delta else "0"), dur,
                                         ("| " + error) if error else ""))
    return rec


SR_FIELDS = ["run_id", "source", "label", "klass", "ts_start", "ts_end", "duration_s", "status",
             "rows_before", "rows_after", "delta", "tables", "error", "host"]


def _land_runs(records, log=print):
    """Land run outcomes APPEND-ONLY: one immutable partition file per landing, never a rewrite.

    Learned 2026-07-21: the old read-modify-write accumulate raced the cloud runner — two hosts
    landing concurrently clobbered each other's whole-table rewrites, a full catch-up pass vanished
    from the ledger, and everything re-ran. An append can never lose another host's writes. Readers
    union this log with the legacy `source_runs` table (ledger_last / monitor)."""
    import warehouse
    if not records:
        return
    part = "%d_%s_%d" % (int(time.time() * 1000), os.uname().nodename.split(".")[0][:20], os.getpid())
    warehouse.write_partition("source_runs_log", part, records, fields=SR_FIELDS)
    ok = sum(1 for r in records if r["status"] == "ok")
    bad = [r["source"] for r in records if r["status"] in ("failed", "timeout", "no-change")]
    log("[run_sources] %d run, %d ok -> source_runs_log%s" % (len(records), ok, ("  FAILED/NO-CHANGE: " + ", ".join(bad)) if bad else ""))


def run_all(cadence=None, only=None, exclude=None, headless_only=False, mac_only=False, workers=6, log=print):
    """Run the enabled sources: headless/creds in PARALLEL, then Mac (browser) sources SEQUENTIALLY.
    `exclude` drops source ids (e.g. browser-on-ISP sources that can't run in a no-Chrome cloud runner)."""
    src = [s for s in reg.SOURCES if s.get("enabled")]
    if only:
        want = set(only)
        src = [s for s in src if s["id"] in want]
    if exclude:
        skip = set(exclude)
        src = [s for s in src if s["id"] not in skip]
    if cadence:
        src = [s for s in src if s.get("cadence") == cadence or cadence == "all"]
    headless = [s for s in src if s["klass"] in ("headless", "creds")]
    # Mac (browser) sources honor `priority` (lower first): the long aggregator sweeps go first, the
    # contention-sensitive anti-bot trio (7-Eleven/CityHive/Bottlecapps) last — the run_mac_queue.sh
    # ordering, now expressed in the registry instead of a wrapper script.
    mac = sorted([s for s in src if s["klass"] == "mac"], key=lambda s: s.get("priority", 50))
    records = []

    if not mac_only and headless:
        log("[run_sources] %d headless sources (parallel x%d) …" % (len(headless), workers))
        from concurrent.futures import as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(run_one, s, log): s for s in headless}
            for fut in as_completed(futs):                 # land EACH result the moment it finishes — the console
                try:                                       # updates live instead of waiting for the slowest source
                    rec = fut.result()
                except Exception as e:
                    s = futs[fut]; rec = dict(run_id="%s-err" % s["id"], source=s["id"], label=s["label"],
                                              klass=s["klass"], status="failed", error=str(e)[:200], delta=0,
                                              rows_before=0, rows_after=0, ts_start=0, ts_end=int(time.time()),
                                              duration_s=0, tables=",".join(s["tables"]), host="")
                records.append(rec)
                _land_runs([rec], log=log)

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
    ap.add_argument("--exclude", default="", help="comma-separated source ids to skip")
    ap.add_argument("--headless-only", action="store_true")
    ap.add_argument("--mac-only", action="store_true")
    ap.add_argument("--workers", type=int, default=6, help="parallel headless workers (lower on RAM-limited cloud runners)")
    ap.add_argument("--due", action="store_true", help="SLO dispatcher: run only sources past their interval_h")
    a = ap.parse_args(argv)
    only = [x.strip() for x in a.only.split(",") if x.strip()] or None
    exclude = [x.strip() for x in a.exclude.split(",") if x.strip()] or None
    if a.due:
        lock = _acquire_lock()
        if not lock:
            print("[run_sources] another dispatcher pass holds the lock — nothing to do.")
            return 0
        due = due_sources()
        if only:
            due = [s for s in due if s["id"] in set(only)]
        headless_only = a.headless_only
        if not a.mac_only and not headless_only and not mac_window_open():
            n_mac = sum(1 for s in due if s["klass"] == "mac")
            if n_mac:
                print("[run_sources] mac window closed (MAC_HOURS=%s) — deferring %d browser "
                      "source(s) to the window" % (os.environ.get("MAC_HOURS", "20-8"), n_mac))
            headless_only = True
        if due:
            print("[run_sources] due: " + ", ".join(s["id"] for s in due))
            run_all(cadence="all", only=[s["id"] for s in due], exclude=exclude,
                    headless_only=headless_only, mac_only=a.mac_only, workers=a.workers)
        else:
            print("[run_sources] no sources due.")
        # Derived master builds run AFTER the landings that triggered them — and only on the plain
        # --due host (the Mac tick): the --headless-only cloud runner skips them so dim_* keeps a
        # single writer. A build that misses this pass fires on the next tick via the ledger.
        if not (a.headless_only or a.mac_only):
            builds = due_builds()
            if builds:
                print("[run_sources] builds due: " + ", ".join(b["id"] for b in builds))
                for b in builds:
                    _land_runs([run_one(b)])
        return 0
    run_all(cadence=a.cadence, only=only, exclude=exclude, headless_only=a.headless_only,
            mac_only=a.mac_only, workers=a.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
