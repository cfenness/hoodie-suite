"""build_product_master.py — the canonical product-master build (brand-dictionary quality).

The generic mapping engine (master_apply.build over field_mappings.json) can't do a longest-match brand
lookup in SQL, so the price-list descriptions ("COTE ROTIE - DOMAINE …") get mangled brands. This builder
adds the quality the master needs:
  1. Build a BRAND DICTIONARY from the sources that HAVE a real brand column (Kroger/Walmart/NC/Binny's/
     Target/Spec's/ABC-inventory) — distinct brands seen >=2x, indexed by first token, longest-first.
  2. For every source, resolve the brand: the clean brand column if present, else the LONGEST dictionary
     brand the description starts with, else the first 1-2 words. Strip size/proof from product_name.
     Alcohol-filter each catalog. Land _stage_product, then shred via master_apply.resolve_hierarchy into
     dim_brand / dim_product / dim_item / dim_sku.

    python build_product_master.py        # rebuilds the product master in the warehouse
"""
import collections, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import master_apply
import precleanse as _precleanse
import sku_match as _sku_match
import placeholders as _placeholders

# origin = SOURCE of the juice (COO — where the liquid is from); bottled_in = where it was bottled (kept
# SEPARATE — a Barbados rum bottled in the US is origin=Barbados, bottled_in=US).
FIELDS = ["brand", "brand_group", "product_name", "flavor", "abv", "style", "category", "origin", "bottled_in",
          "region", "sub_region", "appellation", "varietal", "image",
          "size_ml", "packsize", "container", "pack", "upc", "gtin", "vintage", "edition", "supplier"]

# sources with a REAL brand column — used to seed the brand dictionary
_CLEAN_BRAND = [("kroger_products", "brand"), ("walmart_products", "brand"), ("nc_pricing", "Brand Name"),
                ("binnys_products", "brand"), ("target_products", "brand"), ("specs_products", "brand"),
                ("abc_products", "brand")]

# per-source projection: which columns carry name / size / upc / abv(or proof) / category / clean-brand, + a
# bev-alc filter so non-alcohol rows (a Kroger marinade) don't feed the master.
_CFG = {
    "abc_catalog": dict(name="name", size="size", upc="upc", image="image"),
    "kroger_products": dict(name="product_name", size="size", upc="upc", brand="brand", cat="category",
                            image="image_url", filt=lambda r: r.get("category") == "Adult Beverage", dedup=["product_id"]),
    "walmart_products": dict(name="product_name", size_ml="size_ml", upc="upc", abv="abv", brand="brand",
                             cat="category", filt=lambda r: r.get("is_alcohol")),
    "totalwine_products_full": dict(name="name", size="name", container="container", cat="bev_category",
                                    filt=lambda r: r.get("is_alcoholic")),
    "binnys_products": dict(name="name", brand="brand", dedup=["sku"], varietal="varietal", image="image",  # store×product →
                            region="region", origin="origin", cat="category"),                # distinct products
    "target_products": dict(name="name", brand="brand", cat="category", image="image_url", dedup=["tcin"]),
    "specs_products": dict(name="name", brand="brand", dedup=["sku"]),
    "cityhive_products": dict(name="name", size_ml="size_ml", cat="bev_category", image="image", dedup=["sku"]),  # independent
    # TTB COLA — the federal label registry = the historical backbone (~1M bottle+vintage records). Pre-joined
    # (detail + labels) + deduped in ttb_products; brand extracted from the name via the dictionary; vintage →
    # dim_vintage aux (bottles don't split by vintage). All alcohol, so no bev-alc filter.
    "ttb_products": dict(name="name", cat="category", origin="origin", size="net_contents", vintage="vintage", upc="upc"),

    "or_pricing": dict(name="description", size="size", cat="category", proof="proof"),
    "me_pricing": dict(name="Description", size="Size", upc="UPC", proof="Proof", cat="Product Category"),
    "nc_pricing": dict(name="Brand Name", size="Bottle Size", proof="Proof", brand="Brand Name"),
    "bc_liquor": dict(name="PRODUCT_LONG_NAME", upc="PRODUCT_BASE_UPC_NO", litres="PRODUCT_LITRES_PER_CONTAINER",
                      abv="PRODUCT_ALCOHOL_PERCENT", cat="ITEM_CATEGORY_NAME", origin="PRODUCT_COUNTRY_ORIGIN_NAME"),
    "ut_pricing": dict(name="Description", size="Size"),
    "mont_catalog": dict(name="description", size="size", cat="category"),
    "id_products": dict(name="name", size="size", proof="proof"),
}

_SZ = re.compile(r"\b\d[\d.]*\s?(?:ml|l|lt|ltr|liter|litre|oz|pk|pack|ct|proof|pf|°|deg)\b|"
                 r"\(\s*\d[\d.]*\s?(?:ml|l|oz)[^)]*\)|\b\d{2,3}\s?proof\b", re.I)


def _fnum(x):
    try:
        return float(re.sub(r"[^0-9.]", "", str(x)).strip("."))
    except Exception:
        return None


