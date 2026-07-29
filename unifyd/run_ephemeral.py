#!/usr/bin/env python3
"""run_ephemeral.py — run ONE source's registry entrypoint, land its run record, exit.

The body of an EPHEMERAL pull machine: a throwaway Fly Machine (spawned with `flyctl machine run --rm`)
runs `run_ephemeral.sh <source_id>` → this → `run_sources.run_one(source)` → the source's scraper writes
its catalog + observations to the warehouse → `_land_runs` logs the outcome → the machine self-destroys.

WHY (the isolation contract):
  - Every pull runs on its OWN machine, so a crashing/OOMing pull can never touch another running pull.
  - Adding a new source is a new source_registry row; its next pull gets its own machine — NOTHING else
    is redeployed or restarted, so existing crawls are never disturbed.
  - App deploys don't kill running crawls: ephemeral machines are not a process group, so `fly deploy`
    (which reconciles process-group machines) leaves them running to completion.

Usage:  python3 run_ephemeral.py <source_id> [--run-id ID] [--days N | --all] [--trigger manual|scheduled]

`--run-id` attaches a Hoodie Collect JOURNAL: the run heartbeats its status, streamed console output and
row counts to `_collect/runs/<id>.json` in the shared bucket, so the bench can watch a run that is
executing on a machine it has no other channel to. Without it the run behaves exactly as before (the
dispatcher passes one, so scheduled runs are watchable too).

`--days` / `--all` set the time window for a TIME-BOUND source, via the env knob the registry declares in
its `window` block (e.g. ttb-cola → TTB_DAYS). A source with no `window` declaration rejects them rather
than silently ignoring them — a window that is quietly dropped is how you get a "full" run that is really
the default 14-day slice.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _window_env(src, days, want_all):
    """Translate --days/--all into the source's declared env knob. Returns {} when nothing was asked.
    RAISES when a window was asked for and the source doesn't declare one — never silently ignore it."""
    if days is None and not want_all:
        return {}
    win = (src or {}).get("window")
    if not win:
        raise SystemExit("run_ephemeral: %s declares no `window` in source_registry — refusing to apply "
                         "--days/--all (it would be silently ignored)" % src.get("id"))
    env_key = win["env"]
    if want_all:
        if not win.get("all"):
            raise SystemExit("run_ephemeral: %s declares no 'all' value for %s" % (src.get("id"), env_key))
        return {env_key: str(win["all"])}
    return {env_key: str(int(days))}


def main():
    ap = argparse.ArgumentParser(prog="run_ephemeral.py")
    ap.add_argument("source_id")
    ap.add_argument("--run-id", default=None, help="Hoodie Collect journal id to stream into")
    ap.add_argument("--days", type=int, default=None, help="time-bound source: lookback window in days")
    ap.add_argument("--all", action="store_true", help="time-bound source: full history")
    ap.add_argument("--trigger", default="scheduled", choices=("manual", "scheduled", "api"))
    a = ap.parse_args()
    sid = a.source_id
    try:
        import kroger_api
        kroger_api._load_creds()
    except Exception:
        pass
    import run_sources
    import source_registry
    # Resolve the id in SOURCES first, then BUILDS — so a derived build (dim_sku chain, master_quality,
    # item_identity, the canon head-to-head) runs on its OWN ephemeral machine too, off the Mac. run_one
    # handles both (a build's `code` is `import X; m.build()`); the ledger treatment is identical.
    src = (next((s for s in source_registry.SOURCES if s["id"] == sid), None)
           or next((b for b in getattr(source_registry, "BUILDS", []) if b["id"] == sid), None))
    if not src:
        print("run_ephemeral: no source_registry entry %r" % sid)
        return 2
    overlay = _window_env(src, a.days, a.all)
    print("run_ephemeral: starting %s (%s, klass=%s)%s"
          % (sid, src.get("label"), src.get("klass"),
             (" window=%s" % overlay) if overlay else ""), flush=True)

    # ── journal: the live channel back to Hoodie Collect ────────────────────────────────────────
    # Best-effort throughout. A bench that can't be watched is a degraded bench; a pull that dies
    # because its telemetry failed is a data loss. The former is always the correct trade.
    jrn, rid = None, a.run_id
    on_line = None
    if rid:
        try:
            import run_journal
            import warehouse
            before = {}
            for t in src.get("tables", []):
                try:
                    before[t] = warehouse.row_count_strict(t) or None
                except Exception:
                    before[t] = None            # UNKNOWN, never 0 — a failed read is not an empty table
            jrn = run_journal
            jrn.open_run(rid, sid, label=src.get("label"), klass=src.get("klass"),
                         params={"days": a.days, "all": bool(a.all), "env": overlay},
                         tables=src.get("tables"), rows_before=before, trigger=a.trigger)
            jrn.note(rid, status="running")
            on_line = lambda ln: jrn.log(rid, ln)          # noqa: E731 — one-liner sink by design
        except Exception as e:
            print("run_ephemeral: journal unavailable (%s) — running unwatched" % str(e)[:100], flush=True)

    rec = run_sources.run_one(src, extra_env=overlay or None, on_line=on_line)

    if jrn and rid:
        try:
            after = {}
            import warehouse
            for t in src.get("tables", []):
                try:
                    after[t] = warehouse.row_count_strict(t) or None
                except Exception:
                    after[t] = None
            jrn.close_run(rid, status=rec.get("status"), record=rec, rows_landed=after)
        except Exception as e:
            print("run_ephemeral: journal close failed: %s" % str(e)[:100], flush=True)
    try:
        run_sources._land_runs([rec])            # append the outcome to the shared source_runs_log ledger
    except Exception as e:
        print("run_ephemeral: ledger write skipped: %s" % str(e)[:100])
    print("run_ephemeral DONE %s status=%s rows_after=%s delta=%s err=%s"
          % (sid, rec.get("status"), rec.get("rows_after"), rec.get("delta"), (rec.get("error") or "")[:160]),
          flush=True)
    # "current"/"no-change" = ran fine, nothing new to land; all non-failure outcomes exit 0.
    return 0 if rec.get("status") in ("ok", "no-change", "success", "current") else 1


if __name__ == "__main__":
    sys.exit(main())
