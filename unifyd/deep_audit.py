#!/usr/bin/env python3
"""deep_audit.py — the WEEKLY drift checks the daily digest can't see. The daily layer answers "is data
coming in?"; this layer answers "is the data that's coming in still RIGHT?" Three checks, all deterministic,
all evidence-cited, invoked by `health_digest.py --weekly` (Mondays via run_health_digest.sh):

  field-drift    A source keeps landing rows but one of its FIELDS died — the classic silent selector rot
                 (page restructures, the row parser still matches, one column extracts empty forever).
                 Detected from Parquet FOOTER null-statistics (O(footers), never scans the lake) against a
                 saved per-column baseline (agent_state/health/field_baseline.json). First run seeds the
                 baseline and honestly reports "baseline established", not findings.
  fixture-fail   A parser regressed against its captured reference page (unifyd/fixtures/). Registry-driven:
                 add a scraper = add one FIXTURE_CHECKS row. These pages are frozen, so a failure is always
                 OUR parse breaking, never site drift.
  docs-drift     CLAUDE.md / README.md / SPINE.md reference repo paths that no longer exist — how stale docs
                 quietly start lying (e.g. mdm.html's tab list drifting from reality).

Caveats stated where they bite: footer null-stats cover single-file tables only (v2 bucketed tables are
counted + reported as skipped, not silently omitted), and accumulate-merge tables dilute a dead field
slowly — the JUMP threshold exists to catch drift before the DEAD threshold trips.
"""
import json
import os
import re
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
_BASELINE = os.path.join(_DIR, "agent_state", "health", "field_baseline.json")

# field-drift thresholds (echoed into evidence so every verdict is reproducible)
FIELD_DEAD_PREV = 0.20      # field was populated (<20% null) ...
FIELD_DEAD_CUR = 0.80       # ... and is now dead (>80% null)          → critical
FIELD_JUMP = 0.25           # null-rate rose >25 points since baseline → warn (early drift signal)
MIN_ROWS = 1000             # ignore tiny tables (fixtures, seeds — noise)

# fixture regression registry: add a scraper = add one row. Each check parses a FROZEN captured page,
# so it must always pass — the page can't drift, only our code can.
FIXTURE_CHECKS = [
    dict(id="ttb-cola", fixture="fixtures/cola_debug.html", min_rows=20,
         run="import ttb_cola_scraper as t; recs, _n, diag = t.parse_results(html); result=(len(recs), diag)"),
    dict(id="cex", fixture="fixtures/cex_bls_sample.json", min_rows=200,
         run="import json, cex_ref as c; rows, warns = c.parse([json.loads(html)]); result=(len(rows), {'warns': warns[:3]})"),
    dict(id="cpi", fixture="fixtures/cpi_bls_sample.json", min_rows=500,
         run="import json, cpi_ref as c; rows, warns = c.parse([json.loads(html)]); result=(len(rows), {'warns': warns[:3]})"),
    dict(id="fred", fixture="fixtures/fred_sample.json", min_rows=100,
         run="import json, fred_ref as f; rows = f.parse('MRTSSM4453USN', json.loads(html)); result=(len(rows), {})"),
]

DOC_FILES = ["CLAUDE.md", "README.md", "SPINE.md"]
_DOC_PATH_RE = re.compile(r"\b((?:apps|spine|tools|unifyd|cloudfront)/[A-Za-z0-9_\-./]+\.(?:html|py|js|json|md|sh))\b")
_DOC_SKIP_PREFIX = ("unifyd/agent_state", "unifyd/out", "unifyd/cola_out", "unifyd/full_out",
                    "unifyd/_archive", "unifyd/warehouse.env")


def _finding(code, severity, source, summary, evidence):
    return {"code": code, "severity": severity, "source": source, "summary": summary,
            "evidence": evidence, "kind": "DETERMINISTIC"}


# ── field-drift: per-column null fractions from Parquet footers, diffed against the saved baseline ───────────
def _null_fracs(name):
    """{column: null_fraction} for a single-file table, from footer statistics only. None = table not
    readable this way (v2 bucketed layout / missing stats) — callers must COUNT these, not ignore them."""
    import warehouse
    import pyarrow.parquet as pq
    try:
        if warehouse.remote():
            from pyarrow import fs as pafs
            s3 = pafs.S3FileSystem(endpoint_override=warehouse._endpoint(),
                                   access_key=warehouse._env("AWS_ACCESS_KEY_ID"),
                                   secret_key=warehouse._env("AWS_SECRET_ACCESS_KEY"),
                                   region=warehouse._region(), scheme="https")
            path = "%s/%s/%s.parquet" % (warehouse._bucket(), warehouse._prefix(), name)
            md = pq.read_metadata(path, filesystem=s3)
        else:
            md = pq.read_metadata(os.path.join(getattr(warehouse, "_LOCAL_DIR", "agent_state/warehouse"),
                                               name + ".parquet"))
    except Exception:
        return None
    total = md.num_rows or 0
    if not total:
        return {}
    nulls = {}
    for rg in range(md.num_row_groups):
        g = md.row_group(rg)
        for ci in range(g.num_columns):
            col = g.column(ci)
            st = col.statistics
            key = col.path_in_schema
            if st is None or st.null_count is None:
                nulls[key] = None                          # any absent stat poisons the column — unknown, never guessed
            elif key not in nulls:
                nulls[key] = st.null_count
            elif nulls[key] is not None:
                nulls[key] += st.null_count
    return {k: (round(v / total, 4) if v is not None else None) for k, v in nulls.items()}


