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
import re
import subprocess
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import source_registry as reg

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
_TIMEOUT = {"headless": 5400, "creds": 5400, "mac": 14400,   # 1.5h headless, 4h Mac (browser sweeps are long)
            "build": 10800}                                  # 3h for master builds (dim_sku chain)


def _rows(table):
    """Current row count of one table — layout-aware via warehouse.row_count_strict (single-file
    footer, or the bucket manifest for v2 tables). None if the table genuinely doesn't exist yet.
    RAISES warehouse.RowCountUnavailable on a transient/unknown storage read failure, so the caller
    can tell 'unknown' from 'empty' and never fabricate a drop from a blip."""
    import warehouse
    return warehouse.row_count_strict(table) or None


def _counts(tables):
    """BEFORE-run (or creds-skip) counts. A table we can't read is left UNKNOWN (None), never 0."""
    import warehouse
    out = {}
    for t in tables:
        try:
            out[t] = _rows(t)
        except warehouse.RowCountUnavailable:
            out[t] = None
    return out


def _counts_after(tables, before, log=print):
    """AFTER-run counts, hardened against a transient read blip. A table whose count can't be read is
    assumed UNCHANGED (its before count), NEVER 0 — a guarded warehouse write cannot zero or shrink a
    populated table, so a post-run '0' is a failed read, not a real clobber. Conflating the two is
    what filled the ledger with false 'empty'/'-N clobber' records (haskells 10518->0 while the file
    was intact) and made healthy pulls look like 'ran but didn't land'."""
    import warehouse
    out = {}
    for t in tables:
        try:
            out[t] = _rows(t)
        except warehouse.RowCountUnavailable as e:
            out[t] = before.get(t)
            log("  %-16s after-count unreadable (%s) — assuming UNCHANGED, not a clobber" % (t, str(e)[:70]))
    return out


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
    source checked every 30min) from slipping a full tick each day. Never-run sources are due.

    SELF-HEAL: also includes FAILED sources whose escalating backoff has elapsed (selfheal.retry_due_ids),
    so a transient failure retries in MINUTES instead of waiting a whole cadence — until it recovers or is
    quarantined to a daily probe. Same shared ledger; degrades to interval-only if selfheal is unavailable."""
    now = now or time.time()
    last, _ = ledger_last()
    due = [s for s in reg.SOURCES if s.get("enabled")
           and now - last.get(s["id"], 0) >= _interval_h(s) * 3600 * grace]
    try:
        import selfheal
        retry = selfheal.retry_due_ids(now)
    except Exception:
        retry = set()
    if retry:
        have = {s["id"] for s in due}
        due += [s for s in reg.SOURCES if s.get("enabled") and s["id"] in retry and s["id"] not in have]
    try:
        import run_journal
        # MANUAL-ONLY: nothing is due, because the only way a job runs is a human pressing Run now in
        # Hoodie Collect. Enforced HERE, at the single place due-ness is decided, so every scheduler
        # (the hourly Fly dispatcher, the in-app tick, a --due CLI pass) is covered by one switch
        # rather than three that can drift apart. A manual run does not consult due_sources at all, so
        # it is unaffected. selfheal's retry backoff also stops proposing work — under this rule an
        # `incomplete` source waits for a human to press Run now again, and resume makes that continue
        # from the checkpoint rather than restart.
        if run_journal.manual_only():
            return []
        # ARCHIVED in Hoodie Collect = deduped away and taken OFF THE ACTIVE LIST, so the dispatcher
        # stops scheduling it. Without this, "archived" would only mean "hidden" and a retired
        # duplicate would keep burning machines every tick.
        arch = run_journal.archived_ids()
        if arch:
            due = [s for s in due if s["id"] not in arch]
    except Exception:
        pass                      # fails OPEN — unreadable state must never halt the whole pipeline
    return due


def should_build(headless_only, mac_only, builds=False, no_builds=False):
    """Does THIS --due host run the derived builds? (dim_* single-writer.) Explicit ``no_builds`` wins,
    then explicit ``builds``, else the default: the plain host builds; --headless-only/--mac-only don't.
    The override pair is how builds move to the cloud runner (cloud=--builds, Mac=--no-builds) with no
    cross-host race even if both hosts tick."""
    if no_builds:
        return False
    if builds:
        return True
    return not (headless_only or mac_only)


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
    """One dispatcher pass per HOST (fcntl, non-blocking): a 30-min tick that fires while a 4-hour
    Mac browser sweep is still running must no-op, not stack a second Chrome. The lock lives at a
    MACHINE-GLOBAL path (~/.hoodie/run_sources.lock), not under the checkout — a per-checkout lock
    let a tick from the launchd checkout run concurrently with a pass from a worktree (learned
    2026-07-21). Returns the held file (keep a reference — GC releases the lock) or None."""
    import fcntl
    path = os.path.join(os.path.expanduser("~"), ".hoodie", "run_sources.lock")
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


def _exec(code, timeout_s, env, on_line=None):
    """Run the source's entrypoint as a subprocess and return a subprocess.run-shaped result.

    ONE execution path for every caller (dispatcher tick, CLI pass, Hoodie Collect) — deliberately not a
    streaming fork alongside the original. Two code paths for "run a source" is precisely how the /api/run
    handlers drifted from the registry and got the thin kroger_api run instead of the real atlas bypass;
    this doesn't reintroduce that shape.

    stdout and stderr are MERGED into one pipe so the captured output is in true interleaved order (a
    console showing progress lines out of order relative to the traceback is worse than none). The merged
    text is returned as `.stderr`, which is what run_one's crash-site extraction reads first — so outcome
    classification behaves exactly as it did under capture_output=True.

    `on_line` (optional) receives each line AS IT ARRIVES — the live console. It is wrapped here
    regardless of its own error handling: telemetry must never be able to kill a pull.
    """
    import collections
    proc = subprocess.Popen([PY, "-c", code], cwd=HERE, env=env, text=True, bufsize=1,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Bounded retention: enough tail to recover a traceback + crash site, without holding a multi-hundred-MB
    # crawl log in RAM on a 4GB machine. The journal keeps its own (smaller) display tail.
    tail = collections.deque(maxlen=4000)
    deadline = time.time() + timeout_s
    timed_out = False
    try:
        for line in proc.stdout:
            tail.append(line)
            if on_line:
                try:
                    on_line(line)
                except Exception:
                    pass
            if time.time() > deadline:
                timed_out = True
                break
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
    if timed_out:
        proc.kill()
        proc.wait()
        raise subprocess.TimeoutExpired(cmd="run_one", timeout=timeout_s)
    try:
        proc.wait(timeout=max(1, deadline - time.time()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    return types.SimpleNamespace(returncode=proc.returncode, stdout="", stderr="".join(tail))


def run_one(source, log=print, extra_env=None, on_line=None):
    """Run one source in a subprocess, measure before/after row counts, classify the outcome.
    `extra_env` overlays the subprocess env (e.g. the scheduler forces RESI_ISP_ONLY=1 so an unattended
    run can never open the per-GB residential-proxy tab).
    `on_line` streams the run's output line-by-line to a caller-supplied sink (Hoodie Collect's journal),
    so a run's console survives the ephemeral machine it ran on."""
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
    # HEADFUL on Fly needs a non-datacenter exit — the gates (Kroger Akamai, CityHive Cloudflare, PX) flag the
    # Fly datacenter IP even with a real browser. FREE-FIRST (CLAUDE.md): use the flat-rate ISP pool (fixed
    # per-IP, unlimited bandwidth — the tier that already clears the DoorDash/UE cracks), NOT the per-GB
    # residential session, which is paygo-gated OFF unless FETCH_POLICY=paid. One STICKY exit per source
    # (isp_url key) so the cookie warm and the pull that replays it share an IP — anti-bot cookies are IP-bound.
    # No pool AND no paid opt-in → run bare (may be blocked; that's the honest state, not a silent paid tab).
    if source["klass"] == "mac" and not os.environ.get("BROWSER_PROXY"):
        try:
            import resi
            px = (resi.isp_url("hf-" + sid) if resi.isp_enabled()
                  else (resi._session_url("hf-" + sid) if resi.enabled() else ""))
            if px:
                os.environ["BROWSER_PROXY"] = px
                log("  %-16s headful → %s exit (sticky)" % (sid, "ISP-pool" if resi.isp_enabled() else "residential(paid)"))
            else:
                log("  %-16s headful: no ISP pool + paid off → bare datacenter IP (may be blocked)" % sid)
        except Exception as e:
            log("  %-16s proxy setup error: %s" % (sid, str(e)[:70]))
    # PREP: sources gated on an anti-bot cookie (Kroger Akamai, …) warm it in a real headful browser FIRST,
    # then the pull subprocess inherits the fresh cookie env (see cookie_warm.apply_prep). Runs in-process on
    # this box (which has Chrome+Xvfb — the ephemeral pull machine). A warm failure doesn't abort:
    # the pull just runs cookie-less and reports degraded/no-creds, honestly, instead of being skipped blind.
    if source.get("cookie"):
        try:
            import cookie_warm
            cookie_warm.apply_prep(source, log=log)
        except Exception as e:
            log("  %-16s cookie prep error: %s" % (sid, str(e)[:100]))
    before = _counts(source["tables"])
    code = ("import sys; sys.path.insert(0, %r); import kroger_api; kroger_api._load_creds(); %s"
            % (HERE, source["code"]))
    if source["klass"] == "mac":
        for lk in glob.glob(os.path.expanduser("~/.hoodie_browser_profiles/*/Singleton*")):
            try:
                os.remove(lk)
            except Exception:
                pass
    # FAIL CLASS is STRUCTURAL, not inferred. This function already knows exactly what happened — the
    # signal that killed the child, whether the timeout fired, what coverage said — so it records the
    # class as a fact rather than leaving selfheal to regex it back out of a prose error string later.
    # A scraper that knows something we cannot see (an HTTP 403 wall) declares it on stdout with a
    # `HOODIE_FAIL {"class": "..."}` line, which is read below. Inference is the LEGACY fallback for
    # ledger rows written before this field existed, not the mechanism.
    fail_class = None
    r = None                      # bound even if _exec raises, so the scan below is safe
    status, error = "ok", ""
    timeout_s = source.get("timeout") or _TIMEOUT.get(source["klass"], 5400)   # registry per-source override
    run_token = "%s-%d" % (sid, int(t0))
    # PYTHONUNBUFFERED is what makes the live console actually live. The child writes to a PIPE, not a
    # tty, so CPython block-buffers its stdout (~8KB): every plain `print()` in a scraper sits in that
    # buffer and reaches the streamer only when the buffer fills or the process EXITS. Measured: three
    # lines printed 0.6s apart all arrived together at exit. For a 4-hour crawl that means a console
    # that stays empty for four hours and then dumps everything — the exact opposite of watching a run.
    # (HOODIE_PROGRESS lines used flush=True and so leaked through, which would have made the counters
    # look like they worked while the log stayed blank — a confusing half-broken state.)
    env = dict(os.environ, HOODIE_RUN_TOKEN=run_token, PYTHONUNBUFFERED="1",
               **(extra_env or {}))                                          # coverage stamp + overlays
    try:
        r = _exec(code, timeout_s, env, on_line=on_line)
        if r.returncode != 0:
            status = "failed"
            # A NEGATIVE returncode = killed by a signal, NOT a Python exception (subprocess convention).
            # This is the specs red herring: the 40k-product crawl was OOM-killed (SIGKILL/-9) and the old
            # capture grabbed the last stdout line — which was a CAUGHT per-page "'utf-8' codec…" log from a
            # worker thread — and mislabeled it as the crash. Report the signal honestly instead of echoing
            # unrelated output. -9 under a big in-memory crawl = OOM; that's a resource verdict, not a bug.
            if r.returncode < 0:
                import signal as _sig
                try:
                    nm = _sig.Signals(-r.returncode).name
                except Exception:
                    nm = "SIG%d" % (-r.returncode)
                error = "killed by %s (%d)%s" % (nm, -r.returncode,
                                                 " — OOM likely; reduce crawl memory/concurrency" if -r.returncode == 9 else "")
                if -r.returncode == 9:
                    fail_class = "oom"        # SIGKILL under a big crawl IS the OOM killer — not a guess
            else:
                # Real nonzero EXIT: keep the CRASH SITE — the last traceback 'File "…", line N' frame plus the
                # final message line. Only trust a message line when there IS a traceback; otherwise a caught,
                # logged worker error must not masquerade as the fatal one.
                out = (r.stderr or "").strip() or (r.stdout or "").strip()
                lines = out.splitlines() if out else []
                frames = [l.strip() for l in lines if l.strip().startswith('File "')]
                if frames:
                    error = (" | ".join([frames[-1], lines[-1]]))[:300]
                elif "Traceback (most recent call last)" in out:
                    error = lines[-1][:300]
                else:
                    error = "nonzero exit %d (no traceback — see run log)" % r.returncode
    except subprocess.TimeoutExpired:
        status, error = "timeout", "exceeded %ds" % timeout_s
        fail_class = "timeout"
    except Exception as e:
        status, error = "failed", str(e)[:300]
    # A scraper may DECLARE its failure class — it is the only thing that saw the HTTP status behind an
    # anti-bot wall. Structural: an explicit statement from the run, not a pattern matched against prose.
    if not fail_class:
        m = re.search(r'HOODIE_FAIL\s+(\{.*?\})', (r.stderr if r is not None else "") or "", re.S)
        if m:
            try:
                k = (json.loads(m.group(1)) or {}).get("class")
                if k:
                    fail_class = str(k)[:24]
            except Exception:
                pass
    after = _counts_after(source["tables"], before, log=log)
    dur = round(time.time() - t0, 1)

    b = sum(v for v in before.values() if v) or 0
    a = sum(v for v in after.values() if v) or 0
    delta = a - b
    # VERIFY LANDING, honestly: a clean run that added rows = ok. Added none but the table HAS data = "current"
    # (a stable re-pull — e.g. a 55k catalog with nothing new — is fine, not a failure). Added none and the table
    # is EMPTY = "empty" (genuinely broken — nothing was ever captured). Only "empty" and errors are real problems.
    if status == "ok" and delta <= 0:
        status = "current" if a > 0 else "empty"
    # COVERAGE: how much of the source's universe THIS run actually touched (expected vs landed store/item
    # counts) — the honest signal a cumulative merge can't give. A run can be 'current'/'ok' by row-count yet
    # 'partial' by coverage (blocked mid-crawl); this is what surfaces that. Best-effort — never fails a run.
    # PARTIAL COVERAGE IS A FAILURE. Landing *some* of the outlets and items is not a successful pull —
    # a customer buying this data gets a catalog with holes in it, and a run that reports `ok` on 60% of
    # the shelf is the product lying about itself. So a partial coverage verdict downgrades the run to
    # `incomplete`, which selfheal treats as retryable: the source is re-dispatched on the escalating
    # backoff and, because long crawls now checkpoint and resume, each retry CONTINUES rather than
    # restarting. Nothing stops on incompleteness — it keeps going until the catalog is actually covered.
    cov = {}
    try:
        import coverage as _cov
        cv = _cov.assess(source)
        if status in ("ok", "current") and (cv["items"]["verdict"] == "partial"
                                            or cv["stores"]["verdict"] == "partial"):
            status = "incomplete"
            fail_class = fail_class or "incomplete"
        cov = dict(cov_basis=cv["basis"],
                   landed_items=cv["items"]["landed"], expected_items=cv["items"]["expected"],
                   cov_items_pct=cv["items"]["pct"], cov_items=cv["items"]["verdict"],
                   landed_stores=cv["stores"]["landed"], expected_stores=cv["stores"]["expected"],
                   cov_stores_pct=cv["stores"]["pct"], cov_stores=cv["stores"]["verdict"])
    except Exception:
        cv = None
    # CAPABILITY: the source's optional libraries are imported behind `except: return []` guards so a
    # partial install degrades instead of crashing — which means a MISSING one is otherwise invisible and
    # the run reports clean while quietly producing worse data (pylibdmtx absent → every label read
    # QR-only, reported as full 2D coverage). Declared per-source as `caps=[…]`, the exact mirror of the
    # `requires=[env]` → "no-creds" convention. Nothing is skipped and no data is lost; the degradation
    # just stops being silent. Best-effort — a probe failure must never fail a run.
    caps_missing = []
    try:
        import capability as _cap
        caps_missing = _cap.warnings_for(source.get("caps", []))
        if caps_missing:
            for w in caps_missing:
                log("  %-16s %-9s %s" % (sid, "degraded", "| " + w))
            if status in ("ok", "current"):
                status = "degraded"
            error = " | ".join([e for e in ([error] if error else []) + caps_missing])[:300]
    except Exception:
        pass
    if status == "empty" and not fail_class:
        fail_class = "empty"
    rec = dict(run_id=run_token, source=sid, label=source["label"], klass=source["klass"],
               ts_start=int(t0), ts_end=int(time.time()), duration_s=dur, status=status,
               rows_before=b, rows_after=a, delta=delta, tables=",".join(source["tables"]),
               error=error, fail_class=fail_class or "", host=os.uname().nodename[:40],
               caps_missing=",".join(sorted(_cap.missing(source.get("caps", [])))) if caps_missing else "",
               **cov)
    covnote = ""
    if cv and (cv["items"]["verdict"] == "partial" or cv["stores"]["verdict"] == "partial"):
        covnote = " ⚠cov %s/%s items · %s/%s stores" % (cv["items"]["landed"], cv["items"]["expected"],
                                                        cv["stores"]["landed"], cv["stores"]["expected"])
    log("  %-16s %-9s Δ%-10s %5ss %s%s" % (sid, status, ("%+d" % delta if delta else "0"), dur,
                                           ("| " + error) if error else "", covnote))
    return rec


