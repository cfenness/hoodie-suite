"""ttb_master.py — materialize the TTB COLA registry into `ttb_products` for the product master.

TTB COLA is the federal label registry — every alcohol label ever approved — so it's the HISTORICAL backbone
of the canonical master (~1M bottle records vs ~200k active retail SKUs). The rich data is in `ttb_cola_detail`
(brand_name / fanciful_name / class_type_desc / net_contents / wine_vintage / grape_varietal), and label OCR
(`ttb_cola_labels`) contributes UPCs. This pre-joins + dedups them to the BOTTLE+vintage grain (the master then
collapses vintages into one bottle SKU and tracks them in dim_vintage). Run when the TTB tables refresh:

    python ttb_master.py       # rebuild ttb_products, then run build_product_master.py

`build_product_master._CFG['ttb_products']` maps: name (brand+fanciful) → brand via dictionary, class_type →
category, net_contents → size, wine_vintage → dim_vintage aux, upc from the labels join.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse


def materialize(log=print):
    con = warehouse.connect()
    d = warehouse.uri("ttb_cola_detail").replace("'", "")
    l = warehouse.uri("ttb_cola_labels").replace("'", "")
    # one row per distinct bottle+vintage; brand+fanciful → name; UPC joined from label OCR by ttb_id
    join = (
        "SELECT trim(d.brand_name || CASE WHEN d.fanciful_name IS NOT NULL AND d.fanciful_name<>'' "
        "THEN ' '||d.fanciful_name ELSE '' END) AS name, "
        "d.class_type_desc AS category, d.origin_code AS origin, d.net_contents AS net_contents, "
        "d.wine_vintage AS vintage, d.grape_varietal AS varietal, l.upc AS upc "
        "FROM read_parquet('%s') d LEFT JOIN read_parquet('%s') l ON d.ttb_id = l.ttb_id "
        "WHERE d.brand_name IS NOT NULL AND d.brand_name<>'' "
        "QUALIFY row_number() OVER (PARTITION BY d.brand_name, d.fanciful_name, d.net_contents, d.wine_vintage) = 1"
        % (d, l))
    dst = warehouse.uri("ttb_products").replace("'", "")
    con.execute("COPY (%s) TO '%s' (FORMAT PARQUET)" % (join, dst))
    n = con.execute("SELECT count(*) FROM read_parquet('%s')" % dst).fetchone()[0]
    u = con.execute("SELECT count(*) FROM read_parquet('%s') WHERE upc IS NOT NULL AND upc<>''" % dst).fetchone()[0]
    log("[ttb] ttb_products: %d bottle-grain records (%d with UPC from labels)" % (n, u))
    return n


if __name__ == "__main__":
    materialize()