def field_drift(manifest, now):
    """Diff every pull table's per-column null fractions against the baseline; roll the baseline forward."""
    from concurrent.futures import ThreadPoolExecutor
    findings = []
    try:
        with open(_BASELINE) as f:
            baseline = json.load(f)
    except Exception:
        baseline = {}
    first_run = not baseline

    tables = [s["name"] for s in manifest.get("sources", [])
              if s.get("group") in ("scrape", "accounts", "timeseries") and (s.get("rows") or 0) >= MIN_ROWS]
    with ThreadPoolExecutor(max_workers=24) as ex:
        stats = dict(zip(tables, ex.map(_null_fracs, tables)))

    skipped = sorted(t for t, v in stats.items() if v is None)
    for name in sorted(t for t, v in stats.items() if v is not None):
        cur = stats[name]
        prev = (baseline.get(name) or {}).get("cols", {})
        for col, cf in sorted(cur.items()):
            if cf is None or col == "raw_json":
                continue
            pf = prev.get(col)
            if pf is None:
                continue
            if pf <= FIELD_DEAD_PREV and cf >= FIELD_DEAD_CUR:
                findings.append(_finding("field-drift", "critical", name,
                                         "%s.%s DIED: %.0f%% → %.0f%% null — rows still landing, field no longer extracted" % (
                                             name, col, pf * 100, cf * 100),
                                         {"column": col, "baseline_null": pf, "current_null": cf,
                                          "baseline_at": (baseline.get(name) or {}).get("at"),
                                          "thresholds": {"prev<=": FIELD_DEAD_PREV, "cur>=": FIELD_DEAD_CUR}}))
            elif cf - pf > FIELD_JUMP:
                findings.append(_finding("field-drift", "warn", name,
                                         "%s.%s null-rate rose %.0f%% → %.0f%% since baseline — early drift" % (
                                             name, col, pf * 100, cf * 100),
                                         {"column": col, "baseline_null": pf, "current_null": cf,
                                          "baseline_at": (baseline.get(name) or {}).get("at"),
                                          "threshold_jump": FIELD_JUMP}))
        baseline[name] = {"at": round(now), "cols": {k: v for k, v in cur.items() if v is not None}}

    os.makedirs(os.path.dirname(_BASELINE), exist_ok=True)
    with open(_BASELINE, "w") as f:
        json.dump(baseline, f)

    if first_run:
        findings.append(_finding("field-drift", "info", "baseline",
                                 "field-drift baseline established (%d tables, %d skipped) — comparisons start next week" % (
                                     len(stats) - len(skipped), len(skipped)),
                                 {"tables": len(stats) - len(skipped), "skipped": len(skipped)}))
    if skipped:
        findings.append(_finding("field-drift", "info", "coverage",
                                 "%d table(s) not footer-readable (v2 bucketed layout / no stats) — NOT checked: %s" % (
                                     len(skipped), ", ".join(skipped[:10]) + ("…" if len(skipped) > 10 else "")),
                                 {"skipped": skipped}))
    return findings


# ── fixture regression: frozen page in, expected rows out — a failure is always our parse breaking ───────────
def fixture_regression():
    import sys
    findings = []
    sys.path.insert(0, _DIR)
    for chk in FIXTURE_CHECKS:
        path = os.path.join(_DIR, chk["fixture"])
        try:
            html = open(path, encoding="utf-8", errors="replace").read()
            scope = {"html": html}
            exec(chk["run"], scope)                        # registry-declared one-liner, repo code only
            n, diag = scope["result"]
            if n < chk["min_rows"]:
                findings.append(_finding("fixture-fail", "critical", chk["id"],
                                         "%s: parser regressed vs frozen fixture — %d rows (expected ≥%d). "
                                         "The page can't drift; our code did." % (chk["id"], n, chk["min_rows"]),
                                         {"fixture": chk["fixture"], "rows": n, "min_rows": chk["min_rows"],
                                          "diag": diag}))
        except Exception as e:
            findings.append(_finding("fixture-fail", "critical", chk["id"],
                                     "%s: fixture check errored — %s" % (chk["id"], str(e)[:160]),
                                     {"fixture": chk["fixture"], "error": str(e)[:300]}))
    return findings


# ── docs-drift: repo paths mentioned in the load-bearing docs must exist ─────────────────────────────────────
def docs_drift():
    findings = []
    for doc in DOC_FILES:
        p = os.path.join(_ROOT, doc)
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        missing = sorted({m for m in _DOC_PATH_RE.findall(text)
                          if not m.startswith(_DOC_SKIP_PREFIX) and not os.path.exists(os.path.join(_ROOT, m))})
        if missing:
            findings.append(_finding("docs-drift", "warn", doc,
                                     "%s references %d path(s) that don't exist: %s" % (
                                         doc, len(missing), ", ".join(missing[:6]) + ("…" if len(missing) > 6 else "")),
                                     {"missing": missing}))
    return findings


def run(manifest, now=None):
    """All weekly checks → findings list (same shape the daily digest emits)."""
    now = now or time.time()
    out = []
    for fn in (lambda: field_drift(manifest, now), fixture_regression, docs_drift):
        try:
            out.extend(fn())
        except Exception as e:
            out.append(_finding("audit-error", "warn", "deep_audit",
                                "a weekly check crashed (reported, not swallowed): %s" % str(e)[:160],
                                {"error": str(e)[:300]}))
    return out
