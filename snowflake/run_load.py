#!/usr/bin/env python3
"""run_load.py — run the Snowflake load from a PYTHON host (the Fly machine), no snowsql binary needed.

The container-friendly twin of load.sh: regenerate the build from the live warehouse (change-aware),
execute the SQL through snowflake-connector-python, and commit the load ledger on success. Everything
stays on the box — nothing in CI.

Credentials from the environment:
  Snowflake — the ONLY new secrets to add (`fly secrets set …`):
    SNOWFLAKE_ACCOUNT              e.g. ab12345.us-east-1
    SNOWFLAKE_USER
    SNOWFLAKE_PASSWORD            (or SNOWFLAKE_PRIVATE_KEY = PEM text, for key-pair auth;
                                   + optional SNOWFLAKE_PRIVATE_KEY_PASSPHRASE)
    SNOWFLAKE_ROLE               (optional)
    SNOWFLAKE_WAREHOUSE          (optional, default UNIFYD_LOAD)
    SNOWFLAKE_DATABASE           (optional, default UNIFYD)
  Tigris — REUSED from the app's existing warehouse env (already set on the Fly machine), so you
  don't set them again: AWS_ENDPOINT_URL_S3 · BUCKET_NAME · AWS_ACCESS_KEY_ID · AWS_SECRET_ACCESS_KEY.

Deps: snowflake-connector-python (pyarrow + duckdb are already in the engine, for the --live regen).

  python snowflake/run_load.py            # change-aware live load (regenerate --live, then load)
  python snowflake/run_load.py --offline  # load the committed sql/ as-is (no warehouse regen)
  python snowflake/run_load.py --dry-run  # regenerate + print the plan, connect to nothing
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SQLDIR = os.path.join(HERE, "sql")
STAGE = "WH"
# 00_config.template.sql is NOT here on purpose: it is one-time admin documentation, and its only
# executable statements were CREATE/USE WAREHOUSE. The connection already selects warehouse+database
# (see _connect), so running it added nothing except a hard requirement that the loader role hold
# CREATE WAREHOUSE ON ACCOUNT — which is a privilege a data-loading role has no business holding.
FILES = ["01_database.sql", "02_stage.sql", "03_raw_tables.sql",
         "04_master.sql", "05_marts.sql", "07_grants.sql", "06_validate.sql"]
GEN = os.path.join(HERE, "build_snowflake_sql.py")


def _tigris_tokens():
    """The stage placeholders, derived from the app's EXISTING warehouse creds (unifyd/warehouse.py's
    env) so the load reuses what the Fly machine already has — only the Snowflake creds are new."""
    endpoint = os.environ.get("AWS_ENDPOINT_URL_S3", "https://fly.storage.tigris.dev")
    return {
        "TIGRIS_BUCKET": os.environ.get("TIGRIS_BUCKET") or os.environ.get("BUCKET_NAME", ""),
        "TIGRIS_PREFIX": os.environ.get("TIGRIS_PREFIX") or os.environ.get("WAREHOUSE_PREFIX", "warehouse"),
        "TIGRIS_ENDPOINT": (os.environ.get("TIGRIS_ENDPOINT")
                            or endpoint.replace("https://", "").replace("http://", "")),
        "TIGRIS_KEY_ID": os.environ.get("TIGRIS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID", ""),
        "TIGRIS_SECRET": os.environ.get("TIGRIS_SECRET") or os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
    }


def _substitute(text, toks):
    for k, v in toks.items():
        text = text.replace("${%s}" % k, v)
    return text


def _load_private_key(pem):
    """PEM text → DER bytes for the connector's private_key arg (key-pair auth)."""
    from cryptography.hazmat.primitives import serialization
    pw = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    key = serialization.load_pem_private_key(pem.encode(), password=pw.encode() if pw else None)
    return key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _connect():
    try:
        import snowflake.connector as sf
    except Exception:
        sys.exit("run_load: snowflake-connector-python not installed — "
                 "`pip install snowflake-connector-python`")
    acct, user = os.environ.get("SNOWFLAKE_ACCOUNT"), os.environ.get("SNOWFLAKE_USER")
    if not (acct and user):
        sys.exit("run_load: set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER (see the module docstring)")
    kw = dict(account=acct, user=user, database=os.environ.get("SNOWFLAKE_DATABASE", "UNIFYD"),
              warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "UNIFYD_LOAD"))
    if os.environ.get("SNOWFLAKE_ROLE"):
        kw["role"] = os.environ["SNOWFLAKE_ROLE"]
    pk = os.environ.get("SNOWFLAKE_PRIVATE_KEY")
    if pk:
        kw["private_key"] = _load_private_key(pk)
    elif os.environ.get("SNOWFLAKE_PASSWORD"):
        kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        sys.exit("run_load: set SNOWFLAKE_PASSWORD or SNOWFLAKE_PRIVATE_KEY")
    return sf.connect(**kw)


