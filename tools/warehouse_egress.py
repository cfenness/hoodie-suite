#!/usr/bin/env python3
"""warehouse_egress.py — archive/handoff copy of the Tigris warehouse to Google Drive.

READ-ONLY against Tigris. Nothing here writes to, deletes from, or mutates the bucket: the
inventory reads Parquet footers + manifests, and the copy is `rclone copy` (one-way, source
untouched). There is deliberately no code path that calls warehouse.write_*.

This is a COPY JOB, not an export — Parquet stays Parquet. Drive is not a query engine; the
analytical home stays Parquet-in-object-store / Snowflake. The only conversion is an opt-in
CSV rendering of the small human-facing reference tables (--reference-csv).

Usage (from the repo root, on a machine that HAS the Tigris env trio):

    python3 tools/warehouse_egress.py preflight
    python3 tools/warehouse_egress.py inventory
    python3 tools/warehouse_egress.py copy      [--include-raw-payloads] [--reference-csv]
    python3 tools/warehouse_egress.py verify
    python3 tools/warehouse_egress.py all       [--include-raw-payloads] [--reference-csv]

Env (same trio warehouse.py reads — `fly storage create` sets these on the Fly machine):
    AWS_ENDPOINT_URL_S3   https://fly.storage.tigris.dev
    BUCKET_NAME           (or WAREHOUSE_BUCKET)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION   (region usually "auto")
    WAREHOUSE_PREFIX      key prefix inside the bucket (default "warehouse")
    GDRIVE_REMOTE         rclone remote name for Drive (default "gdrive")
    GDRIVE_BASE           Drive folder (default "Hoodie")

The rclone Tigris remote is configured INLINE via env (RCLONE_CONFIG_TIGRIS_*), so this never
writes credentials into rclone.conf and leaves no cred on disk.
"""
import argparse
import json
import os
import subprocess
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "unifyd"))

MANIFEST_DOC = os.path.join(ROOT, "docs", "handoff", "DATA-EGRESS-MANIFEST.md")

# Small, human-facing lookup tables — the ones people actually open in Sheets. Everything else
# stays Parquet.
REFERENCE_TABLES = ("cpi_reference", "cex_reference", "tax_rates")
REFERENCE_PREFIXES = ("census_",)

# Append-only raw JSON. Big, low-value in an archive, and it can carry unfiltered payload bodies.
# Excluded unless explicitly asked for.
RAW_TABLE = "raw_payloads"


# ── env / preflight ────────────────────────────────────────────────────────────────────────

def _env(*names, default=""):
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return default


def _placeholder(v):
    """Some sandboxes inject a literal placeholder into the AWS vars. That is NOT a credential,
    and treating it as one produces a confusing auth failure three steps later."""
    return v.lower() in ("", "proxy-injected", "changeme", "unset", "none", "null")


