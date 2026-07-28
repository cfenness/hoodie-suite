"""selfheal.py — retry-with-backoff + quarantine (SCRAPING-PLATFORM.md P2 self-healing).

THE GAP: the dispatcher is interval-based — a source that just FAILED attempted recently, so it isn't
"due" again until interval_h passes. One transient blip = a whole cadence of staleness, the opposite of
sellable. THE HEAL: a FAILED source becomes due-for-retry on an ESCALATING BACKOFF (minutes, not a day);
after QUARANTINE_AT consecutive failures it's QUARANTINED — stop hammering a source that's genuinely down,
back off to a daily probe, and let the health digest escalate it to a human — while last-good data keeps
serving (cumulative tables never shrink; health flags the staleness honestly).

This is the missing DISPATCHER-level heal. The in-run heals already exist as run_one prep: cookie refresh
(cookie_warm — Kroger/TotalWine/Albertsons/Ahold) and proxy rotation (resi). Together they cover the
common failure modes: auth expiry → re-warm; IP burned → rotate; transient/blocked → backoff-retry;
genuinely down → quarantine + alert.

Reads the shared source_runs ledger for each source's recent statuses → consecutive_failures → the retry
schedule. Pure logic is injectable; the ledger read is isolated. Deterministic, never mocked.
"""

import os
import time

import warehouse

# retryable failures vs a real success (which resets the counter). `no-creds` is HONEST, not transient —
# retrying won't conjure a missing key — so it neither counts as a failure nor resets (the source is simply
# skipped by run_one until the cred lands). Unknown statuses are ignored the same way.
# `incomplete` = the run worked but covered only PART of the source's outlets/items. That is a failure
# of the product, not a warning, so it belongs here — and being retryable is the point: each retry
# resumes from the checkpoint rather than restarting, so the backoff schedule drives the crawl to full
# coverage on its own instead of leaving a half-captured catalog sitting until tomorrow's cadence.
FAIL = {"failed", "timeout", "empty", "incomplete"}
BENIGN = {"ok", "current", "no-change", "success"}

BACKOFF_BASE_S = float(os.environ.get("HEAL_BACKOFF_BASE_S", "300"))          # first retry ~5 min after a fail
BACKOFF_FACTOR = float(os.environ.get("HEAL_BACKOFF_FACTOR", "3"))            # 5m, 15m, 45m, ~2.25h, …
BACKOFF_MAX_S = float(os.environ.get("HEAL_BACKOFF_MAX_S", str(6 * 3600)))    # capped at 6h
QUARANTINE_AT = int(os.environ.get("HEAL_QUARANTINE_AT", "6"))               # consecutive fails → quarantine
QUARANTINE_S = float(os.environ.get("HEAL_QUARANTINE_S", str(24 * 3600)))     # quarantined = at most a daily probe


def consecutive_failures(statuses):
    """`statuses` NEWEST-first. The leading run of retryable failures; stops at the first real success. A
    `no-creds`/unknown status in the run neither counts nor resets (skipped)."""
    n = 0
    for st in statuses:
        if st in FAIL:
            n += 1
        elif st in BENIGN:
            break
    return n


def backoff_s(cf):
    """Seconds to wait after `cf` consecutive failures before retrying. 0 if none; QUARANTINE_S once quarantined."""
    if cf <= 0:
        return 0.0
    if cf >= QUARANTINE_AT:
        return QUARANTINE_S
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (BACKOFF_FACTOR ** (cf - 1)))


def retry_due(now, last_attempt, cf):
    """A failed source is due-for-retry once its backoff has elapsed since the last attempt."""
    return cf > 0 and (now - last_attempt) >= backoff_s(cf)


def quarantined(cf):
    return cf >= QUARANTINE_AT


def recent_by_source(limit=8):
    """({source: [statuses newest-first]}, {source: last_attempt_ts}) from BOTH ledgers."""
    rows = []
    for fn, name in ((warehouse.query, "source_runs"), (warehouse.query_parts, "source_runs_log")):
        try:
            rows += fn(name, "SELECT source, status, ts_start FROM t")
        except Exception:
            pass
    by = {}
    for r in rows:
        by.setdefault(r["source"], []).append((float(r["ts_start"] or 0), r.get("status")))
    statuses, last_attempt = {}, {}
    for sid, lst in by.items():
        lst.sort(reverse=True)                       # newest first
        statuses[sid] = [s for _, s in lst[:limit]]
        last_attempt[sid] = lst[0][0] if lst else 0
    return statuses, last_attempt


def states(now=None, statuses=None, last_attempt=None):
    """Per-source heal state: {source: {consecutive_failures, quarantined, retry_due, next_retry_in_s}}."""
    now = now or time.time()
    if statuses is None or last_attempt is None:
        statuses, last_attempt = recent_by_source()
    out = {}
    for sid, sts in statuses.items():
        cf = consecutive_failures(sts)
        la = last_attempt.get(sid, 0)
        out[sid] = {
            "consecutive_failures": cf,
            "quarantined": quarantined(cf),
            "retry_due": retry_due(now, la, cf),
            "next_retry_in_s": max(0, round(backoff_s(cf) - (now - la))) if cf > 0 else None,
        }
    return out


def retry_due_ids(now=None):
    """Source ids that FAILED and whose backoff has elapsed → the dispatcher should re-run them NOW (ahead of
    their normal interval). A quarantined source only reappears here once a day."""
    return {sid for sid, st in states(now).items() if st["retry_due"]}


def main():
    st = states()
    failing = {k: v for k, v in st.items() if v["consecutive_failures"] > 0}
    q = [k for k, v in failing.items() if v["quarantined"]]
    due = sorted(k for k, v in failing.items() if v["retry_due"])
    print("[selfheal] %d sources failing | %d quarantined | %d retry-due now" % (len(failing), len(q), len(due)))
    if due:
        print("  retry now: " + ", ".join(due))
    if q:
        print("  QUARANTINED (>= %d fails, daily probe + health escalates): %s" % (QUARANTINE_AT, ", ".join(sorted(q))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
