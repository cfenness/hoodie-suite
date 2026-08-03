"""Offline test for release_train's staleness guard: python tools/release_train_staleness_test.py

The guard exists because a deploy tool is only as good as the guards IN it. Running a copy behind
origin/main deploys with older protections and says nothing — that is not hypothetical: the
drift-baseline fallback was merged, deployed, then skipped on the very next deploy because the
invocation came from a working directory 36 commits back, with every release still reading
`complete`.

The property under test is the DISTINCTION: strictly-behind refuses, everything else proceeds.
A guard that also blocked someone editing release_train itself would just get disabled. No network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release_train as rt

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if not cond:
        FAILED.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — %s" % detail) if not cond else ""))


def with_git(responses):
    """Stub rt.git with a {(args tuple prefix): (rc, out)} table."""
    def fake(*args, **kw):
        for prefix, resp in responses.items():
            if args[:len(prefix)] == prefix:
                return resp
        return (1, "")
    rt.git = fake


real_git = rt.git
MINE = open(os.path.abspath(rt.__file__), "r", encoding="utf-8").read()
REL = os.path.relpath(os.path.abspath(rt.__file__), rt.ROOT).replace(os.sep, "/")
try:
    print("identical file -> current")
    with_git({("fetch",): (0, ""), ("show",): (0, MINE)})
    state, _ = rt.tool_staleness()
    check("byte-identical tool reads as current", state == "current", state)

    print("\nstrictly behind -> behind (the case that caused the miss)")
    with_git({("fetch",): (0, ""), ("show",): (0, MINE + "\n# newer\n"),
              ("rev-list", "--count"): (0, "36"), ("merge-base",): (0, "")})
    state, detail = rt.tool_staleness()
    check("36 commits behind + differing tool reads as behind", state == "behind", state)
    check("the detail names the commit count", "36 commit" in detail, detail)

    print("\nup to date but locally edited -> diverged (must NOT block)")
    with_git({("fetch",): (0, ""), ("show",): (0, MINE + "\n# local edit\n"),
              ("rev-list", "--count"): (0, "0"), ("merge-base",): (0, "")})
    state, _ = rt.tool_staleness()
    check("0 commits behind with local edits is not stale", state == "diverged", state)

    print("\non a feature branch ahead of main -> diverged (must NOT block)")
    with_git({("fetch",): (0, ""), ("show",): (0, MINE + "\n# theirs\n"),
              ("rev-list", "--count"): (0, "0"), ("merge-base",): (1, "")})
    state, _ = rt.tool_staleness()
    check("a branch not behind main is not stale", state == "diverged", state)

    print("\nunresolvable -> unknown, and unknown never blocks")
    with_git({("fetch",): (1, "network down")})
    state, _ = rt.tool_staleness()
    check("a failed fetch reads as unknown", state == "unknown", state)
    check("unknown does not block a deploy", rt.enforce_current_tool("deploy") is True)

    print("\nenforcement")
    with_git({("fetch",): (0, ""), ("show",): (0, MINE + "\n# newer\n"),
              ("rev-list", "--count"): (0, "36"), ("merge-base",): (0, "")})
    os.environ.pop(rt.STALE_OK_ENV, None)
    check("behind BLOCKS a deploy", rt.enforce_current_tool("deploy") is False)
    os.environ[rt.STALE_OK_ENV] = "1"
    check("the documented override lets it through",
          rt.enforce_current_tool("deploy") is True)
    os.environ.pop(rt.STALE_OK_ENV, None)
finally:
    rt.git = real_git

print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
sys.exit(1 if FAILED else 0)