def preflight(require_rclone=True):
    """Hard-fail with the EXACT missing variable(s). Never falls back to warehouse local-disk
    mode — a silent local fallback would 'succeed' having archived an empty dev directory."""
    endpoint = _env("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT")
    bucket = _env("BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET")
    key = _env("AWS_ACCESS_KEY_ID")
    secret = _env("AWS_SECRET_ACCESS_KEY")

    missing = []
    if not endpoint:
        missing.append("AWS_ENDPOINT_URL_S3 (or TIGRIS_ENDPOINT)")
    if not bucket:
        missing.append("BUCKET_NAME (or WAREHOUSE_BUCKET)")
    if _placeholder(key):
        missing.append("AWS_ACCESS_KEY_ID" + (" (present but a placeholder: %r)" % key if key else ""))
    if _placeholder(secret):
        missing.append("AWS_SECRET_ACCESS_KEY" + (" (present but a placeholder)" if secret else ""))
    if missing:
        print("PREFLIGHT FAILED — Tigris is not configured in this environment.\n", file=sys.stderr)
        for m in missing:
            print("  missing: %s" % m, file=sys.stderr)
        print("\nRefusing to fall back to warehouse local-disk mode: that would archive an empty\n"
              "dev directory and report success. Run this where the Tigris trio is set\n"
              "(the Fly machine, or a shell with `fly storage` creds exported).", file=sys.stderr)
        raise SystemExit(2)

    if require_rclone and not _which("rclone"):
        print("PREFLIGHT FAILED — rclone is not installed.\n"
              "  macOS:  brew install rclone\n"
              "  linux:  curl https://rclone.org/install.sh | sudo bash", file=sys.stderr)
        raise SystemExit(2)

    remote = _env("GDRIVE_REMOTE", default="gdrive")
    if require_rclone:
        listing = _run(["rclone", "listremotes"], capture=True).stdout or ""
        if ("%s:" % remote) not in listing:
            print("PREFLIGHT FAILED — no rclone remote named %r.\n\n"
                  "Configure it (you do the OAuth in the browser):\n"
                  "    rclone config\n"
                  "      n) New remote\n"
                  "      name> %s\n"
                  "      Storage> drive\n"
                  "      client_id / client_secret> (blank is fine)\n"
                  "      scope> 1  (full access)\n"
                  "      Edit advanced config> n\n"
                  "      Use web browser to automatically authenticate> y\n"
                  "    -> sign in with the HOODIE Google account, not a personal one\n\n"
                  "Confirm with:  rclone about %s:" % (remote, remote, remote), file=sys.stderr)
            raise SystemExit(2)

    print("preflight ok — endpoint=%s bucket=%s prefix=%s drive=%s:"
          % (endpoint, bucket, _env("WAREHOUSE_PREFIX", default="warehouse"), remote))
    return {"endpoint": endpoint, "bucket": bucket,
            "prefix": _env("WAREHOUSE_PREFIX", default="warehouse"), "remote": remote}


def _which(exe):
    from shutil import which
    return which(exe)


def _run(cmd, capture=False, check=False, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=capture, text=True, check=check, env=e)


def _rclone_env(cfg):
    """Tigris as an rclone remote, inline. No rclone.conf entry, no cred written to disk."""
    return {
        "RCLONE_CONFIG_TIGRIS_TYPE": "s3",
        "RCLONE_CONFIG_TIGRIS_PROVIDER": "Other",
        "RCLONE_CONFIG_TIGRIS_ENDPOINT": cfg["endpoint"],
        "RCLONE_CONFIG_TIGRIS_ACCESS_KEY_ID": _env("AWS_ACCESS_KEY_ID"),
        "RCLONE_CONFIG_TIGRIS_SECRET_ACCESS_KEY": _env("AWS_SECRET_ACCESS_KEY"),
        "RCLONE_CONFIG_TIGRIS_REGION": _env("AWS_REGION", default="auto"),
    }


# ── inventory ──────────────────────────────────────────────────────────────────────────────

def _s3fs(cfg):
    from pyarrow import fs as pafs
    return pafs.S3FileSystem(endpoint_override=cfg["endpoint"],
                             access_key=_env("AWS_ACCESS_KEY_ID"),
                             secret_key=_env("AWS_SECRET_ACCESS_KEY"),
                             region=_env("AWS_REGION", default="auto"), scheme="https")


def _walk(s3, base):
    """Every object under `base` as {path, size}. Recursive listing, read-only."""
    from pyarrow import fs as pafs
    out = []
    for i in s3.get_file_info(pafs.FileSelector(base, recursive=True, allow_not_found=True)):
        if i.type == pafs.FileType.File:
            out.append({"path": i.path, "size": i.size or 0})
    return out


