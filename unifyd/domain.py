"""domain.py — the canonical data model for the production app.

ONE source of truth for the entities and measures the whole system builds on — replacing
the ad-hoc, per-app data shapes of the single-file suite. A proper star schema:

  dim_product    (SKU + brand hierarchy + category / tier attributes)
  dim_account    (on/off-premise outlets in a market)
  dim_date       (monthly calendar)
  fact_depletion (product x account x month  ->  cases, revenue)   <- the "book"

Everything else — Prism's cuts/pulse, the dashboards, the CRM account view — becomes a
QUERY over this, so numbers reconcile across every cut. Synthetic today (see seed.py),
real facts later; the schema doesn't change when the data becomes real.
"""

# hierarchy levels (matches the spine)
LEVELS = ["portfolio", "brandFamily", "brand", "productFamily", "sku"]

# canonical measures — the metrics every surface speaks in
MEASURES = [
    {"key": "cases",      "label": "Cases",          "unit": "num",  "agg": "sum"},
    {"key": "revenue",    "label": "Revenue",        "unit": "usd",  "agg": "sum"},
    {"key": "vol_growth", "label": "Volume growth",  "unit": "pct",  "agg": "yoy",  "of": "cases"},
    {"key": "rev_growth", "label": "Revenue growth", "unit": "pct",  "agg": "yoy",  "of": "revenue"},
    {"key": "velocity",   "label": "Velocity",       "unit": "dec",  "agg": "rate", "of": "cases"},
    {"key": "pod",        "label": "Points of dist.","unit": "num",  "agg": "distinct", "of": "account_id"},
    {"key": "share",      "label": "Share",          "unit": "pct",  "agg": "share", "of": "cases"},
    {"key": "premium",    "label": "Premium index",  "unit": "idx",  "agg": "index"},
]

# taxonomies the generator draws from (kept small + realistic)
CATEGORIES = {
    "Whiskey": ["Bourbon", "Rye", "Scotch", "Irish"],
    "Tequila": ["Blanco", "Reposado", "Añejo"],
    "Vodka":   ["Domestic", "Imported", "Flavored"],
    "Rum":     ["White", "Spiced", "Aged"],
    "Gin":     ["London Dry", "Contemporary"],
    "Cognac":  ["VS", "VSOP", "XO"],
}
PRICE_TIERS = ["Value", "Premium", "Super-Premium", "Ultra"]
TIER_PRICE = {"Value": 14, "Premium": 26, "Super-Premium": 44, "Ultra": 78}   # per-bottle $ anchor
SIZES_ML = [750, 1000, 1750, 375, 50]

CHANNELS = {
    "on":  ["Bar & Grill", "Fine Dining", "Hotel / Casino", "Nightclub", "Brewpub"],
    "off": ["Liquor Store", "Grocery", "Club", "Convenience", "Drug"],
}

# table column contracts (stable Parquet schemas)
DIM_PRODUCT    = ["product_id", "sku", "brand", "brand_family", "portfolio",
                  "category", "subcategory", "price_tier", "size_ml", "abv", "unit_price"]
DIM_ACCOUNT    = ["account_id", "name", "channel", "subchannel", "chain_status",
                  "city", "zip", "lat", "lng", "market"]
DIM_DATE       = ["date_id", "year", "month", "label"]
FACT_DEPLETION = ["date_id", "account_id", "product_id", "cases", "revenue"]

TABLES = {"dim_product": DIM_PRODUCT, "dim_account": DIM_ACCOUNT,
          "dim_date": DIM_DATE, "fact_depletion": FACT_DEPLETION}
