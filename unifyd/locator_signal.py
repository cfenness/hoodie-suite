#!/usr/bin/env python3
"""locator_signal.py — the "why go here" layer under the Product Locator.

Today's locator answers WHO CARRIES IT: a distributor-fed account list (`/api/locator` →
vtinfo, name/address/lat/lng and nothing else). That's the commodity every brand-site
locator ships, and because it's depletion-fed it routes people to stores that sold out
three weeks ago. This module answers WHY GO HERE, entirely from data we already land:

  price + promo    retail_observations.price / .promo / .on_promo   (per store, per day)
  on-hand          retail_observations.qty / .in_stock
  sell-through     fact_velocity.implied_units / .confidence        (velocity.py)
  instrument tier  obs_quality_source.qual_tier                     (obs_quality.py)
  geography        src_outlets (source, store_id) → lat/lng/state   (dim_outlet.py's input)

THE PRICE PRIMITIVE IS GOOGLE-FLIGHTS-SHAPED, not "cheapest". A verdict answers "is this a
GOOD price for this item, here, now" by scoring the offer against a REFERENCE DISTRIBUTION —
the same item, in the same trade area, over a trailing window. Three rules keep it honest,
all load-bearing:

  1. RANK BY PERCENTILE, NOT % OFF. A 40%-off cut on an inflated everyday price can still sit
     above the area median (the mattress-store problem). Discount depth is a secondary badge;
     it is NEVER the sort key.
  2. COMPARE LIKE WITH LIKE. Everything is scored as a per-750ml single-bottle equivalent
     (price_signal.unit_price) AND only against its own bottle FORMAT. Normalizing alone is not
     enough: a 1.75L is genuinely cheaper per unit than a 750ml, so one mixed pool drags the
     median down and makes every 750ml read "high". Measured live: $13.37 (1.75L) → $27.26
     (375ml) on the same brand in the same city. See reference_pools().
  3. SUPPRESS, DON'T GUESS. Under MIN_REF priced stores the verdict is None with a `reason` —
     we show the price and drop the superlative. Same discipline as velocity.CONF_FLOOR and
     the representativeness floor. Google Flights declines a verdict on thin routes; so do we.

A FLAT market yields no verdict either — if the trailing spread is inside FLAT_TOL, no store
is meaningfully a deal and every band would be noise. This is why there is no hardcoded
control-state list: where the state sets one statewide shelf price the distribution IS flat,
so "no deal to be had here" falls out of the data instead of out of a table someone has to
maintain (and can get wrong).

Pure functions first, warehouse I/O confined to `offers()` at the bottom — so all of the math
is testable with no credentials and nothing running. See locator_signal_test.py.
"""
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import price_signal

# --- verdict tunables (env-overridable; defaults chosen to fail toward "no claim") ----------
MIN_REF = int(os.environ.get("LOC_MIN_REF", "4"))          # priced stores needed before ANY band
TRAIL_DAYS = int(os.environ.get("LOC_TRAIL_DAYS", "90"))   # reference window
FLAT_TOL = float(os.environ.get("LOC_FLAT_TOL", "0.04"))   # p10→p90 spread under this ⇒ flat market
LOW_SUPPLY_DAYS = float(os.environ.get("LOC_LOW_SUPPLY_DAYS", "3"))   # days-of-supply ⇒ "selling out"
VEL_CONF_FLOOR = float(os.environ.get("VEL_CONF_FLOOR", "0.2"))       # mirrors velocity.py

# percentile cutoffs for the band. Lower percentile = cheaper relative to the local pool.
BANDS = ((0.10, "great"), (0.30, "good"), (0.70, "typical"))          # else "high"

