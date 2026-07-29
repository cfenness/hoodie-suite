#!/usr/bin/env python3
"""run_journal.py — the LIVE record of a single run, written where anyone can read it.

The gap this closes: a pull runs on its own ephemeral Fly machine and its stdout dies with the machine.
Today the only trace left behind is one summary row in `source_runs_log` — so a run that lands nothing
leaves `status="empty", error=""` and no way to ask *why* without a scraper happening to persist its own
debug table (which is exactly how the kroger Akamai 403 stayed invisible for 9 consecutive runs).

A journal is one small JSON doc per run, in the SHARED warehouse bucket:

    _collect/runs/<run_id>.json

The producer (the ephemeral machine, via run_sources.run_one) heartbeats into it while the run is in
flight; the consumer (the app, serving Hoodie Collect) polls it. Same doc for a workbench-triggered run
and a dispatcher-scheduled one — so the bench shows SCHEDULED runs live too, not just its own.

Design rules, each one load-bearing:

  - **The journal can never break the run.** Every public call is wrapped; a storage failure degrades to
    a no-op and the pull continues. A telemetry layer that can fail the thing it observes is worse than
    no telemetry (see the "quiet degrades" rule in CLAUDE.md — this one degrades LOUDLY, into `warnings`).
  - **Writes are throttled, not per-line.** A chatty scraper emits thousands of lines; we hold the doc in
    memory and flush at most every `_MIN_FLUSH_S`. Status transitions and close() always flush immediately.
  - **Counts are labelled by provenance.** `rows_seen` is what the scraper CLAIMS mid-run (its own progress
    lines); `rows_landed` is what the warehouse actually holds. They are different numbers and the bench
    must never merge them.
  - **Every count names the table it was measured against.** A source's `tables` gets edited over time
    (kroger's ledger mixes counts of `kroger_products` (36,812) and `kroger_atlas_products` (0) under one
    source id), so a bare number is not comparable across history. The journal always carries `tables`.

Progress convention (opt-in, incremental — a scraper that never adopts it simply reports no `rows_seen`):
a scraper may print a line

    HOODIE_PROGRESS {"rows": 1234, "stage": "store 12/133", "pct": 9.0}

    rows/stage/pct populate the named counters; ANY other key is kept under `metrics` (rss_mb, ok,
    empty, unreachable, ...) so a run can report whatever it measures without the journal deciding
    for it what matters.

and the streamer folds it into the journal. Anything else printed is captured as ordinary log tail.
"""
import json
import os
import re
import time

_PREFIX = "_collect/runs"
_MIN_FLUSH_S = float(os.environ.get("JOURNAL_FLUSH_S", "3"))
_LOG_TAIL = int(os.environ.get("JOURNAL_LOG_TAIL", "600"))       # lines kept in the doc
_LINE_MAX = 2000                                                 # per-line clamp

PROGRESS_RE = re.compile(r"HOODIE_PROGRESS\s+(\{.*\})\s*$")
_METRIC_MAX = 40                          # bound the per-run metric set; a runaway emitter can't bloat the doc

# run_id -> {"doc": {...}, "last_flush": ts}
_OPEN = {}


def _key(run_id):
    return "%s/%s.json" % (_PREFIX, run_id)


def new_id(source_id):
    """A run id that sorts chronologically and names its source."""
    return "%s-%d" % (source_id, int(time.time() * 1000))


def _flush(run_id, force=False):
    st = _OPEN.get(run_id)
    if not st:
        return False
    now = time.time()
    if not force and (now - st["last_flush"]) < _MIN_FLUSH_S:
        return False
    doc = st["doc"]
    doc["updated_at"] = int(now)
    try:
        import warehouse
        warehouse.put_bytes(_key(run_id), json.dumps(doc, default=str).encode())
        st["last_flush"] = now
        return True
    except Exception as e:
        # Never raise into the run. Record that telemetry itself is degraded, so the bench can say
        # "this run's journal is incomplete" instead of silently showing a stale doc as current.
        w = doc.setdefault("warnings", [])
        msg = "journal write failed: %s" % str(e)[:120]
        if msg not in w:
            w.append(msg)
        return False


