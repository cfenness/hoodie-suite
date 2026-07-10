"""warehouse.py — cheap columnar storage for pulled datasets.

Data lands as **Parquet** in object storage and is queried **in place** with DuckDB
(`read_parquet`), so there's no always-on warehouse compute and no per-GB warehouse bill:
- Remote mode: **Tigris** (Fly's S3-compatible object store, free tier). `fly storage create`
  sets the AWS_* env trio automatically, so this "just works" on the deployed machine.
- Local mode (no bucket configured): Parquet files under `agent_state/warehouse/`, so dev
  and headless tests run the identical code path.

Deps: pyarrow (write), duckdb (query). Both are optional at import time — only loaded when
you actually read/write, so the engine gains no hard dependency until the connector runs.

Env (Tigris on Fly provides the first three via `fly storage create`):
  AWS_ENDPOINT_URL_S3   e.g. https://fly.storage.tigris.dev   (presence => remote mode)
  BUCKET_NAME           the bucket                            (or WAREHOUSE_BUCKET)
  AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION
  WAREHOUSE_PREFIX      key prefix inside the bucket (default 'warehouse')
"""
import os


def _env(*names, default=""):
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return default


def _endpoint(): return _env("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT")
def _bucket():   return _env("BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET")
def _prefix():   return _env("WAREHOUSE_PREFIX", default="warehouse")
def _region():   return _env("AWS_REGION", default="auto")

_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "warehouse")


def remote():
    """True when object storage is configured; else local-disk mode."""
    return bool(_endpoint() and _bucket())


def _local_path(name):
    os.makedirs(_LOCAL_DIR, exist_ok=True)
    return os.path.join(_LOCAL_DIR, name + ".parquet")


def _s3_key(name):
    p = _prefix()
    return (p + "/" if p else "") + name + ".parquet"


def uri(name):
    """Physical location of dataset `name`: an s3:// URI (remote) or a local file path."""
    return ("s3://%s/%s" % (_bucket(), _s3_key(name))) if remote() else _local_path(name)


