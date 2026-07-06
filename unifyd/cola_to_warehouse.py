"""cola_to_warehouse.py — load a scraped TTB COLA CSV into the warehouse as Parquet.

The one-time 1996->today COLA backfill is millions of rows — too big for the in-memory JSON
state store (built for thousands, on a 1GB box). Its home is the warehouse: **Parquet in
object storage (Tigris), queried in place by DuckDB** — no always-on compute, no per-GB bill,
and the exact file a dedicated Hoodie Snowflake ingests later via COPY INTO (Parquet is the
neutral on-ramp). This lands the scraper's cola_labels.csv as the `ttb_cola` dataset.

Run it on the machine that did the scrape (your Mac), with the Tigris creds in the env so it
writes to object storage rather than local disk:

    export AWS_ENDPOINT_URL_S3=https://fly.storage.tigris.dev
    export BUCKET_NAME=<your-bucket>
    export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_REGION=auto
    python unifyd/cola_to_warehouse.py --csv ~/hoodie-cola/cola_labels.csv

With no bucket configured it writes local Parquet under agent_state/warehouse/ (same code path,
handy for a dry run). Needs: pyarrow, duckdb  (pip install pyarrow duckdb).
"""
import argparse, csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse


def main():
    ap = argparse.ArgumentParser(description="Load a TTB COLA CSV into the warehouse as Parquet.")
    ap.add_argument("--csv", required=True, help="path to cola_labels.csv from the backfill scrape")
    ap.add_argument("--name", default="ttb_cola", help="warehouse dataset name (default: ttb_cola)")
    a = ap.parse_args()
    if not os.path.exists(a.csv):
        sys.exit("no such file: " + a.csv)

    where = ("TIGRIS bucket " + warehouse._bucket()) if warehouse.remote() else \
            ("LOCAL " + warehouse._LOCAL_DIR + "  (no bucket env set — this is a dry run)")
    size_mb = os.path.getsize(a.csv) / 1e6
    print("loading %s (%.1f MB) -> %s as '%s'" % (a.csv, size_mb, where, a.name))

    with open(a.csv, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))          # the file's own columns (index OR enriched schema)
    res = warehouse.write_parquet_from_csv(a.name, a.csv, fields=header)
    print("wrote %s rows -> %s" % (f"{res['rows']:,}", res["uri"]))

    # verify by querying the Parquet back through DuckDB (in place)
    n = warehouse.query(a.name, "SELECT count(*) AS c FROM t")[0]["c"]
    yrs = warehouse.query(a.name,
        "SELECT min(\"Approval Date\") AS first, max(\"Approval Date\") AS last FROM t "
        "WHERE \"Approval Date\" <> ''")
    print("verify: %s rows queryable; approval-date range %s" % (f"{n:,}", yrs[0] if yrs else "n/a"))
    print("done. dataset '%s' is now queryable via warehouse.query('%s', ...)." % (a.name, a.name))


if __name__ == "__main__":
    main()