def inventory(cfg):
    """Physical truth from object storage, joined to logical row counts from warehouse.py.

    Three layouts coexist (warehouse.py docstring + write_partition), and the archive must carry
    all of them plus `_manifest/` — a v2 bucketed table WITHOUT its manifest cannot be resolved
    back to its live part files, so a parquet-only copy is an unreadable archive:
      v1 flat        <prefix>/<name>.parquet
      v2 bucketed    <prefix>/<name>/__b=<hex>/part-v<n>.parquet  + <prefix>/_manifest/<name>.json
      date-partition <prefix>/<name>/*.parquet
    """
    import warehouse

    s3 = _s3fs(cfg)
    base = "%s/%s" % (cfg["bucket"], cfg["prefix"])
    objects = _walk(s3, base)

    tables, manifests, failures = {}, 0, []
    for o in objects:
        rel = o["path"][len(base) + 1:]
        if rel.startswith("_manifest/"):
            manifests += 1
            continue
        if "/" in rel:
            name, layout = rel.split("/", 1)[0], ("bucketed" if "/__b=" in rel else "partitioned")
        elif rel.endswith(".parquet"):
            name, layout = rel[:-8], "flat"
        else:
            continue
        t = tables.setdefault(name, {"name": name, "layout": layout, "objects": 0, "bytes": 0})
        t["objects"] += 1
        t["bytes"] += o["size"]
        if layout == "bucketed":
            t["layout"] = "bucketed"

    for t in tables.values():
        try:
            t["rows"] = warehouse.row_count(t["name"])
        except Exception as e:
            t["rows"] = None
            failures.append("row_count(%s): %s" % (t["name"], e))

    # Anything sitting at the bucket ROOT, outside the warehouse prefix. Under the default
    # prefix this is normally empty (v1 tables live INSIDE <prefix>/), but a table written when
    # WAREHOUSE_PREFIX was blank would land here and be missed by a prefix-scoped copy.
    stray = [o for o in _walk(s3, cfg["bucket"])
             if not o["path"].startswith(base + "/") and o["path"].endswith(".parquet")]

    return {"tables": sorted(tables.values(), key=lambda t: -t["bytes"]),
            "manifests": manifests, "stray": stray, "failures": failures, "cfg": cfg}


def _gb(n):
    return n / (1024.0 ** 3)


def _fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


def write_manifest_doc(inv):
    os.makedirs(os.path.dirname(MANIFEST_DOC), exist_ok=True)
    cfg = inv["cfg"]
    total_b = sum(t["bytes"] for t in inv["tables"])
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L = []
    L.append("# Data egress manifest — Tigris → Google Drive\n")
    L.append("Archive/handoff copy of the Hoodie warehouse. Parquet stays Parquet; the")
    L.append("analytical home remains Parquet-in-object-store / Snowflake.\n")
    L.append("- generated: %s" % stamp)
    L.append("- source: `s3://%s/%s` (endpoint `%s`) — **read-only**" % (cfg["bucket"], cfg["prefix"], cfg["endpoint"]))
    L.append("- tables: %d | objects: %d | manifests: %d | total: %s"
             % (len(inv["tables"]), sum(t["objects"] for t in inv["tables"]),
                inv["manifests"], _fmt_bytes(total_b)))
    L.append("")
    L.append("`_manifest/` is part of the archive, not an extra: a v2 *bucketed* table cannot be")
    L.append("resolved back to its live part files without its manifest JSON, so a parquet-only")
    L.append("copy would restore as unreadable.\n")

    L.append("## Datasets\n")
    L.append("| table | rows | size | objects | layout | physical prefix |")
    L.append("|---|---:|---:|---:|---|---|")
    for t in inv["tables"]:
        rows = "n/a" if t["rows"] is None else "{:,}".format(t["rows"])
        phys = "`%s/%s%s`" % (cfg["prefix"], t["name"], ".parquet" if t["layout"] == "flat" else "/")
        L.append("| `%s` | %s | %s | %d | %s | %s |"
                 % (t["name"], rows, _fmt_bytes(t["bytes"]), t["objects"], t["layout"], phys))
    L.append("")

    big = [t for t in inv["tables"][:8] if t["bytes"] > 0]
    if big:
        L.append("## Large tables (dominate transfer time)\n")
        for t in big:
            rows = "n/a rows" if t["rows"] is None else "{:,} rows".format(t["rows"])
            L.append("- `%s` — %s, %s across %d objects" % (t["name"], rows, _fmt_bytes(t["bytes"]), t["objects"]))
        L.append("")

    raw = next((t for t in inv["tables"] if t["name"] == RAW_TABLE), None)
    L.append("## raw_payloads\n")
    if raw:
        L.append("`raw_payloads` is append-only raw JSON (%s, %d objects) and is **excluded by "
                 "default** — it is scraper exhaust rather than master data, and it can carry "
                 "unfiltered response bodies. Include with `--include-raw-payloads`."
                 % (_fmt_bytes(raw["bytes"]), raw["objects"]))
    else:
        L.append("Not present in this bucket.")
    L.append("")

    if inv["stray"]:
        L.append("## Objects outside the warehouse prefix\n")
        for o in inv["stray"]:
            L.append("- `%s` (%s)" % (o["path"], _fmt_bytes(o["size"])))
        L.append("")

    if inv["failures"]:
        L.append("## Inventory warnings\n")
        for f in inv["failures"]:
            L.append("- %s" % f)
        L.append("")

    with open(MANIFEST_DOC, "w") as f:
        f.write("\n".join(L) + "\n")
    print("wrote %s (%d tables, %s)" % (MANIFEST_DOC, len(inv["tables"]), _fmt_bytes(total_b)))
    return MANIFEST_DOC


