"""The journal keeps every metric a run reports about itself.

The bug this pins down, and what it cost: run_journal.log() folded HOODIE_PROGRESS lines into the doc but
whitelisted exactly rows/stage/pct, dropped every other key, and then returned — so the extras did not
survive as log text either. Discarded twice over, silently.

ue_catalog was emitting rss_mb, ok, empty and unreachable in every heartbeat specifically so an OOM kill
would name its own cause. None of it ever reached the journal, and two rounds of memory debugging were
spent inferring from the outside what the run had already measured and reported.

Same discard-nothing rule the scrapers live under: the reporting layer does not get to decide that a
field the run bothered to measure is uninteresting.

Pure in-memory journal writes — no network, no warehouse.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("STATE_BUCKET", "")          # keep _flush local/no-op
import run_journal as rj  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


rid = "test-metrics-1"
rj.open_run(rid, "ubereats", label="t", klass="headless")

rj.log(rid, "HOODIE_PROGRESS " + json.dumps(
    {"rows": 4124, "stage": "171/62,938 stores", "pct": 0.3,
     "ok": 39, "empty": 35, "unreachable": 1972, "rss_mb": 3310}))
doc = rj._OPEN[rid]["doc"]

# the named counters still work exactly as before
check("rows_seen", doc.get("rows_seen"), 4124)
check("stage", doc.get("stage"), "171/62,938 stores")
check("pct", doc.get("pct"), 0.3)

# ...and the rest is no longer thrown away
met = doc.get("metrics") or {}
check("rss kept", met.get("rss_mb"), 3310)
check("ok kept", met.get("ok"), 39)
check("empty kept", met.get("empty"), 35)
check("unreachable kept", met.get("unreachable"), 1972)

# a progress line is still folded, never left in the log tail
check("not duplicated into log", any("HOODIE_PROGRESS" in l for l in doc.get("log") or []), False)

# later beats update, they don't accumulate junk
rj.log(rid, "HOODIE_PROGRESS " + json.dumps({"rows": 9000, "rss_mb": 1200}))
check("metric updates in place", (doc.get("metrics") or {}).get("rss_mb"), 1200)
check("earlier metric retained", (doc.get("metrics") or {}).get("ok"), 39)

# a runaway emitter cannot bloat the doc without bound
rj.log(rid, "HOODIE_PROGRESS " + json.dumps({"k%d" % i: i for i in range(200)}))
check("metric count bounded", len(doc.get("metrics") or {}) <= 4 + rj._METRIC_MAX, True)

# ordinary lines are unaffected
rj.log(rid, "[ue] resuming shard 7/8")
check("plain line still logged", any("resuming shard" in l for l in doc.get("log") or []), True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("── %d failed" % len(fails))
    sys.exit(1)
print("── journal metrics: every reported measure is kept (11 checks)")
