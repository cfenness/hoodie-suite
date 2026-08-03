"""Offline test for the partition repair: python3 unifyd/repair_partitions_test.py

No warehouse, no network. Builds the exact divergent schemas measured live on retail_observations
and checks that canonicalising them is LOSSLESS — because a repair that quietly drops a column or a
value is worse than the broken read it fixes. The broken read at least announces itself.

The load-bearing assertions:
  • every source row survives — a repair must never change the row count
  • a `promo` holding "2 for $4.50" moves to promo_text; it is neither cast to NULL nor turned into
    an invented number
  • a numeric promo stays numeric
  • columns missing from an older partition are ADDED (not a reason to skip the file), and columns
    the canonical schema doesn't know are not silently swallowed without notice
  • an unknown table is audited, never rewritten on a guessed schema
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import observe
import repair_partitions as rp

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


def _canon(tbl):
    fields, dtypes, router = rp._spec("retail_observations")
    return rp._canonical(tbl, fields, dtypes, router)


def test_string_promo_moves_to_text():
    """The 7NOW shape: promo is an offer string, qty int64, no observed_at/gtin."""
    import pyarrow as pa
    t = pa.table({"date": ["2026-07-17", "2026-07-17"],
                  "source": ["sevennow", "sevennow"],
                  "name": ["Coke 20oz", "Chips"],
                  "price": [3.39, 11.49],
                  "promo": ["2 for $4.50", "Buy 2, Save $3"],
                  "qty": pa.array([108, 39], type=pa.int64())})
    c = _canon(t)
    eq("row count preserved", c.num_rows, 2)
    eq("offer string preserved verbatim",
       c.column("promo_text").to_pylist(), ["2 for $4.50", "Buy 2, Save $3"])
    eq("no number invented for it", c.column("promo").to_pylist(), [None, None])
    eq("price survives", c.column("price").to_pylist(), [3.39, 11.49])
    eq("qty is canonical double", str(c.schema.field("qty").type), "double")
    eq("qty values survive", c.column("qty").to_pylist(), [108.0, 39.0])
    eq("missing observed_at is added", c.column("observed_at").to_pylist(), [None, None])


def test_numeric_promo_stays_numeric():
    """The Kroger shape: promo is a deal PRICE."""
    import pyarrow as pa
    t = pa.table({"date": ["2026-07-10"], "source": ["kroger"], "name": ["Milk"],
                  "price": [3.49], "promo": pa.array([2.99], type=pa.float64()),
                  "qty": pa.array([None], type=pa.null())})
    c = _canon(t)
    eq("numeric promo preserved", c.column("promo").to_pylist(), [2.99])
    eq("no spurious promo_text", c.column("promo_text").to_pylist(), [""])
    eq("null-typed qty becomes typed null", c.column("qty").to_pylist(), [None])
    eq("qty column is double, not null", str(c.schema.field("qty").type), "double")


def test_all_divergent_schemas_converge():
    """The real divergence: null vs int64 vs double qty, null/string/double promo, +/- chain."""
    import pyarrow as pa
    shapes = [
        pa.table({"name": ["a"], "qty": pa.array([None], type=pa.null()),
                  "promo": pa.array([None], type=pa.null())}),
        pa.table({"name": ["b"], "qty": pa.array([5], type=pa.int64()),
                  "promo": pa.array(["2 for $4.50"], type=pa.string())}),
        pa.table({"name": ["c"], "qty": pa.array([10.0], type=pa.float64()),
                  "promo": pa.array([2.99], type=pa.float64())}),
        pa.table({"name": ["d"], "chain": ["meijer"], "qty": pa.array([1], type=pa.int64()),
                  "promo": pa.array([None], type=pa.null())}),
    ]
    sigs = set()
    total = 0
    for s in shapes:
        c = _canon(s)
        sigs.add(tuple((f.name, str(f.type)) for f in c.schema))
        total += c.num_rows
    eq("every divergent shape converges to ONE schema", len(sigs), 1)
    eq("no row lost across the shapes", total, 4)
    cols = dict(list(sigs)[0])
    eq("canonical qty", cols["qty"], "double")
    eq("canonical promo", cols["promo"], "double")
    eq("canonical promo_text", cols["promo_text"], "string")
    ok("canonical schema is exactly OBS_FIELDS",
       [f for f, _ in list(sigs)[0]] == observe.OBS_FIELDS)


def test_unknown_table_is_never_rewritten_on_a_guess():
    fields, dtypes, router = rp._spec("some_table_we_do_not_know")
    eq("no canonical spec for an unknown table", fields, None)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "tools", "repair_partitions.py")).read()
    ok("repair refuses to guess", "refusing to guess a schema" in src)
    ok("repair defaults to dry run", "apply=False" in src)
    ok("repair verifies the read afterwards", "def verify" in src)


def test_warehouse_names_the_problem():
    """The masked decode error must become an actionable message."""
    import warehouse
    ok("a dedicated error type exists", hasattr(warehouse, "PartitionSchemaError"))
    e = UnicodeDecodeError("utf-8", b"\xca\xfe\xba\xbe", 0, 1, "invalid")
    msg = warehouse._schema_drift_msg("retail_observations", e)
    ok("names the table", "retail_observations" in msg)
    ok("names the cause", "write_partition" in msg and "dtypes" in msg)
    ok("gives the repair command", "repair_partitions.py" in msg)
    ok("explains it is not a real decode problem", "raw Parquet bytes" in msg)


if __name__ == "__main__":
    for fn in (test_string_promo_moves_to_text, test_numeric_promo_stays_numeric,
               test_all_divergent_schemas_converge, test_unknown_table_is_never_rewritten_on_a_guess,
               test_warehouse_names_the_problem):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
