"""prism.py — the spine behind the Prism mobile app (apps/prism.html).

Prism is the "every cut of your book, one screen" surface. This module is its DATA
CONTRACT: a deterministic, always-on model that serves the two mobile tabs —

  cuts(measure)  -> for each dimension (category, channel, sub-channel, state, package,
                    chain status): its members with a value, a period delta, a 12-pt
                    sparkline, and a materiality weight (share of book).
  pulse(measure) -> the ranked "what's moving" feed: the biggest movers across every
                    dimension, weighted by materiality, each with a one-line narration.
  bundle(measure)-> {measure, measures[], dimensions[], cuts[], pulse[], provenance} —
                    exactly what GET /api/prism returns.

Deterministic (hash-seeded), so the app renders the same book every load and works with
NO API key and offline (the client caches it). This is the reusable backbone: today the
web app renders it; an RN head later would render the same contract. The seam where real
depletion/POS facts replace the model is `_seed`/`_member_series`.
"""
import hashlib, math

# ---- measures: key, label, unit, kind. unit drives client formatting ----
MEASURES = [
    {"key": "vol_growth", "label": "Volume growth",  "unit": "pct",    "kind": "growth"},
    {"key": "rev_growth", "label": "Revenue growth", "unit": "pct",    "kind": "growth"},
    {"key": "pod_growth", "label": "POD growth",     "unit": "pct",    "kind": "growth"},
    {"key": "activation", "label": "Activation",     "unit": "pct",    "kind": "growth"},
    {"key": "velocity",   "label": "Velocity",       "unit": "dec",    "kind": "level"},
    {"key": "premium",    "label": "Premium index",  "unit": "idx",    "kind": "level"},
    {"key": "pod_eff",    "label": "POD efficiency", "unit": "dec",    "kind": "level"},
    {"key": "share",      "label": "Share",          "unit": "pct",    "kind": "level"},
]
_MEAS = {m["key"]: m for m in MEASURES}

# ---- dimensions and their members (bev-alc book) ----
DIMENSIONS = [
    {"key": "category",     "label": "Category",
     "members": ["Whiskey", "Tequila", "Vodka", "Rum", "Gin", "Cognac", "Liqueur"]},
    {"key": "channel",      "label": "Channel",
     "members": ["Off-Premise", "On-Premise"]},
    {"key": "subch",        "label": "Sub-channel",
     "members": ["Liquor Store", "Grocery", "Club", "Convenience", "Bar & Grill",
                 "Fine Dining", "Hotel / Casino"]},
    {"key": "state",        "label": "State",
     "members": ["TX", "FL", "CA", "NY", "IL", "GA", "NJ", "AZ"]},
    {"key": "pkg_size",     "label": "Package size",
     "members": ["50ml", "375ml", "750ml", "1L", "1.75L"]},
    {"key": "chain_status", "label": "Chain status",
     "members": ["Chain", "Independent"]},
]
_DIM = {d["key"]: d for d in DIMENSIONS}


def _seed(*parts):
    """Stable float in [0,1) from the joined parts — the deterministic randomness source."""
    h = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _span(measure):
    """(low, high, delta_scale) for a measure's value range + how big its period moves run."""
    return {
        "vol_growth": (-17.0, 19.0, 6.0), "rev_growth": (-15.0, 23.0, 6.5),
        "pod_growth": (-11.0, 15.0, 4.0), "activation": (18.0, 74.0, 5.0),
        "velocity":   (2.4, 19.0, 1.6),   "premium":    (78.0, 142.0, 7.0),
        "pod_eff":    (0.8, 4.6, 0.4),    "share":      (2.0, 30.0, 3.0),
    }[measure]


