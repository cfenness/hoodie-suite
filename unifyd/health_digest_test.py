"""Offline test for health_digest.py's render_text(): python unifyd/health_digest_test.py

Narrow on purpose: build_digest()'s own checks (registry hygiene, coverage, SLA, freshness) each
already ride on real, independently-tested modules (deploy_drift, deep_audit, dq_aggregate, sla,
geo_gap) and health_digest.py itself is a composition layer with no dedicated suite (same
established gap as server.py — see agent_checks.py's own "Paired test exists" note). This suite
exists specifically to lock in one real regression: render_text() crashed in production for 47+
hours (2026-07-30 22:49 through the next deploy) because it assumed every finding's `evidence` is a
dict, when several real call sites — deploy_drift.findings(), and this file's OWN
"the check itself did not run" except-clause fallbacks (drift/geo-gap/dq) — hand back a plain
string. `main()` calls render_text() unconditionally, so ANY finding with string evidence took the
whole digest down before it ever wrote a latest.json — a hard crash with nothing degraded to fall
back on, not a quiet miss.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health_digest as hd

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)


def _digest(findings):
    now = time.time()
    counts = {"critical": 0, "warn": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1
    return {
        "as_of": now, "warehouse": {"label": "s3://x", "kind": "production"},
        "sources_checked": 3, "totals": {"grand_rows": 42},
        "ok": counts["critical"] == 0 and counts["warn"] == 0,
        "counts": counts, "new_criticals": 0, "findings": findings,
    }


def _finding(severity, evidence, new=True):
    return {"code": "x", "severity": severity, "source": "s", "summary": "summary text",
            "first_seen": time.time(), "new": new, "evidence": evidence}


print("the exact live regression: deploy_drift-shaped string evidence must not crash render_text()")
d = _digest([_finding("warn", "deploy once with tools/release_train.py deploy to set the baseline")])
try:
    text = hd.render_text(d)
    ok("no crash", True)
    ok("the string evidence is still shown, not swallowed", "deploy once with" in text)
except Exception as e:
    ok("no crash (got %r)" % e, False)

print("\nthis file's own except-clause fallbacks pass a bare str(e) — same shape, must survive")
d = _digest([_finding("warn", "urlopen error [Errno 8] nodename nor servname provided")])
try:
    text = hd.render_text(d)
    ok("no crash on a str(e)-shaped evidence", True)
    ok("the error text is preserved", "nodename nor servname" in text)
except Exception as e:
    ok("no crash (got %r)" % e, False)

print("\ndict evidence (the common case, every deep_audit/dq_aggregate/coverage finding) still works")
d = _digest([_finding("critical", {"rows": 100, "empty": ""})])
text = hd.render_text(d)
ok("dict evidence renders", '"rows": 100' in text)
ok("empty-string values are still dropped (pre-existing filter, unchanged)", '"empty"' not in text)

print("\nNone / empty evidence produce no evidence line at all, for either shape")
d = _digest([_finding("info", None), _finding("info", ""), _finding("info", {})])
text = hd.render_text(d)
ok("no 'evidence:' line for None", text.count("evidence:") == 0)

print("\na finding with NO 'new' key worth of drift doesn't fail the age formatting either")
d = _digest([_finding("warn", "x", new=False)])
text = hd.render_text(d)
ok("renders an aged (since MM-DD) suffix instead of crashing", "(since " in text)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
