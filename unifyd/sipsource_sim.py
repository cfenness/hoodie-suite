#!/usr/bin/env python3
"""sipsource_sim.py — a SYNTHETIC SipSource depletion feed, matched to the real shape so we can
prove the ingest pipeline (NRT-PLAN.md Phase 4 / §1d) at ~500M-row scale BEFORE the real feed exists.

SipSource (WSWA) is monthly wine & spirits DEPLETION data (distributor → retailer shipments), the
industry standard. Grain at its most aggregated is ~300–600M rows: month × market × distributor ×
supplier × brand × item × premise, with 9-litre-case and dollar measures. We don't have the file,
but we know the shape — so this generates a deterministic, seeded universe and streams fact rows
straight to HIVE-PARTITIONED parquet (period=YYYYMM/market=XX/…), exactly the layout §1d prescribes
so the site never scans the raw grain.

Design for scale (this is the point):
  • dimensions are a fixed seeded universe (numpy int arrays), never regenerated
  • facts are produced COLUMNAR with numpy and written per (period, market) partition — no Python
    list-of-dicts, so 500M rows stay bounded in memory (one partition at a time)
  • partition-per-(month, market) => partition pruning: a one-market or one-month query touches a
    few files, not the lake

    python sipsource_sim.py --rows 25_000_000 --months 37          # representative proof slice
    python sipsource_sim.py --rows 500_000_000 --months 37 --name sip_raw   # full stress test
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SEED = 20260721
NAME = "sip_raw"

# 50 markets (states) — SipSource reports by state/territory.
MARKETS = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
           "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
           "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
           "VA", "WA", "WV", "WI", "WY"]
PREMISE = ["OFF", "ON"]                                   # off-premise (retail) vs on-premise (bar/restaurant)
# Category universe — spirits + wine classes SipSource tracks (drives price + volume shape).
CATEGORIES = ["Vodka", "Whiskey-Bourbon", "Whiskey-Scotch", "Whiskey-Irish", "Tequila", "Mezcal",
              "Rum", "Gin", "Cognac", "Brandy", "Liqueur", "RTD-Cocktail", "Wine-Red", "Wine-White",
              "Wine-Sparkling", "Wine-Rose", "Sake", "Vermouth", "Cordial", "Bitters"]
# ~15 national distributors (SipSource is distributor depletions).
DISTRIBUTORS = ["Southern Glazer's", "RNDC", "Breakthru", "Empire", "Johnson Bros", "Republic",
                "Winebow", "Martignetti", "Allied", "Opici", "Horizon", "Young's Market",
                "Standard", "Wirtz", "Heidelberg"]

# Universe sizes (fixed — the real book is this order of magnitude).
N_SUPPLIERS = 450
N_BRANDS = 5000
N_ITEMS = 60000
SIZES_ML = [50, 100, 187, 200, 375, 500, 700, 750, 1000, 1500, 1750, 3000]


def _universe():
    """Deterministic dimension universe as numpy arrays: item→brand→supplier→category maps + prices.
    Fixed seed so every run (and the real ingest's expectations) sees the same book."""
    import numpy as np
    rng = np.random.default_rng(SEED)
    brand_supplier = rng.integers(0, N_SUPPLIERS, N_BRANDS)                 # brand -> supplier
    brand_category = rng.integers(0, len(CATEGORIES), N_BRANDS)            # brand -> category (a brand is ONE
    item_brand = rng.integers(0, N_BRANDS, N_ITEMS)                        #   category, like real life: Tito's=Vodka)
    item_category = brand_category[item_brand]                            # item inherits its brand's category
    item_size = rng.choice(len(SIZES_ML), N_ITEMS)                         # item -> size index
    # base price per 9L case by category (rough real-world spread), × item-level jitter
    cat_case_price = np.array([180, 320, 520, 300, 360, 480, 220, 260, 640, 240, 200, 150,
                               110, 100, 190, 120, 240, 160, 180, 90], dtype=float)
    item_case_price = cat_case_price[item_category] * rng.uniform(0.6, 1.8, N_ITEMS)
    return dict(brand_supplier=brand_supplier, brand_category=brand_category, item_brand=item_brand,
                item_category=item_category, item_size=item_size, item_case_price=item_case_price)


FIELDS = ["period", "market", "premise", "distributor", "supplier_id", "brand_id", "item_id",
          "category", "size_ml", "cases_9l", "cases_physical", "revenue_usd"]


def generate(total_rows, months=37, start_year=2023, start_month=6, name=NAME, log=print):
    """Stream `total_rows` synthetic depletion facts across `months`, hive-partitioned by
    period=YYYYMM/market=XX. Returns {rows, partitions, seconds, bytes}."""
    import numpy as np
    import warehouse
    U = _universe()
    per_pm = max(1, total_rows // (months * len(MARKETS)))          # rows per (period, market) partition
    periods = []
    y, m = start_year, start_month
    for _ in range(months):
        periods.append("%04d%02d" % (y, m))
        m += 1
        if m > 12:
            m = 1; y += 1
    rng = np.random.default_rng(SEED + 1)
    t0 = time.time()
    total, nparts, nbytes = 0, 0, 0
    for pi, period in enumerate(periods):
        # a mild monthly trend + seasonality so YoY/mart math is non-trivial
        season = 1.0 + 0.15 * np.sin((pi % 12) / 12.0 * 2 * np.pi) + 0.004 * pi
        for market in MARKETS:
            n = per_pm
            items = rng.integers(0, N_ITEMS, n)
            brands = U["item_brand"][items]
            rows = {
                "period": np.full(n, period),
                "market": np.full(n, market),
                "premise": np.where(rng.random(n) < 0.72, "OFF", "ON"),      # ~72% off-premise
                "distributor": np.array(DISTRIBUTORS)[rng.integers(0, len(DISTRIBUTORS), n)],
                "supplier_id": U["brand_supplier"][brands].astype(np.int32),
                "brand_id": brands.astype(np.int32),
                "item_id": items.astype(np.int32),
                "category": np.array(CATEGORIES)[U["item_category"][items]],
                "size_ml": np.array(SIZES_ML)[U["item_size"][items]].astype(np.int16),
            }
            cases = (rng.lognormal(0.6, 1.1, n) * season).astype(np.float32)  # 9L cases, right-skewed
            rows["cases_9l"] = np.round(cases, 3)
            rows["cases_physical"] = np.round(cases * (9000.0 / (np.array(SIZES_ML)[U["item_size"][items]]
                                                                 * 12.0)), 2).astype(np.float32)
            rows["revenue_usd"] = np.round(cases * U["item_case_price"][items], 2).astype(np.float32)
            part = "period=%s/market=%s/part-0" % (period, market)
            res = _write_hive(name, part, rows, log=None)
            total += n
            nparts += 1
            nbytes += res
        log("  %s: %d markets landed (running %s rows, %.1fs)" % (period, len(MARKETS), f"{total:,}", time.time() - t0))
    dt = time.time() - t0
    log("[sipsource] generated %s rows across %d partitions in %.1fs (%.0f rows/s), ~%.2f GB"
        % (f"{total:,}", nparts, dt, total / max(dt, 0.01), nbytes / 1e9))
    return dict(rows=total, partitions=nparts, seconds=round(dt, 1), bytes=nbytes)


def _write_hive(name, part, cols, log=print):
    """Write one hive partition (period=…/market=…/part-0.parquet) columnar from numpy arrays.
    Returns bytes written. Uses warehouse's remote/local switch + zstd."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import warehouse
    table = pa.table({k: pa.array(v) for k, v in cols.items()})
    if warehouse.remote():
        rel = "%s/%s.parquet" % (name, part)
        warehouse._retry(lambda: pq.write_table(table, "%s/%s/%s" % (warehouse._bucket(), warehouse._prefix(), rel),
                                                filesystem=warehouse._s3fs(), compression="zstd"), "sip %s" % part)
        return 0
    p = os.path.join(warehouse._LOCAL_DIR, name, part + ".parquet")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    pq.write_table(table, p, compression="zstd")
    return os.path.getsize(p)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate a synthetic SipSource depletion feed at scale.")
    ap.add_argument("--rows", type=int, default=25_000_000, help="total fact rows (default 25M proof slice)")
    ap.add_argument("--months", type=int, default=37)
    ap.add_argument("--name", default=NAME)
    a = ap.parse_args(argv)
    r = generate(a.rows, months=a.months, name=a.name)
    print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
