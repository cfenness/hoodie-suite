"""iowa_bq.py — Iowa Liquor Sales via the BigQuery public mirror → warehouse.

Iowa retired its Socrata API (the new "Iowa Data Hub" is a Cloudflare-fronted Next.js SPA with no
public data API — /resource/, /api/views/, /api/id/ all 404). But Google hosts the canonical,
always-current mirror as a BigQuery PUBLIC dataset:

    bigquery-public-data.iowa_liquor_sales.sales   — every Class-E spirits transaction since 2012
    (store, product, price, date; ~30M+ rows, a few GB).

We do NOT mirror the 30M raw rows. We land the useful, right-sized slices as warehouse Parquet:
  · iowa_stores        — the ~2k Class-E licensees (name/address/city/county + point) → OUTLETS,
                         mappable, and each a real demand-weighted account (txns + total sales).
  · iowa_products      — the product dimension (item / category / vendor / size / cost + retail).
  · iowa_sales_monthly — month × product statewide sales (bottles / dollars / litres) = the DEMAND
                         signal (also corroborates [[cola-tiering]]).

ACTIVATION (this session can't run GCP OAuth, so it's inert until you do this):
  1. Add the dep on the box:  pip install google-cloud-bigquery   (also uncomment it in requirements.txt)
  2. Create a GCP service account with role "BigQuery Job User"; download its JSON key.
  3. Set Fly secrets:  GCP_SA_JSON='<the key json>'   GCP_PROJECT='<your-project-id>'
     (querying a PUBLIC dataset bills only YOUR project for bytes scanned — ~5GB/query, well within
      the 1 TB/month free tier.)
  4. Run:  python iowa_bq.py            (or build(only="iowa_stores") for one)
google-cloud-bigquery is lazy-imported so importing this module never breaks the server.
"""
import json, os

PUBLIC = "bigquery-public-data.iowa_liquor_sales.sales"

# Each landed slice is a GROUP BY that collapses 30M transactions to a right-sized table. Columns are
# the documented Iowa Liquor Sales schema; store_location is kept as a string ("POINT (lng lat)") and
# parsed to lat/lng downstream (avoids a GEOGRAPHY-vs-STRING assumption we can't test without creds).
QUERIES = {
    "iowa_stores": """
        SELECT store_number,
               ANY_VALUE(store_name)  AS store_name,
               ANY_VALUE(address)     AS address,
               ANY_VALUE(city)        AS city,
               ANY_VALUE(county)      AS county,
               ANY_VALUE(zip_code)    AS zip,
               ANY_VALUE(CAST(store_location AS STRING)) AS store_location,
               COUNT(*)               AS txns,
               ROUND(SUM(sale_dollars), 0) AS total_sales
        FROM `%s`
        GROUP BY store_number
    """ % PUBLIC,
    "iowa_products": """
        SELECT item_number,
               ANY_VALUE(item_description) AS item_description,
               ANY_VALUE(category)         AS category,
               ANY_VALUE(category_name)    AS category_name,
               ANY_VALUE(vendor_number)    AS vendor_number,
               ANY_VALUE(vendor_name)      AS vendor_name,
               ANY_VALUE(pack)             AS pack,
               ANY_VALUE(bottle_volume_ml) AS bottle_volume_ml,
               ANY_VALUE(state_bottle_cost)   AS bottle_cost,
               ANY_VALUE(state_bottle_retail) AS bottle_retail
        FROM `%s`
        GROUP BY item_number
    """ % PUBLIC,
    "iowa_sales_monthly": """
        SELECT FORMAT_DATE('%%Y-%%m', date) AS month,
               item_number,
               ANY_VALUE(item_description) AS item_description,
               ANY_VALUE(category_name)    AS category_name,
               SUM(bottles_sold)           AS bottles_sold,
               ROUND(SUM(sale_dollars), 2) AS sale_dollars,
               ROUND(SUM(volume_sold_liters), 1) AS volume_liters
        FROM `%s`
        GROUP BY month, item_number
    """ % PUBLIC,
}


def _client():
    """A BigQuery client from a service-account key (GCP_SA_JSON) or Application Default Creds."""
    from google.cloud import bigquery
    sa = os.environ.get("GCP_SA_JSON", "").strip()
    proj = os.environ.get("GCP_PROJECT", "").strip() or None
    if sa:
        from google.oauth2 import service_account
        info = json.loads(sa)
        creds = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=proj or info.get("project_id"), credentials=creds)
    return bigquery.Client(project=proj)          # falls back to GOOGLE_APPLICATION_CREDENTIALS / ADC


def build(only=None, log=print):
    """Run the slice queries and land each as a warehouse Parquet dataset. Returns per-slice stats."""
    import warehouse
    client = _client()
    out = []
    for name, sql in QUERIES.items():
        if only and name != only:
            continue
        log("iowa_bq: querying %s …" % name)
        job = client.query(sql)
        rows = [dict(r) for r in job.result()]
        for r in rows:                               # warehouse wants plain scalars, no None
            for k, v in list(r.items()):
                if v is None:
                    r[k] = ""
        fields = list(rows[0].keys()) if rows else []
        res = warehouse.write_parquet(name, rows, fields=fields)
        gb = (job.total_bytes_processed or 0) / 1e9
        log("iowa_bq: %s → %s rows (%.1f GB scanned) → %s" % (name, format(res["rows"], ","), gb, res["uri"]))
        out.append({"name": name, "rows": res["rows"], "gb_scanned": round(gb, 2)})
    return out


if __name__ == "__main__":
    import sys
    only = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    for r in build(only=only):
        print("done:", r)
