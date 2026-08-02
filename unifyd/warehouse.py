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

Two layouts coexist (NRT-PLAN.md §1/§8d). v1: one `<name>.parquet` per table — the default,
unchanged. v2 "bucketed": big accumulating catalogs split into md5-prefix hash buckets
(`<name>/__b=<hex>/part-v<n>.parquet`) with a per-table JSON manifest
(`_manifest/<name>.json`) recording layout + live part files. The public API is the same for
both — `query`/`row_count`/`write_accumulate`/`list_datasets` route through the manifest and
fall back to v1 when absent, so existing callers (including hoodie-backend, which imports
this file) never change.
"""
import hashlib
import json
import os
import time


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


# Transient remote failures worth retrying — network, not data. The httpfs READ strings were added
# after a measured false zero: DuckDB surfaced a dropped part-file footer read as
# "IO Error: Failed to read connection error for HTTP GET to 'https://fly.storage.tigris.dev/…'",
# which matched none of the write-side markers, so a read blip went straight to the caller as [].
# NO_SUCH_UPLOAD was added after a live occurrence building Cockpit's preview-snapshot feature: the
# FIRST-ever write to a brand-new small key ("...NO_SUCH_UPLOAD during CompleteMultipartUpload...")
# failed outright with zero retries, because the string matched none of the markers below either —
# same failure SHAPE as the read-side gap this comment already documents, just on the write side.
# "Server returned nothing" was added after a live occurrence on query_parts("scrape_runs", ...):
# one glob'd partition among hundreds hit "IO Error: Server returned nothing (no headers, no data)
# error for HTTP GET" — a one-off Tigris blip (the SAME file read clean on the very next attempt,
# seconds later) — but the string matched none of the markers below, so ONE partition among many
# raised past the caller and the server's /api/jobs board silently showed 0 running/0 recent for
# EVERY source, not just the one flaky read. The retry loop this list feeds is the whole fix; the
# gap was always just an unrecognized message shape, same as every entry above it.
_TRANSIENT = ("curlCode: 28", "NETWORK_CONNECTION", "Timeout was reached", "timed out",
              "Connection reset", "SlowDown", "503", "InternalError", "RequestTimeout",
              "Failed to read connection", "connection error for HTTP", "Connection refused",
              "Unable to connect", "Could not establish connection", "HTTP GET error",
              "NO_SUCH_UPLOAD", "NoSuchUpload", "Server returned nothing")


def _retry(fn, what="s3 write"):
    """Run a remote read/write with bounded exponential backoff. Tigris multipart uploads
    occasionally time out mid-part (curlCode 28 / AWS NETWORK_CONNECTION during UploadPart) — a
    transient network blip, not a data problem — and that was surfacing as a whole failed pull (e.g.
    offprem-census losing a 5000s crawl to one dropped part). Retrying the write is safe: every
    warehouse write is a full-object PUT or a fresh part file, so a re-run overwrites cleanly and
    is idempotent. Reads are trivially safe to retry. Local-disk I/O doesn't come through here.
    Env: WAREHOUSE_WRITE_RETRIES (default 4)."""
    import time as _t
    tries = max(1, int(os.environ.get("WAREHOUSE_WRITE_RETRIES", "4")))
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1 or not any(s in str(e) for s in _TRANSIENT):
                raise
            _t.sleep(min(2 ** i, 30))          # 1s, 2s, 4s, 8s … capped 30s


def _local_path(name):
    os.makedirs(_LOCAL_DIR, exist_ok=True)
    return os.path.join(_LOCAL_DIR, name + ".parquet")


def _s3_key(name):
    p = _prefix()
    return (p + "/" if p else "") + name + ".parquet"


def uri(name):
    """Physical location of dataset `name`: an s3:// URI (remote) or a local file path."""
    return ("s3://%s/%s" % (_bucket(), _s3_key(name))) if remote() else _local_path(name)


def _partition_files(name):
    """Every part of the DATE-PARTITIONED layout: <prefix>/<name>/*.parquet (written by
    write_partition, one file per date×source). Empty list when `name` is not partitioned.

    This layout was invisible to the row counters: they resolved `uri(name)` → <name>.parquet, which
    for a partitioned table does not exist, so the 404 was classified as genuine absence and the table
    counted 0. retail_observations — 15.1M rows across ~200 parts — read as EMPTY to every caller,
    including run_sources' landing verification. Any source writing a partitioned table therefore
    recorded rows_before=0/rows_after=0/delta=0 on every run and had its status forced to `empty`
    regardless of what it actually landed."""
    rel = "%s/%s" % (_prefix(), name)
    try:
        if remote():
            from pyarrow import fs as pafs
            sel = pafs.FileSelector("%s/%s" % (_bucket(), rel), recursive=False, allow_not_found=True)
            return [i.path for i in _s3fs().get_file_info(sel) if i.path.endswith(".parquet")]
        d = os.path.join(_LOCAL_DIR, name)
        if not os.path.isdir(d):
            return []
        return [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".parquet")]
    except Exception:
        return []


def _partition_rows(paths):
    """Sum the row counts of the given part files from their FOOTERS only (never reads data).
    Concurrent because a partitioned table can have hundreds of parts and each footer is a round trip.
    Raises if ANY part fails to read — a partial sum is a wrong number, and a wrong number here is
    exactly what this whole fix exists to stop."""
    import pyarrow.parquet as pq
    from concurrent.futures import ThreadPoolExecutor
    fsys = _s3fs() if remote() else None

    def _one(p):
        return pq.read_metadata(p, filesystem=fsys).num_rows if fsys else pq.read_metadata(p).num_rows
    with ThreadPoolExecutor(max_workers=min(16, max(4, len(paths)))) as ex:
        return sum(ex.map(_one, paths))


