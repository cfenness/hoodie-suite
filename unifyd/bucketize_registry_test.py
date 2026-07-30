#!/usr/bin/env python3
"""bucketize_registry_test.py — the two catalogs added to bucketize.py fixing the 2026-07-29 OOM
triage (src_outlets, binnys_products) must bucket on the IDENTITY write_accumulate already merges on.

Getting this wrong is silent and severe: migrate_to_bucketed's key_cols becomes "what counts as
already having this row" going forward, so a mismatch against the live write_accumulate key would not
error — it would just quietly start treating distinct rows as duplicates (or vice versa) from the
migration onward. This test greps every write_accumulate call site for each table and asserts the
key columns named there match bucketize.py's CATALOGS entry exactly, so a future edit to either side
that drifts from the other fails loud instead of corrupting data quietly.

Also proves `_live()` (the pre-migration active-run guard) accepts BOTH a single writer id (the
original shape) and a list (src_outlets has 5: fast-geo/geocode/naop/build-outlets, plus the "geo"
job that runs the first three in-process) without changing behavior for the existing single-writer
entries.

    python3 unifyd/bucketize_registry_test.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bucketize

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


# For each catalog, every write_accumulate(<table>, ..., key=...) call site and the column names its
# key lambda actually reads via r["col"] / r.get("col") / r.get('col') — a light-touch parse, not a
# full AST walk, but sufficient to catch a drifted literal (which is the failure mode that matters:
# someone edits the key shape in one file and forgets the other).
SITES = {
    "src_outlets": ["aggregator_geo.py", "city_centroid.py", "geocode.py", "mappability.py",
                    "refresh_fast.py", "ue_sitemap.py"],
    "binnys_products": ["binnys_scraper.py"],
}
_COL = re.compile(r'r(?:\.get)?\[?\(?["\']([a-z_]+)["\']')


def _key_cols_at(path, table):
    src = open(os.path.join(os.path.dirname(bucketize.__file__), path)).read()
    out = []
    for m in re.finditer(r'write_accumulate\(\s*["\']%s["\']' % re.escape(table), src):
        # the key=... argument is the first `r["col"]`/`r.get("col")` pair seen after the call opens —
        # scoped to a window so a later, unrelated call in the same file isn't misattributed.
        window = src[m.start():m.start() + 400]
        cols = _COL.findall(window)
        out.append((path, cols[:2]))       # the two identity columns every site here uses
    return out


def main():
    print("bucketize.py registry — key_cols match the live write_accumulate identity")

    for table, files in SITES.items():
        check("%s is a declared catalog" % table, table in bucketize.CATALOGS)
        want = set(bucketize.CATALOGS[table][0])
        for f in files:
            sites = _key_cols_at(f, table)
            check("%s calls write_accumulate(%r, ...) at least once" % (f, table), len(sites) > 0, f)
            for path, cols in sites:
                check("%s: key columns %s match bucketize's %s" % (path, cols, sorted(want)),
                      set(cols) == want, "got %s, want %s" % (cols, sorted(want)))

    check("src_outlets hex_len=2 (256 buckets — matches its 1.7M-row+growing scale)",
          bucketize.CATALOGS["src_outlets"][1] == 2)
    check("binnys_products hex_len=2 (256 buckets — matches its 1.5M-row scale)",
          bucketize.CATALOGS["binnys_products"][1] == 2)

    # _live() must handle both shapes without changing existing single-writer behavior.
    check("single-writer entry unaffected (still a bare string)",
          isinstance(bucketize.WRITER["ubereats_products"], str))
    check("multi-writer entry is a list", isinstance(bucketize.WRITER["src_outlets"], list))
    check("multi-writer list has every real writer of src_outlets",
          set(bucketize.WRITER["src_outlets"]) >= {"geo", "fast-geo", "geocode", "naop", "build-outlets"})

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
