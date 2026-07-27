"""Tests for sla (SCRAPING-PLATFORM.md P2): python sla_test.py
Verifies per-source freshness status (fresh/at_risk/breach/never) against the SLA target and the sellable
roll-up (% within SLA). Injects sources + a synthetic last-success ledger; no warehouse, no network."""
import os
import sys

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)
os.environ["SLA_SLACK"] = "2.0"      # breach at >2 cycles stale
os.environ["SLA_AT_RISK"] = "0.8"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sla

passed = failed = 0


def ok(name, cond):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name)
    passed += bool(cond)
    failed += not cond


H = 3600.0
NOW = 1_000_000.0
# interval_h=24 → target = 24*2 = 48h. freshness at 10h, at_risk ~40h, breach >48h.
SRCS = [
    {"id": "fresh_src", "enabled": True, "interval_h": 24},
    {"id": "atrisk_src", "enabled": True, "interval_h": 24},
    {"id": "breach_src", "enabled": True, "interval_h": 24},
    {"id": "never_src", "enabled": True, "interval_h": 24},
    {"id": "tight_src", "enabled": True, "interval_h": 24, "freshness_h": 6},  # explicit SLA overrides
]
LAST_OK = {
    "fresh_src": NOW - 10 * H,     # 10h < 48h → fresh
    "atrisk_src": NOW - 42 * H,    # 42h ≥ 0.8*48=38.4 → at_risk
    "breach_src": NOW - 60 * H,    # 60h > 48h → breach
    # never_src: absent → never
    "tight_src": NOW - 10 * H,     # 10h > freshness_h=6 → breach (explicit SLA is stricter)
}

rows = {r["source"]: r for r in sla.status(now=NOW, sources=SRCS, last_ok=LAST_OK)}
ok("fresh within SLA", rows["fresh_src"]["status"] == "fresh")
ok("at_risk near the target", rows["atrisk_src"]["status"] == "at_risk")
ok("breach past the target", rows["breach_src"]["status"] == "breach")
ok("never-succeeded is 'never'", rows["never_src"]["status"] == "never" and rows["never_src"]["age_h"] is None)
ok("explicit freshness_h overrides interval*slack", rows["tight_src"]["target_h"] == 6.0 and rows["tight_src"]["status"] == "breach")
ok("default target = interval_h * SLA_SLACK", rows["fresh_src"]["target_h"] == 48.0)
ok("age reported in hours", rows["fresh_src"]["age_h"] == 10.0)

s = sla.summary(now=NOW, sources=SRCS, last_ok=LAST_OK)
# within SLA = fresh + at_risk = fresh_src, atrisk_src = 2 of 5
ok("roll-up counts within-SLA (fresh+at_risk)", s["within_sla"] == 2 and s["sources"] == 5)
ok("within_pct is the sellable number", s["within_pct"] == 40.0)
ok("breach list", set(s["breach"]) == {"breach_src", "tight_src"})
ok("never list", s["never"] == ["never_src"])
ok("at_risk list", s["at_risk"] == ["atrisk_src"])

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
