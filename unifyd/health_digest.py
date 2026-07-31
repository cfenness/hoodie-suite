#!/usr/bin/env python3
"""health_digest.py — the daily data-health verdict. ONE deterministic answer to "is every source serving the
right, current data?" — built for zero doubt: every finding is DETERMINISTIC, cites its evidence (real counts,
real timestamps, which log said so), and the whole digest is stamped with WHICH warehouse it read. It never
invents, never averages away a failure, and never stays silent about what it could not check.

What it rides on (all existing, all cheap):
  - monitor.build()            — footer row-counts + freshness + run-log health (the trusted manifest)
  - source_registry.SOURCES    — the CONTRACT: which sources should be running, at what cadence
  - source_runs (parquet)      — authoritative per-source run outcomes from run_sources.py
  - agent_state/monitor_hist   — row-count time-series (for collapse detection)

The checks (each finding carries code / severity / evidence):
  wrong-warehouse   CRIT  reading LOCAL instead of production — every other verdict would be about the wrong data
  manifest-error    CRIT  the warehouse could not be read at all
  run-failed        CRIT  a registry-enabled source's latest run failed / timed out / errored
  run-degraded      WARN  a run self-reported degraded (parser drift caught by the scraper's own selectors)
  rows-collapsed    CRIT  a pull table's count fell >COLLAPSE_PCT vs its previous point (or to zero)
  coverage-shortfall C/W  last run touched far fewer stores/items than expected — a partial scrape a
                          cumulative merge hides (table didn't shrink, so collapse/staleness can't see it)
  pull-stale        C/W   a registry-enabled source's data is older than its cadence allows
  run-no-change     WARN  a run "succeeded" but landed zero delta (run_sources' own bad-smell status)
  table-missing     WARN  a registry-enabled source's declared table doesn't exist in the warehouse
  no-creds          INFO  a creds-gated source is skipped because its env is absent (honest, known)

Findings persist a `first_seen` stamp across digests so a new break is distinguishable from a known one.
Exit code: 0 = all clear, 1 = warnings only, 2 = at least one critical.

Run:  python3 unifyd/health_digest.py [--json]     (creds via warehouse.env, same as monitor.py __main__)
Cron: unifyd/run_health_digest.sh (launchd com.hoodie.health, daily 07:30 — after the 03:00 sources pass)
"""
import json
import os
import sys
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
_OUT = os.path.join(_DIR, "agent_state", "health")

# ── thresholds: explicit, named, and echoed into every finding's evidence so a verdict is reproducible ─────────
CADENCE_S = {"daily": 86400, "weekly": 7 * 86400}
STALE_WARN_X = 2.0            # data older than 2× cadence  → WARN  (one missed run + slack)
STALE_CRIT_X = 4.0            # data older than 4× cadence  → CRIT  (the source is down, not late)
COLLAPSE_PCT = 0.40           # count fell by >40% vs previous point → CRIT (partial scrape landed as truth)
COLLAPSE_MIN_ROWS = 1000      # ignore collapse on tiny tables (noise, fixtures, seeds)
SLA_MIN_PCT = float(os.environ.get("SLA_MIN_PCT", "95"))   # sellable floor: WARN if <this% of sources within freshness SLA


def _sev_rank(s):
    return {"critical": 0, "warn": 1, "info": 2}[s]


def _ago(s):
    if s is None:
        return "never"
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return "%.1f%s" % (s / n, unit)
    return "%ds" % s


def _finding(code, severity, source, summary, evidence):
    return {"code": code, "severity": severity, "source": source, "summary": summary,
            "evidence": evidence, "kind": "DETERMINISTIC"}