# Canonical bottle formats. A reference pool MUST be size-homogeneous. Normalizing to a per-750ml
# equivalent makes formats arithmetically comparable but NOT economically comparable — a 1.75L is
# genuinely cheaper per unit than a 750ml, so a mixed pool drags the median down and makes every
# 750ml read "high". Measured live in Orlando: unit price ran $13.37 (1.75L) → $27.26 (375ml), a
# ~2x spread that is pure format and nothing to do with deal quality. Rule 2 was necessary but not
# sufficient; this is the other half of it.
FORMATS = (50, 100, 187, 200, 375, 500, 750, 1000, 1500, 1750, 3000, 3785)
FORMAT_TOL = float(os.environ.get("LOC_FORMAT_TOL", "0.06"))   # 1.7L and 1.75L are one format

_ML = {"ml": 1.0, "l": 1000.0, "lt": 1000.0, "ltr": 1000.0, "liter": 1000.0, "litre": 1000.0,
       "oz": 29.5735, "fl": 29.5735}
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|lt?r?|liters?|litres?|fl\.?\s*oz|oz)\b", re.I)
_PACK_RE = re.compile(r"(\d+)\s*(?:-|\s)?\s*(?:pk|pack|ct|count|can|bottle)s?\b", re.I)


def size_ml(name):
    """Millilitres parsed out of a product name ("Tito's 1.75L" → 1750.0). None when the name
    carries no size — a missing size is no signal, never a guessed 750."""
    m = _SIZE_RE.search(name or "")
    if not m:
        return None
    try:
        qty = float(m.group(1))
    except (TypeError, ValueError):
        return None
    unit = re.sub(r"[^a-z]", "", m.group(2).lower())
    mult = _ML.get(unit) or _ML.get(unit[:2]) or (1000.0 if unit.startswith("l") else None)
    if not mult or qty <= 0:
        return None
    ml = qty * mult
    return ml if 10 <= ml <= 20000 else None       # outside real bottle range ⇒ a misparse


def pack_count(name):
    """Bottles/cans per pack ("12 pk 12 oz" → 12). None when singular/unstated."""
    m = _PACK_RE.search(name or "")
    if not m:
        return None
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return None
    return n if 1 < n <= 48 else None


def size_bucket(ml):
    """Snap a parsed size to its canonical format (1700 and 1750 → 1750). None when unparsed —
    an offer with no format can't be scored against a format pool, and won't be."""
    try:
        v = float(ml)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    best = min(FORMATS, key=lambda f: abs(f - v))
    return best if abs(best - v) <= best * FORMAT_TOL else None


def size_label(ml):
    """'750 ml' / '1.75 L' for display — the shopper has to see WHICH bottle we priced."""
    b = size_bucket(ml)
    if not b:
        return ""
    return ("%g L" % (b / 1000.0)) if b >= 1000 else ("%d ml" % b)


def reference_pools(rows):
    """{format → [unit price]} for a set of observations. One pool per bottle format, because a
    verdict is only meaningful against its own format. Rows whose size can't be parsed contribute
    to no pool (they also never get a band — see price_verdict's `no-size`)."""
    pools = {}
    for r in rows:
        ml = r.get("size_ml") or size_ml(r.get("name"))
        b = size_bucket(ml)
        if not b:
            continue
        u = unit_prices(r)[1]
        if u:
            pools.setdefault(b, []).append(u)
    return pools


def shelf_and_promo(row):
    """(everyday, promo_or_None) for an observation. Sources land `price` as the shelf/everyday
    price and `promo` as the deal price when one is running — but they don't all agree on which
    field carries which, so a `promo` that is not strictly cheaper is discarded rather than
    trusted. Returns (None, None) when there's no usable price at all."""
    def num(x):
        try:
            v = float(x)
            return v if (v == v and v > 0) else None
        except (TypeError, ValueError):
            return None
    everyday, promo = num(row.get("price")), num(row.get("promo"))
    if everyday is None and promo is None:
        return (None, None)
    if everyday is None:                      # only a promo landed — it IS the shelf price today
        return (promo, None)
    if promo is not None and promo >= everyday:
        promo = None                          # not a cut; some sources mirror price into promo
    return (everyday, promo)


