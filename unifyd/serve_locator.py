#!/usr/bin/env python3
"""serve_locator.py — the ON-PREMISE half of the locator: where can I DRINK this?

The locator answers "where can I buy the bottle". The other half of the original idea was a place
that makes the drink well — "somewhere near me that makes a great Old Fashioned". That is an
on-premise SERVE, not a bottle, and the spine for it already exists:

  menu_beverages       one row per drink: name, price, category + cocktail_taxonomy's
                       root / sub / base_spirit  (Spirit-forward -> Old Fashioned -> whiskey)
  src_outlets          the outlet spine, with coordinates

The taxonomy and pricing work today. What did NOT work was geography, and the reason is worth
recording: `menu_beverages` carries only an account NAME, and matching a name to an outlet is the
ambiguity problem in miniature. Measured on the current 24 accounts:

    8  resolve to exactly one Florida outlet     -> real coordinates
    4  are chains (Beef O Brady's has 35 FL sites, Ford's Garage 14, Ruby Tuesday 11)
   12  have no outlet record at all

So a third can be placed and two thirds cannot, and the only honest thing to do with a chain whose
menu we hold but whose location we do not is to SAY SO. Putting a pin on one of 35 Beef O Brady's
would be a fabrication, and a locator that sends someone to the wrong branch is worse than one that
admits it does not know which branch.

Every serve therefore carries a `location` verdict — `resolved`, `ambiguous`, or `unlocated` — and
only `resolved` ones are distance-ranked. The counts of the other two are returned, never dropped
silently.

    python3 unifyd/serve_locator.py "old fashioned"
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NAME_KEY_LEN = int(os.environ.get("SERVE_KEY_LEN", "16"))


def name_key(s):
    """Leading alphanumerics of a venue name. Short and blunt on purpose — it is only ever used to
    look up CANDIDATES, and a candidate set of size != 1 is refused rather than guessed."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())[:NAME_KEY_LEN]


def resolve(account, outlets_by_key):
    """(verdict, outlet_or_none, n_candidates).

    resolved  — exactly one outlet carries this name in scope; use its coordinates
    ambiguous — a chain: the menu is real, the branch is unknown, and we will not invent one
    unlocated — no outlet record at all
    """
    cands = outlets_by_key.get(name_key(account)) or []
    if len(cands) == 1:
        return "resolved", cands[0], 1
    if len(cands) > 1:
        return "ambiguous", None, len(cands)
    return "unlocated", None, 0


def matches(row, q):
    """Does this drink answer the query? Matches the drink NAME, the taxonomy's sub-family (so
    "old fashioned" finds a "Million Dollar Old Fashion"), or the base spirit (so "bourbon" finds
    every bourbon drink)."""
    q = (q or "").strip().lower()
    if not q:
        return False
    return any(q in (row.get(f) or "").lower() for f in ("name", "sub", "root", "base_spirit"))


def build(beverages, outlets_by_key, q, center=None, radius_mi=None):
    """The pure core: drinks matching `q`, each with a location verdict.

    Returns {serves, ambiguous, unlocated, out_of_range} — the last three are COUNTS, because a
    serve we cannot place is still evidence the drink is poured nearby, and silently dropping it
    would overstate how little we know.
    """
    import locator_signal
    hits = [b for b in beverages if matches(b, q)]
    serves, ambiguous, unlocated, out_of_range = [], 0, 0, 0
    for b in hits:
        verdict, outlet, n = resolve(b.get("account"), outlets_by_key)
        if verdict == "ambiguous":
            ambiguous += 1
            continue
        if verdict == "unlocated":
            unlocated += 1
            continue
        lat, lng = outlet.get("lat"), outlet.get("lng")
        dist = None
        if center and lat is not None and lng is not None:
            dist = round(locator_signal.haversine_mi(center, (lat, lng)), 1)
            if radius_mi and dist > radius_mi:
                out_of_range += 1
                continue
        serves.append({
            "account": b.get("account"), "venue": outlet.get("store_name") or b.get("account"),
            "city": outlet.get("city"), "state": outlet.get("state"),
            "lat": lat, "lng": lng, "distance_mi": dist,
            "drink": b.get("name"), "description": b.get("description"),
            "price": _num(b.get("price")), "category": b.get("category"),
            "root": b.get("root"), "sub": b.get("sub"), "base_spirit": b.get("base_spirit"),
            "location": "resolved",
        })
    serves.sort(key=lambda s: (s["distance_mi"] if s["distance_mi"] is not None else 9e9,
                               s["price"] if s["price"] is not None else 9e9))
    return {"serves": serves, "ambiguous": ambiguous, "unlocated": unlocated,
            "out_of_range": out_of_range}


def _num(v):
    try:
        f = float(v)
        return f if f == f and f > 0 else None
    except (TypeError, ValueError):
        return None


# --- warehouse-backed ---------------------------------------------------------------------------
def _outlets_by_key(state=None, log=print):
    """name_key -> [outlet]. Scoped to a state when given, because a national name index turns every
    chain ambiguous and resolves nothing."""
    import warehouse
    sql = "SELECT store_name, city, state, lat, lng FROM t WHERE lat IS NOT NULL"
    params = []
    if state:
        sql += " AND state = ?"
        params.append(state)
    try:
        rows = warehouse.query("src_outlets", sql, params)
    except Exception as e:                                    # noqa: BLE001
        log("[serves] src_outlets unavailable: %s" % e)
        return {}
    idx = {}
    for r in rows:
        idx.setdefault(name_key(r.get("store_name")), []).append(r)
    return idx


def serves(query, center=None, radius_mi=25.0, state=None, log=print):
    """Live answer to "where can I drink this near here"."""
    import warehouse
    try:
        bev = warehouse.query("menu_beverages", "SELECT * FROM t")
    except Exception as e:                                    # noqa: BLE001
        log("[serves] menu_beverages unavailable: %s" % e)
        return {"serves": [], "degraded": "menus-unavailable", "ambiguous": 0, "unlocated": 0,
                "out_of_range": 0}
    out = build(bev, _outlets_by_key(state, log=log), query, center=center, radius_mi=radius_mi)
    out["menus_searched"] = len(bev)
    return out


def main(argv):
    q = " ".join(argv) or "old fashioned"
    res = serves(q, state="FL")
    print("query: %r  (searched %d menu items)" % (q, res.get("menus_searched", 0)))
    for s in res["serves"]:
        print("  %-34s %-26s $%-7s %s%s"
              % ((s["venue"] or "")[:34], (s["drink"] or "")[:26],
                 s["price"] if s["price"] is not None else "-",
                 s["city"] or "", " · %.1f mi" % s["distance_mi"] if s["distance_mi"] else ""))
    print("  %d placed · %d at chains we can't pin to a branch · %d venues with no outlet record"
          % (len(res["serves"]), res["ambiguous"], res["unlocated"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
