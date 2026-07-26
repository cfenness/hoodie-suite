#!/usr/bin/env python3
"""source_faith.py — the trustworthy "does every source actually land, on cadence?" signal.

The run-ledger (source_runs / source_runs_log) is the wrong thing to trust: it records what a RUN CLAIMED, and
its read is flaky (a transient Tigris GET error makes every source look like it never ran — the exact reason
there was "no clear sign of faith"). The honest signal is the DATA: for each enabled source, did its output
table actually get fresh rows, judged against that source's own cadence?

verdict per source:
  ok    — freshest output table is younger than its cadence window (daily=24h, weekly=168h, ×GRACE)
  stale — has data but it's older than the window (the daily source that quietly stopped landing)
  dead  — no output table / zero rows / never written
Reads via warehouse.list_datasets() (parquet footers — cheap + robust, retried), NOT the ledger. This is what
the health digest and the Data Console should key "is it working" off of.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import source_registry as reg

GRACE = float(os.environ.get("FAITH_GRACE", "1.6"))        # a run can slip a little past its cadence


def _datasets(retries=3):
    for a in range(retries):
        try:
            return {t["name"]: {"rows": t.get("rows", 0), "mod": t.get("modified", 0) or 0}
                    for t in warehouse.list_datasets()}
        except Exception:
            if a == retries - 1:
                raise
            time.sleep(2)
    return {}


def _window_h(src):
    if src.get("interval_h"):
        return float(src["interval_h"])
    return 168.0 if src.get("cadence") == "weekly" else 24.0


def scan(now=None):
    """[{id, klass, verdict, table, rows, age_h, window_h}] for every ENABLED source, worst first."""
    now = now or time.time()
    tbls = _datasets()
    out = []
    for s in reg.SOURCES:
        if not s.get("enabled"):
            continue
        best = None
        for tn in (s.get("tables") or []):
            i = tbls.get(tn)
            if i and i.get("rows") and (best is None or i["mod"] > best[1]["mod"]):
                best = (tn, i)
        win = _window_h(s)
        if not best:
            out.append({"id": s["id"], "klass": s.get("klass"), "verdict": "dead", "table": None,
                        "rows": 0, "age_h": None, "window_h": win})
            continue
        age_h = (now - best[1]["mod"]) / 3600 if best[1]["mod"] else None
        verdict = "stale" if (age_h is None or age_h > win * GRACE) else "ok"
        out.append({"id": s["id"], "klass": s.get("klass"), "verdict": verdict, "table": best[0],
                    "rows": best[1]["rows"], "age_h": age_h, "window_h": win})
    order = {"dead": 0, "stale": 1, "ok": 2}
    return sorted(out, key=lambda r: (order[r["verdict"]], -(r["age_h"] or 1e9)))


def summary(now=None):
    rows = scan(now)
    c = {"ok": 0, "stale": 0, "dead": 0}
    for r in rows:
        c[r["verdict"]] += 1
    return {"counts": c, "sources": rows,
            "broken": [r["id"] for r in rows if r["verdict"] in ("dead", "stale")]}


def report(now=None, log=print):
    rows = scan(now)
    c = {"ok": 0, "stale": 0, "dead": 0}
    for r in rows:
        c[r["verdict"]] += 1
    log("SOURCE FAITH — ok=%d stale=%d dead=%d (of %d enabled)" % (c["ok"], c["stale"], c["dead"], len(rows)))
    for r in rows:
        if r["verdict"] == "ok":
            continue
        age = ("%.1fd" % (r["age_h"] / 24)) if r["age_h"] else "never"
        log("  %-7s %-17s klass=%-8s table=%-20s rows=%-9s age=%s (window %.0fh)"
            % (r["verdict"].upper(), r["id"], r["klass"], r["table"] or "(none)", r["rows"], age, r["window_h"]))
    return c


def run(log=print):
    return report(log=log)


if __name__ == "__main__":
    report()
