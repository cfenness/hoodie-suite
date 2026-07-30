#!/usr/bin/env python3
"""agent_checks_test.py — the checker must not lie, especially about itself.

This module exists because agent_checks.py flagged its own absence. It is the layer everything else
trusts: if it reports `pass` for a check that never ran, every downstream reviewer — human or model —
inherits a false clean bill. So the centrepiece here is the third state.

    python3 unifyd/agent_checks_test.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    print("agent_checks — the deterministic checker")
    import agent_checks as C

    # --- 1. SKIPPED IS NOT PASSED — the rule the whole layer rests on ----------------------------
    # Every check must report `skipped` when it has nothing to look at. If an inapplicable check
    # returned `pass`, a change touching nothing relevant would come back all-green and a reader would
    # reasonably conclude it had been verified.
    for fn in C.CHECKS:
        out = fn([])                       # nothing changed -> nothing proven
        for f in out:
            check("%s reports skipped on an empty change set" % f["id"],
                  f["status"] == C.SKIP, (f["id"], f["status"]))
            check("%s explains why it skipped" % f["id"], bool(f["detail"] or f["evidence"]), f)

    # --- 2. real failures are detected -----------------------------------------------------------
    tmp = tempfile.mkdtemp(prefix="checks-")
    bad = os.path.join(tmp, "broken.py")
    with open(bad, "w") as fh:
        fh.write("def oops(:\n")
    old = C.REPO
    C.REPO = tmp
    try:
        r = C.check_syntax(["broken.py"])[0]
        check("a syntax error is a FAIL", r["status"] == C.FAIL, r["status"])
        check("...and the evidence names file and line",
              "broken.py" in r["evidence"], r["evidence"])

        with open(os.path.join(tmp, "fine.py"), "w") as fh:
            fh.write("x = 1\n")
        r = C.check_syntax(["fine.py"])[0]
        check("valid python is a PASS", r["status"] == C.PASS, r["status"])
        check("evidence is a count, not a claim", "parse" in r["evidence"], r["evidence"])

        # A truncated page — the failure smoke_check exists to make impossible to hide.
        with open(os.path.join(tmp, "page.html"), "w") as fh:
            fh.write("<html><body>no closing tag")
        r = C.check_html_closed(["page.html"])[0]
        check("a truncated .html is a FAIL", r["status"] == C.FAIL, r["status"])
        check("...and names the file", "page.html" in r["evidence"], r["evidence"])
        with open(os.path.join(tmp, "ok.html"), "w") as fh:
            fh.write("<html><body>fine</body></html>")
        check("a complete .html is a PASS",
              C.check_html_closed(["ok.html"])[0]["status"] == C.PASS)

        # A file listed but absent must not crash or silently pass.
        r = C.check_syntax(["gone.py"])[0]
        check("a missing file does not crash the checker", r["status"] in (C.SKIP, C.PASS), r)
    finally:
        C.REPO = old

    # --- 2b. changed_paths() must not corrupt the FIRST porcelain line -----------------------------
    # Found live, not hypothesized: git status --porcelain's unstaged-only status is a literal
    # leading space (" M path", not "M path"), and a blanket .strip() on the combined subprocess
    # output ate exactly that space off the front of the first line, shifting changed_paths()'s
    # fixed-offset `line[3:]` slice one character into the path. The corrupted path then failed
    # every os.path.exists() filter downstream, so the module silently vanished from every check —
    # no failure, no skip, just absence. Reproduce with a REAL git repo (not a mocked porcelain
    # string) so a future change to the git-status parsing path is what this actually protects.
    repo = tempfile.mkdtemp(prefix="checks-git-")
    C._run(["git", "init", "-q"], cwd=repo)
    C._run(["git", "config", "user.email", "t@example.com"], cwd=repo)
    C._run(["git", "config", "user.name", "t"], cwd=repo)
    # Name it so it sorts FIRST in porcelain output (alphabetically ahead of anything else changed) —
    # that's the position the corruption landed on.
    target = os.path.join(repo, "aaa_module.py")
    with open(target, "w") as fh:
        fh.write("x = 1\n")
    C._run(["git", "add", "aaa_module.py"], cwd=repo)
    C._run(["git", "commit", "-q", "-m", "init"], cwd=repo)
    with open(target, "w") as fh:
        fh.write("x = 2\n")          # unstaged-only modification -> porcelain status " M", not "M "
    old_repo, old_cwd = C.REPO, os.getcwd()
    C.REPO = repo
    os.chdir(repo)
    try:
        found = C.changed_paths(base="HEAD")
    finally:
        C.REPO = old_repo
        os.chdir(old_cwd)
    check("an unstaged-only change (leading-space porcelain status) keeps its full path",
          "aaa_module.py" in found, found)
    check("...not truncated to a substring of the real path",
          not any(p != "aaa_module.py" and p.endswith("aa_module.py") for p in found), found)

    # --- 3. missing coverage is a FINDING, not silence -------------------------------------------
    # This is the check that flagged three of my own untested modules. It has to fail loudly, because
    # "no test file" is otherwise indistinguishable from "nothing to run".
    # Against a REAL file with no paired test — a nonexistent path is correctly filtered out by the
    # checker, so testing with a fake one proved nothing (it "passed" by never looking).
    os.makedirs(os.path.join(tmp, "unifyd"), exist_ok=True)
    with open(os.path.join(tmp, "unifyd", "lonely_module.py"), "w") as fh:
        fh.write("x = 1\n")
    C.REPO = tmp
    try:
        r = [f for f in C.check_paired_tests(["unifyd/lonely_module.py"]) if f["id"] == "coverage"]
    finally:
        C.REPO = old
    check("a changed module with no paired test is reported", bool(r), "no coverage finding emitted")
    if r:
        check("...as a FAIL, not a skip", r[0]["status"] == C.FAIL, r[0]["status"])
        check("...naming the module", "lonely_module" in r[0]["evidence"], r[0]["evidence"])

    # A module that HAS a test gets its suite actually run, and the real summary line as evidence.
    got = C.check_paired_tests(["unifyd/agent_router.py"])
    tst = [f for f in got if f["id"].startswith("test:")]
    check("a module with a paired test has it run", bool(tst), [f["id"] for f in got])
    if tst:
        check("the suite passes", tst[0]["status"] == C.PASS, tst[0])
        check("evidence is the ACTUAL summary line, not a paraphrase",
              "checks," in tst[0]["evidence"], tst[0]["evidence"])

    # --- 4. a missing third-party dep is SKIPPED, never FAIL -------------------------------------
    # Observed: server.py needs flask, which lives in the venv. Calling that a failure blames the code
    # for the checker's environment, and that noise is how a real failure gets scrolled past.
    r = C.check_imports(["unifyd/server.py"])[0]
    check("an unimportable third-party dep is skipped, not failed",
          r["status"] in (C.SKIP, C.PASS), (r["status"], r["evidence"][:80]))
    if r["status"] == C.SKIP:
        check("...and names the dependency", "needs" in r["evidence"], r["evidence"])
    r2 = C.check_imports(["unifyd/agent_router.py"])[0]
    check("a stdlib-only module imports clean", r2["status"] == C.PASS, r2)

    # --- 5. the flask view-name preflight ---------------------------------------------------------
    # Two routed handlers sharing a name crash-loops gunicorn on boot and is syntactically valid —
    # this repo has taken production down with it once.
    r = C.check_flask_views(["unifyd/server.py"])[0]
    check("server.py view names are checked", r["status"] in (C.PASS, C.FAIL), r["status"])
    check("...and the evidence counts the routes", "view" in r["evidence"].lower(), r["evidence"])
    check("unchanged server.py skips the check",
          C.check_flask_views(["unifyd/agent_router.py"])[0]["status"] == C.SKIP)

    # --- 6. the report ---------------------------------------------------------------------------
    rep = C.run_all(["unifyd/agent_router.py"])
    for f in ("paths", "findings", "counts", "ok", "seconds"):
        check("report exposes %s" % f, f in rep, sorted(rep))
    check("counts add up to the findings",
          sum(rep["counts"].values()) == len(rep["findings"]), rep["counts"])
    check("ok is false iff something failed",
          rep["ok"] == (rep["counts"][C.FAIL] == 0), (rep["ok"], rep["counts"]))

    brief = C.as_brief(rep)
    check("the brief states results were already run", "do not repeat" in brief.lower(), brief[:80])
    check("the brief warns that skipped proves nothing",
          "proved NOTHING" in brief or "never as passing" in brief, brief[-200:])
    check("the brief points the model at what checks cannot reach",
          "cannot reach" in brief, brief[-200:])

    # A checker that dies must degrade to `skipped`, never take the report down with it.
    def exploding(paths):
        raise RuntimeError("boom")
    C.CHECKS.append(exploding)
    try:
        rep2 = C.run_all(["unifyd/agent_router.py"])
        bad = [f for f in rep2["findings"] if "boom" in (f.get("detail") or "")]
        check("a checker that raises degrades to skipped", bool(bad) and bad[0]["status"] == C.SKIP,
              bad)
    finally:
        C.CHECKS.remove(exploding)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