# ── copy ───────────────────────────────────────────────────────────────────────────────────

def copy(cfg, inv, include_raw=False, reference_csv=False):
    """rclone copy, S3 -> Drive, structure and Parquet format preserved.

    Per-table failures are collected, not fatal: one unreadable table must not abandon the
    other N. rclone copy is resumable, so a dropped run is just re-run.
    """
    remote, base = cfg["remote"], _env("GDRIVE_BASE", default="Hoodie")
    renv = _rclone_env(cfg)
    failures = []

    args = ["rclone", "copy", "tigris:%s/%s" % (cfg["bucket"], cfg["prefix"]),
            "%s:%s/warehouse" % (remote, base),
            "--transfers", "8", "--checkers", "16", "--fast-list", "--progress"]
    # Deliberately NOT --immutable: the warehouse is live, and a scraper rewriting a part
    # mid-copy would abort the whole run. `copy` is one-way and never touches the source, so a
    # concurrently-rewritten object just lands as whichever version was read.
    if not include_raw:
        args += ["--exclude", "/%s/**" % RAW_TABLE, "--exclude", "/%s.parquet" % RAW_TABLE]

    print("\n$ " + " ".join(args))
    r = _run(args, env=renv)
    if r.returncode != 0:
        failures.append("rclone copy warehouse tree exited %d" % r.returncode)

    for o in inv["stray"]:
        rel = o["path"][len(cfg["bucket"]) + 1:]
        a = ["rclone", "copyto", "tigris:%s" % o["path"],
             "%s:%s/warehouse-root/%s" % (remote, base, rel), "--progress"]
        print("\n$ " + " ".join(a))
        if _run(a, env=renv).returncode != 0:
            failures.append("stray object %s" % rel)

    if reference_csv:
        failures += _reference_csv(cfg, inv)
    return failures


def _reference_csv(cfg, inv):
    """CSV copies of the small reference tables only — the ones humans open in Sheets.
    Everything else stays Parquet on purpose."""
    import tempfile
    try:
        import duckdb  # noqa: F401
        import warehouse
    except Exception as e:
        return ["reference-csv skipped: %s" % e]

    names = [t["name"] for t in inv["tables"]
             if t["name"] in REFERENCE_TABLES or t["name"].startswith(REFERENCE_PREFIXES)]
    if not names:
        return []

    failures = []
    out = tempfile.mkdtemp(prefix="hoodie-refcsv-")
    con = warehouse.connect()
    for n in names:
        try:
            warehouse.attach_view(con, n, "t")
            path = os.path.join(out, n + ".csv")
            con.execute("COPY (SELECT * FROM t) TO '%s' (HEADER, DELIMITER ',')" % path)
            print("  csv: %s" % n)
        except Exception as e:
            failures.append("reference csv %s: %s" % (n, e))

    a = ["rclone", "copy", out, "%s:%s/reference-csv" % (cfg["remote"], _env("GDRIVE_BASE", default="Hoodie")),
         "--progress"]
    print("\n$ " + " ".join(a))
    if _run(a, env=_rclone_env(cfg)).returncode != 0:
        failures.append("rclone copy reference-csv")
    return failures


