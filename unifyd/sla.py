"""sla.py — the per-source freshness SLA the sellable feed rests on (SCRAPING-PLATFORM.md P2).

"Reliable enough to sell" means we can state, at any moment, whether each source is within its freshness
CONTRACT. This reads the shared `source_runs` ledger (last SUCCESSFUL run per source, via
`run_sources.ledger_last`) against a per-source freshness target and reports fresh / at_risk / breach /
never — plus the roll-up (share within SLA) that IS the reliability number a buyer's contract rests on.

Freshness target = the registry's `freshness_h` if set, else `interval_h * SLA_SLACK` — a source more than
`SLA_SLACK` cycles past its last success has MISSED refreshes, i.e. breached. Deterministic, ledger-backed,
never mocked (the same ethos as the dispatcher: measured, not asserted).

    python sla.py            # print the SLA roll-up + any breaches
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_sources as rs
import source_registry as reg

SLA_SLACK = float(os.environ.get("SLA_SLACK", "2.0"))     # breach once age > this many cycles past success
AT_RISK = float(os.environ.get("SLA_AT_RISK", "0.8"))     # at_risk once age >= this fraction of the target


def target_h(s):
    """A source's freshness SLA in hours: explicit registry `freshness_h`, else interval_h * SLA_SLACK."""
    return float(s.get("freshness_h") or rs._interval_h(s) * SLA_SLACK)


def status(now=None, sources=None, last_ok=None):
    """Per-source freshness status against the SLA. `sources`/`last_ok` are injectable (tests); by default
    reads the enabled registry sources and the shared ledger's last-success timestamps."""
    now = now or time.time()
    if sources is None:
        sources = [s for s in reg.SOURCES if s.get("enabled")]
    if last_ok is None:
        _, last_ok = rs.ledger_last()
    out = []
    for s in sources:
        tgt = target_h(s) * 3600.0
        ok = last_ok.get(s["id"], 0)
        if not ok:
            st, age = "never", None
        else:
            age = now - float(ok)
            st = "breach" if age > tgt else ("at_risk" if age >= tgt * AT_RISK else "fresh")
        out.append({"source": s["id"], "target_h": round(target_h(s), 1),
                    "age_h": round(age / 3600.0, 2) if age is not None else None, "status": st})
    return out


def summary(now=None, sources=None, last_ok=None):
    """The sellable reliability number: how many sources are within their freshness SLA right now, plus the
    breach / never lists. `within_sla` counts fresh + at_risk (still inside contract); breach/never are out."""
    rows = status(now, sources, last_ok)
    n = len(rows)
    within = sum(1 for r in rows if r["status"] in ("fresh", "at_risk"))
    return {
        "sources": n,
        "within_sla": within,
        "within_pct": round(100.0 * within / n, 1) if n else None,
        "at_risk": [r["source"] for r in rows if r["status"] == "at_risk"],
        "breach": [r["source"] for r in rows if r["status"] == "breach"],
        "never": [r["source"] for r in rows if r["status"] == "never"],
    }


def main():
    s = summary()
    print("[sla] %s/%s sources within freshness SLA (%s%%)"
          % (s["within_sla"], s["sources"], s["within_pct"]))
    if s["at_risk"]:
        print("  at_risk: " + ", ".join(s["at_risk"]))
    if s["breach"]:
        print("  BREACH:  " + ", ".join(s["breach"]))
    if s["never"]:
        print("  never:   " + ", ".join(s["never"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
