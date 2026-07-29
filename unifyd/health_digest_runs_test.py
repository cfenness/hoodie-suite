#!/usr/bin/env python3
"""health_digest_runs_test.py — the daily verdict must read the ledger runs are actually WRITTEN to.

`_land_runs` moved to the append-only `source_runs_log` partitions on 2026-07-21. Every other
consumer — `ledger_last`, `monitor`, `selfheal`, `cost_ledger` — was updated to union both ledgers.
`health_digest._latest_source_runs` was not, and kept reading the legacy `source_runs` table alone.

Measured in production 2026-07-29 (app machine, real warehouse):

    source_runs      42 rows   17 of 53 enabled sources   newest ts_end 8.0 DAYS old
    source_runs_log 316 rows   53 of 53 enabled sources   newest ts_end current

so `run-failed` / `run-degraded` / `run-no-change` were judging a third of the fleet on a week-old
snapshot and the rest not at all — seven enabled sources sat failed/timeout in the log and the digest
could see none of them. A check with nothing to judge reports healthy. That is the failure class the
digest exists to catch, so it is worth a test rather than a comment.

Pure and stdlib-only: `_latest_source_runs` reads the warehouse through exactly two functions, so the
union logic is tested by stubbing those two. No pyarrow/duckdb, no network — this one always runs.

    python3 unifyd/health_digest_runs_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import health_digest

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


NOW = time.time()


def rec(rid, source, status, ts_end, error=""):
    return dict(run_id=rid, source=source, ts_start=ts_end - 60, ts_end=ts_end,
                duration_s=60, status=status, delta=0, error=error)


# Legacy table: an OLD ok row for a source that has since broken, plus a source only it knows.
LEGACY = [rec("old-1", "binnys", "ok", NOW - 8 * 86400),
          rec("old-2", "legacy-only", "ok", NOW - 8 * 86400)]
# Append-only log: today's real outcomes, including sources the legacy table never had.
LOG = [rec("new-1", "binnys", "failed", NOW - 3600, "killed by SIGKILL (9) — OOM likely"),
       rec("new-2", "sevennow", "failed", NOW - 3600, "AttributeError in sevennow.py"),
       rec("new-3", "cityhive", "timeout", NOW - 3600, "exceeded 14400s")]


def install(legacy, log):
    """Stub the two readers. `missing=None` makes that ledger raise, as an absent table does."""
    def reader(rows):
        def fn(_name, _sql):
            if rows is None:
                raise RuntimeError("no such table")
            return list(rows)
        return fn
    warehouse.query, warehouse.query_parts = reader(legacy), reader(log)


def main():
    print("health_digest — run ledger is unioned, not legacy-only")
    real_q, real_qp = warehouse.query, warehouse.query_parts
    try:
        install(LEGACY, LOG)
        latest = health_digest._latest_source_runs()

        # The whole point: outcomes that exist ONLY in the log must be visible.
        check("a log-only source is visible at all", "sevennow" in latest,
              "sources seen: %s" % sorted(latest))
        check("its failure status survives", (latest.get("sevennow") or {}).get("status") == "failed")
        check("its error text survives (this is what reaches the digest finding)",
              "AttributeError" in (latest.get("sevennow") or {}).get("error", ""),
              repr((latest.get("sevennow") or {}).get("error")))
        check("a log-only timeout is visible", (latest.get("cityhive") or {}).get("status") == "timeout")

        # Newest-ts_end-wins: today's failure must supersede the 8-day-old ok row, not be masked by it.
        check("a fresh log failure beats a stale legacy 'ok' for the same source",
              (latest.get("binnys") or {}).get("status") == "failed",
              "got %r — the stale legacy row won" % (latest.get("binnys") or {}).get("status"))

        # Union, not switch — legacy history for sources predating the log must not be dropped.
        check("legacy-only history is still readable", "legacy-only" in latest)

        # Order must not matter: a stale LOG row must likewise not beat a fresh LEGACY one.
        install([rec("fresh", "swapped", "failed", NOW - 60)], [rec("stale", "swapped", "ok", NOW - 9 * 86400)])
        check("newest-wins is symmetric (a stale log row loses to a fresh legacy one)",
              (health_digest._latest_source_runs().get("swapped") or {}).get("status") == "failed")

        # Each ledger must survive the other being absent/unreadable entirely.
        install(None, LOG)
        check("still reports when the legacy table is absent",
              (health_digest._latest_source_runs().get("sevennow") or {}).get("status") == "failed")
        install(LEGACY, None)
        check("still reports when the log is absent",
              (health_digest._latest_source_runs().get("legacy-only") or {}).get("status") == "ok")
        install(None, None)
        check("both absent degrades to empty rather than raising",
              health_digest._latest_source_runs() == {})
    finally:
        warehouse.query, warehouse.query_parts = real_q, real_qp

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
