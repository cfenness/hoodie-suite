#!/usr/bin/env python3
"""catman.py — category-management deviation from our own data. Derive the norm (what a category SHOULD look
like on shelf, by store format), flag deviations two ways:

  • STORE pivot  — a store missing its format's in-stock core (assortment/distribution voids).
  • BRAND pivot  — a brand voided across the network where peers stock it = the per-brand OUTREACH LIST.

The norm is DERIVED, not licensed (the "master from 0" edge): the core = SKUs stocked in-store at >=`core_thr`
of a store's FORMAT peers. Two rigor points learned building it: (1) "carried" = actually IN STOCK (qty>0), not
merely listed — needs a counts source (Binny's/ABC/7NOW/Total Wine); (2) segment stores by FORMAT before
comparing, else "small store is small" masquerades as a void. This is the engagement wedge: specific, brand-
level, peer-benchmarked, and impossible from sales data.
"""
import argparse, collections, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse


def _load(source, category, where):
    return warehouse.query(source,
        "SELECT store, sku, name, brand, price, qty FROM t WHERE store IS NOT NULL AND name IS NOT NULL "
        "AND qty > 0 AND %s" % where)


def analyze(source="binnys_products", category="Whiskey", core_thr=0.85, edge_stores=("6", "80"),
            where=None, log=print):
    # `where` overrides the category filter (e.g. hemp = "(is_hemp OR thc_mg > 0)"); category is then the label.
    where = where or ("category = '%s'" % category.replace("'", "''"))
    rows = _load(source, category, where)
    if not rows:
        log("[catman] no in-stock rows for %s/%s" % (source, category)); return None
    store_skus = collections.defaultdict(set); sku_name = {}; sku_brand = {}; sku_price = {}
    for r in rows:
        store_skus[r["store"]].add(r["sku"]); sku_name[r["sku"]] = r["name"]
        sku_brand[r["sku"]] = (r.get("brand") or "").strip()
        if r.get("price"): sku_price[r["sku"]] = r["price"]
    sizes = {s: len(v) for s, v in store_skus.items()}
    med = statistics.median(sizes.values())
    real = [s for s, z in sizes.items() if z >= 0.25 * med and s not in edge_stores]
    if len(real) < 6:
        log("[catman] only %d real stores for %s — too thin to format-segment" % (len(real), category)); return None
    rs = sorted(real, key=lambda s: sizes[s]); k = max(1, len(rs) // 3)
    fmt = {**{s: "small" for s in rs[:k]}, **{s: "mid" for s in rs[k:2 * k]}, **{s: "large" for s in rs[2 * k:]}}
    by_fmt = collections.defaultdict(list)
    for s in real:
        by_fmt[fmt[s]].append(s)

    core = {}
    for f, ss in by_fmt.items():
        cnt = collections.Counter()
        for s in ss:
            for sk in store_skus[s]:
                cnt[sk] += 1
        core[f] = {sk for sk, c in cnt.items() if c >= core_thr * len(ss)}

    # STORE pivot: per store, its missing format-core
    store_voids = []
    for s in real:
        f = fmt[s]; miss = core[f] - store_skus[s]
        if miss:
            store_voids.append((s, f, sizes[s], len(miss)))
    store_voids.sort(key=lambda x: -x[3])

    # BRAND pivot: a void instance = (store, core-SKU of that store's format) the store lacks. Aggregate by brand.
    brand_void = collections.defaultdict(lambda: {"instances": 0, "stores": set(), "skus": set()})
    for s in real:
        f = fmt[s]
        for sk in (core[f] - store_skus[s]):
            b = sku_brand.get(sk) or "?"
            bv = brand_void[b]; bv["instances"] += 1; bv["stores"].add(s); bv["skus"].add(sk)
    brands = sorted(brand_void.items(), key=lambda kv: -kv[1]["instances"])

    return {"category": category, "source": source, "n_stores": len(real),
            "formats": {f: (len(ss), int(statistics.median([sizes[s] for s in ss]))) for f, ss in by_fmt.items()},
            "core_sizes": {f: len(c) for f, c in core.items()},
            "store_voids": store_voids, "brand_voids": brands,
            "sku_name": sku_name, "sku_price": sku_price, "fmt": fmt, "by_fmt": by_fmt, "store_skus": store_skus}


def print_report(res, top_brands=12, log=print):
    if not res:
        return
    log("=== CATMAN: %s (%s) — %d stores ===" % (res["category"], res["source"], res["n_stores"]))
    for f in ("small", "mid", "large"):
        if f in res["formats"]:
            n, norm = res["formats"][f]
            log("  %-6s: %d stores, in-stock norm %d SKUs, core %d" % (f, n, norm, res["core_sizes"][f]))
    log("\n-- BRAND pivot (the outreach list): brands most voided vs their format peers --")
    for b, d in res["brand_voids"][:top_brands]:
        sample = sorted(d["skus"], key=lambda sk: -res["sku_price"].get(sk, 0))[:1]
        eg = res["sku_name"].get(sample[0], "") if sample else ""
        log("   %-26s %4d voids across %2d stores   e.g. %s" % (b[:26], d["instances"], len(d["stores"]), eg[:34]))
    log("\n-- STORE pivot: stores with the most core voids --")
    for s, f, z, nv in res["store_voids"][:5]:
        log("   store %-4s [%-5s] %d SKUs on shelf — %d core voids" % (s, f, z, nv))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Category-management deviation (derived norm) on our per-store data.")
    ap.add_argument("--source", default="binnys_products")
    ap.add_argument("--category", default="Whiskey")
    a = ap.parse_args(argv)
    print_report(analyze(source=a.source, category=a.category))
    return 0


if __name__ == "__main__":
    sys.exit(main())
