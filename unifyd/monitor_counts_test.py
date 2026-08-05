"""monitor_counts_test.py — the console's row counts must be reconcilable.

A count nobody can reconcile is worse than no count: it makes every number on the page arguable. The
lister used to sum whatever parquet files were lying in a table's directory and ALSO emit the
top-level file under the same name, so four tables appeared twice with two different counts each and
the warehouse total read ~4.8M rows high. Measured live 2026-08-05:

    src_outlets        listed 1,768,869 AND 2,852,159    manifest truth 1,916,357
    ubereats_products  listed   597,308 AND 2,160,806    manifest truth 2,160,806
    binnys_products    listed 1,534,862 AND 1,534,938    manifest truth 1,534,862
    scrape_runs        listed         2 AND       265

Two things a bucketed table's directory holds that must NOT be summed: SUPERSEDED bucket versions
(binnys_products, 21 files on disk / 16 active) and the pre-migration ROLLBACK COPY that
migrate_to_bucketed deliberately leaves at top level.

Pure — the fold logic is exercised against synthetic footer listings, no warehouse and no duckdb.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def fold(footers, active):
    """The classification the lister performs, lifted out so it can be checked without object storage.

    Mirrors _list_datasets_fast's branch structure exactly; the ratchet below asserts the real function
    still contains the same three decisions.
    """
    out, parted, seen = [], {}, set()
    for rel, mod, rows in footers:
        if "/" not in rel:
            nm = rel[:-8]
            if nm in active:
                continue
            out.append({"name": nm, "rows": rows, "partitioned": False})
            seen.add(nm)
            continue
        top = rel.split("/", 1)[0]
        if top == "_manifest":
            continue
        if top in active and rel not in active[top]:
            continue
        d = parted.setdefault(top, {"name": top, "rows": 0, "partitioned": True})
        d["rows"] += rows
    for d in parted.values():
        if d["name"] in active:
            d["bucketed"] = True
        if d["name"] in seen:
            d["ambiguous"] = True
        out.append(d)
    return out


class Counting(unittest.TestCase):
    def test_rollback_copy_is_not_a_second_table(self):
        footers = [("src_outlets.parquet", 1, 1768869),
                   ("src_outlets/__b=00/part-v2.parquet", 2, 900000),
                   ("src_outlets/__b=01/part-v2.parquet", 2, 1016357)]
        active = {"src_outlets": {"src_outlets/__b=00/part-v2.parquet",
                                  "src_outlets/__b=01/part-v2.parquet"}}
        got = fold(footers, active)
        self.assertEqual(len(got), 1, "one table, one entry")
        self.assertEqual(got[0]["rows"], 1916357)
        self.assertTrue(got[0]["bucketed"])

    def test_superseded_buckets_are_not_summed(self):
        # this is binnys_products: 21 files on disk, 16 active. Summing all of them counted rows twice.
        footers = [("binnys_products/__b=00/part-v1.parquet", 1, 700000),   # superseded
                   ("binnys_products/__b=00/part-v2.parquet", 2, 734862),   # active
                   ("binnys_products/__b=01/part-v1.parquet", 2, 800000)]   # active
        active = {"binnys_products": {"binnys_products/__b=00/part-v2.parquet",
                                      "binnys_products/__b=01/part-v1.parquet"}}
        got = fold(footers, active)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["rows"], 1534862)

    def test_a_plain_partitioned_table_is_unchanged(self):
        # retail_observations et al have no manifest — every part still counts, exactly as before
        footers = [("retail_observations/2026-08-01_a.parquet", 1, 10),
                   ("retail_observations/2026-08-02_b.parquet", 2, 20)]
        got = fold(footers, {})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["rows"], 30)
        self.assertTrue(got[0]["partitioned"])
        self.assertNotIn("bucketed", got[0])

    def test_ambiguous_name_is_flagged_not_silently_resolved(self):
        # a directory AND a top-level file with no manifest to arbitrate: emit one entry and SAY so
        footers = [("scrape_runs.parquet", 1, 2), ("scrape_runs/2026-08-01.parquet", 2, 265)]
        got = fold(footers, {})
        byname = [d for d in got if d["name"] == "scrape_runs"]
        self.assertEqual(len(byname), 2, "today both are emitted...")
        self.assertTrue(any(d.get("ambiguous") for d in byname), "...but the collision must be flagged")

    def test_manifests_unreadable_falls_back_to_old_behaviour(self):
        # a listing failure must not silently zero a table; with no active-map we sum as before
        footers = [("src_outlets/__b=00/part-v2.parquet", 1, 5)]
        self.assertEqual(fold(footers, {})[0]["rows"], 5)


class Ratchet(unittest.TestCase):
    """Structural: the real lister must still make these three decisions."""

    def setUp(self):
        import re
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py"),
                   errors="ignore").read()
        src = re.sub(r'"""(?:.|\n)*?"""', "", src)
        self.body = "\n".join(l for l in src.split("\n") if not l.strip().startswith("#"))

    def test_lister_reads_the_manifests(self):
        self.assertIn("_wh._list_manifests()", self.body)

    def test_superseded_parts_are_skipped(self):
        self.assertIn("if top in active and rel not in active[top]:", self.body)

    def test_read_expr_resolves_bucketed_first(self):
        # the drawer and the workbench both go through read_expr; a bucketed table there must not fall
        # to the glob (matches nothing) or to uri() (the stale rollback copy)
        fn = self.body[self.body.index("def read_expr("):]
        man = fn.index('man.get("layout") == "bucketed"')
        self.assertLess(man, fn.index('s.get("partitioned")'))

    def test_footer_cache_keys_on_size_too(self):
        self.assertIn("cached[3] if len(cached) > 3 else None) == siz", self.body,
                      "mtime alone lets a rewritten part serve a stale row count forever")


if __name__ == "__main__":
    unittest.main(verbosity=2)