def row_count(name):
    """Current row count of `<name>` — from the layout manifest when the table is bucketed (v2), by
    summing part footers when it is date-partitioned, else via its Parquet FOOTER only (cheap — never
    reads data). 0 when the table is genuinely ABSENT.

    A TRANSIENT read error (Tigris timeout / dropped part / 503) is RETRIED, not silently reported
    as 0. A false 0 here poisons everything downstream: a healthy pull whose after-count blipped got
    recorded 'empty' or a -N 'clobber' (e.g. haskells 10518->0 while the Parquet was intact — the
    Mac's flakier S3 reads made this routine), and every console/health verdict is built on this
    count. On a PERSISTENT non-absence read failure it raises RowCountUnavailable so callers can tell
    'unknown' from 'empty' (run_sources then trusts the prior count instead of fabricating a drop)."""
    man = read_manifest(name)
    if man and man.get("layout") == "bucketed":
        return sum(int(p.get("rows") or 0) for p in (man.get("parts") or {}).values())
    parts = _partition_files(name)
    if parts:
        try:
            return _retry(lambda: _partition_rows(parts), "row_count(parts) %s" % name)
        except Exception:
            return 0                                    # display-safe fallback (contract unchanged)
    import pyarrow.parquet as pq
    u = uri(name)
    def _read():
        if u.startswith("s3://"):
            return pq.read_metadata(u[5:], filesystem=_s3fs()).num_rows
        return pq.read_metadata(u).num_rows
    try:
        return _retry(_read, "row_count %s" % name)
    except Exception:
        return 0                                        # display-safe fallback (contract unchanged)


class RowCountUnavailable(Exception):
    """The row count could not be read (transient/unknown storage error) — NOT proof of an empty table."""


def _is_absent(e):
    """True when the storage error means the object genuinely does not exist (a real 0), vs a
    transient/unknown failure (which must never be conflated with empty)."""
    if isinstance(e, FileNotFoundError):
        return True
    m = str(e)
    return any(s in m for s in ("404", "NoSuchKey", "does not exist", "Path does not exist",
                                "NotFound", "No such file", "not found"))


def row_count_strict(name):
    """Like row_count but RAISES RowCountUnavailable on a persistent non-absence read failure instead
    of returning 0 — so the run-ledger path can tell 'unknown' from 'empty' and never fabricate a
    clobber/empty from a transient storage blip (the haskells 10518->0 false report). Genuine absence
    still returns a real 0."""
    man = read_manifest(name)
    if man and man.get("layout") == "bucketed":
        return sum(int(p.get("rows") or 0) for p in (man.get("parts") or {}).values())
    parts = _partition_files(name)
    if parts:
        # A partitioned table that we CAN see parts for but cannot read is UNKNOWN, never 0 — the same
        # rule the single-file path already follows, for the same reason.
        try:
            return _retry(lambda: _partition_rows(parts), "row_count(parts) %s" % name)
        except Exception as e:
            raise RowCountUnavailable("%s (partitioned, %d parts): %s" % (name, len(parts), str(e)[:100]))
    import pyarrow.parquet as pq
    u = uri(name)
    def _read():
        if u.startswith("s3://"):
            return pq.read_metadata(u[5:], filesystem=_s3fs()).num_rows
        return pq.read_metadata(u).num_rows
    try:
        return _retry(_read, "row_count %s" % name)
    except Exception as e:
        if _is_absent(e):
            return 0
        raise RowCountUnavailable("%s: %s" % (name, str(e)[:120]))