def write_parquet(name, records, fields=None):
    """Write list-of-dicts to `<name>.parquet` (Tigris or local). Returns {rows, uri}.
    If `fields` is given, every record is projected onto exactly those columns (missing
    -> None), so the Parquet schema is stable across pulls even when a row lacks a key."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    if fields:
        records = [{k: r.get(k) for k in fields} for r in records]
    table = pa.Table.from_pylist(records) if records else pa.table({f: [] for f in (fields or ["_"])})
    if remote():
        from pyarrow import fs as pafs
        s3 = pafs.S3FileSystem(endpoint_override=_endpoint(), access_key=_env("AWS_ACCESS_KEY_ID"),
                               secret_key=_env("AWS_SECRET_ACCESS_KEY"), region=_region(), scheme="https")
        pq.write_table(table, "%s/%s" % (_bucket(), _s3_key(name)), filesystem=s3)
    else:
        pq.write_table(table, _local_path(name))
    return {"rows": len(records), "uri": uri(name)}


def _s3fs():
    from pyarrow import fs as pafs
    return pafs.S3FileSystem(endpoint_override=_endpoint(), access_key=_env("AWS_ACCESS_KEY_ID"),
                             secret_key=_env("AWS_SECRET_ACCESS_KEY"), region=_region(), scheme="https")


def put_bytes(key, data):
    """Store raw bytes at <bucket>/<key> (remote) or <local>/<key> (disk). Used for label-image
    thumbnails (key like 'label_images/<ttbid>.jpg') so the suite can serve them — the original TTB
    URLs are F5-gated and won't load in a browser. Returns the storage key."""
    if remote():
        with _s3fs().open_output_stream("%s/%s" % (_bucket(), key)) as f:
            f.write(data)
    else:
        p = os.path.join(_LOCAL_DIR, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    return key


def get_bytes(key):
    """Read raw bytes back from <bucket>/<key>, or None if absent."""
    try:
        if remote():
            with _s3fs().open_input_stream("%s/%s" % (_bucket(), key)) as f:
                return f.read()
        p = os.path.join(_LOCAL_DIR, key)
        return open(p, "rb").read() if os.path.exists(p) else None
    except Exception:
        return None


def write_partition(name, part, records, fields=None):
    """Append a TIME-SERIES partition: warehouse/<name>/<part>.parquet (e.g. part='2026-07-10_kroger').
    Daily runs land one file per (date, source) so history accumulates instead of overwriting — the
    spine for tracking price + inventory over time. Idempotent per (name, part). Query with query_parts()."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    if fields:
        records = [{k: r.get(k) for k in fields} for r in records]
    table = pa.Table.from_pylist(records) if records else pa.table({f: [] for f in (fields or ["_"])})
    rel = "%s/%s/%s.parquet" % (_prefix(), name, part)
    if remote():
        pq.write_table(table, "%s/%s" % (_bucket(), rel), filesystem=_s3fs())
    else:
        p = os.path.join(_LOCAL_DIR, name, part + ".parquet")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        pq.write_table(table, p)
    return {"rows": len(records), "part": part}


def query_parts(name, sql=None, params=None):
    """Query ALL partitions of a time-series table (warehouse/<name>/*.parquet) as view `t`. Schemas
    may differ across sources, so union_by_name. Empty-safe (returns [] if no partitions yet)."""
    import duckdb
    con = connect()
    glob = ("s3://%s/%s/%s/*.parquet" % (_bucket(), _prefix(), name)) if remote() \
        else os.path.join(_LOCAL_DIR, name, "*.parquet")
    try:
        con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM "
                    "read_parquet('%s', union_by_name=true)" % glob)
    except Exception:
        return []
    cur = con.execute(sql or "SELECT * FROM t", params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def list_datasets():
    """Every <name>.parquet in the warehouse as [{name, rows, fields}] — CHEAP: reads each Parquet
    footer (row count + schema) only, never the data. Powers the estate model's 'whole thing' view."""
    import pyarrow.parquet as pq
    out = []
    try:
        if remote():
            from pyarrow import fs as pafs
            s3 = pafs.S3FileSystem(endpoint_override=_endpoint(), access_key=_env("AWS_ACCESS_KEY_ID"),
                                   secret_key=_env("AWS_SECRET_ACCESS_KEY"), region=_region(), scheme="https")
            base = "%s/%s" % (_bucket(), _prefix())
            infos = s3.get_file_info(pafs.FileSelector(base, recursive=False))
            for info in infos:
                if info.type != pafs.FileType.File or not info.path.endswith(".parquet"):
                    continue
                name = info.path.rsplit("/", 1)[-1][:-8]
                mod = None
                try:
                    mod = info.mtime.timestamp() if info.mtime else None
                except Exception:
                    pass
                try:
                    md = pq.read_metadata(info.path, filesystem=s3)
                    out.append({"name": name, "rows": md.num_rows, "fields": list(md.schema.names), "modified": mod})
                except Exception:
                    out.append({"name": name, "rows": 0, "fields": [], "modified": mod})
        else:
            import glob
            for p in glob.glob(os.path.join(_LOCAL_DIR, "*.parquet")):
                name = os.path.basename(p)[:-8]
                mod = os.path.getmtime(p)
                try:
                    md = pq.read_metadata(p)
                    out.append({"name": name, "rows": md.num_rows, "fields": list(md.schema.names), "modified": mod})
                except Exception:
                    out.append({"name": name, "rows": 0, "fields": [], "modified": mod})
    except Exception:
        pass
    return out


def write_parquet_from_csv(name, csv_path, fields=None):
    """Stream a (large) CSV into `<name>.parquet` (Tigris or local) WITHOUT building a Python
    list-of-dicts — pyarrow reads it columnar, so a multi-million-row backfill (e.g. the 30-year
    TTB COLA pull) stays cheap on memory. `fields` pins the column set + order and forces every
    column to string, so registry IDs / UPCs keep leading zeros. Returns {rows, uri}."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.csv as pacsv
    conv = pacsv.ConvertOptions(column_types={f: pa.string() for f in fields}) if fields else None
    table = pacsv.read_csv(csv_path, convert_options=conv)
    if fields:
        table = pa.table({f: (table.column(f) if f in table.column_names
                              else pa.nulls(table.num_rows, pa.string())) for f in fields})
    if remote():
        from pyarrow import fs as pafs
        s3 = pafs.S3FileSystem(endpoint_override=_endpoint(), access_key=_env("AWS_ACCESS_KEY_ID"),
                               secret_key=_env("AWS_SECRET_ACCESS_KEY"), region=_region(), scheme="https")
        pq.write_table(table, "%s/%s" % (_bucket(), _s3_key(name)), filesystem=s3)
    else:
        pq.write_table(table, _local_path(name))
    return {"rows": table.num_rows, "uri": uri(name)}


def connect():
    """A DuckDB connection, configured for the Tigris endpoint when in remote mode."""
    import duckdb
    con = duckdb.connect()
    if remote():
        host = _endpoint().replace("https://", "").replace("http://", "")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("SET s3_endpoint='%s';" % host.replace("'", ""))
        con.execute("SET s3_access_key_id='%s';" % _env("AWS_ACCESS_KEY_ID").replace("'", ""))
        con.execute("SET s3_secret_access_key='%s';" % _env("AWS_SECRET_ACCESS_KEY").replace("'", ""))
        con.execute("SET s3_url_style='path';")
        con.execute("SET s3_use_ssl=true;")
    return con


def query(name, sql=None, params=None):
    """Query dataset `name` in place. `sql` may reference the view `t` (the Parquet).
    Defaults to `SELECT * FROM t`. Returns a list of dicts."""
    con = connect()
    src = uri(name).replace("'", "")
    con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM read_parquet('%s')" % src)
    cur = con.execute(sql or "SELECT * FROM t", params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