def _latest_source_runs():
    """Newest run record per source id, unioned from BOTH ledgers — same idiom as `ledger_last`,
    `monitor`, `selfheal` and `cost_ledger`.

    This function used to read the legacy `source_runs` table ALONE, and it was the last consumer
    that never got the dual-read when `_land_runs` moved to the append-only `source_runs_log`
    partitions (2026-07-21). Measured in production 2026-07-29: legacy held 42 rows covering 17 of
    53 enabled sources with a newest `ts_end` 8.0 DAYS old, while the log held 316 rows covering all
    53 and was current. So every run-outcome check in the daily verdict — `run-failed`,
    `run-degraded`, `run-no-change` — was judging a third of the fleet on a week-old snapshot and
    the other two thirds not at all: 7 enabled sources sat `failed`/`timeout` in the log
    (cityhive, doordash-full, geo, outlet-union, sevennow, tax-rates, ubereats-enrich) and the
    digest could not see any of them. A verdict with nothing to judge reports healthy, which is the
    exact failure class this digest exists to catch.

    Union, don't switch: the legacy table is real history for sources that predate the log, and
    newest-`ts_end`-wins means a live log row supersedes its stale legacy twin. Safe on schema —
    the digest reads only `status`, `error` and `ts_end`, all three in `SR_FIELDS`."""
    import warehouse
    latest = {}
    for fn, name in ((warehouse.query, "source_runs"),            # legacy table (history)
                     (warehouse.query_parts, "source_runs_log")):  # append-only log (authoritative now)
        try:
            runs = fn(name, "SELECT * FROM t")
        except Exception:
            continue
        for r in runs:
            sid = r.get("source")
            if sid and (sid not in latest or (r.get("ts_end") or 0) > (latest[sid].get("ts_end") or 0)):
                latest[sid] = r
    return latest


def _registry_dataset_map(manifest):
    """registry source id → its manifest dataset records, matched EXACTLY on the tables the registry declares.
    Exact match only — a fuzzy match would attribute one source's health to another, which is worse than a gap."""
    by_name = {s["name"]: s for s in manifest.get("sources", [])}
    from source_registry import SOURCES
    out = []
    for src in SOURCES:
        ds = [by_name[t] for t in src.get("tables", []) if t in by_name]
        missing = [t for t in src.get("tables", []) if t not in by_name]
        out.append((src, ds, missing))
    return out