def build_brand_dict(log=print):
    cnt = collections.Counter()
    for ds, col in _CLEAN_BRAND:
        try:
            for r in warehouse.query(ds, 'SELECT "%s" b, count(*) n FROM t GROUP BY 1' % col):
                b = (r.get("b") or "").strip()
                if b and len(b) >= 2 and not b.isdigit():
                    cnt[b] += r["n"]
        except Exception:
            pass
    by1 = collections.defaultdict(list)
    for b, n in cnt.items():
        if n >= 2:
            by1[b.lower().split()[0]].append(b)
    for k in by1:
        by1[k].sort(key=len, reverse=True)
    log("[master] brand dictionary: %d brands" % sum(len(v) for v in by1.values()))
    return by1


def resolve_brand(name, by1, clean=None):
    if clean and str(clean).strip():
        return str(clean).strip()
    toks = re.findall(r"[A-Za-z0-9'&.\-]+", (name or "").strip())
    if not toks:
        return None
    low = (name or "").strip().lower()
    for b in by1.get(toks[0].lower(), []):          # longest dictionary brand the name starts with
        if low.startswith(b.lower()):
            return b
    return " ".join(toks[:2])                        # fallback: first 1-2 words


def clean_name(name):
    s = re.sub(_SZ, " ", (name or ""))
    s = re.sub(r"\s*[-–]\s*$", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _clean_vintage(v):
    """Keep only real vintages — a 4-digit year in range, or NV. Drops the junk that leaks into TTB
    wine_vintage (ABV '11.5', size '1.5L', age '8 YR', garbage OCR)."""
    s = str(v or "").strip()
    if re.fullmatch(r"(?:n\.?v\.?|non[- ]?vintage)", s, re.I):
        return "NV"
    m = re.fullmatch(r"(18|19|20)\d{2}", s)
    if m and 1850 <= int(s) <= 2035:
        return s
    return None


def _to_ml(s):
    s = str(s or "")
    m = re.search(r"([\d.]+)\s*(ml|l|lt|ltr|liter|litre|oz)", s, re.I)
    if m:
        v = _fnum(m.group(1))
        u = m.group(2).lower()
        return round(v * (1000 if u.startswith("l") else (29.57 if u == "oz" else 1))) if v else None
    v = _fnum(s) if re.fullmatch(r"\s*[\d.]+\s*", s) else None
    return round(v) if v else None


# ── smarter matching: canonicalize near-duplicate product names WITHIN a brand so they collapse ─────────────
# The exact-string hierarchy shred leaves "Plymouth Gin", "Plymouth Gin - - - Glass" and "PLYMOUTH GIN 82P" as
# THREE products, so the same item looks single-source when 3 sources actually carry it. Canonicalization maps
# each name to a token-set signature (lowercase, drop word-order / punctuation / dup + container/format noise),
# then folds descriptor-only supersets (…+ "82p"/"1935"), and rewrites every row in a cluster to one display
# name — so the existing exact collapse now merges them. Measured lift: ~1,414 → ~3,234 corroborated items.
# format/container words = pure noise, safe to drop from BOTH the match signature and the display name.
_FORMAT_NOISE = {"glass", "bottle", "bottles", "btl", "can", "cans", "pet", "plastic", "gift", "box", "boxed",
                 "boxset", "bag", "each", "ea", "nr", "pack", "packs"}
# articles/prepositions: drop from the match signature only (keep in display — "Ace of Spades" needs its "of").
_STOP = {"the", "a", "an", "and", "of", "with"}
_NOISE_TOK = _FORMAT_NOISE | _STOP
_DESC_TOK = re.compile(r"^\d{2,3}p?$|^\d{2,3}proof$|^\d{4}$|^\d+ml$|^\d+l$")   # proof / vintage / size remnants


def _tokset(name):
    return frozenset(t for t in re.findall(r"[a-z0-9]+", (name or "").lower())
                     if t not in _NOISE_TOK and len(t) > 1)


def _clean_display(name):
    toks = [t for t in re.split(r"\s+", (name or "").strip())
            if t and (set(t) - set("-–")) and t.lower() not in _FORMAT_NOISE]   # drop pure-dash + format words
    s = re.sub(r"#\d+\b\s*", "", " ".join(toks))          # drop "#815"-style SKU refs
    return re.sub(r"\s{2,}", " ", s).strip(" -–")


def canonicalize(staged, log=print):
    import collections
    by_brand = collections.defaultdict(lambda: collections.defaultdict(list))   # brand -> tokset -> rows
    for r in staged:
        ts = _tokset(r.get("product_name"))
        if ts:
            by_brand[r["brand"]][ts].append(r)
    folded = fuzzy = 0
    for brand, tsmap in by_brand.items():
        parent = {}
        keys = sorted(tsmap.keys(), key=len)
        if len(keys) <= 200:                            # descriptor-superset fold (skip huge brands: O(n^2))
            for a in keys:
                for b in keys:
                    if len(b) <= len(a) or not (a < b):
                        continue
                    extra = b - a
                    if extra and all(_DESC_TOK.match(t) for t in extra):   # b == a + only proof/vintage/size
                        parent[a] = b
                        break

        def root(k):
            seen = set()
            while k in parent and k not in seen:
                seen.add(k); k = parent[k]
            return k
        groups = collections.defaultdict(list)
        for ts, rows in tsmap.items():
            rk = root(ts)
            groups[rk].extend(rows)
            if rk != ts:
                fuzzy += 1
        for rows in groups.values():
            names = collections.Counter(_clean_display(r["product_name"]) for r in rows if r.get("product_name"))
            names.pop("", None)
            if not names:
                continue
            # canonical display: fewest signature tokens (the base product, not a descriptor variant),
            # then most common, then shortest — so "Plymouth Gin" wins over "Plymouth Gin 82P".
            canon = sorted(names.items(), key=lambda kv: (len(_tokset(kv[0])), -kv[1], len(kv[0])))[0][0]
            if len(names) > 1:
                folded += len(names) - 1
            for r in rows:
                r["product_name"] = canon
    log("[master] canonicalize: folded %d name-variants (%d via descriptor-superset fuzzy)" % (folded, fuzzy))
    return staged


def build(log=print):
    by1 = build_brand_dict(log)
    staged = []
    for ds, c in _CFG.items():
        try:
            if c.get("dedup"):                        # store-level source → feed the CATALOG (one row per
                part = ", ".join('"%s"' % x for x in c["dedup"])   # distinct product), not every store-row
                rows = warehouse.query(ds, "SELECT * FROM t QUALIFY row_number() OVER (PARTITION BY %s ORDER BY 1)=1" % part)
            else:
                rows = warehouse.query(ds, "SELECT * FROM t")
        except Exception as e:
            log("[master] skip %s: %s" % (ds, str(e)[:60])); continue
        f, kept = c.get("filt"), 0
        for r in rows:
            if f and not f(r):
                continue
            nm = r.get(c["name"])
            if _placeholders.is_placeholder_name(nm):     # drop template/demo products ("I'm a product")
                continue
            brand = resolve_brand(nm, by1, r.get(c["brand"]) if c.get("brand") else None)
            if not brand:
                continue
            if "size_ml" in c:
                sz = _fnum(r.get(c["size_ml"]))
            elif "litres" in c:
                sz = ((_fnum(r.get(c["litres"])) or 0) * 1000) or None
            else:
                sz = _to_ml(r.get(c.get("size"))) if c.get("size") else None
            abv = _fnum(r.get(c["abv"])) if c.get("abv") else \
                ((_fnum(r.get(c["proof"])) / 2) if c.get("proof") and _fnum(r.get(c["proof"])) else None)
            staged.append(dict(brand=brand, brand_group=None, product_name=clean_name(nm), flavor=None, abv=abv,
                style=None, category=r.get(c["cat"]) if c.get("cat") else None,
                origin=((str(r.get(c["origin"])).strip().title() or None) if c.get("origin") and r.get(c["origin"]) else None),
                bottled_in=((str(r.get(c["bottled_in"])).strip().title() or None) if c.get("bottled_in") and r.get(c["bottled_in"]) else None),
                size_ml=sz,
                packsize=None, container=r.get(c["container"]) if c.get("container") else None, pack=None,
                upc=(re.sub(r"\D", "", str(r.get(c["upc"]))) or None) if c.get("upc") and r.get(c["upc"]) else None,
                gtin=None, edition=None, supplier=None, _source=ds,
                vintage=_clean_vintage(r.get(c["vintage"])) if c.get("vintage") else None,
                **{fld: ((str(r.get(c[fld])).strip() or None) if c.get(fld) and r.get(c[fld]) else None)
                   for fld in ("region", "sub_region", "appellation", "varietal", "image")}))
            kept += 1
        log("[master]   %-24s %6d products" % (ds, kept))
    _precleanse.precleanse(staged, log)                 # precleanse: canonicalize brand + cleanse name FIRST
    canonicalize(staged, log)                           # smarter matching: fold near-dup names before the shred
    _sku_match.propagate_upcs(staged, log)              # SKU-first: propagate UPCs across matched item clusters
    log("[master] staged %d rows → _stage_product" % len(staged))
    warehouse.write_parquet("_stage_product", staged, fields=FIELDS + ["_source"])
    con = warehouse.connect()
    h = master_apply.resolve_hierarchy(FIELDS, warehouse.uri("_stage_product").strip("'"), con, built_by="build_product_master")
    dims = {k: v["rows"] for k, v in h.items() if isinstance(v, dict) and "rows" in v}
    log("[master] DONE — %s" % dims)
    try:                                                # pre-compute the workbench list views (fast cold-load)
        import wb_views
        wb_views.build(log=log)
    except Exception as e:
        log("[master] wb_views skipped: %s" % str(e)[:80])
    return dims


if __name__ == "__main__":
    build()
