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
#
# `chain` is the SUBTAG for a source that sweeps multiple retail banners under one connector identity —
# e.g. doordash_full.py prices Walmart/Target/etc through DoorDash's own storefront. That source used to
# tag rows `source=<chain>` (e.g. "walmart"), which is exactly the table/tag proliferation this schema
# exists to prevent: it invents a new identity per retailer instead of using the one DoorDash is already
# known as everywhere else in the suite ("doordash"), and it makes DoorDash's own contribution
# unrecoverable — blended into whatever else also writes source="walmart" with no way to tell them
# apart. `source` is now always "doordash" for this connector (matching its own identity, the way every
# other source uses ITS OWN name); `chain` optionally names which banner within that sweep this row
# belongs to, empty for any source that only ever covers one retailer. `WHERE source = 'doordash'` now
# answers "everything this connector landed, across every chain it touched" in one query — no per-chain
# special-casing needed by a generic reader.
OBS_FIELDS = ["date", "observed_at", "source", "chain", "store", "store_id", "product_id", "upc", "gtin",
              "brand", "name", "price", "promo", "promo_text", "on_promo", "in_stock", "qty",
              "stock_level", "is_hemp"]

# PIN EVERY COLUMN'S TYPE. Without this, pa.Table.from_pylist infers each partition's schema from
# THAT batch's values alone — so a column that happens to be all-None in one run lands as a `null`
# column while another run lands it as int64/double/string, and the union_by_name read across all
# partitions then has to reconcile incompatible types for one column. That does not fail cleanly: it
# corrupts the read with an invalid-UTF8 decode error (write_partition's own docstring records this
# exact failure on ubereats_products_parts, 2026-07-30 — the fix was added there and never applied
# here). Measured on retail_observations 2026-08-03: 13 distinct per-file schemas across 3,622
# partitions, and the whole 51.7M-row table was unreadable to any aggregate query.
#
# `promo` is DOUBLE because that is what consumers mean by it — locator_signal.shelf_and_promo()
# reads it as the deal PRICE and compares it against `price`. Sources that publish an offer STRING
# ("2 for $4.50", "Buy 2, Save $3" — 7NOW does) get `promo_text` instead of having a number invented
# for them or their text silently cast to NULL.
def _dtypes():
    import pyarrow as pa
    return {"date": pa.string(), "observed_at": pa.int64(), "source": pa.string(),
            "chain": pa.string(), "store": pa.string(), "store_id": pa.string(),
            "product_id": pa.string(), "upc": pa.string(), "gtin": pa.string(),
            "brand": pa.string(), "name": pa.string(),
            "price": pa.float64(), "promo": pa.float64(), "promo_text": pa.string(),
            "on_promo": pa.bool_(), "in_stock": pa.bool_(),
            "qty": pa.float64(), "stock_level": pa.string(), "is_hemp": pa.bool_()}


def _f(v):
    """A numeric column stays numeric. A source handing back '12.99' must not make the whole
    partition's column infer as VARCHAR — that divergence is the bug this module now guards."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace("$", "").replace(",", ""))
    except ValueError:
        return None


def split_promo(v):
    """(numeric promo price, promo text). A source may publish either; the schema carries both so
    neither is lost. A bare number in a string ('4.50') is still a price."""
    if v is None or isinstance(v, bool):
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), ""
    s = str(v).strip()
    if not s:
        return None, ""
    try:
        return float(s.replace("$", "").replace(",", "")), ""
    except ValueError:
        return None, s          # a real offer string — keep it verbatim, invent no number


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
        promo_num, promo_txt = split_promo(r.get("promo"))
        if not promo_txt:
            promo_txt = str(r.get("promo_text") or "")
        out.append({"date": date, "observed_at": int(r.get("observed_at") or now), "source": source,
                    "chain": r.get("chain", ""),
                    "store": r.get("store", ""), "store_id": str(r.get("store_id", "") or ""),
                    "product_id": str(r.get("product_id", "") or ""), "upc": str(r.get("upc", "") or ""),
                    "gtin": str(r.get("gtin", "") or ""),
                    "brand": r.get("brand", ""), "name": r.get("name", ""),
                    "price": _f(r.get("price")), "promo": promo_num, "promo_text": promo_txt,
                    "on_promo": bool(r.get("on_promo")), "in_stock": bool(r.get("in_stock")),
                    "qty": _f(r.get("qty")), "stock_level": r.get("stock_level", ""),
                    "is_hemp": bool(r.get("is_hemp"))})
    try:
        warehouse.write_partition("retail_observations", part or ("%s_%s" % (date, source)), out,
                                  OBS_FIELDS, dtypes=_dtypes())
        log("  [observe] +%d %s observations @ %s -> retail_observations" % (len(out), source, date))
    except Exception as e:
        log("  [observe] land failed: %s" % str(e)[:100])
    return len(out)