def _regen(*extra):
    subprocess.check_call([sys.executable, GEN, *extra])



def _stage_mode():
    return os.environ.get("SNOWFLAKE_STAGE_MODE", "internal").strip().lower()


def _wanted_tables():
    """Which RAW tables to upload. SNOWFLAKE_ONLY scopes a run to a comma list — the first proving
    run should be a meaningful SLICE, not 235 tables and 80M rows shovelled through one machine."""
    only = (os.environ.get("SNOWFLAKE_ONLY") or "").strip()
    return [t.strip() for t in only.split(",") if t.strip()] if only else None


def _staged_paths():
    """Every @WH/<path> the generated SQL actually reads, parsed FROM THE SQL.

    Deriving the upload list from the emitted statements (rather than keeping a parallel list of
    tables) makes drift structurally impossible: whatever 03/04 COPY from is exactly what gets staged.
    A second hand-maintained list is how a scoped build ends up emitting DDL for a file nobody
    uploaded — which fails at INFER_SCHEMA, not at something legible.

    Returns [(name, is_dir)] — a trailing slash means a partitioned prefix (a directory of parts).
    """
    pat = re.compile(r"@(?:[A-Z_]+\.[A-Z_]+\.)?WH/([A-Za-z0-9_]+)(/|\.parquet)")
    seen, out = set(), []
    for fname in FILES:
        fp = os.path.join(SQLDIR, fname)
        if not os.path.exists(fp):
            continue
        for name, tail in pat.findall(open(fp).read()):
            key = (name, tail == "/")
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _upload_to_internal_stage(con):
    """Download each Parquet the SQL needs from Tigris and PUT it to the internal stage at the SAME
    @WH/<path>, so 03/04 are byte-identical between stage modes.

    Why push instead of Snowflake pulling: Snowflake does not permit arbitrary S3-compatible
    endpoints. Per their docs only `r2.cloudflarestorage.com` is enabled by default; anything else
    must be allowlisted by Snowflake Support, and an un-allowlisted one fails at CREATE STAGE with
    "Endpoint … not allowed". Pushing costs bandwidth through this machine but needs nobody's
    approval. Flip to SNOWFLAKE_STAGE_MODE=external once the endpoint is approved.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "unifyd"))
    import warehouse

    targets = _staged_paths()
    print("→ staging %d object(s) the SQL reads" % len(targets))
    tmp = tempfile.mkdtemp(prefix="sfload_")
    staged = skipped = 0
    try:
        for name, is_dir in targets:
            try:
                if is_dir:
                    parts = warehouse._partition_files(name)
                    if not parts:
                        print("   skip   %-30s (no partitions)" % name); skipped += 1; continue
                    d = os.path.join(tmp, name)
                    os.makedirs(d, exist_ok=True)
                    n_ok = 0
                    for rel in parts:
                        fn = rel.rsplit("/", 1)[-1]
                        raw = warehouse.get_bytes("%s/%s/%s" % (warehouse._prefix(), name, fn))
                        if raw:
                            open(os.path.join(d, fn), "wb").write(raw); n_ok += 1
                    if not n_ok:
                        print("   skip   %-30s (unreadable parts)" % name); skipped += 1; continue
                    # AUTO_COMPRESS=FALSE: Parquet is already compressed; letting Snowflake gzip it
                    # leaves files the PARQUET file format cannot read.
                    con.cursor().execute("PUT 'file://%s/*.parquet' @%s/%s AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
                                         % (d, STAGE, name))
                    shutil.rmtree(d, ignore_errors=True)
                    print("   staged %-30s %3d parts" % (name, n_ok))
                else:
                    raw = warehouse.get_bytes("%s/%s.parquet" % (warehouse._prefix(), name))
                    if not raw:
                        print("   skip   %-30s (absent)" % name); skipped += 1; continue
                    fp = os.path.join(tmp, "%s.parquet" % name)
                    open(fp, "wb").write(raw)
                    con.cursor().execute("PUT 'file://%s' @%s AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
                                         % (fp, STAGE))
                    os.remove(fp)
                    print("   staged %-30s %8.1f MB" % (name, len(raw) / 1e6))
                staged += 1
            except Exception as e:
                # One unstageable object must not abort the drop — the rest still lands, and the
                # COPY for a missing file is a no-op rather than a failure.
                print("   FAIL   %-30s %s" % (name, str(e)[:100]))
                skipped += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("→ staged %d, skipped %d" % (staged, skipped))
    return staged


def main():
    """Run the drop. Returns the load stats ({rows_total, tables, per_schema, duration_s}) so a
    programmatic caller (unifyd/snowflake_load.py, the registry BUILD) can verify-land them;
    returns None on --dry-run."""
    args = set(sys.argv[1:])
    offline, dry = "--offline" in args, "--dry-run" in args
    t0 = time.time()
    if not offline:
        print("→ regenerating build from the live warehouse (--live, change-aware)…")
        _regen("--live")
    else:
        print("→ using the committed offline build (sql/*.sql)")
    if dry:
        print("→ dry run: build staged in sql/. Not connecting to Snowflake.")
        return None

    toks = _tigris_tokens()
    if not toks["TIGRIS_BUCKET"] or not toks["TIGRIS_KEY_ID"]:
        sys.exit("run_load: warehouse creds not in env (BUCKET_NAME / AWS_ACCESS_KEY_ID) — the stage can't resolve")
    con = _connect()
    per_schema = {}
    total = ntab_total = 0
    try:
        for fname in FILES:
            text = open(os.path.join(SQLDIR, fname)).read()
            if fname == "02_stage.sql":
                text = _substitute(text, toks)          # inject the stage creds in-memory
            for _cur in con.execute_string(text, remove_comments=False):
                pass
            print("→ ran %s" % fname)
            if fname == "02_stage.sql" and _stage_mode() != "external":
                print("→ uploading Parquet to the internal stage (endpoint not allowlisted for s3compat)…")
                _upload_to_internal_stage(con)
        # the headline: total records loaded, straight from INFORMATION_SCHEMA (no scan)
        cur = con.cursor()
        cur.execute("SELECT TABLE_SCHEMA, COUNT(*), COALESCE(SUM(ROW_COUNT),0) "
                    "FROM UNIFYD.INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA IN ('RAW','MASTER') AND TABLE_TYPE='BASE TABLE' GROUP BY 1 ORDER BY 1")
        for schema, ntab, nrows in cur:
            per_schema[schema] = {"tables": int(ntab), "rows": int(nrows or 0)}
            total += int(nrows or 0)
            ntab_total += int(ntab)
            print("   %-8s %3d tables  %15s rows" % (schema, ntab, f"{int(nrows or 0):,}"))
        print("   %-8s %19s rows loaded" % ("TOTAL", f"{total:,}"))
    finally:
        con.close()
    if not offline:
        print("→ committing load state (change ledger)…")
        _regen("--commit-state")
    print("✓ morning drop complete")
    return {"rows_total": total, "tables": ntab_total, "per_schema": per_schema,
            "duration_s": round(time.time() - t0, 1)}


if __name__ == "__main__":
    main()
