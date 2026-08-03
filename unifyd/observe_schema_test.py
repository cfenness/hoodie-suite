"""Offline test for retail_observations schema stability: python3 unifyd/observe_schema_test.py

The bug this locks down: write_partition infers a partition's schema from THAT batch's values when
no dtypes are passed. A column that is all-None in one run lands as `null`, the same column with
values elsewhere lands int64/double/string, and the union_by_name read across partitions then has to
reconcile incompatible types for one column. It does not fail cleanly — it corrupts the read with an
invalid-UTF8 decode error that masks the real message. Measured live: 13 distinct schemas across
3,622 partitions, and the whole 51.7M-row table unreadable to any aggregate query.

The load-bearing assertions:
  • an ALL-NULL batch still writes the DECLARED types, not `null` columns — this is the exact shape
    that poisoned the table
  • two batches with different value shapes produce BYTE-IDENTICAL schemas
  • a promo offer STRING ("2 for $4.50") is preserved in promo_text, never cast to NULL and never
    turned into an invented number — losing it would be worse than the broken read
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import observe

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


def _write(rows, path):
    """Run rows through observe's own normalisation + pinned schema, exactly as record() does."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = []
    for r in rows:
        pn, pt = observe.split_promo(r.get("promo"))
        out.append({"date": "2026-08-03", "observed_at": 1, "source": "t", "chain": "",
                    "store": "", "store_id": "", "product_id": "", "upc": "", "gtin": "",
                    "brand": "", "name": r.get("name", ""),
                    "price": observe._f(r.get("price")), "promo": pn, "promo_text": pt,
                    "on_promo": False, "in_stock": True,
                    "qty": observe._f(r.get("qty")), "stock_level": "", "is_hemp": False})
    dt = observe._dtypes()
    schema = pa.schema([(f, dt[f]) for f in observe.OBS_FIELDS])
    t = pa.Table.from_pylist([{k: r[k] for k in observe.OBS_FIELDS} for r in out], schema=schema)
    pq.write_table(t, path)
    return pq.read_schema(path)


def test_all_null_batch_keeps_declared_types():
    """The poisoning shape: every numeric value None. Inference would give `null` columns."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.parquet")
        s = _write([{"name": "x", "price": None, "promo": None, "qty": None}], p)
        types = {f.name: str(f.type) for f in s}
        eq("qty stays double on an all-null batch", types["qty"], "double")
        eq("promo stays double on an all-null batch", types["promo"], "double")
        eq("price stays double on an all-null batch", types["price"], "double")
        ok("no column degraded to the null type", "null" not in types.values())


def test_schemas_are_identical_across_differently_shaped_batches():
    with tempfile.TemporaryDirectory() as d:
        a = _write([{"name": "x", "price": None, "promo": None, "qty": None}],
                   os.path.join(d, "a.parquet"))
        b = _write([{"name": "y", "price": 3.39, "promo": "2 for $4.50", "qty": 108}],
                   os.path.join(d, "b.parquet"))
        c = _write([{"name": "z", "price": 16.99, "promo": 2.99, "qty": 10.0}],
                   os.path.join(d, "c.parquet"))
        sa = [(f.name, str(f.type)) for f in a]
        sb = [(f.name, str(f.type)) for f in b]
        sc = [(f.name, str(f.type)) for f in c]
        ok("all-null batch == string-promo batch", sa == sb)
        ok("all-null batch == numeric-promo batch", sa == sc)
        # This is the divergence that broke the live table (int64 qty vs double qty vs null qty).
        ok("qty is one type everywhere", len({dict(sa)["qty"], dict(sb)["qty"], dict(sc)["qty"]}) == 1)


def test_promo_offer_text_is_never_lost():
    eq("a numeric promo is a price", observe.split_promo(2.99), (2.99, ""))
    eq("a numeric string is still a price", observe.split_promo("4.50"), (4.50, ""))
    eq("a $-prefixed string is a price", observe.split_promo("$4.50"), (4.50, ""))
    # 7NOW's real values, from the live warehouse.
    eq("an offer string is preserved verbatim", observe.split_promo("2 for $4.50"),
       (None, "2 for $4.50"))
    eq("...and no number is invented for it", observe.split_promo("2 for $4.50")[0], None)
    eq("another real offer string", observe.split_promo("Buy 2, Save $3"), (None, "Buy 2, Save $3"))
    eq("empty is neither", observe.split_promo(""), (None, ""))
    eq("None is neither", observe.split_promo(None), (None, ""))
    ok("promo_text is in the schema", "promo_text" in observe.OBS_FIELDS)

    with tempfile.TemporaryDirectory() as d:
        import pyarrow.parquet as pq
        p = os.path.join(d, "a.parquet")
        _write([{"name": "x", "promo": "2 for $4.50"}], p)
        t = pq.read_table(p)
        eq("the offer string survives the round trip",
           t.column("promo_text").to_pylist()[0], "2 for $4.50")
        eq("...and promo stays numeric-null rather than a guess",
           t.column("promo").to_pylist()[0], None)


def test_numeric_coercion_does_not_poison_the_column():
    eq("a numeric string becomes a float", observe._f("12.99"), 12.99)
    eq("a $ string becomes a float", observe._f("$12.99"), 12.99)
    eq("junk becomes None, not a string", observe._f("n/a"), None)
    eq("None stays None", observe._f(None), None)
    eq("a bool is not a number", observe._f(True), None)


def test_record_passes_dtypes():
    import inspect
    src = inspect.getsource(observe.record)
    ok("record() pins dtypes on the write", "dtypes=_dtypes()" in src)
    ok("record() routes promo through split_promo", "split_promo" in src)


if __name__ == "__main__":
    for fn in (test_all_null_batch_keeps_declared_types,
               test_schemas_are_identical_across_differently_shaped_batches,
               test_promo_offer_text_is_never_lost,
               test_numeric_coercion_does_not_poison_the_column,
               test_record_passes_dtypes):
        print(fn.__name__)
        fn()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