def write_parquet(name, records, fields=None, allow_empty=False):
    """Write list-of-dicts to `<name>.parquet` (Tigris or local). Returns {rows, uri}.
    If `fields` is given, every record is projected onto exactly those columns (missing
    -> None), so the Parquet schema is stable across pulls even when a row lacks a key.

    SAFETY GUARD: writing 0 rows over a table that currently HAS rows is refused (raises),
    because an empty write is never a legitimate rebuild — it's a failed scrape about to
    clobber a good catalog (this is exactly how census_reference got wiped 24,546 -> 0).
    Pass allow_empty=True only for a deliberate truncate."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    man = read_manifest(name)
    if man:
        raise ValueError(
            "write_parquet(%r) refused: table uses the bucketed layout (manifest v%s) — a single-file "
            "overwrite would shadow it. Use write_accumulate (bucket-merge), or rollback_to_single() "
            "first for a deliberate rebuild." % (name, man.get("version")))
    records = list(records or [])
    if fields:
        records = [{k: r.get(k) for k in fields} for r in records]
    if not records and not allow_empty:
        existing = row_count(name)
        if existing > 0:
            raise ValueError(
                "write_parquet(%r) refused: 0 rows would overwrite %d existing rows "
                "(failed pull?). Pass allow_empty=True to force a truncate." % (name, existing))
    table = pa.Table.from_pylist(records) if records else pa.table({f: [] for f in (fields or ["_"])})
    if remote():
        from pyarrow import fs as pafs
        s3 = pafs.S3FileSystem(endpoint_override=_endpoint(), access_key=_env("AWS_ACCESS_KEY_ID"),
                               secret_key=_env("AWS_SECRET_ACCESS_KEY"), region=_region(), scheme="https")
        _retry(lambda: pq.write_table(table, "%s/%s" % (_bucket(), _s3_key(name)), filesystem=s3),
               "write_parquet %s" % name)
    else:
        pq.write_table(table, _local_path(name))
    return {"rows": len(records), "uri": uri(name)}


def write_full_rebuild(name, records, fields=None, allow_empty=False):
    """Write a COMPLETE, from-scratch replacement of `name` — a re-shred, a verified-complete crawl —
    preserving its bucketed layout if it has one.

    `write_parquet` REFUSES outright on a bucketed table ("a flat overwrite would shadow it"), which is
    correct for an accidental caller but wrong for the deliberate full-rebuild case: normalize.py's
    src_outlets re-shred and binnys_scraper's verified-complete-crawl overwrite are BOTH real, scheduled,
    already-working full replacements of tables this fix bucketizes. Without this, the moment either
    table migrates, its next full rebuild would raise instead of landing.

    So: drop to flat, write, re-migrate with the SAME key_cols/hex_len the table already had — the table
    lands back in the layout it was found in, and a plain (never-bucketed) table is untouched by any of
    this (write_parquet, same as always)."""
    man = read_manifest(name)
    if not man:
        return write_parquet(name, records, fields=fields, allow_empty=allow_empty)
    keys, hexlen = man["key_cols"], int(man["hex_len"])
    rollback_to_single(name)
    res = write_parquet(name, records, fields=fields, allow_empty=allow_empty)
    migrate_to_bucketed(name, keys, hex_len=hexlen)
    return res


def _as_key_fn(key):
    """Normalise a `key` into a callable. Accepts a callable (used as-is), a column NAME, or a
    sequence of column names for a composite key."""
    if callable(key):
        return key
    if isinstance(key, str):
        return lambda r: r.get(key)
    if isinstance(key, (list, tuple)):
        cols = tuple(key)
        return lambda r: tuple(r.get(c) for c in cols)
    raise TypeError("write_accumulate(key=...) must be a callable, a column name, or a sequence of "
                    "column names; got %r" % type(key).__name__)


def write_accumulate(name, records, key, fields=None, coverage=True):
    """MERGE `records` into the existing `<name>` table instead of overwriting it: drop existing rows whose
    `key` is being re-written, keep the rest, append `records`. So a small/partial/different-scope run GROWS a
    persistent catalog rather than clobbering a bigger prior run (`write_parquet` is a full overwrite — there
    is no append). `key` maps a row → the identity a re-pull REPLACES: a product id for catalogs, an account
    for per-account menus. Use this for any persistent catalog that accumulates across runs; keep plain
    `write_parquet` for derived/full-rebuild tables and dated partitions.

    Tables migrated to the BUCKETED layout (migrate_to_bucketed / create_bucketed) take the v2 path:
    the merge happens per-bucket in DuckDB (memory bounded by a bucket, not the table) and identity
    comes from the manifest's key_cols — the `key` lambda is not consulted for those tables."""
    records = list(records or [])
    if not records:
        return {"rows": 0, "uri": uri(name)}
    # KEY MAY BE A COLUMN NAME, not just a callable. `key="ts"` reads as obviously correct and has now
    # been written three separate times (snowflake_load, abc_fws, and again by hand) — each time
    # blowing up at runtime with "TypeError: 'str' object is not callable", and each time only AFTER
    # the expensive work was already done. An API whose natural-looking usage is a latent crash is the
    # defect; accept the natural form instead of collecting more instances of the same bug.
    key = _as_key_fn(key)
    man = read_manifest(name)
    if man and man.get("layout") == "bucketed":
        res = _accumulate_bucketed(name, man, records)
    else:
        # READ-MODIFY-WRITE, AND THEREFORE SINGLE-WRITER ONLY. This reads the whole table, drops the
        # keys being replaced, and OVERWRITES. Two callers at once = a lost update: the second writer
        # silently discards everything the first landed. That is not theoretical — an 8-shard UberEats
        # fleet drove ubereats_products from 33,250 rows DOWN to 8,798 the first time the shards lived
        # long enough to overlap. Sharded writers must append parts and let ONE process consolidate.
        #
        # A shard cannot always know it is one, so refuse structurally rather than trusting callers:
        # HOODIE_SHARD is set per-machine for every fleet member.
        _shard = os.environ.get("HOODIE_SHARD") or os.environ.get("UE_SHARD")
        if _shard and str(_shard).split("/")[-1] not in ("1", ""):
            raise RuntimeError(
                "write_accumulate(%r) called from shard %s — concurrent merge would LOSE ROWS. "
                "Shards must write append-only parts (write_partition) and a single writer must "
                "consolidate." % (name, _shard))
        try:
            existing = query(name, "SELECT * FROM t")
        except Exception:
            existing = []
        ks = {key(r) for r in records}
        merged = [e for e in existing if key(e) not in ks] + records
        res = write_parquet(name, merged, fields=fields)
    # Coverage telemetry: record how many distinct items/stores THIS write touched (the honest per-run
    # signal a cumulative merge can't give). Best-effort AFTER the write — must never affect the landing.
    #
    # `coverage=False` for a write that is NOT the source's fact grain. Coverage keeps one reading per
    # source, so with several tables the LAST write wins — and when abc-fws began landing its item
    # master (13,986 rows keyed by sku, no `store` column) after its per-store observations, that
    # catalog write overwrote the real signal: stores read 0 of 133 and a verified 100% crawl was
    # recorded `incomplete`. A catalog write should not be measured against a per-store universe.
    if coverage:
        try:
            import coverage as _cov
            _cov.record_write(name, records, key=key, result=res)
        except Exception:
            pass
    return res


