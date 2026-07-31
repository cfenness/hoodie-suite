#!/usr/bin/env python3
"""dq_aggregate.py — ONE number for "how clean is the whole warehouse", not per-table dq.js runs.

dq.js (the interactive profiler behind apps/dashboard.html and apps/hi) is deep but only ever looks
at ONE already-loaded table at a time, client-side — there has never been a warehouse-wide rollup.
This is deliberately a DIFFERENT, narrower engine, not a Python re-derivation of dq.js's full check
list (grain inference, role assignment, mixed-unit bimodality etc. need a human looking at ONE table
to judge — running them blind across every table would just manufacture noise). It runs two checks
that ARE safe to fire unattended across the whole warehouse because they need no baseline and no
per-table judgement call — a fully-empty column or a whole-row duplicate is never legitimate:

  dq-null-blowout      a column is 100% null in a populated table. Reuses deep_audit._null_fracs
                        (footer stats only, one shredder — no second copy of that parquet-stats
                        logic). Distinct from deep_audit's own field-drift: that needs a saved
                        baseline to judge a PARTIAL null-rate rise; total silence needs no history.
  dq-duplicate-rows     exact whole-row duplicates. A real full-table scan (COUNT vs COUNT DISTINCT
                        *), so it's bounded to DUP_SCAN_MAX_ROWS — tables above that are reported
                        SKIPPED, never silently dropped (same "no silent caps" rule as everywhere
                        else in this repo).

Score = % of checked tables with zero findings. Simple and explainable on purpose — a weighted
formula would need its own justification every time someone asks "why 87 and not 90".

Rides health_digest.py's existing WEEKLY cadence (same reasoning as deep_audit: full-table scans are
too heavy for the daily pass) and is written to the shared warehouse the same way deploy_drift.py
records its baseline — the score has to be readable from wherever Cockpit is served, not just the
machine that computed it:

    python3 unifyd/dq_aggregate.py build            # print the JSON, don't persist
    python3 unifyd/dq_aggregate.py write             # compute + publish to the shared warehouse
"""
import json
import os
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_KEY = "_dq_aggregate.json"

DUP_SCAN_MAX_ROWS = 100_000     # full whole-row dedup scan ceiling — bigger tables are reported skipped
MIN_ROWS = 10                   # ignore near-empty tables (fixtures/seeds/smoke tests) — pure noise floor
# Real data only: skip staging/scratch (`_`-prefixed), run logs, and DERIVED build outputs (matching/
# clustering/crosswalk artifacts) — those are working tables where whole-row repetition or an all-null
# scratch column isn't a defect, it's the shape of the process. Master dims, conformed src_ grain,
# accounts, timeseries and reference tables are the actual sellable/master data this score is about.
CHECK_GROUPS = ("scrape", "accounts", "timeseries", "master", "conformed", "reference")


def _finding(code, severity, source, summary, evidence):
    return {"code": code, "severity": severity, "source": source, "summary": summary,
            "evidence": evidence, "kind": "DETERMINISTIC"}


def _null_blowout(name):
    """Columns 100% null in `name`, or None if the table isn't footer-readable this way (partitioned
    v2 layout / missing stats — callers must count these as skipped, not as clean)."""
    sys.path.insert(0, _DIR)
    import deep_audit
    fracs = deep_audit._null_fracs(name)
    if fracs is None:
        return None
    return sorted(c for c, f in fracs.items() if f == 1.0 and c != "raw_json")


def _dup_rate(name, query_fn):
    total = (query_fn(name, "SELECT COUNT(*) AS n FROM t") or [{}])[0].get("n") or 0
    if not total:
        return 0.0, 0, total
    distinct = (query_fn(name, "SELECT COUNT(*) AS n FROM (SELECT DISTINCT * FROM t) x") or [{}])[0].get("n") or 0
    dup = max(0, total - distinct)
    return (dup / total if total else 0.0), dup, total


