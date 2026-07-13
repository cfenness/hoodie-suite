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


def normalize_catalog(log=print):
    """Shred _stage_product (the cleaned catalog staging) into src_brands / products / items / skus — one
    deduped record per (source, grain-mnemonic), stamped with raw keys + the Hoodie mnemonic at that grain."""
    rows = warehouse.query("_stage_product",
                           "SELECT _source, _source_id, brand, product_name, flavor, category, abv, varietal, "
                           "origin, region, size_ml, container, pack, upc, image FROM t "
                           "WHERE brand IS NOT NULL AND brand <> ''")
    brands, products, items, skus = {}, {}, {}, {}
    for r in rows:
        src = r["_source"]; sid = str(r.get("_source_id") or ""); upc = str(r.get("upc") or "")
        brand = r["brand"]; pname = r.get("product_name") or ""
        bc = H.brand_code(brand)
        pc = bc + H.product_code(pname, brand)
        ic = pc + H.container_code(r.get("container")) + H.size_code(r.get("size_ml"))
        kc = ic + H.pack_code(r.get("pack"), pname)
        brands.setdefault((src, bc), {"source": src, "source_id": sid, "hoodie_brand": bc, "brand": brand})
        products.setdefault((src, pc), {"source": src, "source_id": sid, "hoodie_product": pc, "brand": brand,
                                        "product_name": pname, "flavor": r.get("flavor"), "category": r.get("category"),
                                        "abv": r.get("abv"), "varietal": r.get("varietal"), "origin": r.get("origin"),
                                        "region": r.get("region"), "image": r.get("image")})
        items.setdefault((src, ic), {"source": src, "source_id": sid, "hoodie_item": ic, "brand": brand,
                                     "product_name": pname, "size_ml": r.get("size_ml"), "container": r.get("container")})
        skus.setdefault((src, kc, upc), {"source": src, "source_id": sid, "upc": upc, "hoodie_sku": kc,
                                         "brand": brand, "product_name": pname, "size_ml": r.get("size_ml"),
                                         "container": r.get("container"), "pack": r.get("pack")})
    warehouse.write_parquet("src_brands", list(brands.values()),
                            ["source", "source_id", "hoodie_brand", "brand"])
    warehouse.write_parquet("src_products", list(products.values()),
                            ["source", "source_id", "hoodie_product", "brand", "product_name", "flavor",
                             "category", "abv", "varietal", "origin", "region", "image"])
    warehouse.write_parquet("src_items", list(items.values()),
                            ["source", "source_id", "hoodie_item", "brand", "product_name", "size_ml", "container"])
    warehouse.write_parquet("src_skus", list(skus.values()),
                            ["source", "source_id", "upc", "hoodie_sku", "brand", "product_name", "size_ml",
                             "container", "pack"])
    log("[normalize] catalog: src_brands=%d · src_products=%d · src_items=%d · src_skus=%d"
        % (len(brands), len(products), len(items), len(skus)))
    return {"src_brands": len(brands), "src_products": len(products), "src_items": len(items), "src_skus": len(skus)}


def normalize_outlets(log=print):
    """src_outlets — one record per (source, store) across the retail observation stores + the resolved outlet
    stage. Raw keys (source, store_id) + a store-name mnemonic as the Hoodie outlet code."""
    out = {}
    try:
        for r in warehouse.query_parts("retail_observations",
                                       'SELECT DISTINCT source, store_id, store FROM t WHERE store_id <> \'\''):
            src, sid = r["source"], str(r["store_id"] or "")
            out[(src, sid)] = {"source": src, "store_id": sid, "store_name": r.get("store") or "",
                               "hoodie_outlet": H.brand_code(r.get("store") or sid)}
    except Exception as e:
        log("  [normalize] outlets from observations skipped: %s" % str(e)[:60])
    warehouse.write_parquet("src_outlets", list(out.values()),
                            ["source", "store_id", "store_name", "hoodie_outlet"])
    log("[normalize] src_outlets=%d" % len(out))
    return {"src_outlets": len(out)}


def normalize_facts(log=print):
    """src_pricing + src_inventory — the dated observations, keyed (source, store_id, source_product_id, upc,
    date). Kept raw here (fact_price/fact_inventory carry the resolved store_key/sku_key/hoodie ids)."""
    n = {}
    for grain, cols, cond in [("src_pricing", "price, on_promo, promo", "price IS NOT NULL"),
                              ("src_inventory", "qty, in_stock, stock_level", "1=1")]:
        try:
            rows = warehouse.query_parts("retail_observations",
                                         'SELECT "date", source, store_id, product_id, upc, %s FROM t WHERE %s'
                                         % (cols, cond))
            warehouse.write_parquet(grain, rows)
            n[grain] = len(rows)
            log("[normalize] %s=%d" % (grain, len(rows)))
        except Exception as e:
            log("  [normalize] %s skipped: %s" % (grain, str(e)[:60])); n[grain] = 0
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
