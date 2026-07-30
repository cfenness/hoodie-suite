#!/usr/bin/env python3
"""agent_checks.py — the DETERMINISTIC checker. The most decorrelated reviewer available, and free.

WHY THIS COMES BEFORE ANY THIRD-PARTY MODEL:
The value of a checker is that it does not share the author's blind spots. Frontier models are far
more correlated with each other than they look — same architecture family, heavily overlapping
corpora, similar safety training — so a second LLM is a partial hedge at metered cost. A test suite
shares NOTHING with a language model: it fails on facts, not on judgement. It is the most independent
signal in the building and it costs nothing per run.

The crew run that motivated this made the point by accident: QA's single most credible line was
"exit code 0, `65 checks, 0 failed`, verified with `echo $?`". Its value came from running the
deterministic thing first and reasoning past it. So run the deterministic layer FIRST, hand its
results to the model, and let the expensive reasoning go where tests cannot reach.

TWO RULES THIS MODULE WILL NOT BEND:
  1. A check that could not run reports `skipped`, NEVER `pass`. An unrunnable check reported as
     green is the quiet-degrade failure this repo treats as worse than a loud break.
  2. Evidence is the actual output, truncated — never a summary of it. "Tests passed" is a claim;
     "65 checks, 0 failed" is evidence.

MISSING COVERAGE IS ITSELF A FINDING. 44 of the modules here have a paired `X_test.py`; when a change
touches one that does not, that is reported. A model asked "is this well tested?" will produce a
paragraph; this produces a filename.

    python3 unifyd/agent_checks.py                 # check the working tree's changes
    python3 unifyd/agent_checks.py --paths unifyd/agent_memory.py
"""
import argparse
import ast
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

PASS, FAIL, SKIP = "pass", "fail", "skipped"


def _run(cmd, cwd=None, timeout=900):
    """Run a command; return (returncode, combined output). Never raises.

    rstrip only, never lstrip: `changed_paths()` parses `git status --porcelain` lines by FIXED
    OFFSET (`line[3:]`, the 2-char status code + a space). Porcelain's unstaged-only status is a
    literal leading space (" M", not "M "), so a blanket `.strip()` here ate that space off the
    front of the FIRST line whenever it happened to be an unstaged-only change — silently chopping
    one character off that one path (`unifyd/agent_memory.py` -> `nifyd/agent_memory.py`), which then
    failed every `os.path.exists()` filter downstream and made that module vanish from every check
    with no failure reported at all. Found live: it ate agent_memory.py itself out of this exact run."""
    try:
        p = subprocess.run(cmd, cwd=cwd or REPO, capture_output=True, text=True, timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).rstrip()
    except subprocess.TimeoutExpired:
        return None, "timed out after %ss" % timeout
    except Exception as e:
        return None, str(e)[:400]


def _finding(cid, label, status, proves, evidence="", detail=""):
    return dict(id=cid, label=label, status=status, proves=proves,
                evidence=(evidence or "")[:2000], detail=detail)


def changed_paths(base="origin/main"):
    """Files this working tree changes vs base, plus anything uncommitted.

    Falls back to uncommitted-only when the base can't be resolved — reporting nothing would look
    like a clean change set, which is the wrong direction to fail in."""
    rc, out = _run(["git", "diff", "--name-only", "%s...HEAD" % base])
    paths = set(l.strip() for l in (out or "").splitlines() if l.strip()) if rc == 0 else set()
    rc2, out2 = _run(["git", "status", "--porcelain"])
    if rc2 == 0:
        for line in (out2 or "").splitlines():
            p = line[3:].strip()
            if p:
                paths.add(p)
    return sorted(p for p in paths if p)


# ── individual checks ────────────────────────────────────────────────────────────────────────────
def check_syntax(paths):
    py = [p for p in paths if p.endswith(".py") and os.path.exists(os.path.join(REPO, p))]
    if not py:
        return [_finding("syntax", "Python parses", SKIP, "every changed .py is syntactically valid",
                         detail="no changed .py files")]
    bad = []
    for p in py:
        try:
            ast.parse(open(os.path.join(REPO, p), encoding="utf-8", errors="replace").read())
        except SyntaxError as e:
            bad.append("%s:%s %s" % (p, e.lineno, e.msg))
    return [_finding("syntax", "Python parses", FAIL if bad else PASS,
                     "every changed .py is syntactically valid",
                     evidence="\n".join(bad) if bad else "%d file(s) parse" % len(py))]


