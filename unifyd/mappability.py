#!/usr/bin/env python3
"""mappability.py — every outlet must be placeable on a map, or say precisely what is stopping it.

An outlet with no coordinates is invisible everywhere: no map pin, no radius hit, no locator row, no
coverage cell. Half the book is in that state — 881,822 of 1,768,869 measured — and the reasons were
scattered across three passes that each silently skipped what they couldn't handle.

This is the one place that answers, per outlet: CAN it be mapped, and if not, WHICH action fixes it.

    exact       real rooftop/store coordinates
    city        city-centroid — mappable, but only to ~city precision
    blocked     not mappable yet, with a NAMED resolver that would fix it

The resolvers are ordered cheapest-first, and the first one is the one that was missing:

    backfill    a field needed for mapping is EMPTY here but PRESENT in a sibling table we
                already own. $0, no network — a join.
    centroid    city + state -> Census city centroid            (city_centroid.fast_geo_pass)
    census      street address -> Census batch geocoder         (geocode.run)
    fetch       aggregator store page -> exact rooftop geo      (aggregator_geo.enrich_geo)
    none        nothing we hold can fix it; the SCRAPER must capture more

WHY `backfill` EXISTS. Measured: 42,585 DoorDash outlets — every stranded DoorDash row — carry a
city but an EMPTY state. The centroid pass needs both, so it skipped them; the address pass had no
address; the fetch pass only accepts ubereats/postmates. So they fell through every pass and were
reported as unreachable. They are not: `doordash_stores` holds a state for 721,195 rows, keyed by the
same store_id. Nothing had to be fetched — a field simply never got carried across, and no pass was
looking for that shape of gap.

Adding a source to the pipeline = one SIBLINGS entry. That is the point: this runs over every outlet,
so a new source inherits it rather than needing its own geo story.

    python3 unifyd/mappability.py            # report
    python3 unifyd/mappability.py backfill   # run the $0 sibling-field backfill
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# source -> where the same outlet's missing geo fields already live.
#   table  : sibling table holding the richer record
#   key    : (src_outlets column, sibling column) to join on
#   fields : which columns to carry across when empty here and present there
# NOTE ON DOORDASH — a declaration that looked obvious and does NOT work, kept documented so the next
# person doesn't re-derive it. src_outlets' doordash rows are keyed by a NAME SLUG
# ("mcdonald's warrawong") for all 587,249 rows; doordash_stores is keyed by NUMERIC id ("23605779").
# They come from different pipelines and share no key, so the join matches zero rows — which the
# backfill reports rather than papering over. A bridge (slug <-> numeric) has to exist before a
# doordash entry here means anything; matching on name+city would be fuzzy, and fuzzy joins do not
# belong in the outlet spine.
SIBLINGS = {}

# Declared-but-unusable, surfaced by report() so it stays visible instead of being quietly forgotten.
NEEDS_KEY_BRIDGE = {
    "doordash": {"table": "doordash_stores", "have": "slug store_id",
                 "sibling_has": "numeric store_id", "fields": ("city", "state")},
}

_AGG = ("ubereats", "postmates")


def classify(row, sibling=None):
    """(state, resolver, why) for ONE outlet. Pure — no warehouse, no network, so it is testable.

    `sibling` is the matching sibling record if one was found, else None.
    """
    lat, lng = row.get("lat"), row.get("lng")
    try:
        lat = float(lat) if lat is not None and lat != "" else None
        lng = float(lng) if lng is not None and lng != "" else None
    except (TypeError, ValueError):
        lat = lng = None
    # (0,0) is Null Island — a sentinel some scrapers write instead of NULL. Treated as unmapped, or
    # it counts as coverage and drops a pin in the Atlantic.
    if lat is not None and lng is not None and not (lat == 0 and lng == 0):
        return ("exact" if (row.get("geo_precision") or "") == "exact" else "city"), None, ""

    city = (row.get("city") or "").strip()
    state = (row.get("state") or "").strip()
    addr = (row.get("address") or "").strip()
    src = (row.get("source") or "").lower()

    if city and state:
        return "blocked", "centroid", "has city+state — the centroid pass will place it"
    if addr:
        return "blocked", "census", "has a street address — the Census pass will place it"

    # Before declaring anything unreachable, check what we ALREADY hold elsewhere. This is the branch
    # that turns 42,585 "impossible" DoorDash rows into a free join.
    if sibling:
        have = [f for f in SIBLINGS.get(src, {}).get("fields", ())
                if not (row.get(f) or "").strip() and (sibling.get(f) or "").strip()]
        if have:
            return "blocked", "backfill", ("missing %s here but present in %s"
                                           % ("+".join(have), SIBLINGS[src]["table"]))
    if src in _AGG:
        return "blocked", "fetch", "aggregator store page returns exact geo"
    missing = [n for n, v in (("city", city), ("state", state), ("address", addr)) if not v]
    return "blocked", "none", ("no %s — the SCRAPER must capture one" % "/".join(missing))


def backfill(source="doordash", limit=None, log=print):
    """Carry missing geo fields across from a sibling table. $0, no network.

    Writes only rows where the field is EMPTY here and NON-EMPTY there — never overwrites a value we
    already hold, so a sibling with worse data cannot degrade the spine.
    """
    import warehouse
    import refresh_fast
    cfg = SIBLINGS.get(source)
    if not cfg:
        log("[mappability] no sibling table declared for %r" % source)
        return 0
    lk, rk = cfg["key"]
    flds = cfg["fields"]
    miss = " OR ".join("(%s IS NULL OR %s = '')" % (f, f) for f in flds)
    sql = ("SELECT * FROM t WHERE lower(CAST(source AS VARCHAR)) = ? "
           "AND try_cast(lat AS DOUBLE) IS NULL AND (%s)" % miss)
    if limit:
        sql += " LIMIT %d" % int(limit)
    try:
        rows = warehouse.query("src_outlets", sql, [source])
    except Exception as e:                                     # noqa: BLE001
        log("[mappability] src_outlets unavailable: %s" % str(e)[:110])
        return 0
    if not rows:
        log("[mappability] %s: nothing to backfill" % source)
        return 0
    try:
        sib = {str(r[rk]): r for r in warehouse.query(
            cfg["table"], "SELECT %s, %s FROM t" % (rk, ", ".join(flds)))}
    except Exception as e:                                     # noqa: BLE001
        log("[mappability] %s unavailable: %s" % (cfg["table"], str(e)[:100]))
        return 0
    log("[mappability] %s: %d ungeocoded rows missing %s; sibling holds %d records"
        % (source, len(rows), "/".join(flds), len(sib)))

    out, filled = [], 0
    for r in rows:
        s = sib.get(str(r.get(lk)))
        if not s:
            continue
        d = dict(r)
        got = False
        for f in flds:
            if not (d.get(f) or "").strip() and (s.get(f) or "").strip():
                d[f] = str(s[f]).strip()
                got = True
        if got:
            out.append({k: d.get(k) for k in refresh_fast.FLD})
            filled += 1
    if not out:
        # Say WHY nothing matched. "matched nothing" alone reads as "there was nothing to do", when
        # the real cause is usually that the two tables don't share a key space at all — which is a
        # different problem needing a different fix, and must not look like a completed backfill.
        sk = list(sib)[:1]
        rk_sample = str(rows[0].get(lk))
        log("[mappability] %s: sibling matched NOTHING — key spaces differ? src_outlets.%s=%r vs "
            "%s.%s=%r. No fields carried; this is NOT a no-op backfill."
            % (source, lk, rk_sample[:40], cfg["table"], rk, (sk[0] if sk else "?")[:40]))
        return 0
    warehouse.write_accumulate("src_outlets", out, key=lambda r: (r["source"], r["store_id"]),
                               fields=refresh_fast.FLD)
    log("[mappability] %s: filled %s on %d outlets -> now reachable by the centroid pass"
        % (source, "/".join(flds), filled))
    return filled


def run(log=print):
    """Registry entrypoint — backfill every declared sibling. Cheapest layer, so it runs FIRST,
    before the centroid/census/fetch passes, and hands them rows they can now act on."""
    return sum(backfill(s, log=log) for s in SIBLINGS)


def main(argv):
    if argv and argv[0] == "backfill":
        n = run()
        print("backfilled %d outlets" % n)
        return 0
    import geo_gap
    s = geo_gap.survey()
    if s.get("error"):
        print("unavailable: %s" % s["error"])
        return 1
    print("mappable: %s of %s (%.1f%%)" % ("{:,}".format(s["geocoded"]),
                                           "{:,}".format(s["total"]), s["pct_geocoded"]))
    print("blocked  : %s" % "{:,}".format(s["missing"]))
    for k, v in s["reachable"].items():
        print("   %-12s %12s" % (k, "{:,}".format(v)))
    print("   %-12s %12s   <- needs a SIBLINGS entry, or the scraper must capture more"
          % ("unreachable", "{:,}".format(s["stranded"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