def build_digest(weekly=False):
    now = time.time()
    findings = []

    # Is production actually running what we deployed? `flyctl releases` reports that a deploy
    # succeeded, not what it deployed — production silently stopped matching main twice in one day
    # and was found only by hand. Cheap, and it fails LOUD: an unreachable app or a missing
    # baseline are both reported, never treated as "no drift".
    try:
        import deploy_drift
        findings.extend(deploy_drift.findings(log=lambda *_a, **_k: None))
    except Exception as e:                        # noqa: BLE001
        findings.append(_finding("drift-check-failed", "warn", "deploy",
                                 "the deploy-drift check itself did not run",
                                 str(e)[:120]))

    # An outlet with no lat/lng is invisible everywhere — no map pin, no radius hit, no locator row —
    # and nothing errors when it happens. The geo passes each have an entry condition, so an account
    # matching none of them is stranded silently and forever. Surface the count, per source.
    try:
        import geo_gap
        findings.extend(geo_gap.findings(log=lambda *_a, **_k: None))
    except Exception as e:                        # noqa: BLE001
        findings.append(_finding("geo-gap-check-failed", "warn", "geo",
                                 "the outlet geo-coverage survey did not run",
                                 str(e)[:120]))

    # creds first (same as monitor's __main__): otherwise we'd silently read the LOCAL fallback warehouse and
    # every freshness verdict below would be about the wrong data.
    try:
        import kroger_api
        kroger_api._load_creds()
    except Exception:
        pass

    import monitor
    manifest = monitor.build(record_history=True)
    wh = manifest.get("warehouse") or {}

    if not manifest.get("live") or manifest.get("error"):
        findings.append(_finding("manifest-error", "critical", "warehouse",
                                 "warehouse unreadable — NOTHING below was checked",
                                 {"error": manifest.get("error"), "warehouse": wh}))
        return _assemble(now, wh, manifest, findings, checked=0, weekly=weekly)

    if wh.get("kind") != "production":
        findings.append(_finding("wrong-warehouse", "critical", "warehouse",
                                 "digest is reading the %s warehouse, not production — creds not loaded?" % wh.get("kind"),
                                 {"warehouse": wh, "expected": "production (s3 STATE_BUCKET)"}))

    hist = monitor._load_hist()
    src_runs = _latest_source_runs()
    reg = _registry_dataset_map(manifest)
    enabled = [(s, ds, miss) for s, ds, miss in reg if s.get("enabled")]

    for src, datasets, missing in enabled:
        sid, label = src["id"], src["label"]
        cadence = CADENCE_S.get(src.get("cadence"), CADENCE_S["daily"])
        run = src_runs.get(sid)
        run_status = (run or {}).get("status") or ""
        run_err = (run or {}).get("error") or ""
        run_ts = (run or {}).get("ts_end")

        # creds-gated + env absent = a KNOWN skip, reported honestly, not an alarm
        need = [e for e in (src.get("requires") or []) if not os.environ.get(e)]
        if need:
            findings.append(_finding("no-creds", "info", sid,
                                     "%s skipped — missing %s" % (label, ", ".join(need)),
                                     {"requires": need, "cadence": src.get("cadence")}))
            continue

        if missing:
            findings.append(_finding("table-missing", "warn", sid,
                                     "%s declares table(s) that don't exist in the warehouse: %s" % (label, ", ".join(missing)),
                                     {"missing_tables": missing, "registry_tables": src.get("tables")}))

        # 1) the run log's own verdict — BUT the DATA is the ground truth (a run log can lie; the footer can't).
        # A failed/timed-out LEDGER entry is only a REAL current break if it left the data STALE. If the source's
        # data landed within its cadence window — from this path or any other — the source is healthy and the
        # failed ledger row is a stale artifact (e.g. abc-fws logged a 403 attempt while abc-facets kept landing
        # fresh data). Reporting that as a live CRITICAL is exactly what made the Console misleading. So gate the
        # run-failed finding on the data actually being stale; otherwise fall through to the freshness checks
        # (which will pass and emit nothing).
        newest = max((d.get("modified") or 0 for d in datasets), default=None) or None
        data_age = (now - newest) if newest else None
        if run_status in ("failed", "timeout", "error") and (data_age is None or data_age > cadence * STALE_WARN_X):
            findings.append(_finding("run-failed", "critical", sid,
                                     "%s: last run %s%s" % (label, run_status, (" — " + run_err[:160]) if run_err else ""),
                                     {"run_status": run_status, "error": run_err, "run_at": run_ts,
                                      "run_ago": _ago(now - run_ts) if run_ts else None,
                                      "data_age": _ago(data_age) if data_age is not None else "never",
                                      "tables": src.get("tables")}))
            continue  # don't ALSO report stale/no-change for the same source — one break, one finding

        degraded = [d["name"] for d in datasets if d.get("degraded")]
        if degraded:
            warns = [w for d in datasets if d.get("degraded") for w in (d.get("warnings") or [])][:6]
            findings.append(_finding("run-degraded", "warn", sid,
                                     "%s: run self-reported DEGRADED (parser drift) on %s" % (label, ", ".join(degraded)),
                                     {"datasets": degraded, "warnings": warns}))

        # 1b) COVERAGE — expected vs landed store/item counts for the last run. A cumulative merge never
        # shrinks, so freshness and collapse are BLIND to a blocked/partial scrape: the table stays full and
        # the run reads 'current'/'ok'. Coverage is the only signal that catches "it ran, it 'landed', but it
        # only touched 3% of the catalog." Severe shortfall (<50% of expected) = critical, else warn.
        try:
            import coverage as _cov
            cv = _cov.assess(src)
        except Exception:
            cv = None
        # Only when the source ACTUALLY recorded a run's coverage (last_ts>0). A source with a registry
        # `expected_*` but no coverage history yet would otherwise read 0/expected = "partial" and double-
        # report the existing never-landed/stale critical — that's noise, not a second failure.
        if cv and cv.get("last_ts"):
            short = [(dim, cv[dim]) for dim in ("items", "stores") if cv[dim].get("verdict") == "partial"]
            if short:
                pcts = [d["pct"] for _, d in short if d.get("pct") is not None]
                worst = min(pcts) if pcts else 0
                it, stx = cv["items"], cv["stores"]
                findings.append(_finding("coverage-shortfall", "critical" if worst < 50 else "warn", sid,
                    "%s: last run covered %s/%s items, %s/%s stores (%.0f%% of expected — partial scrape; the "
                    "table didn't shrink, so coverage is the ONLY signal)" % (
                        label, it["landed"], it["expected"], stx["landed"], stx["expected"], worst),
                    {"landed_items": it["landed"], "expected_items": it["expected"], "items_pct": it["pct"],
                     "landed_stores": stx["landed"], "expected_stores": stx["expected"], "stores_pct": stx["pct"],
                     "basis": cv["basis"]}))

        # 2) freshness vs the cadence CONTRACT — data mtime is the truth (a run log can lie; the footer can't)
        newest = max((d.get("modified") or 0 for d in datasets), default=None) or None
        age = (now - newest) if newest else None
        if age is None:
            findings.append(_finding("pull-stale", "critical", sid,
                                     "%s: enabled in the registry but has NEVER landed data" % label,
                                     {"tables": src.get("tables"), "cadence": src.get("cadence"),
                                      "last_run": run_status or "no run recorded"}))
        elif age > cadence * STALE_CRIT_X:
            findings.append(_finding("pull-stale", "critical", sid,
                                     "%s: data is %s old (cadence %s, critical past %s)" % (
                                         label, _ago(age), src.get("cadence"), _ago(cadence * STALE_CRIT_X)),
                                     {"data_age_s": round(age), "cadence": src.get("cadence"),
                                      "threshold": "%sx cadence" % STALE_CRIT_X,
                                      "last_run": run_status or "no run recorded",
                                      "last_run_ago": _ago(now - run_ts) if run_ts else "never"}))
        elif age > cadence * STALE_WARN_X:
            findings.append(_finding("pull-stale", "warn", sid,
                                     "%s: data is %s old (cadence %s, expected within %s)" % (
                                         label, _ago(age), src.get("cadence"), _ago(cadence * STALE_WARN_X)),
                                     {"data_age_s": round(age), "cadence": src.get("cadence"),
                                      "threshold": "%sx cadence" % STALE_WARN_X,
                                      "last_run": run_status or "no run recorded"}))
        elif run_status == "no-change":
            findings.append(_finding("run-no-change", "warn", sid,
                                     "%s: last run finished but landed ZERO delta" % label,
                                     {"run_at": run_ts, "run_ago": _ago(now - run_ts) if run_ts else None,
                                      "tables": src.get("tables")}))

    # 3) count collapse — across EVERY pull table (registry or not): a big drop that landed is a partial scrape
    # masquerading as truth, the single most dangerous silent failure.
    for s in manifest.get("sources", []):
        if s.get("group") not in ("scrape", "accounts", "timeseries"):
            continue
        series = hist.get(s["name"]) or []
        if len(series) < 2:
            continue
        prev, cur = series[-2][1], series[-1][1]
        if prev < COLLAPSE_MIN_ROWS:
            continue
        if cur == 0 or cur < prev * (1 - COLLAPSE_PCT):
            findings.append(_finding("rows-collapsed", "critical", s["name"],
                                     "%s: row count fell %s → %s (−%.0f%%) — partial/empty scrape landed as truth" % (
                                         s["name"], f"{prev:,}", f"{cur:,}", (1 - cur / prev) * 100),
                                     {"previous_rows": prev, "current_rows": cur,
                                      "previous_at": series[-2][0], "current_at": series[-1][0],
                                      "threshold_pct": COLLAPSE_PCT, "conn": s.get("conn")}))

    # 4) registry hygiene — the standing scraper rules, checked mechanically. A registry entry whose module
    # is gone can never run; a module living in BOTH unifyd/ and _archive/ is the exact two-iterations-side-
    # by-side failure the archive standard exists to prevent (kroger_api vs kroger_atlas).
    import re as _re
    archive = set()
    arch_dir = os.path.join(_DIR, "_archive")
    if os.path.isdir(arch_dir):
        archive = {f[:-3] for f in os.listdir(arch_dir) if f.endswith(".py")}
    from source_registry import SOURCES as _ALL
    for src in _ALL:
        mods = set(_re.findall(r"import (\w+)", src.get("code") or "")) - {"warehouse", "re", "sys", "os", "time", "json"}
        for mod in sorted(mods):
            live = os.path.exists(os.path.join(_DIR, mod + ".py"))
            if not live:
                findings.append(_finding("registry-module-missing", "warn", src["id"],
                                         "%s: registry imports `%s` but unifyd/%s.py does not exist%s" % (
                                             src["label"], mod, mod,
                                             " (it is in _archive/ — repoint or restore)" if mod in archive else ""),
                                         {"module": mod, "archived": mod in archive, "enabled": src.get("enabled")}))
            elif mod in archive:
                findings.append(_finding("archive-shadow", "warn", src["id"],
                                         "`%s` exists in BOTH unifyd/ and _archive/ — two iterations side-by-side "
                                         "(archive standard: exactly one active module per source)" % mod,
                                         {"module": mod, "registry_source": src["id"]}))

    # 5) degraded/failed on NON-registry connectors (agent-run pulls like census-acs live in runs.json only)
    reg_ids = {s["id"] for s, _, _ in reg}
    for c in manifest.get("connectors", []):
        if c["conn"] in reg_ids or c.get("status") not in ("error", "degraded"):
            continue
        findings.append(_finding("run-failed" if c["status"] == "error" else "run-degraded",
                                 "critical" if c["status"] == "error" else "warn", c["conn"],
                                 "connector %s: last run %s (%d warning(s))" % (
                                     c["conn"], c.get("run_status") or c["status"], c.get("warnings") or 0),
                                 {"run_status": c.get("run_status"), "run_at": c.get("run_ts"),
                                  "run_ago": _ago(now - c["run_ts"]) if c.get("run_ts") else None,
                                  "trigger": c.get("trigger")}))

    # SLA ROLL-UP — the sellable "reliable enough to sell" headline (SCRAPING-PLATFORM.md P2): the share of
    # sources within their freshness CONTRACT right now. The per-source detail is the pull-stale findings
    # above; this is the ONE number a buyer's contract rests on, tracked in the daily verdict. WARN (not
    # crit — the per-source breaks already carry the crit) once it dips below SLA_MIN_PCT.
    try:
        import sla
        ss = sla.summary(now=now)
        if ss.get("sources"):
            sev = "info" if (ss.get("within_pct") or 0) >= SLA_MIN_PCT else "warn"
            findings.append(_finding(
                "sla-rollup", sev, "platform",
                "%s%% of sources within freshness SLA (%d/%d) — %d breach, %d never, %d at-risk" % (
                    ss["within_pct"], ss["within_sla"], ss["sources"],
                    len(ss["breach"]), len(ss["never"]), len(ss["at_risk"])),
                {"within_pct": ss["within_pct"], "within_sla": ss["within_sla"], "sources": ss["sources"],
                 "min_pct": SLA_MIN_PCT, "breach": ss["breach"][:20], "never": ss["never"][:20],
                 "at_risk": ss["at_risk"][:20]}))
    except Exception as e:
        findings.append(_finding("sla-rollup", "info", "platform",
                                 "SLA roll-up unavailable: %s" % str(e)[:120], {}))

    # weekly deep audit (Mondays / --weekly): field-drift, fixture regression, docs drift — see deep_audit.py
    if weekly:
        import deep_audit
        findings.extend(deep_audit.run(manifest, now))

        # warehouse-wide DQ score (null-blowout + exact-duplicate scan across every master/conformed/
        # accounts/timeseries/scrape table) — same weekly-only cadence as deep_audit for the same
        # reason: real full-table scans, too heavy for the daily pass. Findings ride into this digest
        # like everything else above; the score itself is ALSO published separately to
        # `_dq_aggregate.json` so Cockpit can read a single number without parsing the whole digest.
        try:
            import dq_aggregate
            dqd = dq_aggregate.build(now=now)
            dq_aggregate.write(built=dqd, log=lambda *_a, **_k: None)
            findings.extend(dqd["findings"])
        except Exception as e:                                # noqa: BLE001
            findings.append(_finding("dq-check-failed", "warn", "dq",
                                     "the warehouse DQ aggregate did not run", str(e)[:120]))

    return _assemble(now, wh, manifest, findings, checked=len(enabled), weekly=weekly)