SR_FIELDS = ["run_id", "source", "label", "klass", "ts_start", "ts_end", "duration_s", "status",
             "rows_before", "rows_after", "delta", "tables", "error", "host",
             # coverage (expected vs landed store/item counts for THIS run — the partial-scrape signal)
             "cov_basis", "landed_items", "expected_items", "cov_items_pct", "cov_items",
             "landed_stores", "expected_stores", "cov_stores_pct", "cov_stores"]


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

    # post-pass: normalization scout over the freshly-landed tables (read-only; proposals → normalization_
    # findings). Subprocess + best-effort so a scout failure can never poison the run log above.
    try:
        log("[run_sources] normalization scout …")
        r = subprocess.run([PY, os.path.join(HERE, "normalization_scout.py")],
                           capture_output=True, text=True, timeout=1800)
        tail = (r.stdout or "").strip().splitlines()
        log("[run_sources] scout: %s" % (tail[-1] if tail else "no output (rc=%s)" % r.returncode))
    except Exception as e:
        log("[run_sources] scout skipped: %s" % str(e)[:120])
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
    # Build-host gate (single writer for dim_* / derived tables). Default = the plain --due host builds; the
    # --headless-only/--mac-only hosts don't. To MOVE builds off the Mac to the cloud runner: run the cloud
    # tick with `--builds` and the Mac tick with `--no-builds` — explicit, so exactly one host builds even if
    # both run --due (no cross-host race; the fcntl lock is per-host).
    ap.add_argument("--builds", action="store_true", help="force derived builds ON this host (the single build writer, e.g. the cloud runner)")
    ap.add_argument("--no-builds", action="store_true", help="force derived builds OFF this host (e.g. the Mac tick once the cloud runner owns builds)")
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
        # Derived master builds run AFTER the landings that triggered them, on the SINGLE build-writer host
        # (dim_* single-writer). Default: the plain --due host builds, the --headless-only/--mac-only hosts
        # don't. `--builds`/`--no-builds` override that explicitly so builds can move to the cloud runner
        # (cloud: --builds; Mac: --no-builds). A build that misses a pass fires on the next tick via the ledger.
        if should_build(a.headless_only, a.mac_only, a.builds, a.no_builds):
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
