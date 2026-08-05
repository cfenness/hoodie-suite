"""ue_schema_test.py — a field the scrape captures must reach the table, or say so.

Measured 2026-08-05: ue_catalog wrote 21 fields; the live `ubereats_products` held 16. The five
missing ones were the retailer's OWN category hierarchy, and they were well populated in the parts:

    section        100.0%      subsection      100.0%      section_name    90.0%
    category_path   98.9%      subsection_name   9.8%

They were discarded at every merge for as long as the table had been bucketed, because the bucketed
path ignored `fields=` and could never add a column (fixed in #820). Nothing failed and nothing
warned — `category` simply read 0% on a table of 2.16M rows, which looks exactly like a field the
source does not provide. That is the expensive kind of wrong: it makes a capture gap and a merge bug
indistinguishable.

These checks are structural (no warehouse, no network). The runtime half is the WIDENING line the
consolidate now logs when the parts carry a field the table lacks.
"""
import ast
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _code(fname):
    src = open(os.path.join(HERE, fname), errors="ignore").read()
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))


class WriteSchema(unittest.TestCase):
    def setUp(self):
        import ue_catalog
        self.fields = ue_catalog.PRODUCT_FIELDS

    def test_the_hierarchy_is_in_the_write_schema(self):
        """The comment on PRODUCT_FIELDS says it plainly: a field parsed and not listed here is
        computed and then silently dropped at the write. These five are the retailer's own breadcrumb
        and the only thing that makes `category` answerable."""
        for f in ("section", "subsection", "section_name", "subsection_name", "category_path"):
            self.assertIn(f, self.fields, "the retailer's hierarchy must reach the table: %s" % f)

    def test_raw_json_is_excluded_from_the_lean_write(self):
        # deliberate: the payload is an EVENT, kept append-only in raw_payloads, never a column on an
        # accumulating catalog
        body = _code("ue_catalog.py")
        self.assertIn('k != "raw_json"', body)

    def test_consolidate_passes_the_field_list(self):
        """The merge must hand `fields=` through, or the table can never gain a column — which is the
        exact defect that cost the hierarchy."""
        body = _code("ue_catalog.py")
        cons = body[body.index("def consolidate("):]
        self.assertIn("fields=fields", cons)

    def test_consolidate_names_what_it_would_drop(self):
        """A narrow table is survivable; a narrow table nobody is told about is not. The run output
        must name the fields the parts carry that the table lacks."""
        body = _code("ue_catalog.py")
        cons = body[body.index("def consolidate("):]
        self.assertIn("read_manifest(tbl)", cons)
        self.assertIn("WIDENING", cons)


class BrandIsNotCaptured(unittest.TestCase):
    """Brand is NOT available from the UberEats item endpoint, and the code should not pretend it is.

    Verified against 60 real captured payloads (raw_payloads, 2026-08-05): `itemAttributeInfo` was
    empty in every one and `classifications` in all 40 sampled. `ubereats.parse_item` produces no
    brand because there is nothing to parse. The brand IS present inside the product name
    ("Juicy Juice 100% Juice, Berry"), so it is DERIVABLE — but deriving it into this table would
    present our calculation as UberEats' own data, which is the line drawn in provenance.py.

    This test exists so the hardcoded empty brand reads as a documented fact rather than an oversight.
    """

    def test_brand_is_written_empty_not_guessed(self):
        body = _code("ubereats.py")
        self.assertIn('brand=""', body,
                      "brand must be written empty — the endpoint does not supply it, and guessing "
                      "it here would attribute our derivation to the source")

    def test_brand_is_still_in_the_schema(self):
        # the column stays: it is filled at the MASTER, where resolve_brand marks it derived
        import ue_catalog
        self.assertIn("brand", ue_catalog.PRODUCT_FIELDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