def build(list_datasets_fn=None, query_fn=None, now=None):
    """The full pass. Injectable `list_datasets_fn()` / `query_fn(name, sql)` for testing without a
    real warehouse — production defaults come from monitor.build() and warehouse.query."""
    now = now if now is not None else time.time()
    if list_datasets_fn is None:
        sys.path.insert(0, _DIR)
        import monitor
        list_datasets_fn = lambda: monitor.build(record_history=False).get("sources", [])
    if query_fn is None:
        sys.path.insert(0, _DIR)
        import warehouse
        query_fn = warehouse.query

    sources = list_datasets_fn()
    candidates = sorted((s for s in sources
                         if s.get("group") in CHECK_GROUPS and (s.get("rows") or 0) >= MIN_ROWS),
                        key=lambda s: s["name"])

    findings = []
    checked = []
    skipped_null, skipped_dup = [], []

    for s in candidates:
        name, rows = s["name"], s.get("rows") or 0
        clean = True

        if s.get("partitioned"):
            skipped_null.append(name)                  # footer null-stats only cover single-file tables
        else:
            blown = _null_blowout(name)
            if blown is None:
                skipped_null.append(name)
            elif blown:
                clean = False
                findings.append(_finding(
                    "dq-null-blowout", "critical", name,
                    "%s: %d column(s) 100%% null — %s" % (
                        name, len(blown), ", ".join(blown[:6]) + ("…" if len(blown) > 6 else "")),
                    {"columns": blown, "rows": rows}))

        if rows > DUP_SCAN_MAX_ROWS:
            skipped_dup.append(name)
        else:
            try:
                rate, dup, total = _dup_rate(name, query_fn)
            except Exception:                          # noqa: BLE001 — an unreadable table is a skip, not a crash
                skipped_dup.append(name)
            else:
                if dup > 0:
                    clean = False
                    sev = "critical" if rate >= 0.05 else "warn"
                    findings.append(_finding(
                        "dq-duplicate-rows", sev, name,
                        "%s: %d exact duplicate row(s) (%.1f%% of %s)" % (name, dup, rate * 100, "{:,}".format(total)),
                        {"duplicate_rows": dup, "total_rows": total, "rate": round(rate, 4)}))

        checked.append({"name": name, "clean": clean})

    if skipped_dup:
        findings.append(_finding(
            "dq-coverage", "info", "coverage",
            "%d table(s) too large for a full duplicate scan (>%s rows) — NOT checked for duplicates: %s" % (
                len(skipped_dup), "{:,}".format(DUP_SCAN_MAX_ROWS),
                ", ".join(sorted(skipped_dup)[:10]) + ("…" if len(skipped_dup) > 10 else "")),
            {"skipped": sorted(skipped_dup), "ceiling_rows": DUP_SCAN_MAX_ROWS}))
    if skipped_null:
        findings.append(_finding(
            "dq-coverage", "info", "coverage",
            "%d table(s) not footer-readable for null-blowout (partitioned / no stats) — NOT checked: %s" % (
                len(skipped_null), ", ".join(sorted(skipped_null)[:10]) + ("…" if len(skipped_null) > 10 else "")),
            {"skipped": sorted(skipped_null)}))

    return _assemble(now, checked, findings)


def _assemble(now, checked, findings):
    findings.sort(key=lambda f: ({"critical": 0, "warn": 1, "info": 2}[f["severity"]], f["source"]))
    counts = {"critical": 0, "warn": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1
    n = len(checked)
    clean = sum(1 for c in checked if c["clean"])
    return {
        "as_of": now, "generated_by": "dq_aggregate.py",
        "ok": counts["critical"] == 0,
        "score": round(100 * clean / n) if n else None,
        "tables_checked": n, "tables_clean": clean,
        "counts": counts, "findings": findings,
    }


def findings(now=None, log=print, **build_kwargs):
    """health_digest-shaped findings, so the weekly digest can absorb these unchanged (same idiom as
    deploy_drift.findings / deep_audit.run)."""
    try:
        return build(now=now, **build_kwargs)["findings"]
    except Exception as e:                                    # noqa: BLE001
        log("  [dq] aggregate pass did not run: %s" % str(e)[:120])
        return [_finding("dq-check-failed", "warn", "dq", "the DQ aggregate pass did not run", str(e)[:120])]


def write(now=None, log=print, built=None, **build_kwargs):
    """Compute (unless already computed via `built`) + publish the score to the shared warehouse —
    same remote()-gated pattern as deploy_drift.record: writing to the local fallback would
    "succeed" and be unreadable everywhere Cockpit is actually served, which is worse than not
    writing at all."""
    sys.path.insert(0, _DIR)
    import warehouse
    d = built if built is not None else build(now=now, **build_kwargs)
    if not warehouse.remote():
        log("  [dq] NOT published — no shared warehouse configured (creds absent); the score stays "
            "unreadable from wherever Cockpit is served until this runs somewhere with warehouse creds.")
        return None
    try:
        warehouse.put_bytes(STATE_KEY, json.dumps(d).encode())
        log("  [dq] published: score %s (%d/%d tables clean, %d critical, %d warn) -> shared warehouse"
            % (d["score"], d["tables_clean"], d["tables_checked"], d["counts"]["critical"], d["counts"]["warn"]))
        return d
    except Exception as e:                                    # noqa: BLE001
        log("  [dq] could NOT publish the score (%s)" % str(e)[:90])
        return None


def main(argv):
    cmd = argv[0] if argv else "build"
    if cmd == "write":
        return 0 if write() else 1
    d = build()
    print(json.dumps(d, indent=2))
    return 2 if d["counts"]["critical"] else (1 if d["counts"]["warn"] else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
