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

FIELDS = ["brand", "brand_group", "product_name", "flavor", "abv", "style", "category", "origin",
          "size_ml", "packsize", "container", "pack", "upc", "gtin", "vintage", "edition", "supplier"]

# sources with a REAL brand column — used to seed the brand dictionary
_CLEAN_BRAND = [("kroger_products", "brand"), ("walmart_products", "brand"), ("nc_pricing", "Brand Name"),
                ("binnys_products", "brand"), ("target_products", "brand"), ("specs_products", "brand"),
                ("abc_products", "brand")]

# per-source projection: which columns carry name / size / upc / abv(or proof) / category / clean-brand, + a
# bev-alc filter so non-alcohol rows (a Kroger marinade) don't feed the master.
_CFG = {
    "abc_catalog": dict(name="name", size="size", upc="upc"),
    "kroger_products": dict(name="product_name", size="size", upc="upc", brand="brand", cat="category",
                            filt=lambda r: r.get("category") == "Adult Beverage"),
    "walmart_products": dict(name="product_name", size_ml="size_ml", upc="upc", abv="abv", brand="brand",
                             cat="category", filt=lambda r: r.get("is_alcohol")),
    "totalwine_products_full": dict(name="name", size="name", container="container", cat="bev_category",
                                    filt=lambda r: r.get("is_alcoholic")),
    "binnys_products": dict(name="name", brand="brand"),
    "target_products": dict(name="name", brand="brand", cat="category"),
    "specs_products": dict(name="name", brand="brand"),
    "or_pricing": dict(name="description", size="size", cat="category", proof="proof"),
    "me_pricing": dict(name="Description", size="Size", upc="UPC", proof="Proof", cat="Product Category"),
    "nc_pricing": dict(name="Brand Name", size="Bottle Size", proof="Proof", brand="Brand Name"),
    "bc_liquor": dict(name="PRODUCT_LONG_NAME", upc="PRODUCT_BASE_UPC_NO", litres="PRODUCT_LITRES_PER_CONTAINER",
                      abv="PRODUCT_ALCOHOL_PERCENT", cat="ITEM_CATEGORY_NAME"),
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


def _to_ml(s):
    s = str(s or "")
    m = re.search(r"([\d.]+)\s*(ml|l|lt|ltr|liter|litre|oz)", s, re.I)
    if m:
        v = _fnum(m.group(1))
        u = m.group(2).lower()
        return round(v * (1000 if u.startswith("l") else (29.57 if u == "oz" else 1))) if v else None
    v = _fnum(s) if re.fullmatch(r"\s*[\d.]+\s*", s) else None
    return round(v) if v else None


def build(log=print):
    by1 = build_brand_dict(log)
    staged = []
    for ds, c in _CFG.items():
        try:
            rows = warehouse.query(ds, "SELECT * FROM t")
        except Exception as e:
            log("[master] skip %s: %s" % (ds, str(e)[:60])); continue
        f, kept = c.get("filt"), 0
        for r in rows:
            if f and not f(r):
                continue
            nm = r.get(c["name"])
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
                style=None, category=r.get(c["cat"]) if c.get("cat") else None, origin=None, size_ml=sz,
                packsize=None, container=r.get(c["container"]) if c.get("container") else None, pack=None,
                upc=(re.sub(r"\D", "", str(r.get(c["upc"]))) or None) if c.get("upc") and r.get(c["upc"]) else None,
                gtin=None, vintage=None, edition=None, supplier=None, _source=ds))
            kept += 1
        log("[master]   %-24s %6d products" % (ds, kept))
    log("[master] staged %d rows → _stage_product" % len(staged))
    warehouse.write_parquet("_stage_product", staged, fields=FIELDS + ["_source"])
    con = warehouse.connect()
    h = master_apply.resolve_hierarchy(FIELDS, warehouse.uri("_stage_product").strip("'"), con, built_by="build_product_master")
    dims = {k: v["rows"] for k, v in h.items() if isinstance(v, dict) and "rows" in v}
    log("[master] DONE — %s" % dims)
    return dims


if __name__ == "__main__":
    build()