def unit_prices(row):
    """(everyday_u, effective_u) per-750ml-equivalent for an observation, using the size/pack
    parsed off the product name. effective_u reflects the promo when one is running. Either may
    be None — an unsized name yields no comparable price, which is honest, not a failure."""
    everyday, promo = shelf_and_promo(row)
    ml = row.get("size_ml") or size_ml(row.get("name"))
    pack = row.get("pack") or pack_count(row.get("name"))
    e = price_signal.unit_price(everyday, ml, pack)
    p = price_signal.unit_price(promo, ml, pack) if promo is not None else None
    return (e, p if p is not None else e)


def _pct(xs, q):
    """Linear-interpolated quantile of a sorted list."""
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    i = q * (len(xs) - 1)
    lo, frac = int(i), i - int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def price_verdict(u, reference):
    """Is `u` (a per-750ml unit price) a good price against `reference` (the trailing unit
    prices for this item in this trade area)?

    `reference` must already be FORMAT-HOMOGENEOUS — see reference_pools(). Scoring a 750ml
    against a pool containing 1.75Ls is the bug this signature exists to prevent.

    Returns {band, percentile, median, delta, pct_off_median, n, reason}. `band` is None —
    with a reason — whenever a claim would be unsupported:
      thin-pool     fewer than MIN_REF priced stores
      flat-market   p10→p90 spread inside FLAT_TOL (uniform pricing; nothing here is a deal)
      no-price      the offer itself has no comparable unit price
      no-size       the product name carries no parseable size, so it has no peer group
    """
    xs = sorted(float(x) for x in (reference or []) if isinstance(x, (int, float)) and x > 0)
    out = {"band": None, "percentile": None, "median": None, "delta": None,
           "pct_off_median": None, "n": len(xs), "reason": None}
    if u is None or u <= 0:
        out["reason"] = "no-price"
        return out
    if len(xs) < MIN_REF:
        out["reason"] = "thin-pool"
        return out
    med = statistics.median(xs)
    out["median"] = round(med, 2)
    out["delta"] = round(u - med, 2)
    out["pct_off_median"] = round((med - u) / med, 3) if med > 0 else None
    p10, p90 = _pct(xs, 0.10), _pct(xs, 0.90)
    if med > 0 and (p90 - p10) / med < FLAT_TOL:
        out["reason"] = "flat-market"
        return out
    below = sum(1 for x in xs if x < u)
    ties = sum(1 for x in xs if x == u)
    out["percentile"] = round((below + ties / 2.0) / len(xs), 3)
    for cut, name in BANDS:
        if out["percentile"] <= cut:
            out["band"] = name
            break
    else:
        out["band"] = "high"
    return out


def promo_signal(history, today_index=None):
    """Wait-or-buy, from this store's own promo history for this item.

    `history` is [(date_str, on_promo_bool), ...]. Consecutive on-promo days are one EPISODE;
    the median gap between episode starts is the cadence. Needs >= 2 episodes — one promo is
    an anecdote, not a period. Returns {period_days, days_since, likely_soon, episodes}.

    This is the piece where we beat Google Flights rather than imitate it: they're guessing at
    an airline's revenue-management black box, we're reading a schedule (banners flip promos on
    a fixed weekday — see the Publix Wednesday cadence).
    """
    out = {"period_days": None, "days_since": None, "likely_soon": False, "episodes": 0}
    rows = sorted((d, bool(p)) for d, p in (history or []) if d)
    if not rows:
        return out
    starts, prev = [], False
    for d, on in rows:
        if on and not prev:
            starts.append(d)
        prev = on
    out["episodes"] = len(starts)
    last_day = today_index or rows[-1][0]
    if starts:
        out["days_since"] = _days(starts[-1], last_day)
    if len(starts) < 2:
        return out
    gaps = [g for g in (_days(a, b) for a, b in zip(starts, starts[1:])) if g and g > 0]
    if not gaps:
        return out
    out["period_days"] = int(statistics.median(gaps))
    if out["days_since"] is not None and out["period_days"]:
        out["likely_soon"] = out["days_since"] >= 0.8 * out["period_days"]
    return out