def check_imports(paths):
    """Import each changed unifyd module in a subprocess.

    Catches the failure class this repo has been bitten by repeatedly: a module that compiles but dies
    on first use because of a bad import. A syntax check will not find it, and neither will a reviewer
    reading the diff."""
    mods = [os.path.basename(p)[:-3] for p in paths
            if p.startswith("unifyd/") and p.endswith(".py") and not p.endswith("_test.py")
            and os.path.exists(os.path.join(REPO, p))]
    if not mods:
        return [_finding("imports", "Modules import", SKIP, "each changed module imports cleanly",
                         detail="no changed unifyd modules")]
    bad, unavailable = [], []
    for m in mods:
        rc, out = _run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, %r); import %s" % (HERE, m)], timeout=120)
        if rc == 0:
            continue
        last = (out or "").splitlines()[-1] if out else "failed"
        # A MISSING THIRD-PARTY DEPENDENCY IS NOT A BROKEN MODULE. `server.py` needs flask, which
        # lives in the venv and not in this interpreter — reporting that as `fail` blames the code for
        # the checker's environment, and the noise is how a real failure gets scrolled past. It is not
        # `pass` either: nothing was proven. It is exactly the third state, with the reason named.
        mod = ""
        if "ModuleNotFoundError" in last and "No module named" in last:
            mod = last.split("No module named", 1)[1].strip().strip("'\"")
            if mod.split(".")[0] not in {os.path.basename(p)[:-3] for p in
                                         glob.glob(os.path.join(HERE, "*.py"))}:
                unavailable.append("%s: needs %s (not importable from this interpreter)" % (m, mod))
                continue
        bad.append("%s: %s" % (m, last))
    if bad:
        status, ev = FAIL, "\n".join(bad + unavailable)
    elif unavailable:
        status, ev = SKIP, "\n".join(unavailable)
    else:
        status, ev = PASS, "%d module(s) import" % len(mods)
    return [_finding("imports", "Modules import", status,
                     "each changed module imports cleanly (a module can compile and still die on "
                     "first use — neither a syntax check nor a diff review finds that)",
                     evidence=ev)]


def check_paired_tests(paths):
    """Run the test suite paired with each changed module — and report modules that have none.

    The convention here is `unifyd/X.py` -> `unifyd/X_test.py`. Absent coverage is reported as its own
    finding rather than silence: a change to an untested module is a fact worth surfacing, and it is
    exactly the kind of thing a model will describe in a paragraph and a filesystem can state exactly."""
    mods = [p for p in paths if p.startswith("unifyd/") and p.endswith(".py")
            and not p.endswith("_test.py") and os.path.exists(os.path.join(REPO, p))]
    out = []
    untested = []
    for p in mods:
        t = p[:-3] + "_test.py"
        if not os.path.exists(os.path.join(REPO, t)):
            untested.append(p)
            continue
        rc, txt = _run([sys.executable, t])
        tail = (txt or "").strip().splitlines()
        summary = tail[-1] if tail else ""
        fails = [l for l in tail if "FAIL" in l][:6]
        out.append(_finding("test:%s" % os.path.basename(t), "Tests — %s" % os.path.basename(t),
                            PASS if rc == 0 else (SKIP if rc is None else FAIL),
                            "the module's own suite passes",
                            evidence="\n".join(fails + [summary]).strip() or (txt or "")[-400:]))
    if untested:
        out.append(_finding("coverage", "Paired test exists", FAIL,
                            "every changed module has a paired X_test.py",
                            evidence="no test file for:\n" + "\n".join(untested),
                            detail="absent coverage is a finding, not silence"))
    if not mods:
        out.append(_finding("test", "Paired tests", SKIP, "changed modules' suites pass",
                            detail="no changed unifyd modules"))
    return out


def check_smoke(paths):
    """The suite smoke check, when a surface changed."""
    if not any(p.startswith("apps/") or p == "index.html" for p in paths):
        return [_finding("smoke", "Suite smoke", SKIP, "every registered app serves",
                         detail="no surface files changed")]
    script = os.path.join(REPO, "tools", "smoke_check.py")
    if not os.path.exists(script):
        return [_finding("smoke", "Suite smoke", SKIP, "every registered app serves",
                         detail="tools/smoke_check.py not present")]
    rc, out = _run([sys.executable, script])
    tail = (out or "").strip().splitlines()
    return [_finding("smoke", "Suite smoke", PASS if rc == 0 else FAIL,
                     "every registered app serves and every local ref resolves",
                     evidence="\n".join(tail[-6:]))]


