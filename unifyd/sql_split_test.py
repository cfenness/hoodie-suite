"""sql_split_test.py — the workbench splits statements in the BROWSER; this checks that splitter.

Why it lives here rather than only in the page: the split decides what gets sent to /api/sql, and the
server accepts exactly one statement per request. Get the split wrong and a perfectly good query is
either truncated or rejected — `WHERE note = 'a; b'` becoming two broken statements is the obvious
case, and it is the kind of thing nobody notices until they type an apostrophe into a search.

The rules mirror sql_console._strip: a ';' inside a string literal, a line comment, or a block comment
is not a separator.

Runs the real function out of apps/sql-workbench.html under node. Skips cleanly where node is absent
(no new dependency — the suite must stay runnable on a bare machine).
"""
import os

import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "apps", "sql-workbench.html")

CASES = [
    ("SELECT 1", 1, "no terminator"),
    ("SELECT 1;", 1, "trailing ';' is not a second statement"),
    ("SELECT 1; SELECT 2", 2, "two on one line"),
    ("SELECT 1;\nSELECT 2;\nSELECT 3;", 3, "a scratchpad"),
    ("SELECT * FROM t WHERE note = 'a; b'", 1, "';' inside a string literal"),
    ("SELECT * FROM t WHERE s = 'it''s; ok'", 1, "doubled-quote escape, then a ';'"),
    ("SELECT 1 -- ; not a split\n; SELECT 2", 2, "';' in a line comment is not a split"),
    ("SELECT /* ; */ 1", 1, "';' in a block comment is not a split"),
    ("   ;;;   ", 0, "nothing but separators is no statements"),
    ("SELECT 1;\n\n-- a note\nSELECT 2", 2, "a comment between statements"),
    ('SELECT "a;b" FROM t', 1, "';' inside a quoted identifier"),
]


def _extract():
    src = open(PAGE, errors="ignore").read()
    a = src.index("function statements(sql){")
    b = src.index("function current(){")
    return src[a:b]


class Splitter(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_cases(self):
        import json
        js = _extract() + "\nconst C = %s;\n" % json.dumps([[c[0], c[1]] for c in CASES]) + """
        let bad = [];
        for (const [sql, want] of C) {
          const got = statements(sql).length;
          if (got !== want) bad.push(JSON.stringify(sql) + " want " + want + " got " + got);
        }
        const src = "SELECT 1;\\nSELECT * FROM t WHERE x = 'a;b';\\nSELECT 3";
        const parts = statements(src).map(s => s.sql.trim());
        if (parts[1] !== "SELECT * FROM t WHERE x = 'a;b'")
          bad.push("reconstruction: " + JSON.stringify(parts));
        console.log(bad.length ? "FAIL " + bad.join(" | ") : "OK");
        """
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[:400])
        self.assertEqual(out.stdout.strip(), "OK", out.stdout.strip()[:600])

    def test_the_page_still_has_the_pieces(self):
        # a structural floor that works with no node: the workspace's contract with the server is that
        # it sends ONE statement per request. If the splitter or the per-statement send disappears, the
        # page is silently back to posting whatever is in the box.
        src = open(PAGE, errors="ignore").read()
        for token in ("function statements(sql)", "function current()", "async function one(sql)",
                      "id=\"runall\"", "RESULTS.push("):
            self.assertIn(token, src, "workspace piece missing: %s" % token)

    def test_run_all_stops_at_the_first_failure(self):
        # statement 3 usually depends on statement 2; a wall of errors caused by one broken statement
        # tells you nothing about which one broke
        src = open(PAGE, errors="ignore").read()
        self.assertIn("stopped at statement", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
