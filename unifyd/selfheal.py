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
import re
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


# ── FAILURE CLASSES: different errors tell different stories, so they get different policies ────────
# One backoff curve for every failure is a policy with no point of view. A crawl that ran out of clock
# should resume in minutes; a source being 403'd by an anti-bot wall should NOT be hammered — retrying
# fast makes the block worse and can burn the exit IP. An OOM kill and a selector drift will both
# reproduce exactly on retry, so retrying at all is just noise that hides a real defect behind a
# perpetually-red source. Each class states what the error MEANS and what the right response is.
#
#   mult  — multiplies the backoff (a productive failure retries sooner than a hostile one)
#   auto  — may the dispatcher retry this at all, or does it need a human/code change?
CLASSES = {
    # The work was PRODUCTIVE and is checkpointed; a retry resumes rather than restarts. Retry soon.
    "incomplete": {"mult": 0.2, "auto": True,
                   "story": "ran fine, covered only part of the catalog — resume continues from the checkpoint"},
    "timeout":    {"mult": 0.2, "auto": True,
                   "story": "ran out of clock mid-crawl — resume continues; if it repeats, raise the timeout"},
    # HOSTILE: an anti-bot wall. Retrying quickly is actively harmful — it deepens the block.
    "blocked":    {"mult": 6.0, "auto": True,
                   "story": "refused by an anti-bot wall (403/captcha) — backing off hard; a fast retry "
                            "makes the block worse. Needs a different exit IP or a warmed session, not persistence"},
    # DETERMINISTIC: the same run will fail the same way. Retrying cannot help and hides the defect.
    "oom":        {"mult": 0.0, "auto": False,
                   "story": "killed by the OOM killer — the same config will die identically. Needs more "
                            "memory (registry `mem`) or a smaller batch, not a retry"},
    "parse":      {"mult": 0.0, "auto": False,
                   "story": "ran but could not parse the source — the site's shape changed. Identical code "
                            "gives an identical result; needs a selector fix"},
    "empty":      {"mult": 3.0, "auto": True,
                   "story": "ran clean and landed nothing at all — usually a broken recipe rather than a "
                            "blip; retried slowly while it wants a human look"},
    "unknown":    {"mult": 1.0, "auto": True, "story": "failed for an unclassified reason"},
}

_BLOCKED_RE = re.compile(
    r"\b(403|401|429|access denied|forbidden|captcha|blocked|bot ?detect|perimeterx|datadome|"
    r"incapsula|cloudflare|akamai|edgesuite|forter|rate.?limit)\b", re.I)
_OOM_RE = re.compile(r"killed by SIG(KILL|9)|\boom\b|out of memory|MemoryError|exit code 137", re.I)
_PARSE_RE = re.compile(
    r"selector|could not parse|no products found|unrecognized header|schema|KeyError|"
    r"AttributeError|IndexError|JSONDecodeError|degraded", re.I)


def classify(status, error="", fail_class=None):
    """Which failure story is this?

    STRUCTURAL FIRST: run_one records `fail_class` on the run as a FACT — it saw the signal that killed
    the child, whether the timeout fired, what coverage said, and any `HOODIE_FAIL` the scraper declared.
    When that field is present it is authoritative and nothing is inferred.

    The status/error inspection below is the LEGACY FALLBACK, for ledger rows written before the field
    existed. Matching prose is guesswork — an error message is written for a human, so its wording drifts
    and the classifier silently drifts with it. Never make it the primary path."""
    if fail_class:
        return fail_class if fail_class in CLASSES else "unknown"
    e = str(error or "")
    if status in BENIGN or status == "no-creds":
        return None
    if _OOM_RE.search(e):
        return "oom"
    if _BLOCKED_RE.search(e):
        return "blocked"
    if status == "timeout":
        return "timeout"
    if status == "incomplete":
        return "incomplete"
    if _PARSE_RE.search(e):
        return "parse"
    if status == "empty":
        return "empty"
    return "unknown" if status in FAIL else None


def backoff_s(cf, klass=None):
    """Seconds to wait after `cf` consecutive failures before retrying, scaled by the failure CLASS.
    0 if none; QUARANTINE_S once quarantined; float('inf') for a class that must not auto-retry."""
    if cf <= 0:
        return 0.0
    spec = CLASSES.get(klass or "unknown", CLASSES["unknown"])
    if not spec["auto"]:
        return float("inf")                          # needs a human/code change — never auto-retried
    if cf >= QUARANTINE_AT:
        return QUARANTINE_S
    base = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (BACKOFF_FACTOR ** (cf - 1)))
    return max(60.0, base * spec["mult"])


def retry_due(now, last_attempt, cf, klass=None):
    """A failed source is due-for-retry once its class-scaled backoff has elapsed. A class marked
    auto=False has an infinite backoff, so it is never due — it waits for a human."""
    return cf > 0 and (now - last_attempt) >= backoff_s(cf, klass)


def quarantined(cf):
    return cf >= QUARANTINE_AT


def recent_by_source(limit=8):
    """({source: [statuses newest-first]}, {source: last_attempt_ts}) from BOTH ledgers."""
    rows = []
    for fn, name in ((warehouse.query, "source_runs"), (warehouse.query_parts, "source_runs_log")):
        try:
            try:
                rows += fn(name, "SELECT source, status, ts_start, error, fail_class FROM t")
            except Exception:
                # legacy partitions predate fail_class — fall back to inference for those rows
                rows += [dict(r, fail_class="") for r in fn(name, "SELECT source, status, ts_start, error FROM t")]
        except Exception:
            pass
    by = {}
    for r in rows:
        by.setdefault(r["source"], []).append(
            (float(r["ts_start"] or 0), r.get("status"), r.get("error") or "", r.get("fail_class") or ""))
    statuses, last_attempt, last_error, last_class = {}, {}, {}, {}
    for sid, lst in by.items():
        lst.sort(key=lambda t: t[0], reverse=True)   # newest first
        statuses[sid] = [st for _, st, _, _ in lst[:limit]]
        last_attempt[sid] = lst[0][0] if lst else 0
        last_error[sid] = lst[0][2] if lst else ""
        last_class[sid] = lst[0][3] if lst else ""
    return statuses, last_attempt, last_error, last_class


def states(now=None, statuses=None, last_attempt=None, last_error=None, last_class=None):
    """Per-source heal state, including WHICH failure story this is and whether it can be auto-retried
    at all: {source: {consecutive_failures, klass, story, auto, quarantined, retry_due, next_retry_in_s}}."""
    now = now or time.time()
    if statuses is None or last_attempt is None:
        statuses, last_attempt, last_error, last_class = recent_by_source()
    last_error, last_class = last_error or {}, last_class or {}
    out = {}
    for sid, sts in statuses.items():
        cf = consecutive_failures(sts)
        la = last_attempt.get(sid, 0)
        klass = classify(sts[0] if sts else None, last_error.get(sid, ""),
                         last_class.get(sid)) if cf > 0 else None
        spec = CLASSES.get(klass or "unknown", CLASSES["unknown"])
        bo = backoff_s(cf, klass) if cf > 0 else 0.0
        out[sid] = {
            "consecutive_failures": cf,
            "klass": klass,
            "story": spec["story"] if cf > 0 else None,
            "auto": bool(spec["auto"]) if cf > 0 else True,
            "quarantined": quarantined(cf),
            "retry_due": retry_due(now, la, cf, klass),
            "next_retry_in_s": (None if bo == float("inf")
                                else max(0, round(bo - (now - la)))) if cf > 0 else None,
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