def _days(a, b):
    """Whole days between two YYYY-MM-DD strings. None if either won't parse."""
    import datetime
    try:
        fmt = "%Y-%m-%d"
        return (datetime.datetime.strptime(b[:10], fmt) - datetime.datetime.strptime(a[:10], fmt)).days
    except (TypeError, ValueError):
        return None


def stock_signal(row, tier, velocity=None):
    """What we can honestly say about availability at this store.

    COUNT-tier sources give real on-hand, so they can carry a count and — paired with
    fact_velocity — a days-of-supply urgency flag. STATE-tier sources only ever knew in/out, so
    they say "in stock" and stop; inventing a count we never observed is exactly the failure
    obs_quality.py exists to prevent.

    Returns {in_stock, qty, tier, days_of_supply, urgency, units_per_week, confidence}.
    """
    out = {"in_stock": bool(row.get("in_stock")), "qty": None, "tier": tier,
           "days_of_supply": None, "urgency": None, "units_per_week": None, "confidence": None}
    if tier != "count":
        return out
    try:
        qty = float(row.get("qty")) if row.get("qty") is not None else None
    except (TypeError, ValueError):
        qty = None
    if qty is None:
        return out
    out["qty"] = int(qty)
    out["in_stock"] = qty > 0
    if not velocity:
        return out
    conf = velocity.get("confidence")
    upw = velocity.get("units_per_week")
    if conf is None or conf < VEL_CONF_FLOOR or not upw or upw <= 0:
        return out                      # below the floor velocity.py itself suppresses at
    out["confidence"] = round(float(conf), 3)
    out["units_per_week"] = round(float(upw), 1)
    out["days_of_supply"] = round(qty / (float(upw) / 7.0), 1)
    if out["days_of_supply"] <= LOW_SUPPLY_DAYS:
        out["urgency"] = "selling-out"
    return out


def clean_store_name(s):
    """Store names arrive percent- and entity-encoded from the aggregator feeds — live example:
    'Abc Fine Wine %26 Spirits Oran'. Decode before display AND before keying, or the same store
    fails to match itself across two sources."""
    import html
    import urllib.parse
    t = (s or "").strip()
    if "%" in t:
        try:
            t = urllib.parse.unquote(t)
        except Exception:                       # noqa: BLE001 — a bad escape is not fatal
            pass
    return " ".join(html.unescape(t).split())


def _name_key(s, n=14):
    """Leading alphanumerics of a cleaned store name. Truncated to a prefix because the feeds also
    truncate: 'ABC Fine Wine & Spirits' vs 'Abc Fine Wine %26 Spirits Oran' agree on the first 14
    and disagree after."""
    return "".join(ch for ch in clean_store_name(s).lower() if ch.isalnum())[:n]


def outlet_key(o):
    """Identity of one PHYSICAL store selling one FORMAT.

    Format belongs in the key. The live Orlando pull showed Total Wine twice — $23.49 and $31.49 —
    and those were not duplicates, they were a 750ml and a 1L at the same store. Keying on the
    store alone would have deleted a real offer and called it deduplication.

    Geo is rounded to ~110m and paired with a name prefix, because two aggregators geocoding the
    same storefront agree closely but rarely exactly. With no coordinates we can only trust the
    feed's own key.
    """
    b = o.get("size_bucket")
    if o.get("lat") is not None and o.get("lng") is not None:
        return ("geo", round(float(o["lat"]), 3), round(float(o["lng"]), 3),
                _name_key(o.get("store")), b)
    return ("src", o.get("source", ""), o.get("store_id", ""), b)


def _pick(a, b):
    """Which of two observations of the same store+format to show: freshest first, then the source
    that actually counts units, then the better price for the shopper."""
    def rankable(o):
        return (o.get("observed_on") or "",
                1 if (o.get("stock") or {}).get("tier") == "count" else 0,
                -(o.get("effective_price") or 9e9))
    return a if rankable(a) >= rankable(b) else b