def _assemble(now, wh, manifest, findings, checked, weekly=False):
    findings.sort(key=lambda f: (_sev_rank(f["severity"]), f["source"]))
    counts = {"critical": 0, "warn": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1

    # first_seen: carry forward from the previous digest so NEW breaks stand out from known ones. The digest now
    # runs in the CLOUD (GitHub Actions / a fresh Fly machine each tick), where there is NO local latest.json — so
    # local-only carry-forward reset every finding's first_seen to "now" and marked them all `new`, which made
    # known errors look brand-new on every run. Fall back to the WAREHOUSE-published digest (_health_digest.json)
    # so first_seen persists across machines and a finding's true age is preserved.
    prev = {}
    try:
        with open(os.path.join(_OUT, "latest.json")) as fh:
            prev = {(f["code"], f["source"]): f.get("first_seen") for f in json.load(fh).get("findings", [])}
    except Exception:
        pass
    if not prev:
        try:
            import warehouse
            raw = warehouse.get_bytes("_health_digest.json")
            if raw:
                prev = {(f["code"], f["source"]): f.get("first_seen")
                        for f in json.loads(raw).get("findings", [])}
        except Exception:
            pass
    for f in findings:
        f["first_seen"] = prev.get((f["code"], f["source"])) or round(now)
        f["new"] = f["first_seen"] == round(now)

    t = manifest.get("totals") or {}
    return {
        "as_of": now, "generated_by": "health_digest.py", "weekly": weekly, "warehouse": wh,
        "ok": counts["critical"] == 0 and counts["warn"] == 0,
        "counts": counts, "sources_checked": checked,
        "new_criticals": sum(1 for f in findings if f["severity"] == "critical" and f["new"]),
        "totals": {"grand_rows": t.get("grand_rows"), "datasets": t.get("datasets"),
                   "last_activity": t.get("last_activity")},
        "findings": findings,
    }


def render_text(d):
    wh = d["warehouse"]
    c = d["counts"]
    lines = []
    lines.append("HOODIE DATA HEALTH — %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(d["as_of"])))
    lines.append("warehouse: %s (%s)   sources checked: %s   total rows: %s" % (
        wh.get("label"), wh.get("kind"), d["sources_checked"],
        f"{d['totals']['grand_rows']:,}" if d["totals"].get("grand_rows") else "?"))
    if d["ok"]:
        lines.append("")
        lines.append("ALL CLEAR — every registry-enabled source is current, no failed/degraded runs, no count collapses.")
        if c["info"]:
            lines.append("(%d known creds-gated skip(s) listed below.)" % c["info"])
    else:
        lines.append("VERDICT: %d CRITICAL / %d WARN / %d info%s" % (
            c["critical"], c["warn"], c["info"],
            "   ← %d NEW critical" % d["new_criticals"] if d["new_criticals"] else ""))
    lines.append("")
    for f in d["findings"]:
        tag = {"critical": "CRIT", "warn": "WARN", "info": "info"}[f["severity"]]
        age = "" if f["new"] else "  (since %s)" % time.strftime("%m-%d", time.localtime(f["first_seen"]))
        star = "NEW " if f["new"] and f["severity"] != "info" else "    "
        lines.append("  [%s] %s%s%s" % (tag, star, f["summary"], age))
        # evidence isn't always a dict — deploy_drift.findings() and every "the check itself did
        # not run" fallback (drift/geo-gap/dq, this file's own except-clauses) hand back a plain
        # string. That mismatch crashed main() outright ('str' object has no attribute 'items'),
        # which is how the digest went stale for 47+ hours in production without a single loud
        # failure — main() never got far enough to write even a degraded latest.json.
        raw_ev = f["evidence"]
        if isinstance(raw_ev, dict):
            ev = {k: v for k, v in raw_ev.items() if v not in (None, "", [])}
            if ev:
                lines.append("         evidence: " + json.dumps(ev, default=str)[:240])
        elif raw_ev:
            lines.append("         evidence: " + str(raw_ev)[:240])
    return "\n".join(lines) + "\n"


def main(argv):
    d = build_digest(weekly="--weekly" in argv)
    os.makedirs(_OUT, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime(d["as_of"]))
    with open(os.path.join(_OUT, "digest-%s.json" % stamp), "w") as fh:
        json.dump(d, fh, indent=1, default=str)
    with open(os.path.join(_OUT, "latest.json"), "w") as fh:
        json.dump(d, fh, indent=1, default=str)
    text = render_text(d)
    with open(os.path.join(_OUT, "latest.txt"), "w") as fh:
        fh.write(text)
    try:                                     # ALSO to the warehouse bucket, so the Fly-served Data Console can
        import warehouse                     # show the SAME verdict the Mac produced (server: /api/monitor/health)
        warehouse.put_bytes("_health_digest.json", json.dumps(d, default=str).encode("utf-8"))
    except Exception:
        pass
    print(json.dumps(d, indent=1, default=str) if "--json" in argv else text)
    return 2 if d["counts"]["critical"] else (1 if d["counts"]["warn"] else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