def _s3fs():
    from pyarrow import fs as pafs
    return pafs.S3FileSystem(endpoint_override=_endpoint(), access_key=_env("AWS_ACCESS_KEY_ID"),
                             secret_key=_env("AWS_SECRET_ACCESS_KEY"), region=_region(), scheme="https")


def put_bytes(key, data):
    """Store raw bytes at <bucket>/<key> (remote) or <local>/<key> (disk). Used for label-image
    thumbnails (key like 'label_images/<ttbid>.jpg') so the suite can serve them — the original TTB
    URLs are F5-gated and won't load in a browser. Returns the storage key."""
    if remote():
        def _put():
            with _s3fs().open_output_stream("%s/%s" % (_bucket(), key)) as f:
                f.write(data)
        _retry(_put, "put_bytes %s" % key)
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


def write_partition(name, part, records, fields=None, dtypes=None):
    """Append a TIME-SERIES partition: warehouse/<name>/<part>.parquet (e.g. part='2026-07-10_kroger').
    Daily runs land one file per (date, source) so history accumulates instead of overwriting — the
    spine for tracking price + inventory over time. Idempotent per (name, part). Query with query_parts().

    `dtypes` ({field: pyarrow type}) pins a column's type explicitly instead of letting
    pa.Table.from_pylist infer it from THIS batch's data alone. Without it, a field that happens to be
    all-None in one batch infers as a null/int64 column, while another batch with real string values
    for the SAME field infers VARCHAR — and query_parts()'s union_by_name across all partitions then has
    to reconcile VARCHAR against INTEGER for one column, which corrupts the read (an invalid-UTF8
    decode error, not a clean type error) rather than failing loudly. Measured live 2026-07-30:
    ubereats_products_parts had 5 distinct per-file schemas from this exact cause, and the registered
    consolidation build (and every ad-hoc query) had been silently broken since — 'no accounts landing'
    traced back to a write-time schema bug, not a scheduling gap. Declare the type for any field that
    can legitimately be all-null in a batch; the caller knows its own schema, inference should never
    have to guess it from whatever happened to be in one batch."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    if fields:
        records = [{k: r.get(k) for k in fields} for r in records]
    schema = None
    if dtypes and fields:
        schema = pa.schema([(f, dtypes.get(f, pa.string())) for f in fields])
    if records:
        table = pa.Table.from_pylist(records, schema=schema)
    elif schema is not None:
        table = pa.table({f: pa.array([], type=t) for f, t in zip(schema.names, schema.types)})
    else:
        table = pa.table({f: [] for f in (fields or ["_"])})
    rel = "%s/%s/%s.parquet" % (_prefix(), name, part)
    if remote():
        _retry(lambda: pq.write_table(table, "%s/%s" % (_bucket(), rel), filesystem=_s3fs()),
               "write_partition %s/%s" % (name, part))
    else:
        p = os.path.join(_LOCAL_DIR, name, part + ".parquet")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        pq.write_table(table, p)
    return {"rows": len(records), "part": part}


_NO_FILES = ("No files found matching", "no files found matching", "IO Error: No files",
             "does not exist")


def query_parts(name, sql=None, params=None):
    """Query ALL partitions of a time-series table (warehouse/<name>/*.parquet) as view `t`. Schemas
    may differ across sources, so union_by_name. Empty-safe (returns [] if no partitions yet).

    A TRANSIENT READ IS NOT AN EMPTY TABLE. This used to be a bare `except: return []`, which
    turned a dropped Tigris connection into a result set indistinguishable from "there is no
    data" — the same false-zero class that has already faked empty runs elsewhere, and exactly
    the quiet degrade that is worse than a crash. Measured live: the identical query returned []
    twice and 6,696 rows on the third try, because one part file's footer read blipped.

    So: retry transients with backoff (the same `_retry` the write path uses), return [] ONLY when
    the glob genuinely matches no files, and RAISE anything else so the caller can degrade loudly.
    """
    con = connect()
    glob = ("s3://%s/%s/%s/*.parquet" % (_bucket(), _prefix(), name)) if remote() \
        else os.path.join(_LOCAL_DIR, name, "*.parquet")

    def _mkview():
        con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM "
                    "read_parquet('%s', union_by_name=true)" % glob)
    try:
        _retry(_mkview, what="parquet view %s" % name)
    except Exception as e:
        if any(s in str(e) for s in _NO_FILES):
            return []                       # genuinely no partitions yet — the documented case
        raise
    cur = _retry(lambda: con.execute(sql or "SELECT * FROM t", params or []),
                 what="parquet scan %s" % name)
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
            from concurrent.futures import ThreadPoolExecutor
            s3 = pafs.S3FileSystem(endpoint_override=_endpoint(), access_key=_env("AWS_ACCESS_KEY_ID"),
                                   secret_key=_env("AWS_SECRET_ACCESS_KEY"), region=_region(), scheme="https")
            base = "%s/%s" % (_bucket(), _prefix())
            infos = [i for i in s3.get_file_info(pafs.FileSelector(base, recursive=False))
                     if i.type == pafs.FileType.File and i.path.endswith(".parquet")]

            def _one(info):
                # each footer read is an independent S3 round-trip (~200ms); reading them SEQUENTIALLY over ~170
                # files is 30s+ (and worse under crawl write-contention) — that hung /api/datasets. Parallelize.
                name = info.path.rsplit("/", 1)[-1][:-8]
                try:
                    mod = info.mtime.timestamp() if info.mtime else None
                except Exception:
                    mod = None
                try:
                    md = pq.read_metadata(info.path, filesystem=s3)
                    return {"name": name, "rows": md.num_rows, "fields": list(md.schema.names), "modified": mod}
                except Exception:
                    return {"name": name, "rows": 0, "fields": [], "modified": mod}
            with ThreadPoolExecutor(max_workers=24) as ex:
                out = list(ex.map(_one, infos))
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
    # v2 (bucketed) tables are directories, not top-level files — surface them from their manifests,
    # replacing any same-name single-file rollback copy so counts reflect the LIVE layout.
    try:
        for man in _list_manifests():
            if man.get("layout") != "bucketed":
                continue
            rows = sum(int(p.get("rows") or 0) for p in (man.get("parts") or {}).values())
            out = [o for o in out if o["name"] != man["table"]]
            out.append({"name": man["table"], "rows": rows, "fields": man.get("fields") or [],
                        "modified": man.get("updated_at")})
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
        _retry(lambda: pq.write_table(table, "%s/%s" % (_bucket(), _s3_key(name)), filesystem=s3),
               "write_parquet_from_csv %s" % name)
    else:
        pq.write_table(table, _local_path(name))
    return {"rows": table.num_rows, "uri": uri(name)}


def connect():
    """A DuckDB connection, configured for the Tigris endpoint when in remote mode."""
    import duckdb
    con = duckdb.connect()
    # No ANSI progress bars. Scraper stdout IS the live console in Hoodie Collect, and a redrawing
    # bar arrives down a pipe as dozens of junk lines that bury the run's actual messages.
    try:
        con.execute("SET enable_progress_bar=false")
    except Exception:
        pass
    if remote():
        host = _endpoint().replace("https://", "").replace("http://", "")
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("SET s3_endpoint='%s';" % host.replace("'", ""))
        con.execute("SET s3_access_key_id='%s';" % _env("AWS_ACCESS_KEY_ID").replace("'", ""))
        con.execute("SET s3_secret_access_key='%s';" % _env("AWS_SECRET_ACCESS_KEY").replace("'", ""))
        con.execute("SET s3_url_style='path';")
        con.execute("SET s3_use_ssl=true;")
    # Memory guard (env-driven so LOCAL rebuilds stay unthrottled). On the Fly server the master tables
    # (dim_sku ~874k→1M) are big enough that an unbounded query OOM-kills the gunicorn worker; a memory_limit
    # + temp_directory makes DuckDB SPILL to disk instead of dying. Set DUCKDB_MEMORY_LIMIT in the server env.
    lim = os.environ.get("DUCKDB_MEMORY_LIMIT")
    if lim:
        safe = lim.replace("'", "")
        con.execute("SET memory_limit='%s';" % safe)
        con.execute("SET temp_directory='%s';" % os.environ.get("DUCKDB_TEMP_DIR", "/tmp").replace("'", ""))
        thr = os.environ.get("DUCKDB_THREADS")
        if thr and str(thr).isdigit():
            con.execute("SET threads=%d;" % int(thr))
    return con


def has_column(name, col):
    """True if table `name` has column `col`. Cheap (reads the schema, LIMIT 0). Used to guard queries that
    reference a newly-added column against tables written before that column existed (e.g. geo_precision on a
    src_outlets that predates the geo layer — the column only appears after the first write that includes it)."""
    try:
        query(name, "SELECT %s FROM t LIMIT 0" % col)
        return True
    except Exception:
        return False


def query(name, sql=None, params=None):
    """Query dataset `name` in place. `sql` may reference the view `t` (the Parquet).
    Defaults to `SELECT * FROM t`. Returns a list of dicts.

    Dual-read: a bucketed (v2) table resolves to its manifest's ACTIVE part files; everything
    else reads the single `<name>.parquet` exactly as before."""
    con = connect()
    man = read_manifest(name)
    if man and man.get("layout") == "bucketed":
        files = [f for p in (man.get("parts") or {}).values() for f in (p.get("files") or [])]
        if not files:
            return []
        lst = ", ".join("'%s'" % _part_sql_path(f).replace("'", "") for f in files)
        con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM read_parquet([%s], union_by_name=true)" % lst)
    else:
        src = uri(name).replace("'", "")
        con.execute("CREATE OR REPLACE VIEW t AS SELECT * FROM read_parquet('%s')" % src)
    cur = con.execute(sql or "SELECT * FROM t", params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Warehouse v2 — manifest-driven BUCKETED layout (NRT-PLAN.md Phase 1).
#
# WHY: the v1 accumulate path materializes the whole table in Python and rewrites one file —
# fine at 100k rows, impossible at 250M. v2 splits a catalog into md5-prefix hash buckets and
# merges ONLY the buckets touched by new records, in DuckDB, so memory and I/O are bounded by
# a bucket (~table/N), not the table.
#
# CONTRACT (cross-repo — hoodie-backend sys.path-imports this file): the v1 public API keeps
# working unchanged; the manifest routes. SINGLE WRITER PER TABLE is assumed (the standing
# one-active-scraper-per-source rule). The manifest is written LAST on every mutation, so a
# crashed merge leaves only orphan part files (ignored — reads use the manifest's file list),
# never a torn table. Rollback = drop the manifest (migrate KEEPS the original single file).
# The manifest `changelog` doubles as the change feed hoodie-canon consumes ("which buckets
# changed since version W" — see manifest_changelog).
#
# Bucket assignment MUST be identical Python-side (new records) and SQL-side (migration):
# both use md5 of the same '|'-joined key string — never a runtime-version-dependent hash.
# ══════════════════════════════════════════════════════════════════════════════════════════════

_MAN_DIR = "_manifest"
_MAN_TTL = 15.0     # seconds — short per-process cache so hot read paths (server /api) don't
_MAN_CACHE = {}     # add an S3 round-trip per call for the ~170 unmigrated tables; busted on write.


def _man_rel(name):
    return "%s/%s.json" % (_MAN_DIR, name)


def read_manifest(name):
    """The table's layout manifest, or None (= v1 single-file layout). Cached ~15s per process."""
    now = time.time()
    hit = _MAN_CACHE.get(name)
    if hit and now - hit[0] < _MAN_TTL:
        return hit[1]
    man = None
    try:
        if remote():
            with _s3fs().open_input_stream("%s/%s/%s" % (_bucket(), _prefix(), _man_rel(name))) as f:
                man = json.loads(f.read().decode("utf-8"))
        else:
            p = os.path.join(_LOCAL_DIR, _MAN_DIR, name + ".json")
            man = json.loads(open(p, "rb").read().decode("utf-8")) if os.path.exists(p) else None
    except Exception:
        man = None
    _MAN_CACHE[name] = (now, man)
    return man


def _write_manifest(name, man):
    data = json.dumps(man, separators=(",", ":")).encode("utf-8")
    if remote():
        def _put():
            with _s3fs().open_output_stream("%s/%s/%s" % (_bucket(), _prefix(), _man_rel(name))) as f:
                f.write(data)
        _retry(_put, "manifest %s" % name)
    else:
        p = os.path.join(_LOCAL_DIR, _MAN_DIR, name + ".json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    _MAN_CACHE[name] = (time.time(), man)


def _drop_manifest(name):
    try:
        if remote():
            _s3fs().delete_file("%s/%s/%s" % (_bucket(), _prefix(), _man_rel(name)))
        else:
            p = os.path.join(_LOCAL_DIR, _MAN_DIR, name + ".json")
            if os.path.exists(p):
                os.remove(p)
    except Exception:
        pass
    _MAN_CACHE.pop(name, None)


def _list_manifests():
    """Every layout manifest in the warehouse (one small JSON read per v2 table)."""
    mans = []
    try:
        if remote():
            from pyarrow import fs as pafs
            base = "%s/%s/%s" % (_bucket(), _prefix(), _MAN_DIR)
            names = [i.path.rsplit("/", 1)[-1][:-5] for i in _s3fs().get_file_info(pafs.FileSelector(base))
                     if i.type == pafs.FileType.File and i.path.endswith(".json")]
        else:
            import glob as _g
            names = [os.path.basename(p)[:-5] for p in _g.glob(os.path.join(_LOCAL_DIR, _MAN_DIR, "*.json"))]
        for n in names:
            m = read_manifest(n)
            if m:
                mans.append(m)
    except Exception:
        pass
    return mans


def _part_rel(name, bucket_hex, version):
    return "%s/__b=%s/part-v%d.parquet" % (name, bucket_hex, version)


def _part_sql_path(rel):
    """A part file's path as DuckDB read_parquet wants it (s3:// or local)."""
    return ("s3://%s/%s/%s" % (_bucket(), _prefix(), rel)) if remote() else os.path.join(_LOCAL_DIR, rel)


def _part_write(rel, table):
    import pyarrow.parquet as pq
    if remote():
        _retry(lambda: pq.write_table(table, "%s/%s/%s" % (_bucket(), _prefix(), rel),
                                      filesystem=_s3fs(), compression="zstd"), "part_write %s" % rel)
    else:
        p = os.path.join(_LOCAL_DIR, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        pq.write_table(table, p, compression="zstd")


def _part_delete(rel):
    try:
        if remote():
            _s3fs().delete_file("%s/%s/%s" % (_bucket(), _prefix(), rel))
        else:
            p = os.path.join(_LOCAL_DIR, rel)
            if os.path.exists(p):
                os.remove(p)
    except Exception:
        pass


def _list_part_rels(name):
    """Every `__b=*/*.parquet` file currently on storage for `name`, as prefix-relative paths
    (includes orphans from a crashed run — the manifest, not this listing, defines what's live)."""
    rels = []
    try:
        if remote():
            from pyarrow import fs as pafs
            root = "%s/%s/" % (_bucket(), _prefix())
            for i in _s3fs().get_file_info(pafs.FileSelector(root + name, recursive=True)):
                if i.type == pafs.FileType.File and "/__b=" in i.path and i.path.endswith(".parquet"):
                    rels.append(i.path[len(root):])
        else:
            import glob as _g
            for p in _g.glob(os.path.join(_LOCAL_DIR, name, "__b=*", "*.parquet")):
                rels.append(os.path.relpath(p, _LOCAL_DIR).replace(os.sep, "/"))
    except Exception:
        pass
    return rels


def _key_sql(key_cols):
    """SQL expression for the identity key. MUST stay in lockstep with _key_py — producing the
    same string for the same row is what makes the Python-side bucket match the SQL-side one."""
    return " || '|' || ".join("COALESCE(CAST(\"%s\" AS VARCHAR), '')" % c for c in key_cols)


def _key_py(rec, key_cols):
    return "|".join("" if rec.get(c) is None else str(rec.get(c)) for c in key_cols)


def _bucket_hex(key, hex_len):
    return hashlib.md5(key.encode("utf-8", "replace")).hexdigest()[:hex_len]


def create_bucketed(name, key_cols, fields, hex_len=2):
    """Declare a NEW table bucketed from birth (e.g. a SipSource-scale feed): writes an empty
    manifest so the first write_accumulate lands bucket parts directly. hex_len 1 = 16 buckets
    (medium tables), 2 = 256 (default — the 250M-row class)."""
    if read_manifest(name):
        raise ValueError("create_bucketed(%r): manifest already exists" % name)
    if row_count(name) > 0:
        raise ValueError("create_bucketed(%r): table already has single-file data — use migrate_to_bucketed" % name)
    man = dict(table=name, layout="bucketed", hex_len=int(hex_len), key_cols=list(key_cols),
               fields=list(fields), version=0, updated_at=int(time.time()), parts={},
               changelog=[dict(op="create", version=0, ts=int(time.time()), buckets=[], delta_rows=0)])
    _write_manifest(name, man)
    return man


def _avg_row_bytes(src):
    """Rough bytes-per-row of a parquet file, from its footer — no data read. Used to decide how wide a
    partitioned write may go: fat rows need few open buffers, lean rows can use many."""
    try:
        import pyarrow.parquet as pq
        md = pq.read_metadata(src[5:], filesystem=_s3fs()) if src.startswith("s3://") else pq.read_metadata(src)
        return int(md.serialized_size / max(1, md.num_rows)) if md.num_rows else 0
    except Exception:
        return 4096                      # unknown → assume fat and stay conservative


def migrate_to_bucketed(name, key_cols, hex_len=2):
    """One-time, VERIFIED migration of an existing single-file table to the bucketed layout.

    One DuckDB pass (COPY … PARTITION_BY) splits the table into md5-prefix buckets; row count
    AND distinct-key count are checked against the original BEFORE the manifest is written; the
    original single file is KEPT untouched as the rollback copy (rollback_to_single = drop the
    manifest). Key columns must be string-typed identity columns (SKUs, UUIDs, store ids) so the
    Python and SQL key strings agree. Run only from the writer host, never mid-scrape."""
    import pyarrow.parquet as pq
    if read_manifest(name):
        raise ValueError("migrate_to_bucketed(%r): already migrated" % name)
    hex_len = int(hex_len)
    if hex_len not in (1, 2):
        raise ValueError("hex_len must be 1 (16 buckets) or 2 (256 buckets)")
    src = uri(name).replace("'", "")
    con = connect()
    ks = _key_sql(key_cols)
    n_rows, n_keys = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT (%s)) FROM read_parquet('%s')" % (ks, src)).fetchone()
    if not n_rows:
        raise ValueError("migrate_to_bucketed(%r): empty/missing — use create_bucketed for new tables" % name)
    u = uri(name)
    schema = (pq.read_metadata(u[5:], filesystem=_s3fs()) if u.startswith("s3://")
              else pq.read_metadata(u)).schema
    names = list(schema.names)
    if "__b" in names:
        raise ValueError("migrate_to_bucketed(%r): table already has a __b column" % name)
    for c in key_cols:
        if c not in names:
            raise ValueError("migrate_to_bucketed(%r): key column %r not in table" % (name, c))
    for rel in _list_part_rels(name):        # leftovers from a crashed attempt — nothing reads them
        _part_delete(rel)
    out_dir = (("s3://%s/%s/%s" % (_bucket(), _prefix(), name)) if remote()
               else os.path.join(_LOCAL_DIR, name)).replace("'", "")
    # A PARTITIONED COPY MUST NOT BUFFER THE TABLE. By default DuckDB preserves insertion order, which
    # for a PARTITION_BY write means holding rows back to emit them in order — so peak memory scales with
    # the TABLE, and the migration inherits the exact whole-table memory problem it exists to remove.
    # Measured: splitting 33,250 ubereats_products rows blew a 1.1GB limit, because every row carries a
    # full raw_json payload. Nothing downstream depends on row order within a bucket (identity is the
    # key columns), so ordering is not worth buying. Single-threaded for the same reason: parallel
    # writers each hold their own buffers.
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET threads=1")
    # BOUND THE OPEN PARTITION BUFFERS. hex_len=2 means 256 output partitions, and a PARTITION_BY write
    # keeps a buffer open per partition — so peak memory is (open partitions x buffered rows), and with a
    # fat raw_json column that blows a gigabyte on a 33k-row table. Ordering was not the cost; breadth
    # was. Cap how many partitions are open at once and flush each sooner: the write becomes several
    # bounded passes instead of one wide one. Wrapped because these pragmas are version-dependent, and a
    # DuckDB without them should still do the migration, just hungrier.
    # The cap trades MEMORY for ROUND TRIPS: fewer open partitions means several sequential passes, each
    # writing to object storage. That was the right trade when rows carried fat payloads (8 open files was
    # the difference between finishing and being OOM-killed). With payloads moved to raw_payloads the rows
    # are lean, memory is no longer the binding constraint, and a low cap just makes the write slow for
    # nothing. Scale it to the actual row weight instead of pinning it to the worst case that used to be.
    _open_files = 8 if _avg_row_bytes(src) > 2048 else 64
    for pragma in ("SET partitioned_write_max_open_files=%d" % _open_files,
                   "SET partitioned_write_flush_threshold=4096"):
        try:
            con.execute(pragma)
        except Exception:
            pass
    con.execute("COPY (SELECT *, substr(md5(%s), 1, %d) AS __b FROM read_parquet('%s')) TO '%s' "
                "(FORMAT PARQUET, PARTITION_BY (__b), COMPRESSION ZSTD)" % (ks, hex_len, src, out_dir))
    parts, total = {}, 0
    for rel in _list_part_rels(name):
        b = rel.split("__b=", 1)[1].split("/", 1)[0]
        pu = _part_sql_path(rel)
        md = pq.read_metadata(pu[5:], filesystem=_s3fs()) if pu.startswith("s3://") else pq.read_metadata(pu)
        e = parts.setdefault(b, dict(files=[], rows=0, updated_at=int(time.time())))
        e["files"].append(rel)
        e["rows"] += md.num_rows
        total += md.num_rows
    chk_keys = con.execute("SELECT COUNT(DISTINCT (%s)) FROM read_parquet('%s/__b=*/*.parquet')"
                           % (ks, out_dir)).fetchone()[0]
    if total != n_rows or chk_keys != n_keys:
        raise ValueError("migrate_to_bucketed(%r): VERIFY FAILED (rows %s->%s, distinct keys %s->%s) — "
                         "manifest NOT written, original file untouched" % (name, n_rows, total, n_keys, chk_keys))
    man = dict(table=name, layout="bucketed", hex_len=hex_len, key_cols=list(key_cols), fields=names,
               version=1, updated_at=int(time.time()), parts=parts,
               changelog=[dict(op="migrate", version=1, ts=int(time.time()), buckets=sorted(parts),
                               delta_rows=int(n_rows))])
    _write_manifest(name, man)
    return {"rows": int(n_rows), "buckets": len(parts), "uri": uri(name)}


def _accumulate_bucketed(name, man, records):
    """v2 merge: group new records into their md5-prefix buckets, then rewrite ONLY the touched
    buckets — DuckDB anti-join (existing minus re-written keys) + the new rows, written as one
    fresh part per bucket. Manifest last (the atomic swap), replaced files deleted after."""
    import pyarrow as pa
    cols = man["fields"]
    kc, hex_len = man["key_cols"], int(man["hex_len"])
    recs = [{k: r.get(k) for k in cols} for r in records]
    buckets = {}
    for r in recs:
        buckets.setdefault(_bucket_hex(_key_py(r, kc), hex_len), []).append(r)
    version = int(man["version"]) + 1
    ks = _key_sql(kc)
    sel = ", ".join('"%s"' % c for c in cols)
    stale = []
    con = connect()     # ONE connection for all buckets — connect() re-runs httpfs setup per call,
    for b in sorted(buckets):                        # which multiplies badly over 256 buckets.
        new_tbl = pa.Table.from_pylist(buckets[b])
        con.register("newt", new_tbl)
        files = ((man.get("parts") or {}).get(b) or {}).get("files") or []
        if files:
            lst = ", ".join("'%s'" % _part_sql_path(f).replace("'", "") for f in files)
            merged = con.execute(
                "SELECT %s FROM read_parquet([%s], union_by_name=true) "
                "WHERE (%s) NOT IN (SELECT (%s) FROM newt) "
                "UNION ALL SELECT %s FROM newt" % (sel, lst, ks, ks, sel)).fetch_arrow_table()
        else:
            merged = con.execute("SELECT %s FROM newt" % sel).fetch_arrow_table()
        rel = _part_rel(name, b, version)
        _part_write(rel, merged)
        stale += files
        man.setdefault("parts", {})[b] = dict(files=[rel], rows=merged.num_rows, updated_at=int(time.time()))
    man["version"] = version
    man["updated_at"] = int(time.time())
    man.setdefault("changelog", []).append(dict(op="accumulate", version=version, ts=int(time.time()),
                                                buckets=sorted(buckets), delta_rows=len(recs)))
    man["changelog"] = man["changelog"][-200:]
    _write_manifest(name, man)      # readers switch to the new parts HERE — never mid-write
    for f in stale:                 # only then drop replaced files (best-effort; orphans are inert)
        _part_delete(f)
    return {"rows": len(recs), "uri": uri(name)}


def rollback_to_single(name):
    """Point readers back at the original single file (KEPT by migrate_to_bucketed) by dropping
    the manifest. Bucket part files are left on storage for inspection — delete them once the
    rollback is confirmed. Refuses if no single-file copy exists to fall back to."""
    import pyarrow.parquet as pq
    if not read_manifest(name):
        raise ValueError("rollback_to_single(%r): no manifest — already single-file" % name)
    u = uri(name)
    try:
        n = (pq.read_metadata(u[5:], filesystem=_s3fs()) if u.startswith("s3://")
             else pq.read_metadata(u)).num_rows
    except Exception:
        n = 0
    if n <= 0:
        raise ValueError("rollback_to_single(%r): no single-file copy exists to fall back to" % name)
    _drop_manifest(name)
    return {"rows": n, "uri": u}


def manifest_changelog(name, since_version=0):
    """The change feed (the seam hoodie-canon consumes — NRT-PLAN.md §8c): changelog entries
    with version > since_version, each naming the buckets touched, when, and how many rows
    landed. [] for v1 tables (no manifest)."""
    man = read_manifest(name)
    if not man:
        return []
    return [e for e in man.get("changelog", []) if int(e.get("version", 0)) > int(since_version)]