def dedupe(offers):
    """Collapse cross-source duplicates of the same store+format into one card.

    This is not cosmetic: every duplicate is also a duplicated vote in the reference pool, so
    double-counting a chain that happens to be on two aggregators skews the median the whole
    product is scored against.
    """
    keep = {}
    for o in offers:
        k = outlet_key(o)
        if k in keep:
            winner = _pick(keep[k], o)
            merged = dict(winner)
            merged["also_seen"] = sorted(set((keep[k].get("also_seen") or [])
                                             + (o.get("also_seen") or [])
                                             + [keep[k]["source"], o["source"]])
                                         - {winner["source"]})
            keep[k] = merged
        else:
            keep[k] = o
    return list(keep.values())


# Blended "Best" rank, mirroring Google Flights' Best tab (which is price + duration + stops,
# not price alone). Weights are explicit so they can be argued with rather than reverse-engineered.
W_PRICE, W_DIST, W_STOCK = 0.5, 0.3, 0.2


def score(offer, max_dist=None):
    """0–1, higher is better. Price percentile dominates, distance discounts it, and a verified
    in-stock count breaks ties. An offer with no verdict scores on distance and stock alone —
    it is not penalized for living in a thin pool."""
    pct = (offer.get("verdict") or {}).get("percentile")
    price_part = (1.0 - pct) if pct is not None else 0.5
    d, md = offer.get("distance_mi"), max_dist
    dist_part = 1.0 - min(1.0, d / md) if (d is not None and md) else 0.5
    st = offer.get("stock") or {}
    stock_part = 1.0 if st.get("qty") else (0.6 if st.get("in_stock") else 0.0)
    return round(W_PRICE * price_part + W_DIST * dist_part + W_STOCK * stock_part, 4)


def rank(offers):
    """Sort by the blended score, cheapest-first as the tiebreak. Returns a new list."""
    md = max((o.get("distance_mi") or 0) for o in offers) if offers else None
    for o in offers:
        o["score"] = score(o, md or None)
    return sorted(offers, key=lambda o: (-o["score"], o.get("effective_price") or 9e9))


