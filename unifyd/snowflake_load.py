#!/usr/bin/env python3
"""snowflake_load.py — the registry-schedulable wrapper around snowflake/run_load.py.

The Snowflake morning drop runs as a normal registry BUILD (source_registry.BUILDS id
"snowflake-load"): the hourly Fly dispatcher gives it its own ephemeral machine, run_one
verify-lands it, and the Data Console / health digest see it like every other job.

WHY a shim (and not `code="import run_load..."` directly): run_one's verify-landing checks a
WAREHOUSE table's row delta, and a job that only writes to Snowflake would read "current"/"empty"
forever. So after a successful drop this lands one row per run in `snowflake_load_runs` — a
small single-file accumulate table keyed on ts (NOT write_partition: row_count_strict only reads
single-file footers / bucket manifests, so a partitioned run table would be invisible to the
verifier). One writer at a time is guaranteed by the dispatcher (a live snowflake-load machine
suppresses respawn), so the accumulate read-modify-write is race-free here. That row gives
run_one a real +1 delta → status "ok", and doubles as the queryable run history for the load.

A failed load raises — the run ledger records "failed", never a silent ok.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# the load program lives in the repo's snowflake/ (sibling of unifyd/) — insert THAT dir, not its
# parent: the parent would shadow the `snowflake` connector package with a namespace dir.
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "snowflake"))

FIELDS = ["ts", "duration_s", "rows_total", "tables", "raw_tables", "raw_rows",
          "master_tables", "master_rows", "scope", "host"]   # scope: 'full' or the SNOWFLAKE_ONLY list


def run():
    """The registry entrypoint: run the change-aware drop, then verify-land the run row."""
    import run_load
    import warehouse
    t0 = time.time()
    stats = run_load.main() or {}
    per = stats.get("per_schema") or {}
    rec = {
        "ts": int(t0),
        "duration_s": round(time.time() - t0, 1),
        "rows_total": int(stats.get("rows_total") or 0),
        "tables": int(stats.get("tables") or 0),
        "raw_tables": int(per.get("RAW", {}).get("tables") or 0),
        "raw_rows": int(per.get("RAW", {}).get("rows") or 0),
        "master_tables": int(per.get("MASTER", {}).get("tables") or 0),
        "master_rows": int(per.get("MASTER", {}).get("rows") or 0),
        # Recorded so the ledger can never present a deliberately partial mirror as a complete one.
        "scope": str(stats.get("scope") or "full")[:200],
        "host": os.environ.get("FLY_MACHINE_ID") or os.uname().nodename,
    }
    warehouse.write_accumulate("snowflake_load_runs", [rec], key="ts", fields=FIELDS)
    print("snowflake_load: landed run row — %s rows across %s tables (RAW %s / MASTER %s)"
          % (rec["rows_total"], rec["tables"], rec["raw_tables"], rec["master_tables"]))
    return rec


if __name__ == "__main__":
    run()