# ── verify ─────────────────────────────────────────────────────────────────────────────────

def _size(target, renv):
    r = _run(["rclone", "size", target, "--json"], capture=True, env=renv)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def verify(cfg, inv):
    """Object count + total bytes, source vs dest. A mismatch is REPORTED, never smoothed over."""
    remote, base = cfg["remote"], _env("GDRIVE_BASE", default="Hoodie")
    renv = _rclone_env(cfg)
    src = _size("tigris:%s/%s" % (cfg["bucket"], cfg["prefix"]), renv)
    dst = _size("%s:%s/warehouse" % (remote, base), renv)

    print("\n── verify ──")
    if not src or not dst:
        print("  could not size %s" % ("source" if not src else "dest"))
        return {"ok": False, "src": src, "dst": dst}

    ok = (src["count"] == dst["count"]) and (src["bytes"] == dst["bytes"])
    print("  source: %d objects, %s" % (src["count"], _fmt_bytes(src["bytes"])))
    print("  dest:   %d objects, %s" % (dst["count"], _fmt_bytes(dst["bytes"])))
    print("  %s" % ("MATCH" if ok else "MISMATCH — investigate before trusting this archive"))
    if not ok:
        print("  (a deliberate --exclude, e.g. raw_payloads, explains a gap; anything else does not)")

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    line = ("\nverified: %s, %d tables, %.2f GB, source %d objects / %s vs dest %d objects / %s — %s\n"
            % (stamp, len(inv["tables"]), _gb(src["bytes"]), src["count"], _fmt_bytes(src["bytes"]),
               dst["count"], _fmt_bytes(dst["bytes"]), "MATCH" if ok else "MISMATCH"))
    if os.path.exists(MANIFEST_DOC):
        with open(MANIFEST_DOC, "a") as f:
            f.write(line)
    print("  appended to %s" % MANIFEST_DOC)
    return {"ok": ok, "src": src, "dst": dst}


# ── cli ────────────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["preflight", "inventory", "copy", "verify", "all"])
    p.add_argument("--include-raw-payloads", action="store_true",
                   help="include the append-only raw JSON table (excluded by default)")
    p.add_argument("--reference-csv", action="store_true",
                   help="also emit CSV copies of the small reference tables to gdrive:<base>/reference-csv")
    a = p.parse_args()

    cfg = preflight(require_rclone=a.cmd in ("copy", "verify", "all"))
    if a.cmd == "preflight":
        return 0

    inv = inventory(cfg)
    if a.cmd in ("inventory", "all"):
        write_manifest_doc(inv)
    if a.cmd == "inventory":
        return 0

    failures = []
    if a.cmd in ("copy", "all"):
        failures += copy(cfg, inv, a.include_raw_payloads, a.reference_csv)

    res = {"ok": True}
    if a.cmd in ("verify", "all"):
        res = verify(cfg, inv)

    if failures:
        print("\n── failures (%d) ──" % len(failures))
        for f in failures:
            print("  - %s" % f)
    drive = "%s:%s" % (cfg["remote"], _env("GDRIVE_BASE", default="Hoodie"))
    print("\ndrive folder: %s   (open with: rclone link %s/warehouse)" % (drive, drive))
    return 0 if (res.get("ok") and not failures) else 1


if __name__ == "__main__":
    raise SystemExit(main())
