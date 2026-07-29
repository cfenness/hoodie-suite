#!/usr/bin/env python3
"""geo_gap.py — which accounts have no geo, and WHICH PASS (if any) will ever fix them.

An outlet with no lat/lng is invisible: it cannot appear on the coverage map, cannot be counted in a
radius, cannot back the locator. The daily `geo` pipeline drains three backlogs, but each one has an
entry condition:

    fast-geo      needs city + state          (city centroid, $0)
    geocode       needs a street address      (Census batch, $0)
    aggregator    needs source ubereats/postmates (store-page fetch, $0)

An outlet meeting NONE of those is stranded permanently. Nothing errors, nothing retries, and no
counter anywhere goes up — it simply never appears. That is the failure mode this module exists to
make visible: not "geocoding is broken" (it isn't), but "N accounts are outside every pass's reach,
and here is which sources they belong to."

The output is deliberately per-source, because the fix differs per source: a source stranded for lack
of a city needs its scraper to capture one, which is a scraper change, not a geo change.

    python3 unifyd/geo_gap.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_AGG = ("ubereats", "postmates")


def survey(log=print):
    """{total, geocoded, missing, reachable{...}, stranded, by_source[...]} — all measured, none assumed."""
    import warehouse
    has_gp = False
    try:
        has_gp = warehouse.has_column("src_outlets", "geo_precision")
    except Exception:                                          # noqa: BLE001
        pass

    # One pass, one GROUP BY: classify every row by the pass that could reach it. Doing this as four
    # separate COUNT queries would let the table move between them and produce a set of numbers that
    # never coexisted.
    # lat/lng of exactly 0 is Null Island — a sentinel some scrapers write instead of NULL. Counting
    # it as geocoded inflates coverage AND drops a pin in the Atlantic, so it is treated as missing.
    cls = ("CASE WHEN try_cast(lat AS DOUBLE) IS NOT NULL AND NOT "
           "(try_cast(lat AS DOUBLE) = 0 AND try_cast(lng AS DOUBLE) = 0) THEN 'geocoded' "
           "     WHEN city IS NOT NULL AND city <> '' AND state IS NOT NULL AND state <> '' "
           "       THEN 'fast-geo' "
           "     WHEN address IS NOT NULL AND address <> '' THEN 'geocode' "
           "     WHEN lower(CAST(source AS VARCHAR)) IN ('ubereats','postmates') THEN 'aggregator' "
           "     ELSE 'stranded' END")
    try:
        rows = warehouse.query(
            "src_outlets",
            "SELECT CAST(source AS VARCHAR) AS source, %s AS bucket, count(*) AS n, "
            "%s AS precision FROM t GROUP BY 1, 2, 4 ORDER BY 3 DESC"
            % (cls, "CAST(geo_precision AS VARCHAR)" if has_gp else "NULL"))
    except Exception as e:                                     # noqa: BLE001
        log("[geo-gap] src_outlets unavailable: %s" % str(e)[:110])
        return {"error": str(e)[:140], "degraded": "outlets-unavailable"}

    tot = {}
    per_src = {}
    for r in rows:
        b, n, src = r["bucket"], int(r["n"] or 0), r["source"] or "?"
        tot[b] = tot.get(b, 0) + n
        d = per_src.setdefault(src, {"source": src, "total": 0, "geocoded": 0, "fast-geo": 0,
                                     "geocode": 0, "aggregator": 0, "stranded": 0})
        d["total"] += n
        d[b] += n

    total = sum(tot.values())
    geocoded = tot.get("geocoded", 0)
    by_source = sorted(per_src.values(), key=lambda d: -(d["total"] - d["geocoded"]))
    return {"total": total, "geocoded": geocoded, "missing": total - geocoded,
            "pct_geocoded": round(100.0 * geocoded / max(1, total), 1),
            "reachable": {k: tot.get(k, 0) for k in ("fast-geo", "geocode", "aggregator")},
            "stranded": tot.get("stranded", 0), "by_source": by_source}


def findings(log=print):
    """health_digest-shaped findings. Stranded accounts are a WARN with per-source evidence, because
    the number is only actionable once you know which scraper has to start capturing a city."""
    s = survey(log=log)
    if s.get("error"):
        return [{"code": "geo-gap-unavailable", "severity": "warn", "source": "geo",
                 "summary": "could not survey outlet geo coverage — gap is UNKNOWN, not zero",
                 "evidence": s["error"], "kind": "DETERMINISTIC"}]
    out = []
    if s["stranded"]:
        worst = [d for d in s["by_source"] if d["stranded"]][:5]
        out.append({
            "code": "geo-stranded", "severity": "warn", "source": "geo",
            "summary": "%s accounts (%.1f%% of the book) can never be geocoded — no pass matches them"
                       % ("{:,}".format(s["stranded"]), 100.0 * s["stranded"] / max(1, s["total"])),
            "evidence": "no city+state, no address, not an aggregator source. Worst: "
                        + "; ".join("%s %s" % (d["source"], "{:,}".format(d["stranded"])) for d in worst)
                        + ". Fix is in the SCRAPER (capture a city or address), not the geo pipeline.",
            "kind": "DETERMINISTIC"})
    pending = sum(s["reachable"].values())
    if pending:
        out.append({
            "code": "geo-backlog", "severity": "info", "source": "geo",
            "summary": "%s accounts are un-geocoded but reachable by a pass" % "{:,}".format(pending),
            "evidence": "fast-geo %s · geocode %s · aggregator %s — the daily `geo` run drains these"
                        % tuple("{:,}".format(s["reachable"][k])
                                for k in ("fast-geo", "geocode", "aggregator")),
            "kind": "DETERMINISTIC"})
    return out


def main():
    s = survey()
    if s.get("error"):
        print("geo-gap unavailable: %s" % s["error"])
        return 1
    print("src_outlets: %s accounts · %s geocoded (%.1f%%) · %s missing"
          % ("{:,}".format(s["total"]), "{:,}".format(s["geocoded"]), s["pct_geocoded"],
             "{:,}".format(s["missing"])))
    print("  reachable by a pass: fast-geo %s · geocode %s · aggregator %s"
          % tuple("{:,}".format(s["reachable"][k]) for k in ("fast-geo", "geocode", "aggregator")))
    print("  STRANDED (no pass matches): %s" % "{:,}".format(s["stranded"]))
    print("\n  %-18s %10s %10s %10s %10s" % ("source", "total", "geocoded", "pending", "stranded"))
    for d in s["by_source"][:25]:
        pend = d["fast-geo"] + d["geocode"] + d["aggregator"]
        print("  %-18s %10s %10s %10s %10s"
              % (d["source"][:18], "{:,}".format(d["total"]), "{:,}".format(d["geocoded"]),
                 "{:,}".format(pend), "{:,}".format(d["stranded"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
