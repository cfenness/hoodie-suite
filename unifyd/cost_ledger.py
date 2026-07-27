#!/usr/bin/env python3
"""cost_ledger.py — make "the recurring bill stays near zero" VERIFIABLE (NRT-PLAN.md Phase 6 / §6).

The plan's whole principle is spend-up-front so ongoing cost is near zero: everything runs on free
tiers (the Mac, GitHub Actions minutes, Tigris object storage), and the only real recurring spend is
metered anti-bot infra (Bright Data requests, residential-proxy GB) on the few sources that need it.
This turns that claim into a number.

MEASURED (from the append-only run ledger — real, not modeled):
  • runs / month per source, total machine-seconds (Σ duration_s), data volume (Σ positive row deltas)
MODELED (env-configurable rates, clearly labelled ESTIMATE — the $ overlay):
  • compute $:  machine-hours × rate for the source's host. Mac = $0, Actions = $0 within free minutes,
    the future ephemeral Fly worker = COST_WORKER_USD_PER_HR (default 0 until we adopt it).
  • infra $:    per the source's registry `cost_class` — free/mac = $0, `bd` ≈ per-1k requests,
    `proxy` ≈ per-GB. Rough by design; the point is the SHAPE (near-zero + where the spend is).

    python cost_ledger.py                 # 30-day cost report, per source + rollup
    python cost_ledger.py --window 7 --json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Env-configurable rates. Defaults reflect TODAY: free Mac + free Actions + Tigris free tier => $0
# recurring, so the ledger honestly reads near-zero. Flip COST_WORKER_USD_PER_HR when the paid worker
# is adopted (NRT §2 gate); set BD/proxy rates to your contract.
def _rate(name, default):
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return float(default)


def rates():
    return {
        "worker_usd_per_hr": _rate("COST_WORKER_USD_PER_HR", 0.0),   # ephemeral Fly worker (0 until adopted)
        "actions_usd_per_hr": _rate("COST_ACTIONS_USD_PER_HR", 0.0),  # GH Actions (free minutes)
        "mac_usd_per_hr": _rate("COST_MAC_USD_PER_HR", 0.0),          # the Mac (owned)
        "bd_usd_per_1k": _rate("COST_BD_USD_PER_1K", 1.5),            # Bright Data ~ $1.5 / 1k requests
        "proxy_usd_per_gb": _rate("COST_PROXY_USD_PER_GB", 3.0),      # residential/ISP ~ $3 / GB
        "tigris_usd_per_gb_mo": _rate("COST_TIGRIS_USD_PER_GB_MO", 0.0),  # free tier
    }

# Rough per-RUN infra units by cost_class → converted to $ via the rates above. Deliberately coarse:
# the ledger surfaces WHERE spend concentrates, not an invoice. Override per source with `bd_calls`
# / `proxy_gb` in the registry when a real figure is known.
_CLASS_UNITS = {
    "free":  {"bd_calls": 0,    "proxy_gb": 0.0},   # direct HTTP / official API
    "mac":   {"bd_calls": 0,    "proxy_gb": 0.0},   # headful browser on the owned Mac
    "bd":    {"bd_calls": 1500, "proxy_gb": 0.0},   # Bright Data metered pull (~a full catalog sweep)
    "proxy": {"bd_calls": 0,    "proxy_gb": 1.5},   # residential/ISP proxy sweep
}
# host each source's compute runs on (for the compute-$ rate). mac-class → mac; everything else the
# cloud runner today. This is where machine-seconds get priced.
def _host_of(src):
    return "mac" if src.get("klass") == "mac" else "actions"


def report(window_days=30, log=print):
    import warehouse
    import source_registry as reg
    now = time.time()
    since = now - window_days * 86400
    by = {}   # source -> aggregates
    sql = ("SELECT source, COUNT(*) runs, SUM(duration_s) secs, "
           "SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) added "
           "FROM t WHERE ts_start >= %d GROUP BY source" % int(since))
    for fn, name in ((warehouse.query, "source_runs"), (warehouse.query_parts, "source_runs_log")):
        try:
            for r in fn(name, sql):
                a = by.setdefault(r["source"], {"runs": 0, "secs": 0.0, "added": 0})
                a["runs"] += int(r["runs"] or 0)
                a["secs"] += float(r["secs"] or 0)
                a["added"] += int(r["added"] or 0)
        except Exception:
            pass
    R = rates()
    per_month = 30.0 / max(window_days, 1)
    src_by_id = {s["id"]: s for s in reg.SOURCES}
    src_by_id.update({b["id"]: b for b in getattr(reg, "BUILDS", [])})
    rows, tot = [], {"machine_hours": 0.0, "compute_usd": 0.0, "infra_usd": 0.0, "runs": 0, "added": 0}
    by_class, by_tier = {}, {}
    for sid, a in sorted(by.items(), key=lambda kv: -kv[1]["secs"]):
        s = src_by_id.get(sid, {})
        cls = s.get("cost_class") or ("build" if sid.startswith("build-") else "free")
        host = _host_of(s)
        hours = a["secs"] / 3600.0
        compute = hours * R["%s_usd_per_hr" % ("mac" if host == "mac" else "actions")]
        units = _CLASS_UNITS.get(cls, _CLASS_UNITS["free"])
        bd_calls = s.get("bd_calls", units["bd_calls"]) * a["runs"]
        proxy_gb = s.get("proxy_gb", units["proxy_gb"]) * a["runs"]
        infra = bd_calls / 1000.0 * R["bd_usd_per_1k"] + proxy_gb * R["proxy_usd_per_gb"]
        row = {"source": sid, "label": s.get("label", sid), "cost_class": cls, "host": host,
               "tier": s.get("tier") or _tier(s), "runs": a["runs"], "machine_hours": round(hours, 2),
               "rows_added": a["added"], "compute_usd_mo": round(compute * per_month, 2),
               "infra_usd_mo": round(infra * per_month, 2),
               "total_usd_mo": round((compute + infra) * per_month, 2)}
        rows.append(row)
        tot["machine_hours"] += hours
        tot["compute_usd"] += compute * per_month
        tot["infra_usd"] += infra * per_month
        tot["runs"] += a["runs"]
        tot["added"] += a["added"]
        by_class[cls] = round(by_class.get(cls, 0) + row["total_usd_mo"], 2)
        by_tier[row["tier"]] = round(by_tier.get(row["tier"], 0) + row["total_usd_mo"], 2)
    tot = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in tot.items()}
    tot["total_usd_mo"] = round(tot["compute_usd"] + tot["infra_usd"], 2)
    return {"window_days": window_days, "as_of": now, "rates": R, "sources": rows,
            "totals": tot, "by_class": by_class, "by_tier": by_tier}


def _tier(s):
    iv = float(s.get("interval_h") or (168 if s.get("cadence") == "weekly" else 24))
    return "hot" if iv <= 8 else "daily" if iv <= 24 else "weekly"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recurring-cost ledger from the run ledger + registry.")
    ap.add_argument("--window", type=int, default=30, help="lookback days")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = report(a.window)
    if a.json:
        print(json.dumps(rep, indent=2))
        return 0
    t = rep["totals"]
    print("Cost ledger — last %d days (× %.2f → monthly)" % (a.window, 30.0 / max(a.window, 1)))
    print("  MEASURED: %d runs · %.1f machine-hours · %s rows added"
          % (t["runs"], t["machine_hours"], f"{t['added']:,}"))
    print("  EST MONTHLY: $%.2f total  (compute $%.2f + infra $%.2f)"
          % (t["total_usd_mo"], t["compute_usd"], t["infra_usd"]))
    print("  by cost_class: %s" % ", ".join("%s=$%.2f" % (k, v) for k, v in sorted(rep["by_class"].items(), key=lambda x: -x[1])))
    print("  %-18s %-7s %6s %8s %10s %9s %9s" % ("source", "class", "runs", "mach-hr", "rows+", "infra$", "total$"))
    for r in rep["sources"][:25]:
        print("  %-18s %-7s %6d %8.1f %10s %9.2f %9.2f"
              % (r["source"][:18], r["cost_class"], r["runs"], r["machine_hours"],
                 f"{r['rows_added']:,}", r["infra_usd_mo"], r["total_usd_mo"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
