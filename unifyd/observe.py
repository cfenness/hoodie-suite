"""observe.py — the shared spine for tracking price + inventory OVER TIME across every retail source.

Two landing patterns, one call each:

  observe.record(source, rows)            # append today's per-store price+inventory to the time-series
  observe.is_hemp(name, category, ...)    # flag hemp/THC/CBD products (we grab them, set them aside)

The time-series table is `retail_observations`, partitioned one file per (date, source) via
warehouse.write_partition — so history accumulates and we can diff any product/store across days.
Each connector still writes its own <conn>_products "latest snapshot" (with the FULL raw record +
image), but the dated, lean observation is what powers change-tracking.

A row passed to record() should carry: store, store_id, product_id, upc, brand, name, price, promo,
in_stock, qty (numeric count if the source gives one), stock_level (status string if that's all it
gives), is_hemp, image_url, url. Missing keys are fine — they land as null.
"""
import re
import time

import warehouse

# hemp / intoxicating-hemp / cannabinoid beverages — we're bev-alc first, but grab these too.
HEMP_RE = re.compile(
    r"\b(hemp|cbd|thc|thca|delta[\s-]?(?:8|9|10)|d8|d9|hhc|cbn|cbg|cbc|"
    r"cannabis|cannabinoid|marijuana|edible|full[\s-]?spectrum)\b", re.I)

# NON-ALCOHOLIC alternatives — a first-class inclusion, not filtered out (Heineken 0.0, Athletic, dealcoholized
# wine, zero-proof spirits, Seedlip…). The retailer's own CATEGORY is the most reliable signal (Total Wine's
# "Non-Alcoholic" category is the reference), so PASS the category too — a name alone misses NA house brands.
NON_ALC_RE = re.compile(
    r"(?:\b(?:non[\s-]?alcoholic|alcohol[\s-]?free|de[\s-]?alcoholi[sz]ed|dealcoholi[sz]ed|"
    r"zero[\s-]?proof|non[\s-]?alc|N\.?A\.?\s+(?:beer| (?:i\.?)?p\.?a))\b|"       # "N/A beer", "NA IPA"
    r"\b0(?:\.\d)?\s*%(?!\s*(?:sugar|carb|fat|sodium|juice|added))|"             # 0% / 0.0% (alcohol, not nutrition)
    r"\b0\.0\b(?:\s*abv)?)", re.I)
# non-alc house brands / lines that don't say "non-alcoholic" in the name
NON_ALC_BRANDS = re.compile(
    r"\b(athletic brewing|athletic\b|heineken\s*0\.?0?|budweiser zero|corona (?:cero|sunbrew)|guinness\s*0|"
    r"lagunitas ipna|best day brewing|partake|ghia|seedlip|ritual zero|lyre'?s|monday (?:gin|zero)|"
    r"gruvi|surely|st\.?\s*agrestis|hop wtr|for bitter for worse|three spirit|sipsmith freeglider)\b", re.I)

# lean, dated time-series columns (the FULL raw record + images live in each <conn>_products snapshot)
# `date` is DAY granularity, which is too coarse for a sweep that runs for hours against a LIVE site:
# the first product and the 13,900th are observed ~4h apart and the retailer changes price/stock in
# between, so they are not the same moment and must not be presented as one. `observed_at` (unix
# seconds) carries the true instant per row. Older partitions lack the column; query_parts reads with
# union_by_name=true, so they simply return null for it rather than breaking.
OBS_FIELDS = ["date", "observed_at", "source", "store", "store_id", "product_id", "upc", "gtin",
              "brand", "name", "price", "promo", "on_promo", "in_stock", "qty", "stock_level", "is_hemp"]


def is_hemp(*texts):
    """True if any of the given text fields looks like a hemp/THC/CBD product."""
    return bool(HEMP_RE.search(" ".join(str(t or "") for t in texts)))


def is_non_alc(*texts):
    """True if the product is a NON-ALCOHOLIC alternative. Pass name AND category (+ brand) — the retailer's
    category is the strongest signal; the name regex + a house-brand list cover the rest (Heineken 0.0,
    Athletic, dealcoholized wine, zero-proof spirits)."""
    s = " ".join(str(t or "") for t in texts)
    return bool(NON_ALC_RE.search(s) or NON_ALC_BRANDS.search(s))


def record(source, rows, date=None, log=print, part=None):
    """Append today's per-store observations for `source` to the retail_observations time-series.

    Default part = '<date>_<source>' — idempotent per (date, source): re-running a source in ONE call replaces
    that day's file. Sources that land in MANY calls (batched) or CONCURRENTLY (sharded, e.g. total_wine_full's
    5 shards) MUST pass a unique `part` per call — otherwise every call overwrites the same file and only the
    last write survives (silent data loss). Pass e.g. part='<date>_<source>_s2b7'; query_parts globs them all."""
    if not rows:
        return 0
    date = date or time.strftime("%Y-%m-%d")
    now = int(time.time())
    out = []
    for r in rows:
        out.append({"date": date, "observed_at": int(r.get("observed_at") or now), "source": source,
                    "store": r.get("store", ""), "store_id": str(r.get("store_id", "") or ""),
                    "product_id": str(r.get("product_id", "") or ""), "upc": str(r.get("upc", "") or ""),
                    "gtin": str(r.get("gtin", "") or ""),
                    "brand": r.get("brand", ""), "name": r.get("name", ""),
                    "price": r.get("price"), "promo": r.get("promo"),
                    "on_promo": bool(r.get("on_promo")), "in_stock": bool(r.get("in_stock")),
                    "qty": r.get("qty"), "stock_level": r.get("stock_level", ""),
                    "is_hemp": bool(r.get("is_hemp"))})
    try:
        warehouse.write_partition("retail_observations", part or ("%s_%s" % (date, source)), out, OBS_FIELDS)
        log("  [observe] +%d %s observations @ %s -> retail_observations" % (len(out), source, date))
    except Exception as e:
        log("  [observe] land failed: %s" % str(e)[:100])
    return len(out)