def build_offers(latest, reference, geo=None, tiers=None, velocity=None, history=None,
                 center=None, series=None):
    """Assemble the ranked offer list — the pure core of `/api/locator/offers`.

    latest     [obs row]                 most recent observation per (source, store_id)
    reference  {format: [unit price]}    per-FORMAT trailing pools (reference_pools). A bare list
                                         is still accepted and treated as one pool for every
                                         format — convenient in tests, wrong against real data,
                                         which is why offers() always passes pools.
    geo        {(source, store_id): {lat,lng,store_name,city,state,address}}
    tiers      {source: qual_tier}       from obs_quality_source
    velocity   {(source, store_id): {units_per_week, confidence}}
    history    {(source, store_id): [(date, on_promo)]}
    series     {(source, store_id): [(date, effective_price)]}   → the sparkline
    center     (lat, lng)                to compute distance_mi
    """
    geo, tiers, velocity, history = geo or {}, tiers or {}, velocity or {}, history or {}
    series = series or {}
    out = []
    for row in latest:
        key = (row.get("source", ""), str(row.get("store_id", "") or ""))
        everyday_u, effective_u = unit_prices(row)
        shelf, promo = shelf_and_promo(row)
        g = geo.get(key, {})
        tier = tiers.get(row.get("source", ""), "state")
        ml = row.get("size_ml") or size_ml(row.get("name"))
        bucket = size_bucket(ml)
        # geo and velocity are per STORE; promo history and the price line are per store+FORMAT.
        sig_key = (key[0], key[1], bucket)
        # Score against this offer's OWN format pool. A dict picks the pool; a bare list is the
        # legacy single-pool path. No format ⇒ no peer group ⇒ no band, stated as `no-size`.
        pool = reference.get(bucket, []) if isinstance(reference, dict) else (reference or [])
        verdict = (price_verdict(effective_u, pool) if bucket
                   else {"band": None, "percentile": None, "median": None, "delta": None,
                         "pct_off_median": None, "n": 0, "reason": "no-size"})
        o = {
            "source": row.get("source", ""), "store_id": key[1],
            "store": clean_store_name(g.get("store_name") or row.get("store", "")),
            "size_ml": ml, "size_bucket": bucket, "size_label": size_label(ml),
            "also_seen": [],
            "address": g.get("address", ""), "city": g.get("city", ""), "state": g.get("state", ""),
            "lat": g.get("lat"), "lng": g.get("lng"),
            "name": row.get("name", ""), "brand": row.get("brand", ""), "upc": row.get("upc", ""),
            "everyday_price": shelf, "effective_price": promo if promo is not None else shelf,
            "on_promo": promo is not None,
            "discount_pct": round((shelf - promo) / shelf, 3) if (promo and shelf) else None,
            "unit_price": effective_u,
            "observed_on": row.get("date"),
            "verdict": verdict,
            "stock": stock_signal(row, tier, velocity.get(key)),
            "promo_outlook": promo_signal(history.get(sig_key, history.get(key))),
            "price_history": [{"date": d, "price": p}
                              for d, p in sorted(series.get(sig_key, series.get(key, []))) if p],
            "distance_mi": None,
        }
        if center and g.get("lat") is not None and g.get("lng") is not None:
            o["distance_mi"] = round(haversine_mi(center, (g["lat"], g["lng"])), 1)
        out.append(o)
    # Dedupe BEFORE ranking so a chain on two aggregators occupies one slot, not two.
    return rank(dedupe(out))


def haversine_mi(a, b):
    """Great-circle miles between (lat, lng) pairs."""
    import math
    r, p = 3958.7613, math.pi / 180.0
    dlat, dlng = (b[0] - a[0]) * p, (b[1] - a[1]) * p
    s = (math.sin(dlat / 2) ** 2
         + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin(dlng / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(s)))


# --- consumer vs brand-embedded render modes ------------------------------------------------
# Google Flights can say "this price is High" because Google's customer is not the airline. Our
# brand-embedded widget sits on the brand's own site pointing at the brand's own retail accounts,
# so a "High" badge there dings a customer's customer — and supplier influence over retail price
# is a genuinely touchy area in three-tier bev-alc. Same engine, two renderings: `consumer` keeps
# the full verdict, `brand` keeps only the positive claims. The FILTERING HAPPENS SERVER-SIDE so
# the negative band never ships to a page we don't control.
POSITIVE_BANDS = ("great", "good")


def for_render_mode(offers, mode="consumer"):
    """Strip claims a given surface isn't allowed to make. Returns a new list."""
    if mode != "brand":
        return offers
    out = []
    for o in offers:
        o = dict(o)
        v = dict(o.get("verdict") or {})
        if v.get("band") not in POSITIVE_BANDS:
            v = {"band": None, "percentile": None, "median": v.get("median"), "delta": None,
                 "pct_off_median": None, "n": v.get("n"), "reason": "suppressed-brand-mode"}
        o["verdict"] = v
        o["promo_outlook"] = {**(o.get("promo_outlook") or {}), "likely_soon": False}  # no "wait"
        out.append(o)
    return out


