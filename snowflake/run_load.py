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
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SQLDIR = os.path.join(HERE, "sql")
FILES = ["00_config.template.sql", "01_database.sql", "02_stage.sql", "03_raw_tables.sql",
         "04_master.sql", "05_marts.sql", "06_validate.sql"]
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


def main():
    args = set(sys.argv[1:])
    offline, dry = "--offline" in args, "--dry-run" in args
    if not offline:
        print("→ regenerating build from the live warehouse (--live, change-aware)…")
        _regen("--live")
    else:
        print("→ using the committed offline build (sql/*.sql)")
    if dry:
        print("→ dry run: build staged in sql/. Not connecting to Snowflake.")
        return

    toks = _tigris_tokens()
    if not toks["TIGRIS_BUCKET"] or not toks["TIGRIS_KEY_ID"]:
        sys.exit("run_load: warehouse creds not in env (BUCKET_NAME / AWS_ACCESS_KEY_ID) — the stage can't resolve")
    con = _connect()
    try:
        for fname in FILES:
            text = open(os.path.join(SQLDIR, fname)).read()
            if fname == "02_stage.sql":
                text = _substitute(text, toks)          # inject the stage creds in-memory
            for _cur in con.execute_string(text, remove_comments=False):
                pass
            print("→ ran %s" % fname)
        # the headline: total records loaded, straight from INFORMATION_SCHEMA (no scan)
        cur = con.cursor()
        cur.execute("SELECT TABLE_SCHEMA, COUNT(*), COALESCE(SUM(ROW_COUNT),0) "
                    "FROM UNIFYD.INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA IN ('RAW','MASTER') AND TABLE_TYPE='BASE TABLE' GROUP BY 1 ORDER BY 1")
        total = 0
        for schema, ntab, nrows in cur:
            total += int(nrows or 0)
            print("   %-8s %3d tables  %15s rows" % (schema, ntab, f"{int(nrows or 0):,}"))
        print("   %-8s %19s rows loaded" % ("TOTAL", f"{total:,}"))
    finally:
        con.close()
    if not offline:
        print("→ committing load state (change ledger)…")
        _regen("--commit-state")
    print("✓ morning drop complete")


if __name__ == "__main__":
    main()
