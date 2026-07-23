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

Usage:  python3 run_ephemeral.py <source_id>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    if len(sys.argv) < 2:
        print("usage: run_ephemeral.py <source_id>")
        return 2
    sid = sys.argv[1]
    try:
        import kroger_api
        kroger_api._load_creds()
    except Exception:
        pass
    import run_sources
    import source_registry
    src = next((s for s in source_registry.SOURCES if s["id"] == sid), None)
    if not src:
        print("run_ephemeral: no source_registry entry %r" % sid)
        return 2
    print("run_ephemeral: starting %s (%s, klass=%s)" % (sid, src.get("label"), src.get("klass")), flush=True)
    rec = run_sources.run_one(src)
    try:
        run_sources._land_runs([rec])            # append the outcome to the shared source_runs_log ledger
    except Exception as e:
        print("run_ephemeral: ledger write skipped: %s" % str(e)[:100])
    print("run_ephemeral DONE %s status=%s rows_after=%s delta=%s err=%s"
          % (sid, rec.get("status"), rec.get("rows_after"), rec.get("delta"), (rec.get("error") or "")[:160]),
          flush=True)
    return 0 if rec.get("status") in ("ok", "no-change", "success") else 1


if __name__ == "__main__":
    sys.exit(main())
