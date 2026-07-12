#!/usr/bin/env python3
"""wb_views.py — pre-compute the Matching workbench's list views as SMALL, denormalized parquet tables at
master-REBUILD time, so the workbench cold-loads by reading a tiny file instead of scanning dim_sku (~874k)
and rebuilding the full name maps (dim_product ~830k + dim_item ~871k) on the first request.

The join (sku -> item -> product -> brand) runs ONCE here, capped, with names already resolved — so the
server endpoints read wb_master / wb_queue / wb_summary directly, no joins or maps at request time.

  wb_master  — corroborated SKUs (sources>=2), the trustworthy master list
  wb_queue   — single-source SKUs (sources=1), the analyst review queue, most-seen first
  wb_summary — rebuild-stable funnel numbers (total / multi / source_rows / by_source)

Called at the tail of build_product_master.build(); the server falls back to the live (cached) computation
when a view table isn't present yet. Standalone: `python wb_views.py`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

CAP = int(os.environ.get("WB_VIEW_CAP", "4000"))       # list views are paginated/scannable in the UI; cap generously
FIELDS = ["sku_key", "item_key", "product_key", "brand", "product_name", "size_ml", "container",
          "pack", "upc", "sources", "source_list", "source_rows"]
_JOIN = ("SELECT s.sku_key, s.item_key, i.product_key, b.brand, p.product_name, i.size_ml, i.container, "
         "s.pack, s.upc, s.sources, s.source_list, s.source_rows "
         "FROM dim_sku s LEFT JOIN dim_item i ON s.item_key = i.item_key "
         "LEFT JOIN dim_product p ON i.product_key = p.product_key "
         "LEFT JOIN dim_brand b ON p.brand_key = b.brand_key ")


def _rows(con, sql):
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _size_label(v):
    if not v:
        return ""
    try:
        v = float(v)
    except Exception:
        return ""
    return ("%gL" % (v / 1000.0)) if v >= 1000 else ("%gml" % v)


def build_merges(con, log=print):
    """Ambiguous MERGE groups for the cluster-review page: SKUs that are the SAME product + size + pack but
    split across DIFFERENT/missing UPCs — i.e. very likely one sellable unit fractured by UPC noise. Grouping
    by (item_key, pack) isolates exactly that (same item = same product+size; same pack = same unit; the only
    thing left differing is the UPC). Distinct-UPC count drives confidence + whether it's a clear merge or a
    genuine judgement call. Prioritized by total source rows (impact). -> wb_merges."""
    MERGE = ("SELECT s.item_key, s.pack, b.brand, p.product_name, i.size_ml, i.container, "
             "count(*) n_skus, sum(s.source_rows) total_rows, list(s.sku_key) sku_keys, list(s.upc) upcs, "
             "list(s.sources) src_counts, list(s.source_list) src_lists, list(s.source_rows) row_counts "
             "FROM dim_sku s LEFT JOIN dim_item i ON s.item_key = i.item_key "
             "LEFT JOIN dim_product p ON i.product_key = p.product_key "
             "LEFT JOIN dim_brand b ON p.brand_key = b.brand_key "
             "GROUP BY s.item_key, s.pack, b.brand, p.product_name, i.size_ml, i.container "
             "HAVING count(*) > 1 ORDER BY total_rows DESC LIMIT %d" % CAP)
    out = []
    for g in _rows(con, MERGE):
        skus, upcs = g["sku_keys"], g["upcs"]
        srcc, srcl, rowc = g["src_counts"], g["src_lists"], g["row_counts"]
        members = []
        for k in range(len(skus)):
            members.append({"sku_key": skus[k], "upc": (upcs[k] or ""), "sources": srcc[k],
                            "source_list": list(srcl[k]) if srcl[k] is not None else [],
                            "source_rows": rowc[k]})
        distinct_upc = len({m["upc"] for m in members if m["upc"]})
        conf = 0.92 if distinct_upc <= 1 else (0.6 if distinct_upc == 2 else 0.42)
        reason = ("Same product / size / pack; UPC missing on some — almost certainly one SKU."
                  if distinct_upc <= 1 else
                  "Same product / size / pack but %d different UPCs — a real merge call." % distinct_upc)
        pk = ("×%s" % g["pack"]) if (g["pack"] and str(g["pack"]) not in ("1", "1.0", "")) else ""
        name = " · ".join(x for x in [(g["product_name"] or "").strip(), _size_label(g["size_ml"]),
                                      (g["container"] or ""), pk] if x) or "(unnamed)"
        out.append({"merge_id": "%s|%s" % (g["item_key"], g["pack"] or ""), "item_key": g["item_key"],
                    "pack": str(g["pack"] or ""), "brand": g["brand"] or "", "name": name,
                    "size_ml": g["size_ml"], "n_skus": g["n_skus"], "total_rows": g["total_rows"],
                    "distinct_upc": distinct_upc, "confidence": conf, "reason": reason,
                    "members": json.dumps(members)})
    warehouse.write_parquet("wb_merges", out,
                            fields=["merge_id", "item_key", "pack", "brand", "name", "size_ml", "n_skus",
                                    "total_rows", "distinct_upc", "confidence", "reason", "members"])
    log("[wb_views] wb_merges=%d ambiguous merge groups" % len(out))
    return len(out)


def build(log=print):
    con = warehouse.connect()
    for t in ("dim_sku", "dim_item", "dim_product", "dim_brand", "_stage_product"):
        try:
            con.execute("CREATE OR REPLACE VIEW %s AS SELECT * FROM read_parquet('%s')"
                        % (t, warehouse.uri(t).strip("'")))
        except Exception as e:
            log("[wb_views] view %s: %s" % (t, str(e)[:60]))
    master = _rows(con, _JOIN + "WHERE s.sources >= 2 ORDER BY s.sources DESC, s.source_rows DESC LIMIT %d" % CAP)
    queue = _rows(con, _JOIN + "WHERE s.sources = 1 ORDER BY s.source_rows DESC LIMIT %d" % CAP)
    warehouse.write_parquet("wb_master", master, fields=FIELDS)
    warehouse.write_parquet("wb_queue", queue, fields=FIELDS)
    agg = _rows(con, "SELECT count(*) total, sum(CASE WHEN sources >= 2 THEN 1 ELSE 0 END) multi FROM dim_sku")[0]
    by = _rows(con, 'SELECT s AS "source", count(*) n FROM (SELECT unnest(source_list) s FROM dim_sku) '
                    "GROUP BY 1 ORDER BY 2 DESC")
    try:
        src_rows = _rows(con, "SELECT count(*) n FROM _stage_product")[0]["n"]
    except Exception:
        src_rows = agg["total"]
    # PRODUCT-level tiering — TTB has no size/UPC so it can only corroborate at the product grain (a registered
    # product also seen in retail = Tier-1). This is TTB's real contribution, invisible at the SKU grain.
    pc = _rows(con, "SELECT count(*) ptot, sum(CASE WHEN sources>=2 THEN 1 ELSE 0 END) pcorr, "
                    "sum(CASE WHEN list_contains(source_list,'ttb_products') "
                    "AND len(list_filter(source_list, x -> x <> 'ttb_products'))>0 THEN 1 ELSE 0 END) ttb_corr, "
                    "sum(CASE WHEN list_contains(source_list,'ttb_products') THEN 1 ELSE 0 END) ttb_tot "
                    "FROM dim_product")[0]
    warehouse.write_parquet("wb_summary", [{"total": agg["total"], "multi": agg["multi"] or 0,
                                            "source_rows": src_rows, "by_source": json.dumps(by),
                                            "prod_total": pc["ptot"], "prod_corr": pc["pcorr"] or 0,
                                            "ttb_corr": pc["ttb_corr"] or 0, "ttb_total": pc["ttb_tot"] or 0}],
                            fields=["total", "multi", "source_rows", "by_source", "prod_total", "prod_corr",
                                    "ttb_corr", "ttb_total"])
    merges = build_merges(con, log=log)
    log("[wb_views] wb_master=%d · wb_queue=%d (cap %d) · total=%d multi=%d · wb_merges=%d"
        % (len(master), len(queue), CAP, agg["total"], agg["multi"] or 0, merges))
    return {"wb_master": len(master), "wb_queue": len(queue), "wb_merges": merges, "total": agg["total"]}


if __name__ == "__main__":
    build()