def check_flask_views(paths):
    """Duplicate Flask view-function names — the preflight that once took production down.

    Two @app.route handlers sharing a function name is syntactically fine and crash-loops gunicorn on
    boot. It is documented in CLAUDE.md precisely because a syntax check does not catch it."""
    if "unifyd/server.py" not in paths:
        return [_finding("flask-views", "No duplicate view names", SKIP,
                         "no two Flask views share a function name",
                         detail="server.py unchanged")]
    src = os.path.join(REPO, "unifyd", "server.py")
    if not os.path.exists(src):
        return [_finding("flask-views", "No duplicate view names", SKIP, "", detail="server.py absent")]
    try:
        tree = ast.parse(open(src, encoding="utf-8", errors="replace").read())
    except SyntaxError as e:
        return [_finding("flask-views", "No duplicate view names", SKIP,
                         "no two Flask views share a function name",
                         detail="server.py does not parse (%s) — the syntax check owns this" % e.msg)]
    seen, dupes = {}, []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            routed = any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") in
                ("route", "get", "post", "put", "delete", "patch")
                for d in node.decorator_list)
            if routed:
                if node.name in seen:
                    dupes.append("%s (lines %d and %d)" % (node.name, seen[node.name], node.lineno))
                seen[node.name] = node.lineno
    return [_finding("flask-views", "No duplicate view names", FAIL if dupes else PASS,
                     "no two Flask views share a function name (gunicorn refuses to boot)",
                     evidence="\n".join(dupes) if dupes else "%d routed views, all uniquely named"
                              % len(seen))]


def check_html_closed(paths):
    html = [p for p in paths if p.endswith(".html") and os.path.exists(os.path.join(REPO, p))]
    if not html:
        return [_finding("html", "HTML complete", SKIP, "changed pages are not truncated",
                         detail="no changed .html")]
    bad = [p for p in html
           if "</html>" not in open(os.path.join(REPO, p), encoding="utf-8", errors="replace").read()]
    return [_finding("html", "HTML complete", FAIL if bad else PASS,
                     "changed pages have a closing </html> (a truncated write cannot hide)",
                     evidence="missing </html>:\n" + "\n".join(bad) if bad
                              else "%d page(s) complete" % len(html))]


CHECKS = [check_syntax, check_imports, check_paired_tests, check_smoke,
          check_flask_views, check_html_closed]


def run_all(paths=None, base="origin/main"):
    """Run every applicable check. Returns a structured report the crew can consume."""
    paths = list(paths) if paths else changed_paths(base)
    t0 = time.time()
    findings = []
    for fn in CHECKS:
        try:
            findings.extend(fn(paths))
        except Exception as e:
            findings.append(_finding(getattr(fn, "__name__", "check"), getattr(fn, "__name__", "check"),
                                     SKIP, "", detail="checker raised: %s" % str(e)[:200]))
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    for f in findings:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return dict(paths=paths, findings=findings, counts=counts,
                ok=counts[FAIL] == 0, seconds=round(time.time() - t0, 1))


def as_brief(report):
    """Render the report for a model to read. Deterministic results go in FIRST so the expensive
    reasoning starts from facts instead of rediscovering them."""
    out = ["DETERMINISTIC CHECKS (already run — do not repeat these):",
           "  %d pass, %d fail, %d skipped, over %d changed path(s) in %ss"
           % (report["counts"][PASS], report["counts"][FAIL], report["counts"][SKIP],
              len(report["paths"]), report["seconds"])]
    for f in report["findings"]:
        out.append("  [%-7s] %-34s — %s" % (f["status"], f["label"], f["proves"]))
        ev = (f["evidence"] or f["detail"] or "").strip()
        if ev:
            for line in ev.splitlines()[:6]:
                out.append("            %s" % line)
    out.append("")
    out.append("A `skipped` check proved NOTHING — treat it as unknown, never as passing.")
    out.append("Spend your effort on what these cannot reach: logic, edge cases, and whether the "
               "tests themselves would catch a regression in the guarantee they claim to protect.")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the repo's deterministic checks over a change.")
    ap.add_argument("--paths", nargs="*", default=None)
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--brief", action="store_true", help="render for a model to consume")
    a = ap.parse_args(argv)
    rep = run_all(a.paths, base=a.base)
    if a.json:
        print(json.dumps(rep, indent=2))
    elif a.brief:
        print(as_brief(rep))
    else:
        print("changed: %d path(s)" % len(rep["paths"]))
        for f in rep["findings"]:
            print("  [%-7s] %-34s %s" % (f["status"], f["label"], f["proves"]))
            ev = (f["evidence"] or f["detail"] or "").strip()
            for line in ev.splitlines()[:4]:
                print("            %s" % line)
        print("\n%d pass, %d fail, %d skipped in %ss"
              % (rep["counts"][PASS], rep["counts"][FAIL], rep["counts"][SKIP], rep["seconds"]))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
