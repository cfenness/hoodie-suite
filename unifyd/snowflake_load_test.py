#!/usr/bin/env python3
"""snowflake_load_test.py — guard for the snowflake-load registry build.

Deterministic + offline (no connector, no network): proves the registry entry, the shim, and the
load program stay wired together — the exact drift class the dispatch guard exists for, applied to
the one build whose program lives OUTSIDE unifyd/ (snowflake/run_load.py), where an import-path or
rename slip would otherwise surface only as a 3am failed machine.

Run: python3 unifyd/snowflake_load_test.py   (exit 0 = clean, 1 = drift). Also importable as test_*.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    fails = []
    import source_registry as reg

    b = next((x for x in getattr(reg, "BUILDS", []) if x["id"] == "snowflake-load"), None)
    if not b:
        fails.append("BUILDS has no 'snowflake-load' entry")
    else:
        if b.get("tables") != ["snowflake_load_runs"]:
            fails.append("snowflake-load must verify-land snowflake_load_runs (got %r)" % b.get("tables"))
        if "SNOWFLAKE_ACCOUNT" not in (b.get("requires") or []):
            fails.append("snowflake-load must declare requires=[SNOWFLAKE_*] — without it a tick before "
                         "the secrets exist reports 'failed' instead of an honest 'no-creds'")
        if b.get("klass") != "build":
            fails.append("snowflake-load klass must be 'build' (dispatcher schedules it via due_builds)")
        if b.get("after"):
            fails.append("snowflake-load must NOT set `after` — a delta=0 dim rebuild lands 'current' "
                         "(not 'ok') and would starve the load; the default any-source trigger is deliberate")

    # the shim imports (and wires snowflake/ onto sys.path)…
    try:
        import snowflake_load
        if not callable(getattr(snowflake_load, "run", None)):
            fails.append("snowflake_load.run missing — the registry code string calls m.run()")
    except Exception as e:
        fails.append("import snowflake_load failed: %s" % e)
        snowflake_load = None

    # …and the load program it wraps resolves with a returning main()
    try:
        import run_load
        if not callable(getattr(run_load, "main", None)):
            fails.append("run_load.main missing")
    except Exception as e:
        fails.append("import run_load failed (snowflake/ moved or renamed?): %s" % e)

    # snowflake/ must stay a plain directory: an __init__.py would make it importable as the
    # package `snowflake` wherever the repo root lands on sys.path, shadowing the real connector.
    sfdir = os.path.join(os.path.dirname(HERE), "snowflake")
    if os.path.exists(os.path.join(sfdir, "__init__.py")):
        fails.append("snowflake/__init__.py exists — it would shadow the snowflake-connector package")

    # the connector ships in the image (the whole point of retiring the runtime pip install)
    reqs = open(os.path.join(HERE, "requirements.txt")).read()
    if "snowflake-connector-python" not in reqs:
        fails.append("snowflake-connector-python missing from unifyd/requirements.txt")

    # the run row the shim lands must carry exactly the declared schema (stable Parquet columns)
    if snowflake_load is not None:
        rec_keys = {"ts", "duration_s", "rows_total", "tables", "raw_tables", "raw_rows",
                    "master_tables", "master_rows", "host"}
        if set(snowflake_load.FIELDS) != rec_keys:
            fails.append("snowflake_load.FIELDS drifted from the record run() builds")

    if fails:
        print("SNOWFLAKE LOAD GUARD FAILED (%d):" % len(fails))
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("snowflake load guard OK — registry entry, shim, and run_load are wired")
    return 0


def test_snowflake_load_wiring():                        # pytest entrypoint
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