def _member_series(dim, member, measure):
    """One member's {value, delta, spark[12], weight_raw} — deterministic."""
    lo, hi, dscale = _span(measure)
    base = lo + (hi - lo) * _seed(measure, dim, member)
    delta = (dscale * 2.0) * (_seed(measure, dim, member, "d") - 0.5)   # +/- dscale
    # 12-month sparkline: a gentle drift landing near `base`, with member-stable wobble.
    spark = []
    for i in range(12):
        drift = (delta / 11.0) * (i - 11)                       # slope so month 12 ~= base
        wob = (hi - lo) * 0.06 * (_seed(measure, dim, member, "s", i) - 0.5)
        spark.append(round(base + drift + wob, 2))
    weight_raw = 0.15 + 0.85 * _seed("wt", dim, member)         # materiality within the dim
    return {"value": round(base, 2), "delta": round(delta, 2),
            "spark": spark, "weight_raw": round(weight_raw, 4)}


def _cut(dim_key, measure):
    d = _DIM[dim_key]
    rows = []
    for m in d["members"]:
        s = _member_series(dim_key, m, measure)
        rows.append({"name": m, **s})
    tot = sum(r["weight_raw"] for r in rows) or 1.0
    for r in rows:
        r["weight"] = round(r["weight_raw"] / tot, 4)            # share of the dimension
        del r["weight_raw"]
    rows.sort(key=lambda r: r["value"], reverse=True)          # leaderboard: biggest first
    return {"dim": dim_key, "label": d["label"], "rows": rows}


def cuts(measure):
    return [_cut(d["key"], measure) for d in DIMENSIONS]


# ---- pulse: the ranked "what's moving" feed ----
_UP = [
    "%(member)s is powering %(dim)s — %(label)s %(val)s, its strongest lane.",
    "%(member)s leads %(dim)s: %(label)s %(val)s and still climbing.",
    "Momentum in %(member)s — %(label)s %(val)s against a soft %(dim)s.",
]
_DOWN = [
    "%(member)s is the drag on %(dim)s — %(label)s %(val)s.",
    "Watch %(member)s: %(label)s %(val)s, the weakest cut of %(dim)s.",
    "%(member)s is bleeding — %(label)s %(val)s across %(dim)s.",
]


def _fmt(value, unit, signed=False):
    if unit == "pct":
        return ("%+.1f%%" if signed else "%.1f%%") % value
    if unit == "idx":
        return "%.0f" % value
    if unit == "dec":
        return "%.1f" % value
    return "%.1f" % value


def pulse(measure, top=6):
    meas = _MEAS[measure]
    growth = meas["kind"] != "level"
    movers = []
    for c in cuts(measure):
        for r in c["rows"]:
            # the "signal" is what the card headlines: for growth measures the growth level
            # itself is the story (biggest growers/decliners); for levels it's the move vs PY.
            signal = abs(r["value"]) if growth else abs(r["delta"])
            score = signal * math.sqrt(r["weight"])             # damp by materiality
            movers.append({"dim": c["dim"], "dim_label": c["label"], **r, "score": round(score, 4)})
    movers.sort(key=lambda m: m["score"], reverse=True)
    feed = []
    for m in movers[:top]:
        # frame up/down by the SAME quantity the card shows, so the words never fight the number
        up = (m["value"] >= 0) if growth else (m["delta"] >= 0)
        val = (_fmt(m["value"], meas["unit"], signed=True) if growth
               else _fmt(m["delta"], "pct", signed=True) + " vs PY")
        tpl = (_UP if up else _DOWN)[int(_seed("tpl", measure, m["dim"], m["name"]) * 3) % 3]
        feed.append({
            "dim": m["dim"], "dim_label": m["dim_label"], "member": m["name"],
            "value": m["value"], "delta": m["delta"], "spark": m["spark"],
            "weight": m["weight"], "up": up,
            "text": tpl % {"member": m["name"], "dim": m["dim_label"].lower(),
                           "label": meas["label"].lower(), "val": val},
        })
    return feed


def bundle(measure=None):
    measure = measure if measure in _MEAS else MEASURES[0]["key"]
    return {
        "measure": measure,
        "measures": MEASURES,
        "dimensions": [{"key": d["key"], "label": d["label"], "n": len(d["members"])}
                       for d in DIMENSIONS],
        "cuts": cuts(measure),
        "pulse": pulse(measure),
        "provenance": "Modeled book — deterministic Prism model (swap for live depletion/POS facts).",
    }