def open_run(run_id, source, label=None, klass=None, params=None, tables=None,
             rows_before=None, host=None, trigger="manual"):
    """Start a journal. `trigger` ∈ manual | scheduled | api — so the bench can tell a human-kicked run
    from a dispatcher tick. `rows_before`/`tables` pin what any later delta is measured against."""
    try:
        doc = {
            "run_id": run_id, "source": source, "label": label, "klass": klass,
            "trigger": trigger, "params": params or {},
            "status": "starting", "stage": None,
            "started_at": int(time.time()), "updated_at": int(time.time()), "finished_at": None,
            "host": host or (os.uname().nodename[:40] if hasattr(os, "uname") else None),
            "machine_id": os.environ.get("FLY_MACHINE_ID"),
            "tables": list(tables or []),
            "rows_before": rows_before or {},      # {table: count|None}  None = UNKNOWN, never 0
            "rows_landed": {},                     # {table: count|None}  observed in the warehouse
            "rows_seen": None,                     # scraper's own claim (HOODIE_PROGRESS)
            "pct": None,
            "log": [], "log_lines_total": 0,
            "exit_code": None, "error": None, "warnings": [],
        }
        _OPEN[run_id] = {"doc": doc, "last_flush": 0.0}
        _flush(run_id, force=True)
        return doc
    except Exception:
        return None


def note(run_id, **patch):
    """Merge fields into the doc (throttled flush). Status changes flush immediately."""
    try:
        st = _OPEN.get(run_id)
        if not st:
            return
        st["doc"].update(patch)
        _flush(run_id, force="status" in patch)
    except Exception:
        pass


def log(run_id, line):
    """Capture one output line. Folds a HOODIE_PROGRESS line into the counters instead of the tail."""
    try:
        st = _OPEN.get(run_id)
        if not st:
            return
        doc = st["doc"]
        line = (line or "").rstrip("\n")[:_LINE_MAX]
        doc["log_lines_total"] += 1
        m = PROGRESS_RE.search(line)
        if m:
            try:
                p = json.loads(m.group(1))
                if isinstance(p, dict):
                    if p.get("rows") is not None:
                        doc["rows_seen"] = p["rows"]
                    if p.get("stage") is not None:
                        doc["stage"] = str(p["stage"])[:120]
                    if p.get("pct") is not None:
                        doc["pct"] = p["pct"]
                    # KEEP EVERY OTHER METRIC. This handler used to whitelist rows/stage/pct and drop
                    # the rest — then return, so the extras did not survive as log text either. A run
                    # reporting MORE about itself had that detail discarded twice over, silently. That
                    # cost two blind rounds of OOM debugging: ue_catalog emitted rss_mb/ok/empty/
                    # unreachable in every heartbeat and none of it ever reached the journal. Same
                    # discard-nothing rule as the scrapers: the reporter does not get to decide that a
                    # field the run bothered to measure is uninteresting.
                    extra = {k: v for k, v in p.items() if k not in ("rows", "stage", "pct")}
                    if extra:
                        met = doc.setdefault("metrics", {})
                        for k, v in list(extra.items())[:_METRIC_MAX]:
                            met[str(k)[:40]] = v if isinstance(v, (int, float, bool, type(None))) \
                                else str(v)[:200]
                    _flush(run_id)
                    return
            except Exception:
                pass                       # malformed progress line → keep it as ordinary output
        doc["log"].append(line)
        del doc["log"][:-_LOG_TAIL]
        _flush(run_id)
    except Exception:
        pass


def close_run(run_id, status=None, record=None, rows_landed=None, error=None, exit_code=None):
    """Finalize. Always flushes. `record` is the run_sources run record, kept verbatim for the bench."""
    try:
        st = _OPEN.get(run_id)
        if not st:
            return None
        doc = st["doc"]
        doc["status"] = status or (record or {}).get("status") or "done"
        doc["finished_at"] = int(time.time())
        doc["duration_s"] = doc["finished_at"] - doc["started_at"]
        if rows_landed is not None:
            doc["rows_landed"] = rows_landed
        if error is not None:
            doc["error"] = str(error)[:600]
        elif (record or {}).get("error"):
            doc["error"] = str(record["error"])[:600]
        if exit_code is not None:
            doc["exit_code"] = exit_code
        if record:
            doc["record"] = record
        _flush(run_id, force=True)
        return dict(doc)
    finally:
        _OPEN.pop(run_id, None)


