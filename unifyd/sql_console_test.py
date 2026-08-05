"""sql_console_test.py — the guard is the only thing standing between an open SQL box and the warehouse.

Everything here is pure (no duckdb, no network), because the guard has to be checkable without the very
infrastructure it protects. Two directions matter equally:
  ALLOW — the reads a person actually types. A guard that refuses `LIKE '%drop%'` is a guard that gets
          turned off, and a guard that is off protects nothing.
  REFUSE — every route from "a query" to "a write". COPY … TO 's3://' is the one that would clobber a
          catalog, and it lives INSIDE a statement that opens with a legal keyword.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sql_console as sc


ALLOW = [
    "SELECT * FROM binnys_products LIMIT 10",
    "select 1",
    "  \n SELECT count(*) FROM retail_observations ;  ",
    "WITH a AS (SELECT 1 x) SELECT * FROM a",
    "DESCRIBE SELECT * FROM src_outlets",
    "SUMMARIZE SELECT * FROM dim_sku",
    "EXPLAIN SELECT * FROM dim_item",
    "SHOW TABLES",
    "FROM binnys_products SELECT brand",                       # DuckDB's FROM-first form
    "TABLE dim_brand",
    "(SELECT 1)",
    "SELECT * FROM t WHERE name ILIKE '%drop table%'",         # banned WORD inside a literal
    "SELECT * FROM t WHERE note = 'we should DELETE this'",
    "SELECT * FROM t WHERE s = 'it''s a copy'",                # doubled-quote escape
    "SELECT * FROM t -- copy this later\n LIMIT 5",            # banned word in a line comment
    "SELECT * FROM t /* insert notes */ LIMIT 5",              # ... and in a block comment
    "SELECT list_value(1,2) AS setup",                         # 'set' is banned; 'setup' must not be
    "SELECT created_at FROM t",                                # 'create' is banned; 'created_at' must not be
    "SELECT * FROM t ORDER BY updated DESC",                   # 'update' banned; 'updated' must not be
    "SELECT max(price) FROM t GROUP BY brand HAVING count(*) > 2",
    "SELECT a.brand FROM dim_sku a JOIN dim_item b USING (item_key)",
    "PIVOT t ON source USING count(*)",
    "VALUES (1),(2)",
]

REFUSE = [
    ("", "empty"),
    ("   ", "empty"),
    ("COPY (SELECT 1) TO 's3://hoodie/x.parquet'", "copy"),
    ("SELECT 1; DROP TABLE dim_sku", "two statements"),
    ("SELECT 1;SELECT 2", "two statements"),
    ("DROP TABLE dim_sku", "drop"),
    ("DELETE FROM dim_sku", "delete"),
    ("INSERT INTO dim_sku VALUES (1)", "insert"),
    ("UPDATE dim_sku SET x=1", "update"),
    ("CREATE TABLE x AS SELECT 1", "create"),
    ("ATTACH 'x.db'", "attach"),
    ("INSTALL httpfs", "install"),
    ("PRAGMA disable_verification", "pragma"),
    ("SET s3_access_key_id='x'", "set"),
    ("SELECT * FROM read_text('/etc/passwd')", "read_text"),
    ("WITH a AS (SELECT 1) COPY a TO 'x.csv'", "copy inside a legal opener"),
    ("SELECT 1 UNION ALL SELECT 2; COPY (SELECT 1) TO 'x'", "trailing write"),
    ("EXPLAIN COPY (SELECT 1) TO 'x'", "write behind EXPLAIN"),
    ("SELECT * FROM t WHERE 1=1 /* ok */ ; DELETE FROM t", "write after a comment"),
    ("dim_sku", "bare identifier is not a statement"),
    ("EXPORT DATABASE 'x'", "export"),
    ("CHECKPOINT", "checkpoint"),
]


class Guard(unittest.TestCase):
    def test_allows_real_reads(self):
        for sql in ALLOW:
            with self.subTest(sql=sql):
                self.assertTrue(sc.guard(sql))

    def test_refuses_writes(self):
        for sql, why in REFUSE:
            with self.subTest(why=why):
                with self.assertRaises(sc.SqlRefused, msg="ALLOWED a write: %r (%s)" % (sql, why)):
                    sc.guard(sql)

    def test_strips_trailing_semicolon_only(self):
        self.assertEqual(sc.guard("SELECT 1 ;  "), "SELECT 1")
        self.assertEqual(sc.guard("SELECT 1"), "SELECT 1")

    def test_literal_blanking_preserves_length(self):
        # offsets must not shift, or the opener match reads the wrong token
        for s in ["SELECT 'abc' FROM t", "SELECT \"a b\" FROM t", "SELECT 1 -- x\nFROM t",
                  "SELECT /* x */ 1", "SELECT 'it''s' FROM t"]:
            self.assertEqual(len(sc._strip(s)), len(s), s)

    def test_error_message_names_the_verb(self):
        # a refusal the user can act on: it must say WHICH word was the problem
        try:
            sc.guard("COPY (SELECT 1) TO 'x'")
            self.fail("not refused")
        except sc.SqlRefused as e:
            self.assertIn("COPY", str(e))


class Resolve(unittest.TestCase):
    def test_binds_only_known_tables(self):
        # resolve() is name-driven; with an explicit known-set it needs no warehouse at all.
        known = {"binnys_products", "src_outlets"}
        import re
        words = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*",
                           sc._strip("SELECT b.brand FROM binnys_products b JOIN src_outlets s ON 1=1 "
                                     "WHERE b.name = 'src_outlets'"))
        self.assertEqual([w for w in dict.fromkeys(words) if w in known],
                         ["binnys_products", "src_outlets"])   # the LITERAL 'src_outlets' is not re-bound

    def test_column_named_like_a_table_is_harmless(self):
        # a false positive costs one unused view, never a wrong answer
        known = {"price"}
        import re
        words = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", sc._strip("SELECT price FROM t"))
        self.assertIn("price", [w for w in words if w in known])


class TimeoutCoversBinding(unittest.TestCase):
    """A structural check, because the failure it guards has no local reproduction.

    Binding a PARTITIONED table is not free: `read_parquet(glob, union_by_name=true)` opens the footer
    of every part to unify schemas, and a table with thousands of date×source parts on object storage
    can be slow. With the timer started AFTER resolve() — how this was first written — that bind had NO
    bound at all: an unbounded wait, which is worse than a refusal because nothing tells you why. The
    ordering is the defect and it is checkable from the source; the bind cost of any particular table is
    a separate question this test makes no claim about.
    """

    def setUp(self):
        import re as _re
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql_console.py"),
                   errors="ignore").read()
        src = _re.sub(r'"""(?:.|\n)*?"""', "", src)                       # docstrings are not code
        self.body = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))

    def test_timer_starts_before_resolve(self):
        run = self.body[self.body.index("def run("):]
        start, res = run.index("timer.start()"), run.index("resolve(stmt")
        self.assertLess(start, res, "timer.start() must precede resolve() — an unbounded bind hangs")

    def test_bind_interrupt_is_noticed(self):
        # interrupt() unblocks the bind but does not necessarily raise, so run() has to check between
        # the bind and the execute — otherwise a killed bind falls through into a query on half-bound views
        run = self.body[self.body.index("def run("):]
        self.assertIn('if timed["v"]:', run.split("cur = con.execute")[0])

    def test_bound_is_defined_even_if_resolve_dies(self):
        # the error return reports `bound`; if resolve() raises before assigning it, the handler
        # NameErrors and the user gets a stack trace instead of "timed out"
        run = self.body[self.body.index("def run("):]
        self.assertLess(run.index("bound, failed, scopes = [], {}, []"),
                        run.index("bound, failed, scopes = resolve(stmt"))


class PartitionScope(unittest.TestCase):
    """The bind is bounded by SIZE, not by a timer — and a bounded bind must announce itself.

    Measured on the live warehouse: retail_observations is 4,301 parts. Listing them takes 4s; unifying
    their footers under union_by_name did not finish in over six minutes, and con.interrupt() did NOT
    stop it (the C-level read holds the GIL, so the timer thread never runs). A timeout therefore cannot
    be the protection here. Capping the bind is — provided the cap is stated, because an unannounced
    30-day window makes a scoped answer look like an all-time one.
    """

    def test_part_date_reads_the_filename(self):
        self.assertEqual(sc._part_date("s3://b/w/retail_observations/2026-08-01_binnys_a1.parquet"),
                         "2026-08-01")
        self.assertEqual(sc._part_date("/w/retail_observations/legacy_chunk_7.parquet"), "")

    def test_undated_parts_are_kept_not_dropped(self):
        # a part we cannot place in the window stays IN — losing rows silently on top of a scope would
        # be two undisclosed reductions stacked
        files = ["/w/t/%s_src.parquet" % d for d in
                 ["2026-01-01", "2026-06-01", "2026-08-01"]] + ["/w/t/legacy.parquet"]
        keep = {"2026-08-01"}
        sel = [f for f in files if (sc._part_date(f) in keep or not sc._part_date(f))]
        self.assertIn("/w/t/legacy.parquet", sel)
        self.assertEqual(len(sel), 2)

    def test_threshold_is_sane(self):
        self.assertGreater(sc.FULL_BIND_MAX, 0)

    def test_scope_is_bounded_in_PARTS_not_days(self):
        # The miss that shipped first: scoping to 30 DAYS left ~1,800 parts, because
        # retail_observations writes one part per date x source across ~60 sources. Under the 4,301
        # total, far over the 400 that defines "too many to open" — so the bind still hung. The window
        # has to be measured in the same unit as the threshold.
        files = ["/w/t/%s_src%02d.parquet" % (d, i)
                 for d in ["2026-%02d-%02d" % (m, day) for m in (6, 7, 8) for day in range(1, 29)]
                 for i in range(60)] + ["/w/t/legacy.parquet"]
        by_date, undated = {}, []
        for f in files:
            d = sc._part_date(f)
            (by_date.setdefault(d, []).append(f) if d else undated.append(f))
        sel, keep = list(undated), set()
        for d in sorted(by_date, reverse=True):
            if len(sel) + len(by_date[d]) > sc.FULL_BIND_MAX and keep:
                break
            sel += by_date[d]
            keep.add(d)
        self.assertLessEqual(len(sel), sc.FULL_BIND_MAX, "bind must be bounded by the PART cap")
        self.assertGreater(len(keep), 0, "at least one day must always bind")
        self.assertIn("/w/t/legacy.parquet", sel, "undated parts stay in")

    def test_run_always_reports_scopes(self):
        # the key is present on BOTH paths — a UI that reads result.scopes must never see undefined
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql_console.py"),
                   errors="ignore").read()
        run = src[src.index("def run("):]
        self.assertEqual(run.count('"scopes": scopes'), 2, "scopes must ride the ok AND the error return")


class BucketedTables(unittest.TestCase):
    """The v2 (bucketed) layout is a manifest, not a directory of parquet files.

    Rows live at <name>/__b=<hex>/part-v<n>.parquet, so the partitioned glob <name>/*.parquet matches
    NOTHING. Binding that glob made the three largest catalogs in the warehouse unreachable by name —
    binnys_products 1,534,862 + src_outlets 1,916,357 + ubereats_products 2,160,806 = 5.6M rows — while
    the monitor still LISTED them, so they looked present and answered "table does not exist". Verified
    live on all three before the fix.
    """

    def setUp(self):
        import re as _re
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql_console.py"),
                   errors="ignore").read()
        src = _re.sub(r'"""(?:.|\n)*?"""', "", src)
        self.body = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))

    def test_bucketed_goes_through_the_manifest_binder(self):
        # warehouse.attach_view already resolves BOTH layouts off the manifest. Re-deriving the path
        # here is exactly how the two drift apart again.
        self.assertIn("warehouse.read_manifest(name)", self.body)
        self.assertIn("warehouse.attach_view(con, name", self.body)

    def test_columns_resolves_the_same_way_as_a_query(self):
        # a sidebar that shows a table's columns while a query against it says "does not exist" is
        # worse than showing nothing
        cols = self.body[self.body.index("def columns("):]
        self.assertIn("_scoped_expr(name)", cols)
        self.assertIn("attach_view", cols)

    def test_empty_bucketed_is_reported_not_silently_dropped(self):
        self.assertIn("genuinely empty", self.body)


class Cells(unittest.TestCase):
    def test_json_safe(self):
        self.assertEqual(sc._cell(None), None)
        self.assertEqual(sc._cell(3), 3)
        self.assertEqual(sc._cell("x"), "x")
        self.assertEqual(sc._cell(b"abc"), "<3 bytes>")
        self.assertIsNone(sc._cell(float("nan")))          # NaN is not JSON — must not reach the browser
        self.assertIsNone(sc._cell(float("inf")))
        big = "x" * 9000
        self.assertLess(len(sc._cell(big)), 4100)
        import datetime
        self.assertEqual(sc._cell(datetime.date(2026, 1, 2)), "2026-01-02")

    def test_row_cap_is_bounded(self):
        self.assertGreater(sc.ROW_CAP, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
