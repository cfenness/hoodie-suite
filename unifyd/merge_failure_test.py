"""merge_failure() must tell a content conflict from any other merge failure.

`integrate` used to report EVERY non-zero merge as "CONFLICT" and then list the unmerged paths —
an empty list whenever the failure was not a conflict. It printed "CONFLICT ... 0 file(s)" and sent
the reader hunting for a conflict that did not exist; the real cause was stale worktree state.
Zero conflicting files is not a conflict. Throwaway repos, no network.
"""
import importlib.util, os, subprocess, sys, tempfile
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("rt", os.path.join(_ROOT, "tools", "release_train.py"))
rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)

passed = failed = 0
def ok(n, c):
    global passed, failed
    if c: passed += 1; print("  ok   %s" % n)
    else: failed += 1; print("  FAIL %s" % n)

def sh(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True)

# a throwaway repo with a genuine content conflict
r = tempfile.mkdtemp()
sh("git","init","-q",r); sh("git","-C",r,"config","user.email","t@t"); sh("git","-C",r,"config","user.name","t")
open(os.path.join(r,"f.txt"),"w").write("base\n")
sh("git","-C",r,"add","."); sh("git","-C",r,"commit","-qm","base","--no-verify")
sh("git","-C",r,"checkout","-qb","other")
open(os.path.join(r,"f.txt"),"w").write("theirs\n")
sh("git","-C",r,"commit","-qam","theirs","--no-verify")
sh("git","-C",r,"checkout","-q","master") if sh("git","-C",r,"rev-parse","--verify","master").returncode==0 else sh("git","-C",r,"checkout","-q","main")
open(os.path.join(r,"f.txt"),"w").write("ours\n")
sh("git","-C",r,"commit","-qam","ours","--no-verify")
res = sh("git","-C",r,"merge","--no-ff","-m","m","other")
out = (res.stdout or "") + (res.stderr or "")
print("A REAL CONTENT CONFLICT")
ok("git merge failed (rc=%d)" % res.returncode, res.returncode != 0)
kind, detail = rt.merge_failure(r, out)
ok("classified as 'conflict' (got %r)" % kind, kind == "conflict")
ok("...and names the conflicting file (%r)" % detail, "f.txt" in detail)

print("\nA NON-CONFLICT FAILURE (unknown ref) — must NOT be called a conflict")
sh("git","-C",r,"merge","--abort")
res = sh("git","-C",r,"merge","--no-ff","-m","m","origin/does-not-exist")
out = (res.stdout or "") + (res.stderr or "")
ok("git merge failed (rc=%d)" % res.returncode, res.returncode != 0)
kind, detail = rt.merge_failure(r, out)
ok("classified as 'failed', NOT 'conflict' (got %r)" % kind, kind == "failed")
ok("...and carries git's own error (%r)" % detail[:44], len(detail) > 0 and "conflict" not in detail.lower())

print("\nA DIRTY TREE — also not a conflict")
open(os.path.join(r,"f.txt"),"w").write("uncommitted local edit\n")
res = sh("git","-C",r,"merge","--no-ff","-m","m","other")
out = (res.stdout or "") + (res.stderr or "")
if res.returncode != 0:
    kind, detail = rt.merge_failure(r, out)
    ok("classified as 'failed' (got %r)" % kind, kind == "failed")
else:
    ok("(git allowed it; skipped)", True)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
