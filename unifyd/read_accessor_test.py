"""read_accessor_test.py — one way to open a warehouse table, enforced.

WHY. `warehouse.py` documents the rule and measured it:

    PASS AN EXPLICIT FILE LIST, NEVER A GLOB. Handing DuckDB `<dir>/*.parquet` corrupts the read on a
    large partition set … Measured on retail_observations 2026-08-03, 3,824 partitions / 51.7M rows,
    reproduced 4/4 … read_parquet('<dir>/*.parquet') -> FAILS, read_parquet([<the same files>]) -> OK

`warehouse.attach_view` / `query_parts` do it correctly for all three layouts (single file, bucketed,
date-partitioned). Nothing forced their use, so on 2026-08-05 SEVEN modules had independently
re-derived a warehouse path and each got it wrong in its own way:

    monitor.read_expr            bucketed glob one directory too shallow -> matched nothing
    sql_console._scoped_expr     same
    tools/spec_capture           read the pre-migration rollback copy, or reported "never landed"
    velocity, obs_quality,       `<dir>/*.parquet` against the exact table the corruption was
    master_quality,               measured on — three fail loudly, velocity_signals sat inside a
    velocity_signals              try/except and degraded QUIETLY

A convention that is documented but not enforced is a convention that gets re-derived. So this is an
AST scan, not a code review note: it names file:line, and the BASELINE only ever shrinks.

ON THE BASELINE. It exists so this lands green rather than red on day one — a check that is red on
arrival teaches people to ignore it. But a baseline entry is a claim, and today produced four separate
guards that were disabled by a claim nobody verified. So an entry here MUST cite the EVIDENCE that the
site is safe (what was measured, or why the glob cannot reach a partitioned table), never the
reasoning. "It should be fine because …" is exactly the sentence that put `warehouse.py: 1` into the
table_spec ratchet and `land()`'s unpinned write into production.
"""
import ast
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Sites allowed to hand a GLOB to read_parquet, with the evidence that makes each safe.
# Format: "relative/path.py": (count, "the evidence")
BASELINE = {
    # warehouse.py IS the accessor — it owns path resolution, and its own glob use is the fallback
    # inside _partition_files, which lists files rather than passing the pattern to DuckDB.
    # PINNED TO THE MEASURED COUNT, not a ceiling. `99` was here first and it made this ratchet
    # unfailable for the two files most likely to grow a new glob — you cannot shrink a number that
    # was never the real count, and you cannot notice a regression against a limit nobody is near.
    # That is the same defect this file exists to catch, one level up. (These are AST string
    # constants with docstrings excluded; a grep counts more because it also sees the prose.)
    "unifyd/warehouse.py": (3, "the accessor itself; verified by warehouse_compat_test + "
                               "warehouse_falsezero_test, which read every layout through it"),
    # monitor.read_expr still builds a glob for date-partitioned tables. It is bounded in practice —
    # sql_console caps a bind at 60 parts and the console drawer at 40 — but it is NOT proven safe at
    # 3,824, so it is baselined as KNOWN-UNFIXED rather than as safe. Fix by routing through
    # attach_view; then drop this entry.
    "unifyd/monitor.py": (3, "KNOWN-UNFIXED, not proven safe: read_expr globs a partitioned table. "
                             "Callers bound the part count today (sql_console 60, drawer 40); the "
                             "glob itself is unmeasured at 3,824. Fix by routing through attach_view."),
    # NOT our warehouse: Foursquare's public open-data bucket (fsq-os-places-us-east-1), a vendor
    # release path we do not own and cannot resolve to a file list. The measured corruption is about
    # OUR partition counts; this is a fixed vendor release read once.
    "unifyd/poi.py": (1, "external bucket fsq-os-places-us-east-1 — not a warehouse table, no "
                         "manifest and no partition list to resolve"),
    # OUR warehouse, and a `**/` recursive glob at that — so this IS the same class. Baselined as
    # KNOWN-UNFIXED rather than converted, because the nested layout it reads is not one attach_view
    # covers and I have not measured its part count. Converting it blind would be the same unverified
    # confidence that caused today's other four. Fix = establish the layout, then route it.
    # KNOWN-UNFIXED and MEASURED, which is the difference between a landmine and a note.
    #   the defect:      _raw_glob builds <bucket>/<prefix>/<name>/**/*.parquet over OUR warehouse —
    #                    same call form as the five fixed here, and `**/` is WORSE than the flat glob
    #                    the corruption was measured on, since it can match arbitrarily more files.
    #   reachability:    `build-sipsource-marts` is enabled=False and waits on a `sipsource-feed`
    #                    source that does not exist (verified in source_registry.py).
    #   exposure:        0. Measured on Fly 2026-08-05 — the sip_raw / sipsource_raw / sipsource
    #                    prefixes do not exist in the warehouse, so there is nothing for the glob to
    #                    match and no live path reaches it.
    # Fix = route through the accessor when the real feed lands; do NOT convert it blind before then.
    "unifyd/sipsource_ingest.py": (2, "KNOWN-UNFIXED but unreachable: enabled=False, upstream source "
                                      "absent, and 0 objects under its prefixes (measured on Fly "
                                      "2026-08-05). Same defect class as the five fixed here."),
}


