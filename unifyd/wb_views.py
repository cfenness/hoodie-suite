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
    warehouse.write_parquet("wb_summary", [{"total": agg["total"], "multi": agg["multi"] or 0,
                                            "source_rows": src_rows, "by_source": json.dumps(by)}],
                            fields=["total", "multi", "source_rows", "by_source"])
    log("[wb_views] wb_master=%d · wb_queue=%d (cap %d) · total=%d multi=%d"
        % (len(master), len(queue), CAP, agg["total"], agg["multi"] or 0))
    return {"wb_master": len(master), "wb_queue": len(queue), "total": agg["total"]}


if __name__ == "__main__":
    build()
