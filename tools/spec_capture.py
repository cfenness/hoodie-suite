#!/usr/bin/env python3
"""spec_capture.py — read the LIVE shape of every warehouse table, so the spec can state facts.

WHY THIS IS A SEPARATE STEP FROM GENERATING THE DOCS
  `tools/data_inventory.py` answers "what tables does the code DEFINE and who writes them" from the
  source tree alone — no credentials, always available. It cannot answer "what columns does the
  landed table actually have", because that lives in the Parquet footers.

  Those two answers disagree more than you would like: 171 tables are written by the code and only 6
  have a declared schema in `table_spec.py`, so for 165 of them the schema is whatever the last
  writer happened to emit. A spec built from declarations alone would document 6 tables and imply
  the other 165 do not exist. This reads the footers instead.

  Schema reads are METADATA ONLY (`LIMIT 0`), so they cost a footer fetch rather than a scan. Row
  counts are the expensive half and are optional (`--counts`).

  Runs ON FLY (nothing runs locally). Writes `docs/spec/_live.json`, which the generator consumes;
  the generator degrades to static-only when that file is absent, and SAYS SO on every page rather
  than printing a declared schema as if it were the landed one.

    python3 tools/spec_capture.py                 # schemas for every table
    python3 tools/spec_capture.py --counts        # + row counts (slower)
    python3 tools/spec_capture.py --only ubereats_products
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "unifyd"))

OUT = os.path.join(ROOT, "docs", "spec", "_live.json")


def tables_from_inventory():
    """Every table the code writes, from the static map — the denominator."""
    sys.path.insert(0, HERE)
    import data_inventory
    rep = data_inventory.build() if hasattr(data_inventory, "build") else None
    if rep and "tables" in rep:
        return sorted(rep["tables"])
    # data_inventory exposes its report through main(); fall back to its json mode.
    import subprocess
    raw = subprocess.check_output([sys.executable, os.path.join(HERE, "data_inventory.py"), "--json"],
                                  text=True)
    return sorted(json.loads(raw).get("tables", {}))


def capture(names, counts=False, log=print):
    import warehouse
    con = warehouse.connect()
    try:
        con.execute("SET memory_limit='2GB'")
    except Exception:
        pass
    out, t0 = {}, time.time()
    for i, name in enumerate(names, 1):
        rec = {"table": name}
        try:
            uri = warehouse.uri(name).strip("'")
            rec["uri"] = uri
            cols = con.execute(
                "DESCRIBE SELECT * FROM read_parquet('%s', union_by_name=true) LIMIT 0" % uri
            ).fetchall()
            rec["columns"] = [{"name": c[0], "type": c[1]} for c in cols]
            rec["landed"] = True
            rec["layout"] = "single file"
        except Exception as single:
            # A PARTITIONED table has no single .parquet — it is warehouse/<name>/*.parquet, and the
            # single-file read 404s. Reporting that as "never landed" would have declared four of
            # the biggest tables in the system nonexistent, `ubereats_products_parts` among them.
            try:
                files = warehouse._partition_files_strict(name)
                if not files:
                    raise RuntimeError("no partitions")
                src = ["s3://%s" % f for f in files] if warehouse.remote() else list(files)
                probe = src[-1]              # the NEWEST partition — the current schema, not the first
                cols = con.execute(
                    "DESCRIBE SELECT * FROM read_parquet('%s', union_by_name=true) LIMIT 0" % probe
                ).fetchall()
                rec["columns"] = [{"name": c[0], "type": c[1]} for c in cols]
                rec["landed"] = True
                rec["layout"] = "partitioned"
                rec["partitions"] = len(files)
                rec["uri"] = probe
                # Schema drift across partitions is the known corruption mode here, so measure it
                # rather than presenting one partition's columns as the table's schema.
                shapes = set()
                for p in (src[:3] + src[-3:]):
                    try:
                        c2 = con.execute("DESCRIBE SELECT * FROM read_parquet('%s') LIMIT 0" % p).fetchall()
                        shapes.add(tuple(sorted(x[0] for x in c2)))
                    except Exception:
                        pass
                rec["schemas_sampled"] = len(shapes)
                out[name] = rec
                if counts:
                    try:
                        rec["rows"] = int(warehouse.row_count(name) or 0)
                    except Exception as e:
                        rec["rows_error"] = str(e)[:120]
                if i % 20 == 0:
                    log("  %d/%d (%ds)" % (i, len(names), int(time.time() - t0)))
                continue
            except Exception:
                pass
            e = single
            # A table the code writes but that has never landed is a REAL and useful finding — it
            # means a registered source has never successfully run. Record the reason, don't drop it.
            rec["landed"] = False
            rec["error"] = str(e).split("\n")[0][:200]
            rec["columns"] = []
        if counts and rec.get("landed"):
            try:
                rec["rows"] = int(warehouse.row_count(name) or 0)
            except Exception as e:
                rec["rows_error"] = str(e)[:120]
        out[name] = rec
        if i % 20 == 0:
            log("  %d/%d (%ds)" % (i, len(names), int(time.time() - t0)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capture live warehouse schemas for the spec.")
    ap.add_argument("--counts", action="store_true", help="also read row counts (slower)")
    ap.add_argument("--only", help="comma-separated table names")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)

    names = [s.strip() for s in a.only.split(",")] if a.only else tables_from_inventory()
    print("capturing %d tables%s" % (len(names), " + counts" if a.counts else ""))
    data = capture(names, counts=a.counts)
    landed = sum(1 for v in data.values() if v.get("landed"))
    payload = {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "tables": data, "n_tables": len(data), "n_landed": landed,
               "counts_included": bool(a.counts)}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
    print("%d/%d landed -> %s" % (landed, len(data), a.out))
    return payload


if __name__ == "__main__":
    main()