# --- warehouse-backed entry point -----------------------------------------------------------
def offers(query, center=None, radius_mi=15.0, mode="consumer", trail_days=TRAIL_DAYS,
           size=None, log=print):
    """Live offers for an item near a point. Reads retail_observations (latest per store +
    trailing reference), src_outlets (geo), obs_quality_source (tier) and fact_velocity.

    `query` matches brand or product name (case-insensitive substring) or an exact UPC.

    THE RESULT IS SCOPED TO ONE BOTTLE FORMAT, like Google Flights scopes to a cabin class. A
    percentile is only meaningful inside its own pool, so percentiles from different formats
    cannot be ranked against each other: measured live, a 375ml at $13.63 scored "great" (it is
    the cheapest 375ml in town) and outranked a 750ml at $23.49 — while being far worse value per
    drop. Mixing formats in one ranked list is therefore wrong even when every individual verdict
    is right. `size` picks the format; the default is the format with the deepest pool, and every
    available format is returned in `formats` so the caller can offer tabs.

    Returns {offers, reference, reference_size, formats, size, trade_area, mode}. Every warehouse
    read is guarded — a missing table degrades one signal, it does not fail the request.
    """
    import warehouse
    q = (query or "").strip()
    if not q:
        return {"offers": [], "reference": {"n": 0}, "trade_area": None, "mode": mode}
    like = "%" + q.lower() + "%"
    # `date` lands as a VARCHAR in retail_observations, so the window is a STRING compare against
    # a Python-computed cutoff — ISO dates sort lexicographically. Comparing it to
    # `CURRENT_DATE - INTERVAL` instead raises a DuckDB binder error (VARCHAR vs TIMESTAMP), which
    # the guard below would swallow into a silent empty result.
    import datetime
    cutoff = (datetime.date.today() - datetime.timedelta(days=int(trail_days))).isoformat()
    try:
        obs = warehouse.query_parts(
            "retail_observations",
            "SELECT * FROM t WHERE date >= ? "
            "AND (lower(brand) LIKE ? OR lower(name) LIKE ? OR upc = ?)",
            [cutoff, like, like, q])
    except Exception as e:                      # noqa: BLE001 — a warehouse miss is a degrade
        log("[locator] observations unavailable: %s" % e)
        return {"offers": [], "reference": {"n": 0}, "trade_area": None, "mode": mode,
                "degraded": "observations-unavailable"}
    if not obs:
        return {"offers": [], "reference": {"n": 0}, "trade_area": None, "mode": mode}

    # A missing side-table degrades ONE signal, but it must never do so quietly — a guard that
    # swallows is indistinguishable from success. Every miss is named in `degraded[]` and the UI
    # prints it.
    degraded = []
    geo = _geo_for(obs, log=log, degraded=degraded)
    if center:
        obs = [r for r in obs
               if _within(geo.get((r.get("source", ""), str(r.get("store_id", "") or ""))),
                          center, radius_mi)]
        if not obs:
            return {"offers": [], "reference": {"n": 0}, "trade_area": None, "mode": mode}

    pools = reference_pools(obs)          # one reference distribution PER BOTTLE FORMAT
    # Key EVERYTHING by (source, store, FORMAT). Keying "latest" by store alone silently drops a
    # real offer whenever one store sells two formats — live, Total Wine listed both a 750ml and a
    # 1L and only one would have survived. The promo history and the sparkline are per-format for
    # the same reason: a 750ml's price line must not contain 1.75L prices.
    def _k(r):
        return (r.get("source", ""), str(r.get("store_id", "") or ""),
                size_bucket(r.get("size_ml") or size_ml(r.get("name"))))

    latest = list({_k(r): r for r in sorted(obs, key=lambda r: r.get("date") or "")}.values())
    hist, series = {}, {}
    for r in obs:
        k = _k(r)
        hist.setdefault(k, []).append((r.get("date"), bool(r.get("on_promo"))))
        eff = shelf_and_promo(r)
        px = eff[1] if eff[1] is not None else eff[0]
        if r.get("date") and px:
            series.setdefault(k, []).append((r["date"], px))

    built = build_offers(latest, pools, geo=geo,
                         tiers=_tiers(log=log, degraded=degraded),
                         velocity=_velocity(latest, log=log, degraded=degraded),
                         history=hist, series=series, center=center)
    # Every format present, with how many stores back it — the caller renders these as tabs.
    formats = [{"bucket": b, "label": size_label(b), "pool": len(pools.get(b, [])),
                "offers": sum(1 for o in built if o.get("size_bucket") == b)}
               for b in sorted({o.get("size_bucket") for o in built} - {None})]
    # Scope to ONE format. Default = deepest pool, which is both the best-evidenced verdict and
    # almost always the format a shopper means.
    want = size_bucket(size) if size else None
    if want is None and formats:
        want = max(formats, key=lambda f: (f["pool"], f["offers"]))["bucket"]
    scoped = [o for o in built if o.get("size_bucket") == want] if want else built
    unsized = [o for o in built if o.get("size_bucket") is None]

    st = price_signal.cluster_stats(pools.get(want, [])) or {"n": 0}
    out = {"offers": for_render_mode(rank(scoped), mode), "reference": st,
           "size": want, "reference_size": size_label(want) if want else "",
           "formats": formats, "unsized_offers": len(unsized),
           "trade_area": _trade_area_label(latest, geo), "mode": mode}
    if degraded:
        out["degraded"] = ", ".join(degraded)
    return out


