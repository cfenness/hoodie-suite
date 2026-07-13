"""normalize.py — the inbound normalization spine: shred every source ROW into a record at each GRAIN it
provides, tagged with the source. One central shredder, re-runnable, one place to maintain.

  one Total Wine row (New Amsterdam Vodka 1.75L PET @ store 920, $19.99, in stock) fans out to:
    src_brands   total-wine · New Amsterdam
    src_products total-wine · New Amsterdam Vodka
    src_items    total-wine · … · 1.75L
    src_skus     total-wine · … · PET · UPC · pack
    src_outlets  total-wine · store 920 "Total Wine Millenia" · address/geo
    src_pricing  total-wine · store920 · sku · date · 19.99
    src_inventory total-wine · store920 · sku · date · in_stock/qty

A source emits only the grains it provides (TTB → brands+products; FL DBPR → outlets; ABC → all). Each src_
record carries BOTH the raw source keys (source, source_id, upc, store_id) AND the Hoodie ID mnemonic at its
grain — so the same real entity from different sources shares the code (corroboration + matching per grain,
without needing a SKU match). src_<grain> then feeds dim_<grain> via the mnemonic matcher.

    python normalize.py               # rebuild all src_ tables
    python normalize.py --catalog     # just the brand/product/item/sku grains
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import hoodie_ids as H


def _clean_src(t):
    """Table name -> clean SYSTEM tag: abc_catalog->abc, total_wine_products->total-wine, ttb_products->ttb."""
    return t.replace("_products", "").replace("_catalog", "").replace("_pricing", "").replace("_", "-")


def _addr_key(street, city, state, zip_):
    """Stable key for an address → its geocode. Used by both the geocode runner (populates geocode_cache)
    and normalize (applies the cache), so geocoding is standard + persistent + incremental."""
    import re
    z = re.sub(r"[^0-9]", "", str(zip_ or ""))[:5]
    return "|".join([str(street or "").strip().lower(), str(city or "").strip().lower(),
                     str(state or "").strip().upper(), z])


def _apply_geocode(out, log=print):
    """Fill lat/lng for any outlet that has an address but no coords, from geocode_cache (keyed by address).
    Geocoding is standard: sources that ship coords keep them; the rest get pinned once their address is cached."""
    try:
        cache = {r["addr_key"]: r for r in warehouse.query("geocode_cache",
                 "SELECT addr_key, CAST(lat AS DOUBLE) lat, CAST(lng AS DOUBLE) lng FROM t")}
    except Exception:
        return
    n = 0
    for o in out.values():
        if o["lat"] is not None or not o["address"]:
            continue
        c = cache.get(_addr_key(o["address"], o["city"], o["state"], o["zip"]))
        if c and c["lat"] is not None:
            o["lat"], o["lng"] = c["lat"], c["lng"]; n += 1
    log("[normalize] geocode cache filled %d outlets (of %d entries)" % (n, len(cache)))


def _platform_map():
    """offprem sku -> platform (Shopify/WooCommerce/…) so the offprem feed reads by SYSTEM, not by metro."""
    m = {}
    try:
        for r in warehouse.query("offprem_products", "SELECT sku, platform FROM t WHERE sku IS NOT NULL"):
            m[str(r["sku"])] = (r.get("platform") or "").lower().replace(" stores", "").replace(" ", "-")
    except Exception:
        pass
    return m


def normalize_catalog(log=print):
    """Shred _stage_product (the cleaned catalog staging) into src_brands / products / items / skus — one
    deduped record per (source, grain-mnemonic), stamped with raw keys + the Hoodie mnemonic at that grain.
    Source is the SYSTEM: offprem rows tag as their platform (shopify/woocommerce/…), so Shopify feeds as
    Shopify across every metro, not as 22 metro tables."""
    plat = _platform_map()
    rows = warehouse.query("_stage_product",
                           "SELECT _source, _source_id, brand, product_name, flavor, category, abv, varietal, "
                           "origin, region, size_ml, container, pack, upc, image FROM t "
                           "WHERE brand IS NOT NULL AND brand <> ''")
    brands, products, items, skus = {}, {}, {}, {}
    for r in rows:
        rawsrc = r["_source"]; sid = str(r.get("_source_id") or ""); upc = str(r.get("upc") or "")
        src = (plat.get(sid) or "offprem") if rawsrc == "offprem_products" else _clean_src(rawsrc)
        brand = r["brand"]; pname = r.get("product_name") or ""
        bc = H.brand_code(brand)
        pc = bc + H.product_code(pname, brand)
        ic = pc + H.container_code(r.get("container")) + H.size_code(r.get("size_ml"))
        kc = ic + H.pack_code(r.get("pack"), pname)
        # ONE product type per product (Wine/Beer/Spirits/Hemp/Cannabis) from category + name + varietal
        ptid, ptname = classify_type(" ".join(str(x) for x in
                                     (r.get("category"), pname, r.get("varietal"), r.get("flavor")) if x))
        brands.setdefault((src, bc), {"source": src, "source_id": sid, "hoodie_brand": bc, "brand": brand})
        products.setdefault((src, pc), {"source": src, "source_id": sid, "hoodie_product": pc, "brand": brand,
                                        "product_name": pname, "flavor": r.get("flavor"), "category": r.get("category"),
                                        "product_type_id": ptid, "product_type": ptname,
                                        "abv": r.get("abv"), "varietal": r.get("varietal"), "origin": r.get("origin"),
                                        "region": r.get("region"), "image": r.get("image")})
        items.setdefault((src, ic), {"source": src, "source_id": sid, "hoodie_item": ic, "brand": brand,
                                     "product_name": pname, "product_type_id": ptid, "product_type": ptname,
                                     "size_ml": r.get("size_ml"), "container": r.get("container")})
        skus.setdefault((src, kc, upc), {"source": src, "source_id": sid, "upc": upc, "hoodie_sku": kc,
                                         "brand": brand, "product_name": pname, "product_type_id": ptid,
                                         "product_type": ptname, "size_ml": r.get("size_ml"),
                                         "container": r.get("container"), "pack": r.get("pack")})
    warehouse.write_parquet("src_brands", list(brands.values()),
                            ["source", "source_id", "hoodie_brand", "brand"])
    warehouse.write_parquet("src_products", list(products.values()),
                            ["source", "source_id", "hoodie_product", "brand", "product_name", "flavor",
                             "category", "product_type_id", "product_type", "abv", "varietal", "origin",
                             "region", "image"])
    warehouse.write_parquet("src_items", list(items.values()),
                            ["source", "source_id", "hoodie_item", "brand", "product_name", "product_type_id",
                             "product_type", "size_ml", "container"])
    warehouse.write_parquet("src_skus", list(skus.values()),
                            ["source", "source_id", "upc", "hoodie_sku", "brand", "product_name", "product_type_id",
                             "product_type", "size_ml", "container", "pack"])
    warehouse.write_parquet("dim_product_type", [{"product_type_id": i, "product_type": n} for i, n in PRODUCT_TYPES],
                            ["product_type_id", "product_type"])
    types = {}
    for p in products.values():
        types[p["product_type"] or "(unknown)"] = types.get(p["product_type"] or "(unknown)", 0) + 1
    log("[normalize] catalog: src_brands=%d · src_products=%d (types %s) · src_items=%d · src_skus=%d"
        % (len(brands), len(products), types, len(items), len(skus)))
    return {"src_brands": len(brands), "src_products": len(products), "src_items": len(items), "src_skus": len(skus)}


# fuzzy column resolver — each outlet source has its own schema, so map by preference (exact col, then contains).
# One resolver handles CA/VA license tables, chain-store tables, on-premise merchant tables without per-table code.
_OUTLET_PREF = {
    "name": ["dba name", "dba", "trade_name", "store_name", "outlet_name", "business_name", "premise_name",
             "name", "merchant", "store"],
    "owner": ["primary name", "owner name", "primary owner", "primary_owner", "owner", "backer", "licensee"],
    "chain": ["chain", "banner", "parent", "brand_group"],   # explicit chain/banner column when a source has one
    "is_chain": ["is_chain", "ischain"],
    "license_type": ["license type", "license_type", "license_types", "credential", "type"],  # → sell-flags
    "address": ["prem addr 1", "location address 1", "premises address", "street_address", "street",
                "address_line_1", "address"],
    "city": ["prem city", "location city", "premises city", "city"],
    "state": ["prem state", "location state", "premises state", "state"],
    "zip": ["prem zip", "location zip", "premises zip", "zip5", "zipcode", "zip", "postal_code", "postal"],
    "lat": ["latitude", "lat"],
    "lng": ["longitude", "lng", "lon", "long"],
    "license": ["license number", "license_id", "license_num", "file number", "vpid", "credential", "outlet_id"],
}


def _outlet_colmap(header):
    low = {h.lower(): h for h in header}
    cm = {}
    for field, prefs in _OUTLET_PREF.items():
        got = None
        for p in prefs:                       # exact (lower) match first
            if p in low:
                got = low[p]; break
        if not got:
            for p in prefs:                   # then contains
                hit = next((h for h in header if p in h.lower()), None)
                if hit:
                    got = hit; break
        if got:
            cm[field] = got
    return cm


# CHAIN / banner grouping — the high-value signal (is this outlet part of a chain?). Explicit chain columns win;
# otherwise derive the banner from the store name against the major US bev-alc / grocery / convenience chains.
# User directive: "especially anything that identifies if the outlet is part of a chain grouping, in any site".
import re as _re
_CHAINS = [(n, _re.compile(p, _re.I)) for n, p in [
    ("7-Eleven", r"\b7[\s-]?eleven\b|\b7-?11\b"), ("Circle K", r"circle\s?k"), ("Speedway", r"speedway"),
    ("Wawa", r"\bwawa\b"), ("QuikTrip", r"quiktrip|\bqt\b"), ("Casey's", r"casey'?s"), ("Sheetz", r"sheetz"),
    ("Kum & Go", r"kum\s?&?\s?go"), ("Murphy", r"murphy\s?(usa|express)"), ("RaceTrac", r"racetrac"),
    ("CVS", r"\bcvs\b"), ("Walgreens", r"walgreen"), ("Rite Aid", r"rite\s?aid"), ("Walmart", r"wal[\s-]?mart"),
    ("Target", r"\btarget\b"), ("Costco", r"costco"), ("Sam's Club", r"sam'?s\s?club"), ("BJ's", r"bj'?s\s?whole"),
    ("Kroger", r"kroger"), ("Albertsons", r"albertsons"), ("Safeway", r"safeway"), ("Publix", r"publix"),
    ("Whole Foods", r"whole\s?foods"), ("Trader Joe's", r"trader\s?joe"), ("Meijer", r"meijer"),
    ("H-E-B", r"\bh[\s-]?e[\s-]?b\b"), ("Food Lion", r"food\s?lion"), ("Giant", r"\bgiant\b"),
    ("Stop & Shop", r"stop\s?&?\s?shop"), ("Total Wine", r"total\s?wine"), ("BevMo", r"bevmo"),
    ("Binny's", r"binny'?s"), ("Spec's", r"spec'?s"), ("ABC Fine Wine", r"abc\s?fine\s?wine"),
    ("Dollar General", r"dollar\s?general"), ("Family Dollar", r"family\s?dollar"), ("Aldi", r"\baldi\b"),
    ("Costco", r"costco"), ("Gopuff", r"gopuff"), ("ampm", r"\bampm\b"), ("Chevron", r"chevron"),
    ("Shell", r"\bshell\b"), ("Exxon", r"exxon"), ("Marathon", r"marathon\s?(gas|petrol|#)")]]


def _chain_of(name, explicit=None):
    """Return the chain/banner grouping for an outlet — explicit column wins, else derive from the name."""
    if explicit and str(explicit).strip() and str(explicit).strip().lower() not in ("none", "null", "false", "0"):
        return str(explicit).strip()
    n = name or ""
    for cn, rx in _CHAINS:
        if rx.search(n):
            return cn
    return ""


# ── sell-flags: what an account CAN sell (license) + DOES sell (products carried) ────────────────────
# 5 flags per account: beer / wine / spirits / hemp / cannabis. Plus rtd_spirits = a beer/wine outlet that
# can carry spirit-based RTDs but NOT full spirits (the class the user wants to find).
# CA ABC license type → (beer, wine, spirits) the outlet may sell — the dominant license source (96k rows).
_CA_LIC = {
    "20": (1, 1, 0),  # off-sale beer & wine (grocery / c-store) → RTD-spirits candidate
    "21": (1, 1, 1),  # off-sale general (liquor store)
    "40": (1, 0, 0),  # on-sale beer
    "41": (1, 1, 0),  # on-sale beer & wine — eating place
    "42": (1, 1, 0),  # on-sale beer & wine — public premises
    "47": (1, 1, 1),  # on-sale general — eating place
    "48": (1, 1, 1),  # on-sale general — public premises
    "61": (1, 0, 0),  # on-sale beer — public premises
    "75": (1, 1, 1),  # brewpub — general
    "23": (1, 0, 0),  # small beer manufacturer
    "01": (1, 0, 0),  # beer manufacturer
    "02": (0, 1, 0),  # winegrower
    "51": (1, 1, 1), "52": (1, 1, 1), "57": (1, 1, 1),  # clubs
    "58": (1, 1, 0),  # caterer's (beer & wine)
    "70": (1, 1, 1),  # on-sale general — restrictive
}
# by SOURCE default (when no license type) → (beer, wine, spirits, hemp)
_SRC_FLAGS = {
    "ab-inbev": (1, 0, 0, 0),                                        # beer locator
    "target": (1, 1, 0, 0), "kroger": (1, 1, 0, 0), "publix": (1, 1, 0, 0), "albertsons": (1, 1, 0, 0),
    "circlek": (1, 1, 0, 0), "cvs": (1, 1, 0, 0),                    # grocery / c-store (beer & wine)
    "total-wine": (1, 1, 1, 0), "binnys": (1, 1, 1, 0), "specs": (1, 1, 1, 0), "abc": (1, 1, 1, 0),  # liquor
}
_BEV = {"wine": r"wine|\bred\b|\bwhite\b|ros[eé]|champagne|sparkling|sangria|riesling|merlot|cabernet|chardonnay",
        "beer": r"beer|lager|\bale\b|\bipa\b|cider|\bmalt\b|seltzer|pilsner|stout|porter|hard\s?soda",
        "spirits": r"spirit|vodka|whisk|\brum\b|\bgin\b|tequila|bourbon|liqueur|cordial|brandy|cognac|mezcal|scotch|schnapps",
        "hemp": r"hemp|\bthc\b|\bcbd\b|delta[\s-]?[89]|cannabinoid"}
_BEV = {k: _re.compile(v, _re.I) for k, v in _BEV.items()}


# ── product TYPE dimension — one type per product (extensible; ids leave room for future types) ──────────
# A product is exactly one type, so the product grain gets (product_type_id, product_type) — a small dimension,
# not 5 booleans. New types (RTD, Cider, Seltzer, Sake, N/A…) just take the next id + a new string value.
PRODUCT_TYPES = [(1, "Wine"), (2, "Beer"), (3, "Spirits"), (4, "Hemp"), (5, "Cannabis")]
_PT_CANNABIS = _re.compile(r"cannabis|marijuana|dispensary|\bweed\b", _re.I)


def classify_type(text, is_hemp=False):
    """Assign ONE product type (id, name) from a product's category/name. Precedence
    cannabis > hemp > spirits > wine > beer. (0, '') when unknown — leaves room for future type ids."""
    t = text or ""
    if _PT_CANNABIS.search(t):
        return (5, "Cannabis")
    if is_hemp or _BEV["hemp"].search(t):
        return (4, "Hemp")
    if _BEV["spirits"].search(t):
        return (3, "Spirits")
    if _BEV["wine"].search(t):
        return (1, "Wine")
    if _BEV["beer"].search(t):
        return (2, "Beer")
    return (0, "")


def _license_flags(source, lic):
    """(beer, wine, spirits, hemp) an outlet may sell, from its license type (CA) or its source default."""
    key = str(lic or "").strip()
    trip = _CA_LIC.get(key) or _CA_LIC.get(key.zfill(2))
    if trip:
        return [bool(trip[0]), bool(trip[1]), bool(trip[2]), False]
    df = _SRC_FLAGS.get(source)
    if df:
        return [bool(df[0]), bool(df[1]), bool(df[2]), bool(df[3])]
    return [False, False, False, False]


# every store-bearing source → (clean SYSTEM tag). Fuzzy-mapped from each table's own columns.
# ab_outlets = the AB InBev retailer locator (national — where their beer is sold), NOT a state ABC.
_OUTLET_TABLES = [("ca_outlets", "ca-abc"), ("ab_outlets", "ab-inbev"), ("target_stores", "target"),
                  ("orlando_merchants", "orlando"), ("doordash_stores", "doordash")]


def normalize_outlets(log=print):
    """src_outlets — pull an outlet through from EVERY store-bearing source, tagged by SYSTEM: the license /
    ABC premise tables (CA ~129k, VA), chain-store tables (Target…), on-premise merchants (Orlando, DoorDash),
    the off-premise PLATFORM retailers (each Shopify / Woo / Wix / Squarespace store is an account), City Hive
    retailers, the resolved outlet stage, and the retail-observation stores. Each outlet carries raw keys
    (source, store_id) + a name mnemonic as the Hoodie outlet code. This is the full book of accounts, not
    just the 3k resolved stage."""
    out = {}
    FLD = ["source", "store_id", "store_name", "chain", "is_chain", "f_beer", "f_wine", "f_spirits", "f_hemp",
           "f_cannabis", "f_rtd_spirits", "address", "city", "state", "zip", "lat", "lng", "phone", "hoodie_outlet"]
    # observation/chain sources whose source-tag IS the banner (the store rows are all that chain)
    SOURCE_CHAIN = {"target": "Target", "kroger": "Kroger", "binnys": "Binny's", "specs": "Spec's",
                    "abc": "ABC Fine Wine", "total-wine": "Total Wine", "totalwine": "Total Wine",
                    "albertsons": "Albertsons", "circlek": "Circle K", "cvs": "CVS", "publix": "Publix"}

    def _num(v):
        try:
            f = float(v)
            return f if f != 0 else None
        except (TypeError, ValueError):
            return None

    def _put(src, sid, nm, **kw):
        nm = (nm or "").strip()
        if not nm and not sid:
            return
        k = (src, sid or nm)
        if k not in out:
            chain = _chain_of(nm, kw.get("chain") or SOURCE_CHAIN.get(src))
            b, w, s, h = _license_flags(src, kw.get("license_type"))   # what this account CAN sell
            out[k] = {"source": src, "store_id": str(sid or ""), "store_name": nm, "chain": chain,
                      "is_chain": bool(chain), "f_beer": b, "f_wine": w, "f_spirits": s, "f_hemp": h,
                      "f_cannabis": False, "f_rtd_spirits": False, "address": kw.get("address") or "",
                      "city": kw.get("city") or "", "state": kw.get("state") or "", "zip": kw.get("zip") or "",
                      "lat": _num(kw.get("lat")), "lng": _num(kw.get("lng")), "phone": kw.get("phone") or "",
                      "hoodie_outlet": H.brand_code(nm or str(sid))}

    # 1) license / ABC / chain / on-premise tables — fuzzy-mapped from each table's own schema
    for tbl, tag in _OUTLET_TABLES:
        try:
            rows = warehouse.query(tbl, "SELECT * FROM t")
        except Exception:
            continue
        if not rows:
            continue
        cm = _outlet_colmap(list(rows[0].keys()))
        g = lambda r, f: (r.get(cm[f]) if cm.get(f) else None)
        n0 = len(out)
        for r in rows:
            nm = (g(r, "name") or "").strip() or (g(r, "owner") or "").strip()   # DBA, else owner/entity
            _put(tag, g(r, "license") or nm, nm, chain=g(r, "chain"), license_type=g(r, "license_type"),
                 address=g(r, "address"), city=g(r, "city"), state=g(r, "state"), zip=g(r, "zip"),
                 lat=g(r, "lat"), lng=g(r, "lng"))
        log("  [normalize] outlets %-18s %-10s +%d" % (tbl, tag, len(out) - n0))

    # 2) off-premise PLATFORM retailers — each distinct store (base/domain) is an account, tagged by platform
    try:
        for r in warehouse.query("offprem_products",
                                 "SELECT DISTINCT base, platform, name FROM t WHERE base IS NOT NULL"):
            plat = (r.get("platform") or "offprem").lower().replace(" stores", "").replace(" ", "-")
            _put(plat, r["base"], r["base"])          # store identity = its domain
    except Exception as e:
        log("  [normalize] offprem stores: %s" % str(e)[:60])
    # City Hive retailers
    try:
        for r in warehouse.query("cityhive_products", "SELECT DISTINCT store, base FROM t WHERE store IS NOT NULL"):
            _put("cityhive", r.get("store") or r.get("base"), r.get("store") or r.get("base"))
    except Exception as e:
        log("  [normalize] cityhive stores: %s" % str(e)[:60])

    # sources already pulled from their raw tables above — don't re-ingest them from the resolved stage or the
    # observations (same source, two capture paths → double-count). ab/va = ab_outlets; target = target_stores.
    RAW_TAGS = {tag for _, tag in _OUTLET_TABLES}
    STAGE_ALIAS = {"ab-outlets": "ab-inbev", "ab-locator": "ab-inbev", "ab": "ab-inbev", "target-stores": "target"}

    # 3) the resolved outlet stage — only sources the raw tables DON'T already cover (Tito's locator, census)
    try:
        for r in warehouse.query("_stage_outlet", "SELECT _source, license_num, source_ref, outlet_name, address, "
                                 "city, state, zip5, lat, lng, phone FROM t WHERE outlet_name IS NOT NULL"):
            tag = _clean_src(r.get("_source") or "outlet")
            tag = STAGE_ALIAS.get(tag, tag)
            if tag in RAW_TAGS:
                continue
            _put(tag, str(r.get("license_num") or r.get("source_ref") or ""),
                 r.get("outlet_name") or "", address=r.get("address"), city=r.get("city"), state=r.get("state"),
                 zip=r.get("zip5"), lat=r.get("lat"), lng=r.get("lng"), phone=r.get("phone"))
    except Exception as e:
        log("  [normalize] _stage_outlet: %s" % str(e)[:60])
    # 4) retail chain stores from the price/inventory observations — only sources with no raw table (specs,
    #    binnys, kroger, abc…); target already came from target_stores with better keys
    try:
        for r in warehouse.query_parts("retail_observations",
                                       'SELECT DISTINCT source, store_id, store FROM t WHERE store_id <> \'\''):
            if r["source"] in RAW_TAGS:
                continue
            _put(r["source"], str(r["store_id"] or ""), r.get("store") or "")
    except Exception as e:
        log("  [normalize] observation stores: %s" % str(e)[:60])

    # 5) geocode — pin any addressed outlet that arrived without coords (CA licenses etc.) from geocode_cache
    _apply_geocode(out, log)
    # 6) product-carried flags — an account that CARRIES wine sells wine (Barefoot → wine), even if its license
    #    row didn't say so. OR the categories of everything each store stocks into its flags.
    _apply_product_flags(out, log)
    # rtd_spirits = a beer/wine outlet that can carry spirit-based RTDs but NOT full spirits (the class to find)
    for o in out.values():
        o["f_rtd_spirits"] = bool((o["f_beer"] or o["f_wine"]) and not o["f_spirits"])

    warehouse.write_parquet("src_outlets", list(out.values()), FLD)
    geo = sum(1 for v in out.values() if v["lat"] is not None)
    fl = {k: sum(1 for v in out.values() if v[k]) for k in ("f_beer", "f_wine", "f_spirits", "f_hemp", "f_rtd_spirits")}
    log("[normalize] src_outlets=%d (%d geocoded) · flags %s" % (len(out), geo, fl))
    return {"src_outlets": len(out)}


def _apply_product_flags(out, log=print):
    """OR product-carried categories into each outlet's sell-flags. offprem / City Hive carry bev_category
    (classify by category+name); retail_observations carries is_hemp only (per-store)."""
    W, B, S = _BEV["wine"].pattern, _BEV["beer"].pattern, _BEV["spirits"].pattern

    def orflag(key, w=0, b=0, s=0, h=0):
        o = out.get(key)
        if not o:
            return
        if w: o["f_wine"] = True
        if b: o["f_beer"] = True
        if s: o["f_spirits"] = True
        if h: o["f_hemp"] = True

    for tbl, plat_col in (("offprem_products", "platform"), ("cityhive_products", None)):
        try:
            expr = "lower(coalesce(bev_category,'')||' '||coalesce(name,''))"
            sel = ("SELECT %s, base, bool_or(regexp_matches(%s,'%s')) w, bool_or(regexp_matches(%s,'%s')) b, "
                   "bool_or(regexp_matches(%s,'%s')) s, bool_or(coalesce(is_hemp,false)) h FROM t "
                   "WHERE base IS NOT NULL GROUP BY %s, base"
                   % (plat_col or "'x'", expr, W, expr, B, expr, S, plat_col or "'x'"))
            for r in warehouse.query(tbl, sel):
                src = ((r.get("platform") or "offprem").lower().replace(" stores", "").replace(" ", "-")
                       if plat_col else "cityhive")
                orflag((src, r["base"]), r["w"], r["b"], r["s"], r["h"])
        except Exception as e:
            log("  [normalize] %s product-flags: %s" % (tbl, str(e)[:70]))
    try:                                    # observation stores → hemp only (no category on observations)
        for r in warehouse.query_parts("retail_observations", "SELECT source, store_id, "
                                       "bool_or(coalesce(is_hemp,false)) h FROM t WHERE store_id<>'' "
                                       "GROUP BY source, store_id"):
            if r["h"]:
                orflag((r["source"], str(r["store_id"])), h=1)
    except Exception as e:
        log("  [normalize] observation product-flags: %s" % str(e)[:70])


def normalize_facts(log=print):
    """src_pricing + src_inventory — the dated observations, keyed (source, store_id, source_product_id, upc,
    date). Kept raw here (fact_price/fact_inventory carry the resolved store_key/sku_key/hoodie ids).
    Keys are CAST to VARCHAR and measures TRY_CAST — retail_observations partitions carry inconsistent types
    across sources (store_id/product_id int vs string, in_stock bool vs string), so an unguarded union_by_name
    scan fails mid-fetch. Explicit casts make every partition unify."""
    n = {}
    # shared key columns, cast to a stable type so partitions from every source union cleanly
    KEYS = ('"date", source, CAST(store_id AS VARCHAR) store_id, '
            'CAST(product_id AS VARCHAR) product_id, CAST(upc AS VARCHAR) upc')
    for grain, cols, cond in [
            ("src_pricing", "TRY_CAST(price AS DOUBLE) price, CAST(on_promo AS VARCHAR) on_promo, "
                            "CAST(promo AS VARCHAR) promo", "price IS NOT NULL"),
            ("src_inventory", "TRY_CAST(qty AS DOUBLE) qty, CAST(in_stock AS VARCHAR) in_stock, "
                              "CAST(stock_level AS VARCHAR) stock_level", "1=1")]:
        try:
            rows = warehouse.query_parts("retail_observations",
                                         'SELECT %s, %s FROM t WHERE %s' % (KEYS, cols, cond))
            warehouse.write_parquet(grain, rows)
            n[grain] = len(rows)
            log("[normalize] %s=%d" % (grain, len(rows)))
        except Exception as e:
            log("  [normalize] %s skipped: %s" % (grain, str(e)[:120])); n[grain] = 0
    return n


def corroboration(log=print):
    """Per grain: records, distinct entities (by Hoodie mnemonic), and how many are CORROBORATED (seen by >=2
    sources) — the tagged multi-source match, computed WITHOUT any exact-key join. Lands src_summary."""
    con = warehouse.connect()
    out = []
    for grain, tbl, idc in [("brand", "src_brands", "hoodie_brand"), ("product", "src_products", "hoodie_product"),
                            ("item", "src_items", "hoodie_item"), ("sku", "src_skus", "hoodie_sku"),
                            ("outlet", "src_outlets", "hoodie_outlet")]:
        try:
            r = con.execute("WITH g AS (SELECT %s k, count(*) recs, count(DISTINCT source) ns "
                            "FROM read_parquet('%s') GROUP BY %s) "
                            "SELECT sum(recs), count(*), count(*) FILTER (WHERE ns>=2) FROM g"
                            % (idc, warehouse.uri(tbl), idc)).fetchone()
            out.append({"grain": grain, "table": tbl, "records": r[0] or 0, "entities": r[1] or 0,
                        "corroborated": r[2] or 0})
        except Exception as e:
            log("  [normalize] corr %s: %s" % (grain, str(e)[:45]))
    warehouse.write_parquet("src_summary", out, ["grain", "table", "records", "entities", "corroborated"])
    log("[normalize] corroboration -> src_summary: %s"
        % {o["grain"]: "%d/%d corr" % (o["corroborated"], o["entities"]) for o in out})
    return out


def build(catalog=True, outlets=True, facts=True, log=print):
    out = {}
    if catalog:
        out.update(normalize_catalog(log))
    if outlets:
        out.update(normalize_outlets(log))
    if facts:
        out.update(normalize_facts(log))
    if catalog or outlets:
        corroboration(log)
    log("[normalize] DONE: %s" % out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--outlets", action="store_true")
    ap.add_argument("--facts", action="store_true")
    a = ap.parse_args()
    if a.catalog or a.outlets or a.facts:
        build(catalog=a.catalog, outlets=a.outlets, facts=a.facts)
    else:
        build()