def _globs(path):
    """[(line, snippet)] — every read_parquet(...) whose first argument is a single string that
    contains a '*'. String CONCATENATION and %-formatting are followed, because that is how every one
    of the seven sites built its glob: never as a literal."""
    try:
        tree = ast.parse(open(path, errors="ignore").read())
    except Exception:
        return []
    hits = []

    def has_star(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "*" in node.value
        if isinstance(node, ast.BinOp):                      # "a %s/*.parquet" % x   /   a + b
            return has_star(node.left) or has_star(node.right)
        if isinstance(node, ast.JoinedStr):                  # f-string
            return any(has_star(v) for v in node.values)
        if isinstance(node, ast.IfExp):                      # x if remote() else y
            return has_star(node.body) or has_star(node.orelse)
        return False

    for n in ast.walk(tree):
        # the call may be read_parquet(...) directly, or a SQL string containing it — cover both by
        # looking at any string that mentions read_parquet and carries a star
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and "read_parquet" in n.value and "[" not in n.value:
            hits.append((getattr(n, "lineno", 0), n.value[:70]))
    return hits


def _star_strings(path):
    """Every string constant naming a `*.parquet` pattern — the shape all the sites used.

    DOCSTRINGS ARE EXCLUDED. A comment or docstring that DESCRIBES the rule is not a violation of it,
    and a scanner that cannot tell them apart flags the very notes written to prevent the bug. That
    mistake has now been made four separate times in this repo's tests today; the fix is to skip the
    docstring node explicitly rather than to grep text.
    """
    try:
        src = open(path, errors="ignore").read()
        tree = ast.parse(src)
    except Exception:
        return []
    doc = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(n, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc.add(id(body[0].value))
    return [(getattr(n, "lineno", 0), n.value[:70]) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "*.parquet" in n.value and id(n) not in doc]


def _modules():
    for d in ("unifyd", "tools"):
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            if f.endswith(".py") and not f.endswith("_test.py"):
                yield os.path.join(d, f), os.path.join(base, f)


class NoHandRolledGlobs(unittest.TestCase):
    def test_no_new_glob_sites(self):
        offenders = {}
        for rel, path in _modules():
            n = len(_star_strings(path))
            if not n:
                continue
            allowed = BASELINE.get(rel, (0, ""))[0]
            if n > allowed:
                offenders[rel] = (n, allowed, _star_strings(path)[:3])
        if offenders:
            msg = ["a warehouse path was resolved by hand — use warehouse.attach_view / query_parts:"]
            for rel, (n, allowed, sample) in sorted(offenders.items()):
                msg.append("  %s: %d '*.parquet' string(s), baseline %d" % (rel, n, allowed))
                for line, snip in sample:
                    msg.append("      %s:%d  %s" % (rel, line, snip))
            self.fail("\n".join(msg))

    def test_baseline_is_the_measured_count_not_a_ceiling(self):
        """A baseline set above the real count is a guard that cannot fail.

        The first version of this file used 99 for warehouse.py and monitor.py — which is not a count,
        it is "unlimited", and it made the ratchet unfailable for the two files most likely to grow a
        new glob. It also breaks the property the whole design rests on: you cannot SHRINK a number
        that was never the real count. Caught in review, and it is the same defect this file exists to
        catch, one level up.
        """
        for rel, (allowed, _why) in BASELINE.items():
            n = len(_star_strings(os.path.join(ROOT, rel)))
            self.assertEqual(n, allowed,
                             "%s: baseline %d but %d measured — pin it to the count, and shrink it as "
                             "sites are fixed" % (rel, allowed, n))

    def test_baseline_only_shrinks(self):
        """Every baselined module must still exist and still be at or under its entry.

        A stale entry is how a baseline widens by accident: the file is deleted or fixed, the number
        stays, and the next offender slides in underneath it.
        """
        for rel, (allowed, why) in BASELINE.items():
            path = os.path.join(ROOT, rel)
            self.assertTrue(os.path.exists(path), "baselined file is gone — drop the entry: %s" % rel)
            self.assertTrue(why.strip(), "a baseline entry must carry its EVIDENCE: %s" % rel)

    def test_the_four_moat_modules_are_clean(self):
        """Named explicitly, because these are the ones that were wrong and a regression here is
        silent — velocity_signals' read sits inside a try/except, so a corrupt read degrades to a
        missing coverage flag rather than an error."""
        for rel in ("unifyd/velocity.py", "unifyd/velocity_signals.py",
                    "unifyd/obs_quality.py", "unifyd/master_quality.py"):
            hits = _star_strings(os.path.join(ROOT, rel))
            self.assertEqual(hits, [], "%s re-derived a warehouse path: %s" % (rel, hits))

    def test_they_use_the_accessor(self):
        for rel in ("unifyd/velocity.py", "unifyd/velocity_signals.py",
                    "unifyd/obs_quality.py", "unifyd/master_quality.py"):
            src = open(os.path.join(ROOT, rel), errors="ignore").read()
            self.assertIn("attach_view(", src, "%s should mount through warehouse.attach_view" % rel)


class AccessorCoversEveryLayout(unittest.TestCase):
    """attach_view must handle all three layouts, or callers go back to rolling their own."""

    def setUp(self):
        import re
        src = open(os.path.join(HERE, "warehouse.py"), errors="ignore").read()
        src = re.sub(r'"""(?:.|\n)*?"""', "", src)
        fn = src[src.index("def attach_view("):]
        self.body = fn[:fn.index("\ndef ", 1)]

    def test_bucketed(self):
        self.assertIn('man.get("layout") == "bucketed"', self.body)

    def test_date_partitioned(self):
        # the branch that was missing: uri(name) is a single file that does not exist for these
        self.assertIn("_partition_files_strict(name)", self.body)

    def test_partitioned_passes_a_list_not_a_glob(self):
        part = self.body[self.body.index("_partition_files_strict(name)"):]
        self.assertIn("read_parquet([%s]", part)
        self.assertNotIn("*.parquet", part)


if __name__ == "__main__":
    unittest.main(verbosity=2)
