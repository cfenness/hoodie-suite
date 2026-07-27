"""Tests for selfheal (SCRAPING-PLATFORM.md P2): python selfheal_test.py
Verifies retry-with-backoff + quarantine: consecutive-failure counting (a success resets, no-creds is
skipped), escalating+capped backoff, retry-due timing, quarantine after N fails, and the states roll-up.
Pure logic — injected statuses + timestamps, no warehouse, no network."""
import os
import sys

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)
os.environ.update(HEAL_BACKOFF_BASE_S="300", HEAL_BACKOFF_FACTOR="3", HEAL_BACKOFF_MAX_S="21600",
                  HEAL_QUARANTINE_AT="6", HEAL_QUARANTINE_S="86400")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selfheal as sh

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


# ── consecutive_failures: newest-first; a real success resets; no-creds/unknown skipped ───────────
ok("no failures", sh.consecutive_failures(["ok", "ok"]) == 0)
ok("3 leading fails", sh.consecutive_failures(["failed", "timeout", "empty", "ok"]) == 3)
ok("success resets (stops counting)", sh.consecutive_failures(["failed", "ok", "failed", "failed"]) == 1)
ok("no-creds neither counts nor resets", sh.consecutive_failures(["failed", "no-creds", "failed", "ok"]) == 2)
ok("current/no-change count as success", sh.consecutive_failures(["failed", "current"]) == 1
   and sh.consecutive_failures(["no-change"]) == 0)

# ── backoff escalates, caps, then quarantines ─────────────────────────────────────────────────────
ok("no backoff at 0 fails", sh.backoff_s(0) == 0.0)
ok("1st retry ~5min", sh.backoff_s(1) == 300.0)
ok("2nd ~15min, 3rd ~45min", sh.backoff_s(2) == 900.0 and sh.backoff_s(3) == 2700.0)
ok("backoff caps at 6h", sh.backoff_s(5) == 21600.0)          # 300*3^4=24300 → capped to 21600
ok("quarantine flips to daily probe", sh.backoff_s(6) == 86400.0 and sh.quarantined(6))
ok("not quarantined below the threshold", not sh.quarantined(5))

# ── retry_due: due once backoff elapsed since last attempt ─────────────────────────────────────────
NOW = 1_000_000.0
ok("not due before backoff (1 fail, 4min ago)", not sh.retry_due(NOW, NOW - 240, 1))   # 240 < 300
ok("due after backoff (1 fail, 6min ago)", sh.retry_due(NOW, NOW - 360, 1))            # 360 >= 300
ok("healthy source never retry-due", not sh.retry_due(NOW, NOW - 999999, 0))
ok("quarantined due only after a day", not sh.retry_due(NOW, NOW - 3600, 6)             # 1h < 24h
   and sh.retry_due(NOW, NOW - 90000, 6))                                              # 25h >= 24h

# ── states + retry_due_ids roll-up ────────────────────────────────────────────────────────────────
statuses = {
    "healthy": ["ok", "ok"],
    "blip":    ["failed"],                       # 1 fail
    "down":    ["failed"] * 7,                    # quarantined
    "recovered": ["ok", "failed", "failed"],     # reset by the recent ok
}
last_attempt = {"healthy": NOW - 100, "blip": NOW - 400, "down": NOW - 100000, "recovered": NOW - 50}
st = sh.states(now=NOW, statuses=statuses, last_attempt=last_attempt)
ok("blip: 1 fail, retry-due (400s > 300s)", st["blip"]["consecutive_failures"] == 1 and st["blip"]["retry_due"])
ok("down: quarantined", st["down"]["quarantined"] and st["down"]["consecutive_failures"] == 7)
ok("down: retry-due (100000s > 86400s daily probe)", st["down"]["retry_due"])
ok("recovered: 0 fails (a success reset it)", st["recovered"]["consecutive_failures"] == 0)
ok("healthy: not retry-due, next_retry None", not st["healthy"]["retry_due"] and st["healthy"]["next_retry_in_s"] is None)

ids = {sid for sid, v in st.items() if v["retry_due"]}
ok("retry-due set = blip + down (not healthy/recovered)", ids == {"blip", "down"})

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
