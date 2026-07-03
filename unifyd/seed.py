"""seed.py — deterministic synthetic dataset for the canonical model (domain.py).

Builds ONE coherent book — dim_product, dim_account, dim_date, fact_depletion — where every
fact ties back to a real dim row and revenue == cases * bottle_price * 12, so aggregations
reconcile across any cut. Hash-seeded (no randomness), so the same seed → the same book:
reproducible demos, stable tests, and a clean swap to real facts later (same schema).

    import seed, warehouse
    summary = seed.build()                 # writes 4 Parquet tables to the warehouse
    warehouse.query("fact_depletion", "SELECT sum(revenue) FROM t")
"""
import hashlib
import domain


def _h(*parts):
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _u(*parts):
    return _h(*parts) / float(16 ** 12)          # stable float in [0,1)


def _pick(seq, *parts):
    return seq[_h(*parts) % len(seq)]


# ---------------------------------------------------------------- dimensions ----
PORTFOLIOS = ["Cornerstone Spirits", "Highland & Co."]
BRAND_WORDS = ["Silver", "Crown", "Harbor", "Ember", "Vega", "Odell", "Marlow", "Kestrel",
               "Foxglen", "Aster", "Bishop", "Cove", "Dune", "Ridge", "Sable", "Wren"]


def products(n=40):
    cats = list(domain.CATEGORIES.keys())
    out = []
    for i in range(n):
        cat = _pick(cats, "cat", i)
        sub = _pick(domain.CATEGORIES[cat], "sub", i)
        tier = _pick(domain.PRICE_TIERS, "tier", i)
        brand = _pick(BRAND_WORDS, "brand", i) + " " + cat
        size = _pick(domain.SIZES_ML, "size", i)
        price = round(domain.TIER_PRICE[tier] * (0.6 + 0.8 * _u("price", i)), 2)
        out.append({
            "product_id": "P%03d" % (i + 1),
            "sku": "%s %s %dml" % (brand, sub, size),
            "brand": brand,
            "brand_family": brand.split()[0],
            "portfolio": _pick(PORTFOLIOS, "pf", brand.split()[0]),
            "category": cat, "subcategory": sub, "price_tier": tier,
            "size_ml": size, "abv": round(37 + 8 * _u("abv", i), 1), "unit_price": price,
        })
    return out


CITIES = [("Orlando", "32801"), ("Orlando", "32803"), ("Winter Park", "32789"),
          ("Orlando", "32819"), ("Maitland", "32751"), ("Orlando", "32806")]


def accounts(n=60, market="Orlando"):
    out = []
    for i in range(n):
        side = "on" if _u("side", i) < 0.55 else "off"
        sub = _pick(domain.CHANNELS[side], "subch", i)
        city, zc = _pick(CITIES, "city", i)
        out.append({
            "account_id": "A%04d" % (i + 1),
            "name": _pick(BRAND_WORDS, "acctname", i) + " " + _pick(
                ["Tavern", "Grille", "Market", "Cellars", "Bar", "Kitchen", "Lounge", "Bottle Shop"], "suffix", i),
            "channel": side, "subchannel": sub,
            "chain_status": "Chain" if _u("chain", i) < 0.4 else "Independent",
            "city": city, "zip": zc,
            "lat": round(28.45 + 0.25 * _u("lat", i), 5),
            "lng": round(-81.45 + 0.35 * _u("lng", i), 5),
            "market": market,
        })
    return out


def dates(months=24, end_year=2026, end_month=6):
    out = []
    y, m = end_year, end_month
    seq = []
    for _ in range(months):
        seq.append((y, m))
        m -= 1
        if m == 0:
            m = 12; y -= 1
    seq.reverse()
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for (yy, mm) in seq:
        out.append({"date_id": "%04d-%02d" % (yy, mm), "year": yy, "month": mm,
                    "label": "%s %d" % (names[mm - 1], yy)})
    return out


# ---------------------------------------------------------------- facts ----
def facts(prods, accts, dts, carry=14):
    """Each account carries a stable subset of SKUs; depletions trend over the calendar.
    revenue == cases * unit_price * 12 (a 12-bottle case), so money reconciles to volume."""
    price = {p["product_id"]: p["unit_price"] for p in prods}
    out = []
    for a in accts:
        # which SKUs this account carries (deterministic subset)
        carried = sorted(prods, key=lambda p: _h("carry", a["account_id"], p["product_id"]))[:carry]
        # on-premise skews premium + lower case volume; off-premise higher volume
        base_vol = 3.0 if a["channel"] == "on" else 9.0
        for p in carried:
            # per-pair baseline + a gentle 24-month trend + seasonal wobble
            b = base_vol * (0.4 + 1.4 * _u("pair", a["account_id"], p["product_id"]))
            trend = (_u("trend", a["account_id"], p["product_id"]) - 0.5) * 0.5   # +/-25% over window
            for i, d in enumerate(dts):
                seas = 1.0 + 0.18 * (1 if d["month"] in (11, 12, 6, 7) else -0.4)
                drift = 1.0 + trend * (i / max(1, len(dts) - 1))
                wob = 0.85 + 0.30 * _u("wob", a["account_id"], p["product_id"], d["date_id"])
                cases = round(b * seas * drift * wob, 1)
                if cases <= 0:
                    continue
                out.append({"date_id": d["date_id"], "account_id": a["account_id"],
                            "product_id": p["product_id"], "cases": cases,
                            "revenue": round(cases * price[p["product_id"]] * 12, 2)})
    return out


# ---------------------------------------------------------------- build ----
def generate(n_products=40, n_accounts=60, months=24, market="Orlando"):
    prods = products(n_products)
    accts = accounts(n_accounts, market)
    dts = dates(months)
    fct = facts(prods, accts, dts)
    return {"dim_product": prods, "dim_account": accts, "dim_date": dts, "fact_depletion": fct}


def build(store=None, **kw):
    """Generate + write the 4 tables to the warehouse. Returns a summary with row counts
    and the total book revenue (a coherence anchor)."""
    store = store or (lambda name, recs, fields: __import__("warehouse").write_parquet(name, recs, fields))
    data = generate(**kw)
    written = {}
    for name, fields in domain.TABLES.items():
        written[name] = store(name, data[name], fields)
    total_rev = round(sum(r["revenue"] for r in data["fact_depletion"]), 2)
    return {"tables": {k: v.get("rows") for k, v in written.items()},
            "total_revenue": total_rev, "market": kw.get("market", "Orlando")}
