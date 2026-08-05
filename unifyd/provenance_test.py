"""provenance_test.py — the derived-vs-stated line, and the ratchet that keeps it drawn.

The rule: until a row reaches the master it is still the SOURCE's data, so a value WE computed must
never be indistinguishable from one the retailer stated. A rep quoting a derived size back as "what
Binny's says" is bad data reaching a customer.

Two kinds of test here, and the second is the one that matters over time:
  1. the primitive behaves (derive/mark/stated/how/freeze)
  2. build_product_master's known derive sites are ACTUALLY marked — a structural check against the
     source, so adding a new name-parse or dictionary fill without marking it fails here rather than
     six months later on a sell sheet.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import provenance as prov

HERE = os.path.dirname(os.path.abspath(__file__))


def _code(fname):
    """Source with docstrings and comments stripped — prose describing a rule is not the rule.

    (Three separate tests in this repo have been fooled by matching their own explanatory comment.)
    """
    src = open(os.path.join(HERE, fname), errors="ignore").read()
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    return "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))


class Primitive(unittest.TestCase):
    def test_derive_sets_and_marks(self):
        r = {}
        self.assertEqual(prov.derive(r, "size_ml", 750, "name-parse"), 750)
        self.assertEqual(r["size_ml"], 750)
        self.assertTrue(prov.is_derived(r, "size_ml"))
        self.assertEqual(prov.how(r, "size_ml"), "name-parse")

    def test_empty_value_claims_nothing(self):
        # the common shape is "fill it only if we found something" — an empty derive must not mark,
        # or every row would claim a derivation it never made
        r = {"varietal": None}
        self.assertIsNone(prov.derive(r, "varietal", None, "dict"))
        self.assertIsNone(prov.derive(r, "varietal", "", "dict"))
        self.assertEqual(prov.fields(r), set())
        self.assertIsNone(r.get(prov.COL))

    def test_derive_returns_existing_when_it_no_ops(self):
        r = {"abv": 40.0}
        self.assertEqual(prov.derive(r, "abv", None, "proof/2"), 40.0)

    def test_stated_hides_only_what_we_computed(self):
        r = {"upc": "080480001", "size_ml": 750}
        prov.mark(r, "size_ml", "name-parse")
        self.assertIsNone(prov.stated(r, "size_ml"))        # ours — must not be shown as the source's
        self.assertEqual(prov.stated(r, "upc"), "080480001")  # theirs — safe to attribute
        self.assertIsNone(prov.how(r, "upc"))

    def test_marks_are_idempotent_and_ordered(self):
        r = {}
        prov.mark(r, "size_ml", "name-parse")
        prov.mark(r, "brand", "dict")
        prov.mark(r, "size_ml", "name-parse")
        self.assertEqual(r[prov.COL], "brand:dict,size_ml:name-parse")

    def test_two_rules_for_one_field_both_recorded(self):
        # a field derived one way then corrected another (class_type: name-classify -> majority) keeps
        # both, because "we changed our mind" is itself provenance
        r = {}
        prov.mark(r, "class_type", "name-classify")
        prov.mark(r, "class_type", "brand-core-majority")
        self.assertEqual(prov.fields(r), {"class_type"})
        self.assertIn("brand-core-majority", r[prov.COL])

    def test_mark_without_how(self):
        r = {}
        prov.mark(r, "size_ml")
        self.assertTrue(prov.is_derived(r, "size_ml"))
        self.assertEqual(prov.how(r, "size_ml"), "")

    def test_freeze_interns_so_a_big_stage_is_cheap(self):
        rows = [{} for _ in range(200)]
        for r in rows:
            prov.mark(r, "size_ml", "name-parse")
            prov.freeze(r)
        # 200 rows, ONE string object — the whole reason a 1.6M-row stage can carry this column
        self.assertEqual(len({id(r[prov.COL]) for r in rows}), 1)

    def test_summarize_counts_rows_per_field(self):
        a, b, c = {}, {}, {}
        prov.mark(a, "size_ml", "name-parse")
        prov.mark(b, "size_ml", "name-parse")
        prov.mark(b, "brand", "dict")
        self.assertEqual(prov.summarize([a, b, c]), {"size_ml": 2, "brand": 1})

    def test_unmarked_row_reads_clean(self):
        r = {"size_ml": 750}
        self.assertEqual(prov.fields(r), set())
        self.assertFalse(prov.is_derived(r, "size_ml"))
        self.assertEqual(prov.stated(r, "size_ml"), 750)


class BuildProductMasterIsMarked(unittest.TestCase):
    """The ratchet. Each known derive site in build_product_master must be marked at its site."""

    def setUp(self):
        self.src = _code("build_product_master.py")

    def test_stage_writes_the_provenance_column(self):
        self.assertIn("_prov.COL", self.src,
                      "_stage_product must land the _derived column or the marks never reach the warehouse")
        self.assertNotIn("_size_basis", self.src,
                         "_size_basis was the size-only predecessor — it must not come back alongside _derived")

    def test_every_derive_site_is_marked(self):
        # (what we compute, the token that proves it's marked)
        sites = [
            ("size_ml from the product name", 'size_ml"] = "name-parse"'),
            ("brand from the dictionary, not a brand column", '_der["brand"]'),
            ("pack count read off the name", '_der["pack"] = "name-parse"'),
            ("abv computed as proof/2", '_der["abv"] = "proof/2"'),
            ("class_type from the name/dictionary", '_prov.derive(s, "class_type"'),
            ("varietal filled from the dictionary", '_prov.derive(s, "varietal"'),
            ("flavor filled from the dictionary", '_prov.derive(s, "flavor"'),
            ("core_name — always ours, no source ships one", '_prov.derive(s, "core_name"'),
        ]
        for what, token in sites:
            with self.subTest(what):
                self.assertIn(token, self.src, "unmarked derive: %s" % what)

    def test_build_logs_the_derive_rate(self):
        # a derive rate that jumps (a source silently dropping a column) has to be visible in the run
        # output, not absorbed. This is the difference between a degrade and a quiet degrade.
        self.assertIn("_prov.summarize(staged)", self.src)

    def test_freeze_before_write(self):
        self.assertIn("_prov.freeze(", self.src,
                      "1.6M un-interned marker strings is real memory on a build that already OOMs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
