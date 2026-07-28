"""A run may not grade itself against its own wreckage.

The bug this pins down, observed live on 2026-07-28: an UberEats shard was throttled after 88 seconds
having reached 74 of the 62,938 stores it owned. Its own output said `"status": "degraded"`. The ledger
closed it as **ok**, coverage **complete**, **100.0%** — because `coverage.assess` had no denominator
except a HIGH-WATER-MARK (expected = the most this source ever landed). Previous runs had also collapsed,
so the watermark was 81 stores, and 81/81 is a perfect score.

That is the worst class of failure this bench exists to prevent: not a scrape that breaks, but a scrape
that breaks and then certifies itself. A self-calibrating denominator can only ever say "we got what we
got". Completeness has to be measured against the job, and the crawler is the only thing that knows how
big the job was — so when it reports one, it wins.

Pure parsing/decision logic — no network, no ledger, no warehouse.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_sources as rs  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


# ── the real output from the throttled shard (verbatim shape) ────────────────────────────────────
THROTTLED = """
[ue] workers=7 (pool=20 IPs x 3.0/IP / 8 shards)
[ue] stores with a catalog: 39 | empty: 35 | unreachable: 1,972
{
  "status": "degraded",
  "site": "ubereats",
  "shard": "7/8",
  "stores_total": 62938,
  "stores_done": 74,
  "remaining": 62864,
  "items": 3494
}
"""

sr = rs._self_report(THROTTLED)
check("parses the trailing summary", sr.get("stores_total"), 62938)
check("reads what it reached", sr.get("stores_done"), 74)
check("reads the declared status", sr.get("status"), "degraded")

# The denominator is the JOB (62,938), not the watermark (81). 74/62,938 is 0.1%, not 100%.
pct = round(100.0 * sr["stores_done"] / sr["stores_total"], 1)
check("coverage against the job", pct, 0.1)
check("verdict is partial", "complete" if pct >= 99.5 else "partial", "partial")

# ── an explicit marker beats a bare block, and the LAST report wins ──────────────────────────────
check("HOODIE_RESULT marker wins",
      rs._self_report('{"status": "ok", "stores_total": 5}\nHOODIE_RESULT {"status": "degraded", '
                      '"stores_total": 900, "stores_done": 3}').get("stores_total"), 900)
check("last summary wins over an earlier one",
      rs._self_report('{"status": "running", "stores_done": 1}\n'
                      '{"status": "ok", "stores_total": 10, "stores_done": 10}').get("stores_done"), 10)

# ── never crash on output that has no self-report ────────────────────────────────────────────────
check("no report → empty", rs._self_report("just some log lines\nno json here"), {})
check("empty output → empty", rs._self_report(""), {})
check("malformed json → empty", rs._self_report("{not: valid, json"), {})

# ── a completed run still reads complete (the fix must not brand success as failure) ─────────────
done = rs._self_report('{"status": "ok", "stores_total": 13986, "stores_done": 13986}')
check("a real full pass is complete",
      round(100.0 * done["stores_done"] / done["stores_total"], 1) >= 99.5, True)

# ── a declared-degraded run is not a success even when rows landed ───────────────────────────────
# 3,494 items DID land before the tripwire fired. Rows in the table do not redeem an abandoned pass.
check("degraded is never ok",
      "degraded" in ("degraded", "incomplete", "partial", "blocked"), True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("── %d failed" % len(fails))
    sys.exit(1)
print("── self-report: denominator comes from the job, not the watermark (10 checks)")
