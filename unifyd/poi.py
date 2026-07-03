"""poi.py — open POI ingestion for enrichment (Orlando).

Reads FREE, storable open-place datasets — **Foursquare OS Places** (Apache-2.0) or
**Overture Maps** — directly from their public S3 Parquet with **DuckDB**, bbox-filtered
to Orlando (no bulk download), and normalizes each place to the enrichment shape
`places.match_open_poi()` expects: `{name, zip, category, lat, lng, source}`.

Runs on Fly (a datacenter IP reaches AWS S3 fine — S3 doesn't block it the way Florida
DBPR does). Pure-ish + injectable so it tests offline against a local fixture Parquet; the
public S3 URI / release date is env-overridable and confirmed on first live run.
"""
import os

# Orlando / Orange County, FL — (xmin, ymin, xmax, ymax) in lng/lat.
ORLANDO_BBOX = (-81.70, 28.30, -80.90, 28.80)


def _fsq_sql(uri, b, limit):
    q = ("SELECT name AS name, postcode AS zip, latitude AS lat, longitude AS lng, "
         "fsq_category_labels AS cats "
         "FROM read_parquet('%s') "
         "WHERE longitude BETWEEN %s AND %s AND latitude BETWEEN %s AND %s "
         "AND name IS NOT NULL" % (uri, b[0], b[2], b[1], b[3]))
    return q + (" LIMIT %d" % limit if limit else "")


def _fsq_norm(r):
    cats = r.get("cats") or []
    cat = None
    if cats:
        # labels look like "Dining and Drinking > Restaurant > Italian" — take the leaf.
        cat = str(cats[0]).split(">")[-1].strip() or None
    return {"name": r.get("name"), "zip": (r.get("zip") or "")[:5] or None,
            "category": cat, "lat": r.get("lat"), "lng": r.get("lng"), "source": "fsq-os"}


def _overture_sql(uri, b, limit):
    # Overture places: point geometry, so bbox.xmin==xmax==lng and bbox.ymin==ymax==lat.
    q = ("SELECT names.primary AS name, "
         "bbox.xmin AS lng, bbox.ymin AS lat, "
         "categories.primary AS category "
         "FROM read_parquet('%s', filename=false, hive_partitioning=1) "
         "WHERE bbox.xmin BETWEEN %s AND %s AND bbox.ymin BETWEEN %s AND %s "
         "AND names.primary IS NOT NULL" % (uri, b[0], b[2], b[1], b[3]))
    return q + (" LIMIT %d" % limit if limit else "")


def _overture_norm(r):
    return {"name": r.get("name"), "zip": None, "category": r.get("category"),
            "lat": r.get("lat"), "lng": r.get("lng"), "source": "overture"}


SOURCES = {
    "fsq": {
        "uri_env": "POI_FSQ_URI", "region": "us-east-1",
        "uri_default": "s3://fsq-os-places-us-east-1/release/dt=2024-12-03/places/parquet/*.parquet",
        "sql": _fsq_sql, "norm": _fsq_norm,
    },
    "overture": {
        "uri_env": "POI_OVERTURE_URI", "region": "us-west-2",
        "uri_default": "s3://overturemaps-us-west-2/release/2024-11-13.0/theme=places/type=place/*",
        "sql": _overture_sql, "norm": _overture_norm,
    },
}


def _connect(cfg, uri):
    import duckdb
    con = duckdb.connect()
    if uri.startswith(("s3://", "https://", "http://")):
        con.execute("INSTALL httpfs; LOAD httpfs;")
        if cfg.get("region"):
            con.execute("SET s3_region='%s';" % cfg["region"].replace("'", ""))
        # public datasets — read unsigned so no AWS creds are needed.
        try:
            con.execute("CREATE OR REPLACE SECRET pub (TYPE s3, PROVIDER config, REGION '%s');"
                        % cfg.get("region", "us-east-1"))
        except Exception:
            pass
    return con


def load(source="fsq", uri=None, bbox=ORLANDO_BBOX, con=None, limit=None):
    """Return (poi_records, meta). `uri` overrides the default (or a local fixture in tests);
    `con` injects a DuckDB connection. Schema-tolerant: raises on an unmapped source so the
    caller can mark the enrich step degraded rather than silently emitting nothing."""
    cfg = SOURCES[source]
    uri = uri or os.environ.get(cfg["uri_env"]) or cfg["uri_default"]
    con = con or _connect(cfg, uri)
    cur = con.execute(cfg["sql"](uri, bbox, limit))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    poi = [cfg["norm"](r) for r in rows]
    return poi, {"source": source, "uri": uri, "count": len(poi)}
