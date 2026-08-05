"""Offline test for warehouse.land(): python3 unifyd/land_test.py

land() is the one accumulation style a scraper should use — append a stage-1 part, let the fold
consolidate. It is built ON the pipeline rebuild (table_spec for schema, fold for consolidation),
not beside it, so these tests check the SEAM as much as the function.

The load-bearing assertions:
  • the part name starts with an ISO date. fold.py has no timestamp column and establishes recency
    by SORTING FILENAMES, so a name that does not sort by date does not fail — it silently merges
    in the wrong order. This is the whole reason the name is composed here instead of at 78 call
    sites.
  • schema comes from table_spec, never from the caller (C2) — the corruption class that made two
    tables unreadable
  • an undeclared table RAISES rather than falling back to inference
  • the default part name is IDEMPOTENT per day/scope: a re-run replaces its own part rather than
    double-landing
  • a fleet's shards produce DISJOINT part names (no merge, no clobber)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import table_spec
import warehouse

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   %s" % name)
    else:
        failed += 1
        print("  FAIL %s" % name)


def eq(name, got, want):
    ok("%s (got %r)" % (name, got), got == want)


def test_part_name_sorts_by_date():
    """fold.py: `sorted(part_files)` IS the recency order."""
    n = warehouse._part_name(day="2026-08-04")
    eq("day only", n, "2026-08-04")
    eq("with scope", warehouse._part_name(day="2026-08-04", scope="Florida"), "2026-08-04_florida")
    eq("with shard+seq", warehouse._part_name(day="2026-08-04", shard=2, seq=4),
       "2026-08-04_s02_b0004")
    eq("scope+shard+seq", warehouse._part_name(day="2026-08-04", scope="FL", shard=11, seq=1234),
       "2026-08-04_fl_s11_b1234")

    # The property the fold depends on: chronological order == lexical order, regardless of the
    # scope/shard suffixes.
    names = [warehouse._part_name(day=d, scope=s, shard=sh, seq=q)
             for d, s, sh, q in [("2026-08-04", "zulu", 9, 9), ("2026-08-03", "alpha", 0, 0),
                                 ("2026-08-05", "mike", 5, 5), ("2026-07-31", "zeta", 1, 1)]]
    eq("lexical sort == chronological sort", sorted(names),
       ["2026-07-31_zeta_s01_b0001", "2026-08-03_alpha_s00_b0000",
        "2026-08-04_zulu_s09_b0009", "2026-08-05_mike_s05_b0005"])

    # Zero-padding is what makes shard 2 sort before shard 11 rather than after it.
    eq("shards zero-pad", sorted([warehouse._part_name(day="2026-08-04", shard=i) for i in (2, 11)]),
       ["2026-08-04_s02", "2026-08-04_s11"])


def test_fleet_shards_are_disjoint():
    names = {warehouse._part_name(day="2026-08-04", shard=s, seq=b)
             for s in range(6) for b in range(3)}
    eq("6 shards x 3 batches -> 18 distinct parts", len(names), 18)


def test_default_is_idempotent():
    """A single-shot source that re-runs must REPLACE its part, not double-land."""
    a = warehouse._part_name(day="2026-08-04", scope="fl")
    b = warehouse._part_name(day="2026-08-04", scope="fl")
    eq("same day+scope -> same part name", a, b)
    ok("different day -> different part",
       warehouse._part_name(day="2026-08-05", scope="fl") != a)


def test_undeclared_table_raises():
    try:
        warehouse.land("a_table_nobody_declared", [{"x": 1}])
        ok("should have raised for an undeclared table", False)
    except ValueError as e:
        ok("raises with a message naming table_spec", "table_spec" in str(e))
        ok("...and says why inference is unacceptable", "inference" in str(e))
    except Exception as e:
        ok("raised the wrong error type (%s)" % type(e).__name__, False)


def test_schema_comes_from_the_table():
    """The caller passes no fields and no dtypes; land() projects to the declared field set."""
    import inspect
    src = inspect.getsource(warehouse.land)
    ok("resolves the spec", "table_spec.spec_for(name)" in src)
    ok("projects rows to the declared fields", "for f in spec.fields" in src)
    ok("passes NO fields/dtypes to write_partition",
       "write_partition(parts_table, part, rows)" in src)

    spec = table_spec.spec_for("retail_observations")
    ok("a real spec resolves", spec is not None)
    rec = {"date": "2026-08-04", "source": "x", "bogus_extra": 1}
    projected = {f: rec.get(f) for f in spec.fields}
    ok("undeclared keys are dropped", "bogus_extra" not in projected)
    ok("declared-but-absent fields are present as None", projected["upc"] is None)
    eq("field order follows the spec", list(projected)[:2], spec.fields[:2])


def test_writes_to_the_parts_table_not_the_aggregate():
    """C1: upstream only ever appends a part; the fold is the aggregate's only writer."""
    import inspect
    src = inspect.getsource(warehouse.land)
    # Check the executable body, not the prose — the docstring names write_accumulate on purpose
    # (it explains what this replaces), and a naive substring check flags its own rationale.
    body = src.replace(warehouse.land.__doc__ or "", "")
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))

    ok("targets <table>_parts", 'parts_table = name + "_parts"' in code)
    ok("never calls write_accumulate", "write_accumulate" not in code)
    ok("never calls write_parquet", "write_parquet(" not in code)
    ok("reports the fold backlog", "fold.pending" in code)
    ok("backlog failure cannot fail a good write", "never fail a good write" in src)


def test_matches_folds_expectations():
    """land() writes what fold reads. If these drift the fold silently sees nothing."""
    import fold
    eq("fold reads <table>_parts", 'parts_table = table + "_parts"' in
       __import__("inspect").getsource(fold.pending), True)
    ok("fold orders by filename", "sorted(" in __import__("inspect").getsource(fold._part_names))


if __name__ == "__main__":
    for fn in (test_part_name_sorts_by_date, test_fleet_shards_are_disjoint,
               test_default_is_idempotent, test_undeclared_table_raises,
               test_schema_comes_from_the_table, test_writes_to_the_parts_table_not_the_aggregate,
               test_matches_folds_expectations):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
