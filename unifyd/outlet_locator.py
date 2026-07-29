#!/usr/bin/env python3
"""outlet_locator.py — the locator's account layer, read from OUR outlet spine.

The Product Locator has been showing 150 accounts. Not because we hold 150 — `src_outlets` is ~1.76M
rows — but because the account list was a LIVE PASSTHROUGH to VIP's brand locator: a third party's
ZIP-keyed page walk, bounded at 3 pages of 50. We were paying to acquire a national outlet universe
and then rendering someone else's sample of it.

This is the account layer read from the spine we own. It answers "what accounts are near this point",
and it answers it with a TRUE COUNT — the number of matching accounts in the warehouse, which is not
the same as the number of pins shipped to a browser. Those two being conflated is exactly how "150"
came to look like an answer.

Layering, so it stays honest about what each layer knows:

  accounts (here)   1.76M outlets — WHERE they are, and what categories they're licensed/known to
                    sell (f_spirits / f_beer / f_wine / f_hemp). NOT product-level.
  offers            `locator_signal` — the ~2.6k stores where we hold priced observations, so we can
                    say verified-in-stock / good-price. Product-level, much narrower.
  distribution      VIP vtinfo — who the distributor says carries a brand. Third-party, brand-level.

An account layer cannot claim a store stocks a given bottle, and does not pretend to: `carries` is
returned as null unless an offer or distribution row backs it.

    python3 unifyd/outlet_locator.py 29.76 -95.36 25
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAX_POINTS = int(os.environ.get("LOCATOR_MAX_POINTS", "3000"))

# The category flags src_outlets actually carries. Anything not in here is not a filter we can honour,
# and asking for one is an error rather than a silently-ignored parameter.
CATEGORIES = ("spirits", "beer", "wine", "hemp", "cannabis", "rtd_spirits")


def _bbox(lat, lng, radius_mi):
    """Bounding box for a radius — a cheap pre-filter DuckDB can push down to the Parquet row groups.

    Haversine on 1.76M rows per request would be a full scan every time; the box narrows it to the
    neighbourhood first, then the exact circle is applied to what survives.
    """
    dlat = radius_mi / 69.0
    import math
    dlng = radius_mi / max(1e-6, 69.0 * math.cos(math.radians(lat)))
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def query(center, radius_mi=15.0, category=None, chains_only=None, q=None,
          limit=None, log=print):
    """Accounts near `center`. Returns {accounts, total, shown, truncated, by_source, by_category}.

    `total` is the count in the warehouse; `shown` is what we shipped. They differ whenever the
    result is bigger than a map can usefully draw, and the difference is stated rather than implied.
    """
    import warehouse
    limit = limit or MAX_POINTS
    if not center:
        return {"accounts": [], "total": 0, "shown": 0, "truncated": False,
                "error": "no location", "by_source": [], "by_category": {}}
    lat, lng = center
    s, n, w, e = _bbox(lat, lng, radius_mi)

    # NOT (0,0): Null Island. Some scrapers write 0.0 rather than NULL for "no location" — measured
    # live on ubereats and postmates, whose lat range starts at exactly 0.000. Left in, those rows
    # count as geocoded and drop pins in the Atlantic.
    where = ["try_cast(lat AS DOUBLE) IS NOT NULL", "try_cast(lng AS DOUBLE) IS NOT NULL",
             "NOT (try_cast(lat AS DOUBLE) = 0 AND try_cast(lng AS DOUBLE) = 0)",
             "try_cast(lat AS DOUBLE) BETWEEN ? AND ?", "try_cast(lng AS DOUBLE) BETWEEN ? AND ?"]
    params = [s, n, w, e]
    if category:
        if category not in CATEGORIES:
            return {"accounts": [], "total": 0, "shown": 0, "truncated": False,
                    "error": "unknown category %r (have: %s)" % (category, ", ".join(CATEGORIES)),
                    "by_source": [], "by_category": {}}
        where.append("f_%s = TRUE" % category)
    if chains_only is True:
        where.append("is_chain = TRUE")
    elif chains_only is False:
        where.append("(is_chain IS NULL OR is_chain = FALSE)")
    if q:
        where.append("(lower(CAST(store_name AS VARCHAR)) LIKE ? OR lower(CAST(chain AS VARCHAR)) LIKE ?)")
        params += ["%" + q.lower() + "%", "%" + q.lower() + "%"]
    wsql = " WHERE " + " AND ".join(where)

    try:
        rows = warehouse.query(
            "src_outlets",
            "SELECT CAST(source AS VARCHAR) AS source, CAST(store_id AS VARCHAR) AS store_id, "
            "CAST(store_name AS VARCHAR) AS store_name, CAST(chain AS VARCHAR) AS chain, "
            "is_chain, CAST(address AS VARCHAR) AS address, CAST(city AS VARCHAR) AS city, "
            "CAST(state AS VARCHAR) AS state, CAST(zip AS VARCHAR) AS zip, "
            "try_cast(lat AS DOUBLE) AS lat, try_cast(lng AS DOUBLE) AS lng, "
            "CAST(geo_precision AS VARCHAR) AS geo_precision, "
            "f_spirits, f_beer, f_wine, f_hemp, f_cannabis, f_rtd_spirits FROM t" + wsql, params)
    except Exception as ex:                                   # noqa: BLE001
        log("[accounts] src_outlets query failed: %s" % str(ex)[:110])
        return {"accounts": [], "total": 0, "shown": 0, "truncated": False,
                "degraded": "outlets-unavailable", "error": str(ex)[:140],
                "by_source": [], "by_category": {}}

    # Exact circle. The box is a square around it, so its corners reach ~1.41x the radius — without
    # this a "within 5 mi" filter quietly returns accounts 7 miles away.
    import locator_signal
    hits = []
    for r in rows:
        d = locator_signal.haversine_mi((lat, lng), (r["lat"], r["lng"]))
        if d <= radius_mi:
            r["distance_mi"] = round(d, 1)
            hits.append(r)
    hits.sort(key=lambda r: r["distance_mi"])

    by_source, by_cat = {}, {}
    for r in hits:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        for c in CATEGORIES:
            if r.get("f_" + c):
                by_cat[c] = by_cat.get(c, 0) + 1

    total = len(hits)
    shown = hits[:limit]
    for r in shown:
        # Never claim product-level stock from an account row. The offers layer sets this when it has
        # an observation; here it is explicitly unknown, not implicitly false.
        r["carries"] = None
    return {"accounts": shown, "total": total, "shown": len(shown),
            "truncated": total > len(shown), "limit": limit,
            "by_source": sorted(({"source": k, "n": v} for k, v in by_source.items()),
                                key=lambda x: -x["n"]),
            "by_category": by_cat}


def main(argv):
    if len(argv) < 2:
        print("usage: outlet_locator.py <lat> <lng> [radius_mi] [category]")
        return 1
    lat, lng = float(argv[0]), float(argv[1])
    rad = float(argv[2]) if len(argv) > 2 else 25.0
    cat = argv[3] if len(argv) > 3 else None
    res = query((lat, lng), radius_mi=rad, category=cat)
    if res.get("error"):
        print("error: %s" % res["error"]); return 1
    print("%d accounts within %.0f mi (showing %d)" % (res["total"], rad, res["shown"]))
    for s in res["by_source"][:12]:
        print("   %-16s %6d" % (s["source"], s["n"]))
    print("   categories: %s" % res["by_category"])
    for a in res["accounts"][:10]:
        print("  %-34s %-16s %5.1f mi  %s" % ((a["store_name"] or "?")[:34],
                                              (a["city"] or "")[:16], a["distance_mi"],
                                              a["geo_precision"] or "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
