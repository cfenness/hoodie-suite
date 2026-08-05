"""sql_graph_test.py — a join builder is only worth having if its SQL is right and its claims are honest.

Two failure modes matter more than the rest, and both produce something that LOOKS like an answer:

  a cross-product wearing a join's clothes — an unjoined table multiplies the row count by its whole
  length, and the grid fills with plausible rows

  a guess presented as knowledge — "these tables join on `name`" is a sentence that reads identically
  whether it came from a declared key or from two columns coincidentally sharing a word

So build_sql refuses to emit an unjoined table, and every proposed link carries the BASIS it came from.

Pure: link ranking and SQL generation are exercised with injected columns/keys — no warehouse, no
duckdb, no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sql_graph


class Links(unittest.TestCase):
    def setUp(self):
        self._cols, self._keys = sql_graph._cols, sql_graph.declared_keys

    def tearDown(self):
        sql_graph._cols, sql_graph.declared_keys = self._cols, self._keys

    def inject(self, cols, keys=None):
        sql_graph._cols = lambda t: cols.get(t, [])
        sql_graph.declared_keys = lambda t: (keys or {}).get(t, [])

    def test_declared_key_wins(self):
        self.inject({"src_outlets": ["source", "store_id", "lat", "store_uuid"],
                     "obs": ["source", "store_id", "price", "store_uuid"]},
                    {"src_outlets": ["source", "store_id"]})
        l = sql_graph.links(["src_outlets", "obs"])[0]
        self.assertEqual(l["basis"], "declared")
        self.assertEqual(l["on"], [["source", "source"], ["store_id", "store_id"]])
        # the sentence has to name the table that declared it — "trust me" is not an explanation
        self.assertIn("src_outlets declares", l["why"])

    def test_identity_column_when_nothing_is_declared(self):
        self.inject({"a": ["store_uuid", "x"], "b": ["store_uuid", "y"]})
        l = sql_graph.links(["a", "b"])[0]
        self.assertEqual(l["basis"], "identity")
        self.assertEqual(l["on"], [["store_uuid", "store_uuid"]])

    def test_shared_name_is_labelled_a_guess(self):
        self.inject({"a": ["widget_ref", "x"], "b": ["widget_ref", "y"]})
        l = sql_graph.links(["a", "b"])[0]
        self.assertEqual(l["basis"], "shared")
        self.assertIn("guess", l["why"])            # the page repeats this word to the user

    def test_generic_columns_are_never_a_join(self):
        # joining on `name` or `price` is a cross-product wearing a join's clothes
        self.inject({"a": ["name", "price", "category"], "b": ["name", "price", "category"]})
        self.assertEqual(sql_graph.links(["a", "b"]), [])

    def test_no_shared_columns_means_no_link(self):
        self.inject({"a": ["x"], "b": ["y"]})
        self.assertEqual(sql_graph.links(["a", "b"]), [])

    def test_ranked_best_first(self):
        self.inject({"a": ["store_uuid", "k"], "b": ["store_uuid", "k"], "c": ["k"]},
                    {"a": ["k"]})
        got = sql_graph.links(["a", "b", "c"])
        self.assertEqual(got[0]["basis"], "declared")
        self.assertLessEqual(got[0]["rank"], got[-1]["rank"])


class Compose(unittest.TestCase):
    SPEC = {
        "tables": ["src_outlets", "retail_observations"],
        "select": [{"table": "src_outlets", "column": "store_name"},
                   {"table": "retail_observations", "column": "price"}],
        "joins": [{"left": "src_outlets", "right": "retail_observations",
                   "on": [["source", "source"], ["store_id", "store_id"]], "type": "inner"}],
    }

    def test_generates_a_qualified_limited_join(self):
        r = sql_graph.build_sql(self.SPEC)
        self.assertTrue(r["ok"], r.get("error"))
        sql = r["sql"]
        self.assertIn('INNER JOIN "retail_observations"', sql)
        self.assertIn('"source" = ', sql)
        self.assertIn("LIMIT 200", sql)
        self.assertNotIn("SELECT *", sql)     # never across a join — one raw_json column ruins it

    def test_colliding_column_names_are_aliased(self):
        spec = dict(self.SPEC, select=[{"table": "src_outlets", "column": "price"},
                                       {"table": "retail_observations", "column": "price"}])
        sql = sql_graph.build_sql(spec)["sql"]
        self.assertIn('AS "src_outlets_price"', sql)
        self.assertIn('AS "retail_observations_price"', sql)

    def test_a_single_column_is_not_needlessly_aliased(self):
        sql = sql_graph.build_sql(self.SPEC)["sql"]
        self.assertNotIn("AS \"src_outlets_store_name\"", sql)

    def test_unjoined_table_is_refused_not_cross_producted(self):
        # THE important one: an unjoined table multiplies the row count by its whole length and the
        # result looks like a real answer
        spec = dict(self.SPEC, tables=self.SPEC["tables"] + ["dim_sku"],
                    select=self.SPEC["select"] + [{"table": "dim_sku", "column": "brand"}])
        r = sql_graph.build_sql(spec)
        self.assertFalse(r["ok"])
        self.assertIn("dim_sku", r["error"])
        self.assertIn("no join path", r["error"])

    def test_three_tables_chain_in_any_order(self):
        # the join list is whatever order the canvas produced; each table must attach to something
        # already in the FROM, so a "backwards" list still resolves
        spec = {"tables": ["a", "b", "c"],
                "select": [{"table": "c", "column": "z"}],
                "joins": [{"left": "b", "right": "c", "on": [["k", "k"]]},
                          {"left": "a", "right": "b", "on": [["j", "j"]]}]}
        r = sql_graph.build_sql(spec)
        self.assertTrue(r["ok"], r.get("error"))
        self.assertLess(r["sql"].index('JOIN "b"'), r["sql"].index('JOIN "c"'))

    def test_left_join_is_honoured(self):
        spec = dict(self.SPEC, joins=[dict(self.SPEC["joins"][0], type="left")])
        self.assertIn("LEFT JOIN", sql_graph.build_sql(spec)["sql"])

    def test_no_fields_is_an_error_not_a_star(self):
        r = sql_graph.build_sql(dict(self.SPEC, select=[]))
        self.assertFalse(r["ok"])
        self.assertIn("field", r["error"])

    def test_where_rides_through(self):
        sql = sql_graph.build_sql(dict(self.SPEC, where="price > 10"))["sql"]
        self.assertIn("WHERE price > 10", sql)

    def test_limit_can_be_removed_deliberately(self):
        sql = sql_graph.build_sql(dict(self.SPEC, limit=0))["sql"]
        self.assertNotIn("LIMIT", sql)

    def test_generated_sql_passes_the_read_only_guard(self):
        # the builder writes SQL that still has to survive the console's guard — if it ever emitted
        # something the guard refuses, the button would just be broken
        import sql_console
        self.assertTrue(sql_console.guard(sql_graph.build_sql(self.SPEC)["sql"]))


class ProbeHonesty(unittest.TestCase):
    def test_probe_always_declares_itself_sampled(self):
        # a bare "94%" implies a census; every return path must carry sampled=True
        import re
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql_graph.py"),
                   errors="ignore").read()
        body = re.sub(r'"""(?:.|\n)*?"""', "", src)
        fn = body[body.index("def probe("):]
        fn = fn[:fn.index("\ndef ", 1)]            # probe() only — not everything after it
        # every `return {...}` in probe must carry the sampled flag; check each one, not a total
        for m in re.finditer(r"return \{", fn):
            depth, i = 0, m.end() - 1
            while i < len(fn):
                depth += (fn[i] == "{") - (fn[i] == "}")
                if depth == 0:
                    break
                i += 1
            self.assertIn('"sampled"', fn[m.start():i + 1],
                          "a probe return without sampled=True reads as a census")


if __name__ == "__main__":
    unittest.main(verbosity=2)
