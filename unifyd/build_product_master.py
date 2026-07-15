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
import dict_apply as _dict_apply
import master_apply
import precleanse as _precleanse
import sku_match as _sku_match
import placeholders as _placeholders

# origin = SOURCE of the juice (COO — where the liquid is from); bottled_in = where it was bottled (kept
# SEPARATE — a Barbados rum bottled in the US is origin=Barbados, bottled_in=US).
FIELDS = ["brand", "brand_group", "product_name", "class_type", "core_name", "flavor", "abv", "style", "category",
          "origin", "country", "state", "bottled_in", "region", "sub_region", "appellation", "varietal", "image",
          "taste", "body", "food_pairing", "expert_rating", "finish",   # ecommerce PDP describing-fields
          "size_ml", "packsize", "container", "pack", "upc", "gtin", "vintage", "edition", "supplier"]

# sources with a REAL brand column — used to seed the brand dictionary
_CLEAN_BRAND = [("kroger_products", "brand"), ("walmart_products", "brand"), ("nc_pricing", "Brand Name"),
                ("binnys_products", "brand"), ("target_products", "brand"), ("specs_products", "brand"),
                ("abc_products", "brand")]

# per-source projection: which columns carry name / size / upc / abv(or proof) / category / clean-brand, + a
# bev-alc filter so non-alcohol rows (a Kroger marinade) don't feed the master.
_CFG = {
    # ABC via its SearchSpring facet API (abc_facets.py) — rich, retailer-tagged: varietal/type/region/country/
    # class from ABC's OWN drill-path taxonomy (authoritative, not name-guessed). No UPC in the facet feed; the
    # store-inventory abc_catalog (UPC) still feeds observe, and both share `sku`.
    "abc_products": dict(name="name", brand="brand", varietal="varietal", region="region", country="country",
                         origin="country", sub_region="sub_region", cat="type", size="size", image="image", id="sku"),
    "kroger_products": dict(name="product_name", size="size", upc="upc", brand="brand", cat="category",
                            image="image_url", filt=lambda r: r.get("category") == "Adult Beverage", dedup=["product_id"]),
    "walmart_products": dict(name="product_name", size_ml="size_ml", upc="upc", abv="abv", brand="brand",
                             cat="category", varietal="varietal", region="region", vintage="vintage",
                             container="container", image="image", flavor="flavor", food_pairing="pairing",
                             filt=lambda r: r.get("is_alcohol")),
    # Total Wine — landed by total_wine.crawl_land (mobile-UA microdata + the structured attributes JSON), so it
    # carries the geo the old thin `totalwine_products_full` table never did (varietal/origin/region/appellation).
    "total_wine_products": dict(name="name", brand="brand", size="size", cat="category", abv="abv", image="image",
                                varietal="varietal", origin="origin", region="region", sub_region="sub_region",
                                appellation="appellation", country="country", state="state", style="style",
                                taste="taste", body="body", food_pairing="food_pairing",
                                expert_rating="expert_rating", finish="finish", dedup=["sku"]),
    "binnys_products": dict(name="name", brand="brand", dedup=["sku"], varietal="varietal", image="image",  # store×product →
                            region="region", sub_region="sub_region", appellation="appellation",             # distinct products
                            abv="abv", origin="origin", cat="category"),
    "target_products": dict(name="name", brand="brand", cat="category", image="image_url", dedup=["tcin"]),
    "specs_products": dict(name="name", brand="brand", dedup=["sku"], varietal="varietal", region="region",
                           country="origin", state="state", cat="type", abv="abv", image="image",
                           upc="upc", vintage="vintage", taste="tasting_notes"),   # upc now parsed from image
    "cityhive_products": dict(name="name", size_ml="size_ml", cat="bev_category", image="image", dedup=["sku"]),  # independent
    # off_premise.run_census — independent retailers on Shopify/Woo/Squarespace/Wix (free direct-fetch sweep)
    "offprem_products": dict(name="name", brand="brand", size_ml="size_ml", cat="bev_category", upc="upc",
                             abv="abv", image="image", varietal="varietal", origin="origin", region="region",
                             sub_region="sub_region", appellation="appellation", container="container",
                             vintage="vintage", bottled_in="bottled_in", id="sku", dedup=["base", "name"]),
    # TTB COLA — the federal label registry = the historical backbone (~1M bottle+vintage records). Pre-joined
    # (detail + labels) + deduped in ttb_products; brand extracted from the name via the dictionary; vintage →
    # dim_vintage aux (bottles don't split by vintage). All alcohol, so no bev-alc filter.
    "ttb_products": dict(name="name", brand="brand_name", cat="category", origin="origin", size="net_contents",
                         vintage="vintage", upc="upc", abv="abv", varietal="varietal", id="ttb_id"),

    "or_pricing": dict(name="description", size="size", cat="category", proof="proof",
                       origin="countryoforigin", container="containertype"),
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


# style/category words that pollute the brand dictionary (a brewery named "Saison") — never a brand ON THEIR OWN.
_STYLE_WORDS = {"saison", "ipa", "ale", "lager", "stout", "porter", "pilsner", "pils", "beer", "cider", "seltzer",
                "mead", "rum", "ron", "vodka", "gin", "whiskey", "whisky", "bourbon", "rye", "scotch", "tequila",
                "mezcal", "brandy", "cognac", "liqueur", "wine", "red", "white", "rose", "rosé", "champagne",
                "prosecco", "sparkling", "sake", "reserve", "select", "premium", "classic"}


def _scan_brand(name, by1):
    """The BASE KNOWN dictionary brand appearing ANYWHERE in the name — skips a leading store/distributor prefix
    ("Broadway Spirits Bacardi Superior" -> Bacardi). Ignores lone style words. None if no recognized brand."""
    toks = re.findall(r"[A-Za-z0-9'&.\-]+", _precleanse.unescape(name or ""))
    for i in range(len(toks)):
        tail = " ".join(toks[i:]).lower()
        cands = [b for b in by1.get(toks[i].lower(), [])
                 if tail.startswith(b.lower()) and b.lower() not in _STYLE_WORDS]
        if cands:
            return min(cands, key=len)              # the BASE brand (Bacardi, not Bacardi Superior) — the
    return None                                     # sub-brand/expression is the product core, so this merges


def _known_brand(c, by1):
    t = re.findall(r"[A-Za-z0-9'&.\-]+", c or "")
    return bool(t) and any(b.lower() == (c or "").strip().lower() for b in by1.get(t[0].lower(), []))


def resolve_brand(name, by1, clean=None):
    # A KNOWN brand in the NAME wins over a store/distributor prefix. The off-premise (Shopify) feeds put the
    # STORE in the brand column ("Broadway Spirits", "Happysliquor", "Jensens Liquors"), which fragments one
    # product across dozens of store-"brands". So trust the brand column only when it is ITSELF a known brand;
    # otherwise, if the name carries a recognized brand, use that.
    dict_brand = _scan_brand(name, by1)
    nm = _precleanse.unescape(name or "").strip()
    if clean and str(clean).strip():
        c = _precleanse.unescape(str(clean)).strip()   # D&#39;Aquino -> D'Aquino
        # Override the column ONLY on the off-premise pattern: the NAME starts with the store column, and the
        # column isn't itself a known brand. Then the real brand is what appears AFTER the store prefix.
        if dict_brand and not _known_brand(c, by1) and nm.lower().startswith(c.lower()):
            return dict_brand
        return c.title() if c.isupper() else c          # TTB brand_name is ALL-CAPS -> readable display
    if dict_brand:                                      # no brand column (City Hive) — scan the name
        return dict_brand
    toks = re.findall(r"[A-Za-z0-9'&.\-]+", _precleanse.unescape(name or "").strip())
    return " ".join(toks[:2]) if toks else None         # fallback: first 1-2 words (unknown indie brand)


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


def _sane_ml(v):
    """Reject implausible size parses (75000 / 375000 ml — ml/L or ×N bugs) so a bad mapped value doesn't poison
    the item grain; the name re-parse then supplies a clean size. Retail bev-alc runs ~1 ml .. 20 L."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v) if 1 <= v <= 20000 else None


_PACKN = re.compile(r"\b(\d{1,2})\s*(?:pk|pack|packs|ct|count)\b|\bpack\s*of\s*(\d{1,2})\b|\b(\d{1,2})\s*x\s*\d",
                    re.I)


def _pack_of(name):
    """Pack count from the product NAME: '6-pack' / '12pk' / '4 pack' / 'pack of 6' / '6 x 355ml' → the integer.
    Singles (1) and absurd counts return None so a pack signal only fires when it's real — this is what keeps a
    6-pack from clustering with the single (the pack is part of the SKU grain)."""
    m = _PACKN.search(name or "")
    if not m:
        return None
    n = next((g for g in m.groups() if g), None)
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return n if 2 <= n <= 48 else None


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
_DESC_TOK = re.compile(r"^\d{2,3}p?$|^\d{2,3}proof$|^\d{4}$|^\d+(ml|l|lit|ltr|cl|oz|pk|pt|qt)$")  # proof/vintage/size remnants


def _tokset(name):
    return frozenset(t for t in re.findall(r"[a-z0-9]+", _precleanse.deaccent(name).lower())
                     if t not in _NOISE_TOK and len(t) > 1)


def _clean_display(name):
    toks = [t for t in re.split(r"\s+", (name or "").strip())
            if t and (set(t) - set("-–")) and t.lower() not in _FORMAT_NOISE]   # drop pure-dash + format words
    s = re.sub(r"#\d+\b\s*", "", " ".join(toks))          # drop "#815"-style SKU refs
    return re.sub(r"\s{2,}", " ", s).strip(" -–")


# GENERIC beverage-type words carry no product identity within a brand+size ("New Amsterdam Vodka" == "New
# Amsterdam"), so removing them lets those variants fold. Flavor/expression/varietal words (Citron, Black,
# Reserve, Cabernet) are NOT here — they distinguish real products and must survive.
_CATEGORY_TOK = {"vodka", "gin", "rum", "whiskey", "whisky", "bourbon", "tequila", "mezcal", "cognac", "brandy",
                 "liqueur", "cordial", "wine", "beer", "ale", "lager", "cider", "scotch", "vermouth", "sake",
                 "soju", "spirits", "spirit", "liquor", "schnapps"}


# pack/format counts: "4-pack", "6-4pks", "3 8", "8pk", "12 x 355", "24 count"…
_PACK_RE = re.compile(r"\b\d+\s*[-x/]?\s*(pack|pk|pks|cans?|bottles?|btls?|ct|count)\b|"
                      r"\b\d+\s*[-x]\s*\d+\s*(pks?|pack|cans?)?\b|\b\d+\s+\d+\b", re.I)


def _clean_core(core, s=None):
    """Normalize the product-KEY core into just the product DISTINGUISHER — everything that belongs at another
    grain level is pulled out: pack/format counts (sku level: "Mango 4-pack" -> "Mango"), the row's own
    flavor/varietal (product attributes, kept in their fields + the key, not duplicated in the core), and its
    region/appellation/origin (attributes, so "Reserve Paso Robles" == "Reserve California" -> "Reserve"). Then
    each token runs through the data dictionaries (drop noise/format/size, collapse synonyms). '' for a flagship."""
    core = _PACK_RE.sub(" ", core or "")
    if s:                                        # attributes captured at their own grain -> not the product core
        for fld in ("flavor", "varietal", "region", "sub_region", "appellation", "origin", "bottled_in"):
            v = (s.get(fld) or "").strip()
            if len(v) > 2:
                core = re.sub(r"\b" + re.escape(v) + r"\b", " ", core, flags=re.I)
    out = []
    for t in re.findall(r"[A-Za-z0-9'&.\-]+", core):
        nt = _dict_apply.norm_token(t.lower())
        if nt == t.lower():          # unchanged -> keep original case for display
            out.append(t)
        elif nt:                     # synonym-mapped (e.g. silver -> white)
            out.append(nt)
        # else '' -> drop (noise/size)
    return re.sub(r"\s+", " ", " ".join(out)).strip(" -&.,")


def _core_key(name):
    """The product's IDENTITY tokens within its brand — the tokset minus size/proof/vintage remnants and generic
    beverage-type words, THEN normalized through the data dictionaries (dict_apply: drop noise/size/format tokens,
    collapse descriptor synonyms so "Superior Silver" == "Superior Blanco"). Two names with the same core (+ same
    brand + size) are the same product. The dictionaries are editable data, not hardcoded rules."""
    out = set()
    for t in _tokset(name):
        if _DESC_TOK.match(t) or t in _CATEGORY_TOK:
            continue
        nt = _dict_apply.norm_token(t)          # '' drops (noise/size); else the canonical synonym token
        if nt:
            out.add(nt)
    return frozenset(out)


def _ncmp(s):
    return re.sub(r"[^a-z0-9]", "", _precleanse.deaccent(s).lower())


def _canon_product_name(brand, name_counter, rows):
    """Enforce consistent verbiage: the product reads as 'Brand + type/flavor' (New Amsterdam -> New Amsterdam
    Vodka). Prefer the CONSENSUS source name that already leads with the brand; ensure it's brand-prefixed; and
    if a product resolves to just the bare brand, append its beverage type so the flagship still names itself."""
    ranked = sorted(name_counter.items(), key=lambda kv: (-kv[1], -len(_tokset(kv[0])), len(kv[0])))
    bl = _ncmp(brand)
    canon = next((n for n, _ in ranked if bl and _ncmp(n).startswith(bl)), None) or ranked[0][0]
    if brand and not _ncmp(canon).startswith(bl):                 # not brand-led -> prepend the brand
        canon = ("%s %s" % (brand, canon)).strip()
    if brand and _ncmp(canon) == bl:                              # resolved to the bare brand -> add its type
        types = collections.Counter()
        for n, c in name_counter.items():
            for t in _tokset(n):
                if t in _CATEGORY_TOK:
                    types[t] += c
        for r in rows:
            for t in _tokset(r.get("category")):
                if t in _CATEGORY_TOK:
                    types[t] += 1
        if types:
            canon = "%s %s" % (brand, types.most_common(1)[0][0].title())
    return canon


def canonicalize(staged, log=print):
    """Fold product-name variants to ONE canonical string per (brand, significant-core) so the same product
    corroborates across sources. Groups by the core-key (identity tokens, minus size/proof/vintage remnants and
    generic type words) — O(n), so it scales to EVERY brand (the old descriptor-superset fold was O(n^2) and
    skipped brands with >200 name-variants, exactly the popular brands with the most cross-source overlap).
    Flavor/expression tokens survive the core-key, so distinct products (Absolut vs Absolut Citron, Jim Beam vs
    Jim Beam Black) stay apart; size still separates SKUs downstream."""
    import collections
    groups = collections.defaultdict(list)
    for r in staged:
        groups[(r.get("brand"), _core_key(r.get("product_name")))].append(r)
    folded = 0
    for rows in groups.values():
        names = collections.Counter(_clean_display(r["product_name"]) for r in rows if r.get("product_name"))
        names.pop("", None)
        if not names:
            continue
        # canonical display: enforced verbiage — 'Brand + type/flavor', from the cross-source consensus.
        canon = _canon_product_name(rows[0].get("brand"), names, rows)
        if len(names) > 1:
            folded += len(names) - 1
        for r in rows:
            r["product_name"] = canon
    log("[master] canonicalize: folded %d name-variants to canonical (brand+core, all brands)" % folded)
    return staged


def split_by_abv(staged, log=print):
    """Un-merge products that canonicalization over-collapsed. Proof/ABV DISTINGUISHES spirit expressions (Old
    Forester 86 vs 100 proof) but the proof token is stripped as a 'descriptor', so with no UPC to separate them
    they land in one cluster spanning 43% / 46.5% / 50% ABV. ABV is the reliable signal: where a (brand, product,
    size) cluster carries 2+ materially different ABVs, bind the proof back into the product name so they split
    into distinct products. Rows with no ABV stay in the base (unspecified) product — honestly un-splittable."""
    groups = collections.defaultdict(list)
    for r in staged:
        groups[(r.get("brand"), r.get("product_name"), r.get("size_ml"))].append(r)
    split = 0
    for rows in groups.values():
        abvs = {round(float(r["abv"]), 1) for r in rows if r.get("abv")}
        if len(abvs) >= 2 and (max(abvs) - min(abvs)) >= 1.0:      # genuinely different strengths = different SKUs
            for r in rows:
                if r.get("abv"):
                    r["product_name"] = "%s %d Proof" % (r["product_name"], round(float(r["abv"]) * 2))
                    split += 1
    log("[master] split %d rows by ABV (distinct-proof expressions no UPC could separate)" % split)
    return staged


def build_source_xwalk(con, log=print):
    """(source, source_product_id) -> product_key / item_key / sku_key. Recomputes the SAME md5 identity keys
    master_apply.resolve_hierarchy assigns, but over _stage_product (which now carries _source + _source_id) —
    so every source catalog row maps to the master key it landed in. This is what lets the fact tables resolve
    UPC-LESS observations (ABC/Binny's/Target/Total Wine, keyed by the retailer's own product id) to a master
    product — the ~7M rows the UPC path could never reach."""
    keys = master_apply._keyexprs(FIELDS)
    uri = warehouse.uri("_stage_product")                 # quoted 's3://…'
    sql = ("SELECT _source AS source, CAST(_source_id AS VARCHAR) AS product_id, "
           "%s AS product_key, %s AS item_key, %s AS sku_key "
           "FROM read_parquet('%s') WHERE _source_id IS NOT NULL AND _source_id <> '' "
           "GROUP BY 1, 2, 3, 4, 5" % (keys["product"], keys["item"], keys["sku"], uri))
    # normalize the catalog TABLE name to the OBSERVATION source name so facts join directly:
    # abc_catalog->abc, binnys_products->binnys, total_wine_products->total-wine …
    def _obs_src(t):
        return t.replace("_products", "").replace("_catalog", "").replace("_", "-")
    rows = [dict(source=_obs_src(r[0]), product_id=r[1], product_key=r[2], item_key=r[3], sku_key=r[4])
            for r in con.execute(sql).fetchall()]
    warehouse.write_parquet("xwalk_source_sku", rows,
                            ["source", "product_id", "product_key", "item_key", "sku_key"])
    log("[master] xwalk_source_sku: %d (source,product_id)->sku rows" % len(rows))
    return len(rows)


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
        idc = c.get("id") or ((c.get("dedup") or [None])[0])   # the source's product-id col (matches obs.product_id)
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
            sz = _sane_ml(sz)                            # drop insane parses (75000/375000 ml) so name re-parse wins
            if not sz:                                   # source left size blank → parse it from the product NAME
                sz = _sane_ml(_to_ml(nm))                # (e.g. "New Amsterdam Vodka 1750ml" → 1750)
            pk = _pack_of(nm)                            # pack count from the name ("6-pack"/"12pk"/"4 x 355ml")
            abv = _fnum(r.get(c["abv"])) if c.get("abv") else \
                ((_fnum(r.get(c["proof"])) / 2) if c.get("proof") and _fnum(r.get(c["proof"])) else None)
            staged.append(dict(brand=brand, brand_group=None, product_name=clean_name(nm),
                flavor=(str(r.get(c["flavor"])).strip() or None) if c.get("flavor") and r.get(c["flavor"]) else None, abv=abv,
                style=(str(r.get(c["style"])).strip() or None) if c.get("style") and r.get(c["style"]) else None,
                category=r.get(c["cat"]) if c.get("cat") else None,
                origin=((str(r.get(c["origin"])).strip().title() or None) if c.get("origin") and r.get(c["origin"]) else None),
                bottled_in=((str(r.get(c["bottled_in"])).strip().title() or None) if c.get("bottled_in") and r.get(c["bottled_in"]) else None),
                size_ml=sz,
                packsize=None, container=r.get(c["container"]) if c.get("container") else None,
                pack=(_fnum(r.get(c["pack"])) if c.get("pack") and r.get(c["pack"]) else None) or pk,
                upc=(re.sub(r"\D", "", str(r.get(c["upc"]))) or None) if c.get("upc") and r.get(c["upc"]) else None,
                gtin=None, edition=None, supplier=None, _source=ds,
                _source_id=(str(r.get(idc)) if idc and r.get(idc) is not None else None),
                vintage=_clean_vintage(r.get(c["vintage"])) if c.get("vintage") else None,
                **{fld: ((str(r.get(c[fld])).strip() or None) if c.get(fld) and r.get(c[fld]) else None)
                   for fld in ("region", "sub_region", "appellation", "varietal", "image", "country", "state",
                               "taste", "body", "food_pairing", "expert_rating", "finish")}))
            kept += 1
        log("[master]   %-24s %6d products" % (ds, kept))
    _precleanse.precleanse(staged, log)                 # precleanse: canonicalize brand + cleanse name FIRST
    canonicalize(staged, log)                           # smarter matching: fold near-dup names before the shred
    split_by_abv(staged, log)                           # then un-merge distinct-proof expressions ABV separates
    _sku_match.propagate_upcs(staged, log)              # SKU-first: propagate UPCs across matched item clusters
    # TTB<->retail bridge: the class/type is a FIELD on the registry side, baked into the NAME on the retail side.
    # Extract a canonical class + a class/type-stripped name-core from the FINAL name, so the product KEY aligns
    # "Buffalo Trace" (TTB, category=bourbon) with "Buffalo Trace Kentucky Straight Bourbon Whiskey" (retail).
    # The product KEY is brand + class_type + core_name, so core_name must be just the DISTINGUISHER — not the
    # brand and NOT the store/distributor prefix. Retail titles bury the product in store noise ("Happysliquor
    # Bacardi Superior", "My Store BACARDI Superior White Rum", "(Online) Bacardi Superior"), which otherwise
    # fragments one product into dozens. Since the brand is already resolved, take the name FROM the brand onward
    # (drops the store prefix), strip the brand token itself, then strip class/type + size -> "Superior".
    import class_type as _classt
    for s in staged:
        pn = (s.get("product_name") or "").strip()
        brand = (s.get("brand") or "").strip()
        src = pn
        if brand:
            i = pn.lower().find(brand.lower())
            if i >= 0:
                src = pn[i + len(brand):]                # everything AFTER the brand (store prefix + brand dropped)
        ct, _cc, core = _classt.classify(src, s.get("varietal") or s.get("category") or "")
        # fill what the regex classifier misses via the CLASS dictionary (brand->class: "Budweiser" -> beer,
        # "Montecristo" -> cigar). Editable data, so the null class_types keep shrinking without touching code.
        s["class_type"] = ct or _dict_apply.classify(s.get("product_name") or "", "class") or None
        # varietal + flavor are among the CLEANEST fields but most retailers don't supply them — derive from the
        # NAME where the source didn't ("… Cabernet Sauvignon" -> varietal; "Margarita Pineapple" -> flavor).
        if not s.get("varietal"):
            s["varietal"] = _dict_apply.classify(s.get("product_name") or "", "varietal") or None
        if not s.get("flavor"):
            s["flavor"] = _dict_apply.classify(s.get("product_name") or "", "flavor") or None
        # core_name gets the SAME dictionary treatment as _core_key: strip pack-counts ("Mango 4-pack" -> "Mango"),
        # drop noise/size tokens, collapse descriptor synonyms — so the product KEY aligns across sources.
        s["core_name"] = _clean_core(core, s) or None    # empty for a flagship (product == brand) — that's fine
    # class_type CONSISTENCY: class is now part of the product key, so a row where the class wasn't detected
    # ("Bacardi Superior" with no rum word) would split from its siblings ("... Rum" -> rum). Within each
    # (brand, core) group, adopt the MAJORITY class for the blanks + clear misdetection outliers (a class seen
    # <1/3 as often as the modal), while preserving genuine near-balanced splits (Old Overholt bourbon vs rye).
    from collections import Counter as _Counter
    _grp = {}
    for s in staged:
        _grp.setdefault(((s.get("brand") or "").strip().lower(), (s.get("core_name") or "").strip().lower()), []).append(s)
    _fixed = 0
    for g in _grp.values():
        cts = _Counter(s["class_type"] for s in g if s.get("class_type"))
        if not cts:
            continue
        modal, mcount = cts.most_common(1)[0]
        for s in g:
            cur = s.get("class_type")
            if (not cur) or (cur != modal and cts[cur] * 3 < mcount):
                if cur != modal:
                    s["class_type"] = modal
                    _fixed += 1
    log("[master] class_type consistency: %d rows aligned to their brand+core majority" % _fixed)
    log("[master] staged %d rows → _stage_product" % len(staged))
    warehouse.write_parquet("_stage_product", staged, fields=FIELDS + ["_source", "_source_id"])
    con = warehouse.connect()
    h = master_apply.resolve_hierarchy(FIELDS, warehouse.uri("_stage_product").strip("'"), con, built_by="build_product_master")
    dims = {k: v["rows"] for k, v in h.items() if isinstance(v, dict) and "rows" in v}
    try:                                                # source (source, product_id) -> sku_key crosswalk
        build_source_xwalk(con, log=log)               # (recomputes the SAME md5 keys over _stage_product)
    except Exception as e:
        log("[master] source xwalk skipped: %s" % str(e)[:80])
    log("[master] DONE — %s" % dims)
    try:                                                # mint/reuse durable Hoodie IDs for the rebuilt entities
        import hoodie_ids                                # (persistent registry — same real entity keeps its id)
        hoodie_ids.assign(log=log)
    except Exception as e:
        log("[master] hoodie_ids skipped: %s" % str(e)[:80])
    try:                                                # inbound normalization: shred every source row into the
        import normalize                                # per-grain src_<grain> feeds (tagged + Hoodie-mnemonic)
        normalize.build(log=log)
    except Exception as e:
        log("[master] normalize (src_<grain>) skipped: %s" % str(e)[:80])
    try:                                                # pre-compute the workbench list views (fast cold-load)
        import wb_views
        wb_views.build(log=log)
    except Exception as e:
        log("[master] wb_views skipped: %s" % str(e)[:80])
    return dims


if __name__ == "__main__":
    build()
