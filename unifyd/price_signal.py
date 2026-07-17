"""price_signal.py — bottle price as a SOFT cross-source clustering signal.

Price is NEVER an identity key — it varies by market, tax, control-state markup, and promo. But normalized
to a per-750ml single-bottle equivalent it becomes a strong CORROBORATION signal *within* a candidate cluster:

  • Two rows that already agree on brand + product + size and whose unit price agrees within a band are very
    likely the same item. This is the lever for the store-name-brand fragmentation (the master's dominant
    fragmenter, [[master-fanout-brand-resolution]]): "My Store BACARDI Superior" and "Sipsy BACARDI Superior"
    are one real product sold by two retailers a few dollars apart — same size + same ~unit price → re-join.
  • Conversely, rows keyed to the SAME product whose unit price DIVERGES wildly flag a size/pack mislabel or an
    over-merge of two genuinely different products under one key.

Tolerance is RELATIVE (a ratio to the cluster median), not absolute dollars. Chris's "within a few bucks" holds
*inside one chain*; across markets/control-states the same bottle legitimately swings more, so we compare each
row to its cluster's median and keep the signal soft — it corroborates and flags, it never merges or splits on
its own. Same contract as the CLIP image signal ([[image-match-signal]]): a name-gated booster, not a key.

Size/pack normalization is what makes prices comparable: a 1.75L and a 750ml of the same product have very
different shelf prices but nearly the same price-per-750ml, so we divide out size and pack first.
"""
import statistics

STD_ML = 750.0


def _num(x):
    try:
        v = float(x)
        return v if v == v else None       # drop NaN
    except (TypeError, ValueError):
        return None


def unit_price(price, size_ml, pack=None):
    """Price per 750ml SINGLE-bottle equivalent — size- and pack-invariant so the same product at different
    sizes/packs is comparable. Returns None when inputs are missing or nonsensical (never guesses)."""
    p, ml = _num(price), _num(size_ml)
    if p is None or ml is None or p <= 0 or ml <= 0:
        return None
    pk = _num(pack)
    pk = pk if (pk and pk > 0) else 1.0
    bottles = (ml / STD_ML) * pk
    if bottles <= 0:
        return None
    return round(p / bottles, 2)


def cluster_stats(unit_prices):
    """Median + spread for a cluster's unit prices (Nones dropped). None if no usable price."""
    xs = sorted(u for u in (unit_prices or []) if isinstance(u, (int, float)) and u > 0)
    if not xs:
        return None
    med = statistics.median(xs)
    return {"n": len(xs), "median": round(med, 2), "min": round(xs[0], 2), "max": round(xs[-1], 2),
            "spread_ratio": round(xs[-1] / xs[0], 2) if xs[0] > 0 else None}


def agrees(u, median, tol=0.35):
    """Does unit price `u` sit within ±`tol` (fractional) of the cluster median? Returns None when either side
    is unknown — a missing price is NOT a vote against a match, it's simply no signal."""
    u, median = _num(u), _num(median)
    if u is None or median is None or u <= 0 or median <= 0:
        return None
    return abs(u - median) / median <= tol


def corroboration(u, median, tol=0.35):
    """Soft signal in ~[-1, 1]: ~1 for tight agreement, decaying to negative for large divergence. None when
    either side is unknown (never penalize a missing price). Feed this into candidate ranking / adjudication."""
    u, median = _num(u), _num(median)
    if u is None or median is None or u <= 0 or median <= 0:
        return None
    dev = abs(u - median) / median
    if dev <= tol:
        return round(1.0 - 0.5 * (dev / tol), 3)          # 1.0 (exact) → 0.5 (edge of band)
    return round(max(-1.0, 0.5 - (dev - tol)), 3)         # outside band → soft, capped penalty


def divergence(unit_prices, tol_ratio=2.0, min_n=2):
    """DQ: indices of cluster members whose unit price is > tol_ratio× the median or < 1/tol_ratio× it — a
    likely size/pack mislabel or an over-merge of two different products keyed together. Empty until min_n
    priced members exist (no divergence call on a singleton)."""
    st = cluster_stats(unit_prices)
    if not st or st["n"] < min_n:
        return []
    med = st["median"]
    out = []
    for i, u in enumerate(unit_prices):
        if isinstance(u, (int, float)) and u > 0 and (u > med * tol_ratio or u < med / tol_ratio):
            out.append(i)
    return out


def _selftest():
    # size/pack invariance: 1.75L ≈ 750ml of the same product on a per-750 basis
    assert unit_price(37.42, 1750) == round(37.42 / (1750 / 750), 2)          # Jack Daniel's 1.75L → ~16.04
    assert unit_price(21.12, 750) == 21.12                                     # 750ml → itself
    assert unit_price(14.57, 355, pack=12) == round(14.57 / ((355 / 750) * 12), 2)  # 12-pack normalized per-750
    assert unit_price(None, 750) is None and unit_price(10, 0) is None and unit_price(-5, 750) is None
    # cluster + agreement
    ups = [21.12, 22.50, 20.80, 44.00]   # three chains agree ~21, one is 2× (over-merge / 1.75L mislabeled 750)
    st = cluster_stats(ups)
    assert st["n"] == 4 and 20 < st["median"] < 23
    assert agrees(21.5, st["median"]) is True and agrees(44.0, st["median"]) is False
    assert agrees(None, st["median"]) is None                                 # missing price = no vote
    assert corroboration(21.2, st["median"]) > 0.8 and corroboration(44.0, st["median"]) < 0
    assert divergence(ups, tol_ratio=2.0) == [3]                              # flags the 44.00 outlier
    assert divergence([21.12], tol_ratio=2.0) == []                           # singleton → no call
    print("price_signal self-test OK")


if __name__ == "__main__":
    _selftest()