# ── consumer side (the app) ──────────────────────────────────────────────────────────────────────
# A run whose machine dies — destroyed, OOM-killed, evicted — never gets to write a terminal status, so
# its journal is frozen mid-flight saying "running". Read back naively that is a run which appears to be
# working forever: the exact lying status this bench exists to remove. Heartbeats are cheap and frequent
# (throttled to ~3s, and every landed batch writes), so silence well past that means nobody is home.
STALE_AFTER_S = float(os.environ.get("JOURNAL_STALE_AFTER_S", "900"))    # 15 min of no heartbeat
_LIVE = ("queued", "starting", "running")


def _mark_liveness(doc):
    """Annotate a doc with heartbeat age and, when it has gone quiet mid-run, say so instead of
    repeating a status the run can no longer be updating."""
    if not doc:
        return doc
    age = time.time() - (doc.get("updated_at") or doc.get("started_at") or 0)
    doc["heartbeat_age_s"] = int(age)
    if doc.get("status") in _LIVE and age > STALE_AFTER_S:
        doc["stale"] = True
        doc["reported_status"] = doc["status"]      # what it last claimed, kept for the record
        doc["status"] = "lost"
        doc["error"] = (doc.get("error") or "") or (
            "no heartbeat for %dm — the machine is gone (destroyed, OOM-killed or evicted) and the run "
            "never wrote a final status. Work already landed is kept; a rerun resumes from the "
            "checkpoint." % (age / 60))
    return doc


def read(run_id):
    """The journal doc for one run, or None. Liveness-checked: see _mark_liveness."""
    try:
        import warehouse
        raw = warehouse.get_bytes(_key(run_id))
        return _mark_liveness(json.loads(raw)) if raw else None
    except Exception:
        return None


def _list_keys():
    """Every journal key. Object-store listing, with a local-disk fallback for dev."""
    import warehouse
    if warehouse.remote():
        from pyarrow import fs as pafs
        fs = warehouse._s3fs()
        sel = pafs.FileSelector("%s/%s" % (warehouse._bucket(), _PREFIX), recursive=True,
                                allow_not_found=True)
        return [i.path.split("/", 1)[1] for i in fs.get_file_info(sel) if i.path.endswith(".json")]
    base = os.path.join(warehouse._LOCAL_DIR, _PREFIX)
    if not os.path.isdir(base):
        return []
    return ["%s/%s" % (_PREFIX, f) for f in os.listdir(base) if f.endswith(".json")]


def recent(limit=40, source=None, active_only=False):
    """Recent journals, newest first. Cheap-ish (one listing + N small reads) — the bench polls the
    ACTIVE ones, so keep `limit` tight when active_only is set."""
    try:
        keys = _list_keys()
    except Exception:
        return []
    # run ids embed epoch-ms, so the key name sorts chronologically without reading anything
    ids = sorted((k.rsplit("/", 1)[-1][:-5] for k in keys), key=_sort_key, reverse=True)
    if source:
        ids = [i for i in ids if i.rsplit("-", 1)[0] == source]
    # SCAN BOUND. active_only skips terminal runs but still has to READ each doc to know, so an
    # unbounded scan gets slower every day as journals accumulate — and this is on the bench's main
    # page load. Ids sort newest-first by embedded epoch-ms, and a run in flight is by definition
    # recent, so looking past the newest `scan` docs cannot find a live one.
    scan = int(os.environ.get("JOURNAL_SCAN_MAX", "60"))
    out = []
    for rid in ids[:max(scan, limit)]:
        if len(out) >= limit:
            break
        d = read(rid)
        if not d:
            continue
        if active_only and d.get("status") in ("ok", "done", "failed", "timeout", "empty", "lost",
                                               "no-change", "current", "incomplete", "no-creds"):
            continue
        out.append(d)
    return out


def _sort_key(run_id):
    try:
        return int(run_id.rsplit("-", 1)[-1])
    except Exception:
        return 0