def _geo_for(obs, log=print, degraded=None):
    """(source, store_id) → outlet geo, from src_outlets."""
    try:
        import warehouse
        rows = warehouse.query("src_outlets",
                               "SELECT source, store_id, store_name, address, city, state, lat, lng FROM t")
    except Exception as e:                      # noqa: BLE001
        log("[locator] src_outlets unavailable: %s" % e)
        if degraded is not None:
            degraded.append("no-store-geo")
        return {}
    return {(r.get("source", ""), str(r.get("store_id", "") or "")): r for r in rows}


def _tiers(log=print, degraded=None):
    """source → qual_tier from obs_quality_source; empty (⇒ everything treated STATE) on a miss.
    Treating everything as STATE is the SAFE degrade — it withholds counts rather than asserting
    ones we can't vouch for — but it is still a degrade, so it is reported."""
    try:
        import warehouse
        return {r["source"]: r.get("qual_tier", "state")
                for r in warehouse.query("obs_quality_source", "SELECT source, qual_tier FROM t")}
    except Exception as e:                      # noqa: BLE001
        log("[locator] obs_quality_source unavailable: %s" % e)
        if degraded is not None:
            degraded.append("no-instrument-tiers")
        return {}


def _velocity(latest, log=print, degraded=None):
    """(source, store_id) → {units_per_week, confidence} from fact_velocity's latest week."""
    try:
        import warehouse
        rows = warehouse.query(
            "fact_velocity",
            "SELECT source, store_id, avg(implied_units) units_per_week, avg(confidence) confidence "
            "FROM t WHERE week = (SELECT max(week) FROM t) GROUP BY source, store_id")
    except Exception as e:                      # noqa: BLE001
        log("[locator] fact_velocity unavailable: %s" % e)
        if degraded is not None:
            degraded.append("no-velocity")
        return {}
    return {(r.get("source", ""), str(r.get("store_id", "") or "")): r for r in rows}


def _within(g, center, radius_mi):
    if not g or g.get("lat") is None or g.get("lng") is None:
        return False
    return haversine_mi(center, (g["lat"], g["lng"])) <= radius_mi


def _trade_area_label(latest, geo):
    """Human label for the comparison pool ("Houston, TX area"). We deliberately do NOT invent a
    neighborhood name — ZIPs are mail routes and we have no neighborhood layer yet, so the label
    stays at the grain we can actually defend."""
    seen = {}
    for r in latest:
        g = geo.get((r.get("source", ""), str(r.get("store_id", "") or ""))) or {}
        city, state = (g.get("city") or "").strip(), (g.get("state") or "").strip()
        if city:
            seen[(city, state)] = seen.get((city, state), 0) + 1
    if not seen:
        return None
    (city, state), _ = max(seen.items(), key=lambda kv: kv[1])
    return ("%s, %s area" % (city, state)) if state else ("%s area" % city)