_BENCH_STATE_KEY = "_collect/bench_state.json"


def bench_state():
    """The workbench's human-set state (confirmed / archived), from the shared bucket. {} if unreadable."""
    try:
        import warehouse
        raw = warehouse.get_bytes(_BENCH_STATE_KEY)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def manual_only():
    """True when NOTHING may auto-dispatch — the only way a job runs is a human pressing Run now in
    Hoodie Collect.

    The point is stability before automation: a source is proven by hand, watched end to end, and
    CONFIRMED in the bench; only then is it worth a scheduler. Automating sources whose reliability
    nobody has verified is how the pipeline filled with runs that reported clean while landing nothing.
    Once each source is known-good by hand, the scheduler is a small step rather than a leap of faith.

    Reads the bench state (togglable from the workbench) with a COLLECT_MANUAL_ONLY env override.
    FAILS OPEN — unreadable state means scheduling continues as before, because silently freezing all
    capture on a storage blip is far worse than one unattended run.
    """
    env = os.environ.get("COLLECT_MANUAL_ONLY")
    if env is not None:
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    try:
        return bool(bench_state().get("manual_only"))
    except Exception:
        return False


def archived_ids():
    """Source ids ARCHIVED in the workbench — deduped away and taken off the active list, so the
    dispatcher stops scheduling them.

    FAILS OPEN by design: if the state can't be read we return an empty set and everything keeps running.
    The opposite (treating an unreadable state as "archive everything") would silently halt the entire
    pipeline on a transient storage blip, which is far worse than one extra run of a retired source.
    """
    try:
        return set((bench_state().get("archived") or {}).keys())
    except Exception:
        return set()


def reconcile_live(live_machine_ids, log=print):
    """Flip runs whose MACHINE NO LONGER EXISTS to `lost`, immediately.

    A journal is written by the pull machine, so a machine that is destroyed (killed, OOM-reaped, or
    stopped by hand) never closes its own run — the doc sits at `running` until the staleness timer
    trips 15 minutes later. For that whole window the bench reports dead work as live, and "is it
    running?" is the first question anyone asks it. Observed: 8 stopped shards plus a stopped probe
    still reading `running` while a fresh fleet was already up, giving 17 live runs when 8 existed.

    Liveness is a fact about MACHINES, so ask the machine list rather than inferring from a heartbeat.
    Runs with no machine_id (never spawned, or a local run) are left alone — absence of an id is not
    evidence of death.
    """
    live = {str(m) for m in (live_machine_ids or []) if m}
    if not live:
        return 0                          # an empty/failed machine list must not mark everything dead
    n = 0
    for rid in _list_keys():
        try:
            doc = read(rid)
            if not doc or doc.get("status") not in _LIVE:
                continue
            mid = doc.get("machine_id")
            if not mid or str(mid) in live:
                continue
            doc["reported_status"] = doc.get("status")
            doc["status"] = "lost"
            doc["stale"] = True
            doc["error"] = doc.get("error") or "machine %s no longer exists — run did not close" % mid
            doc["finished_at"] = doc.get("finished_at") or int(time.time())
            doc["updated_at"] = int(time.time())
            import warehouse
            warehouse.put_bytes(_key(rid), json.dumps(doc, default=str).encode())
            n += 1
        except Exception:
            continue
    if n:
        log("journal: reconciled %d run(s) whose machine is gone -> lost" % n)
    return n


def prune(keep_days=14):
    """Drop journals older than keep_days. Journals are small but unbounded; the ledger is the
    permanent record, the journal is the live/recent detail."""
    cut = (time.time() - keep_days * 86400) * 1000
    dropped = 0
    try:
        import warehouse
        from pyarrow import fs as pafs                                   # noqa: F401  (remote only)
        for k in _list_keys():
            rid = k.rsplit("/", 1)[-1][:-5]
            if _sort_key(rid) and _sort_key(rid) < cut:
                try:
                    if warehouse.remote():
                        warehouse._s3fs().delete_file("%s/%s" % (warehouse._bucket(), k))
                    else:
                        os.remove(os.path.join(warehouse._LOCAL_DIR, k))
                    dropped += 1
                except Exception:
                    pass
    except Exception:
        pass
    return dropped
