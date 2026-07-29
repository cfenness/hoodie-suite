#!/usr/bin/env python3
"""release_train.py — safe integration and deploy across concurrent Claude Code sessions.

Several sessions work this repo at once, each in its own worktree on its own branch. That is the
right isolation for EDITING and the wrong one for SHIPPING: nothing reconciles the branches, and
`flyctl deploy` ships the LOCAL TREE, not `main`. Two failure modes follow, both observed here:

  • a deploy run from a worktree (or from a main checkout parked on a feature branch) pushes that
    branch to production and silently reverts whatever another session shipped;
  • a branch's work looks unmerged forever because `git rev-list main..branch` counts COMMITS, and
    a squash-merge leaves the commits behind even though the content landed. You cannot tell
    stranded work from merged work by commit count, so both get ignored.

This tool is DETERMINISTIC on purpose. The rules that keep a deploy safe are mechanical — clean
checkout at origin/main, --remote-only, verify the release actually landed, re-pin the dispatcher
when the registry moved — and mechanical rules belong somewhere they cannot be forgotten or
reasoned around at 2am. Judgment (should these two changes land together? is this conflict
resolution correct?) stays with a human or an agent reading this output.

    python3 tools/release_train.py survey            # read-only truth report
    python3 tools/release_train.py integrate         # merge open PRs → integration branch, test
    python3 tools/release_train.py deploy            # explicit, separate, gated

`survey` touches nothing. `integrate` writes only to its own worktree and branch. `deploy` is the
only command that can affect production, it is never run implicitly by the other two, and it
refuses to run from a dirty or non-origin/main tree.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INTEGRATION_PREFIX = "integration/"
DEPLOY_WT = ".claude/worktrees/_deploy"
# The lock lives in the COMMON git dir, which every worktree shares — so it actually excludes the
# other sessions. A lock inside a worktree would be invisible to them, which is worse than none.
LOCK_NAME = "release-train.lock"
LOCK_STALE_SEC = int(os.environ.get("RT_LOCK_STALE_SEC", "3600"))
# Interpreters to try for the check sweep, best first. The bare system python3 skipped 22 of 38
# suites for want of pyarrow/duckdb/flask — a check that did not run proves nothing, so prefer an
# interpreter that can actually execute them. Override with RT_PYTHON.
_VENVS = [
    os.environ.get("RT_PYTHON", ""),
    os.path.expanduser("~/Desktop/Desktop - Chris\u2019s MacBook Pro/Projects/hoodie-backend/venv/bin/python"),
    os.path.join(ROOT, "venv", "bin", "python"),
    os.path.join(ROOT, ".venv", "bin", "python"),
]


def checker_python():
    """The most capable interpreter available — the one that skips the fewest suites."""
    best, best_score = sys.executable, -1
    for cand in _VENVS:
        if not cand or not os.path.exists(cand):
            continue
        score = 0
        for mod in ("duckdb", "pyarrow", "flask", "numpy"):
            rc, _ = sh([cand, "-c", "import %s" % mod], timeout=60)
            score += 1 if rc == 0 else 0
        if score > best_score:
            best, best_score = cand, score
    return best


# --- plumbing --------------------------------------------------------------------------------
def git(*args, cwd=ROOT, check=False):
    """Run git and return (rc, stdout+stderr). Git puts plenty of useful failure detail on stderr,
    and dropping it made a worktree failure report a harmless tracking notice as its cause."""
    p = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), (p.stderr or "").strip()[:400]))
    out = (p.stdout or "").strip()
    if p.returncode and (p.stderr or "").strip():
        out = (out + "\n" + p.stderr.strip()).strip()
    return p.returncode, out


def sh(cmd, cwd=ROOT, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def common_git_dir():
    _, d = git("rev-parse", "--git-common-dir")
    return d if os.path.isabs(d) else os.path.join(ROOT, d)


def primary_worktree():
    """The MAIN checkout, not whichever worktree happens to be running this.

    Deploy hazards are properties of the primary checkout — that is the one a human is most likely
    to `flyctl deploy` from — so reporting the current worktree instead would point the warning at
    the wrong tree. The common git dir lives inside the primary worktree, so its parent is it.
    """
    d = common_git_dir()
    return os.path.dirname(d) if os.path.basename(d) == ".git" else ROOT


def reset_scratch(path):
    """Make `path` usable for `git worktree add`, whatever state it is in.

    `git worktree remove` only handles a REGISTERED worktree. An orphan directory — left by an
    interrupted run, or by a remove issued against a differently-resolved path — is not registered,
    so remove fails and the directory survives to block the next `worktree add` with
    "already exists". Prune, then remove the tree outright.
    """
    git("worktree", "remove", "--force", path)
    git("worktree", "prune")
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


def scratch(name):
    """Path for a throwaway worktree, always under the PRIMARY checkout.

    Anchoring these to ROOT put them inside whichever worktree invoked the tool, so a run from a
    feature worktree nested them at <worktree>/.claude/worktrees/_integration — invisible to a
    cleanup run from the primary checkout, and left behind as an untracked directory that then
    blocked the next `git worktree add`. Same reasoning as the lock living in the common git dir:
    shared state belongs somewhere all the sessions agree on.
    """
    return os.path.join(primary_worktree(), ".claude", "worktrees", name)


def default_branch():
    rc, out = git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return out.split("/")[-1] if rc == 0 and out else "main"


# --- lock ------------------------------------------------------------------------------------
def lock_path():
    return os.path.join(common_git_dir(), LOCK_NAME)


def acquire_lock(what):
    """Exclude the other sessions. Stale locks (crashed run) expire rather than wedging the repo."""
    p = lock_path()
    if os.path.exists(p):
        try:
            held = json.load(open(p))
        except Exception:                                     # noqa: BLE001
            held = {}
        age = time.time() - float(held.get("at", 0))
        if age < LOCK_STALE_SEC:
            return False, ("held by pid %s (%s) since %s — %ds ago"
                           % (held.get("pid"), held.get("what"),
                              held.get("when"), int(age)))
        os.unlink(p)                                          # stale, reclaim
    with open(p, "w") as f:
        json.dump({"pid": os.getpid(), "what": what, "at": time.time(),
                   "when": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    return True, None


def release_lock():
    try:
        os.unlink(lock_path())
    except OSError:
        pass


# --- the load-bearing question: is this branch's content already in main? ----------------------
def content_in_main(branch, base):
    """Does merging `branch` into `base` change anything?

    This is the question `git rev-list base..branch` is constantly mistaken for. Commit counts
    answer "are these commits reachable", which a squash-merge or a rebase makes permanently wrong.
    Merging into a scratch tree and comparing to the base tree answers what we actually care about:
    is there any CONTENT here that main does not already have.

    Returns "merged" | "ahead" | "conflicts" | "unknown".
    """
    rc, out = git("merge-tree", "--write-tree", base, branch)
    if rc != 0:
        # Non-zero also means a real conflict (git >=2.38 reports conflicts this way).
        return "conflicts" if out else "unknown"
    tree = out.splitlines()[0].strip() if out else ""
    if not tree:
        return "unknown"
    rc2, base_tree = git("rev-parse", "%s^{tree}" % base)
    if rc2 != 0:
        return "unknown"
    return "merged" if tree == base_tree.strip() else "ahead"


# --- survey ----------------------------------------------------------------------------------
def worktrees():
    _, out = git("worktree", "list", "--porcelain")
    items, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                items.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        if k == "worktree":
            cur["path"] = v
        elif k == "branch":
            cur["branch"] = v.split("/")[-1]
        elif k in ("prunable", "locked", "detached", "bare"):
            cur[k] = v or True
    if cur:
        items.append(cur)
    return items


def dirty(path):
    """Uncommitted changes in a worktree — work that a deploy would either ship or lose."""
    if not os.path.isdir(path):
        return None
    rc, out = git("status", "--porcelain", cwd=path)
    return None if rc != 0 else len([l for l in out.splitlines() if l.strip()])


def open_prs():
    rc, out = sh(["gh", "pr", "list", "--state", "open", "--limit", "50", "--json",
                  "number,title,headRefName,mergeable,mergeStateStatus,author,isDraft"])
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except Exception:                                         # noqa: BLE001
        return None


def survey(args):
    base = "origin/" + default_branch()
    git("fetch", "--quiet", "origin")
    print("RELEASE TRAIN — survey  (read-only)\n" + "=" * 78)

    # 1. deploy hazards, first because one of these is how production gets clobbered
    print("\nDEPLOY HAZARDS")
    hazards = []
    primary = primary_worktree()
    rc, head = git("rev-parse", "--abbrev-ref", "HEAD", cwd=primary)
    if head != default_branch():
        hazards.append("the PRIMARY checkout (%s) is on '%s', not %s — `flyctl deploy` from there "
                       "would ship that branch to production"
                       % (os.path.basename(primary), head, default_branch()))
    n = dirty(primary)
    if n:
        hazards.append("the PRIMARY checkout has %d uncommitted change(s) — a deploy would ship them" % n)
    rc, behind = git("rev-list", "--count", "HEAD..%s" % base, cwd=primary)
    if behind and behind != "0":
        hazards.append("the PRIMARY checkout is %s commit(s) BEHIND %s" % (behind, base))
    lp = lock_path()
    if os.path.exists(lp):
        try:
            h = json.load(open(lp))
            hazards.append("a release train is already running: pid %s (%s) since %s"
                           % (h.get("pid"), h.get("what"), h.get("when")))
        except Exception:                                     # noqa: BLE001
            pass
    print("  none found" if not hazards else "")
    for h in hazards:
        print("  !! %s" % h)

    # 2. worktrees
    wts = worktrees()
    prunable = [w for w in wts if w.get("prunable")]
    dirty_wts = []
    for w in wts:
        d = dirty(w["path"])
        if d:
            dirty_wts.append((w, d))
    print("\nWORKTREES — %d registered, %d prunable, %d with uncommitted work"
          % (len(wts), len(prunable), len(dirty_wts)))
    for w, d in sorted(dirty_wts, key=lambda x: -x[1])[:12]:
        print("  %-42s %s  %d uncommitted" % (w.get("branch", "(detached)"),
                                              os.path.basename(w["path"]), d))
    if prunable:
        print("  %d prunable (their directory is gone): `git worktree prune` is safe" % len(prunable))

    # 3. branches — the part commit counts get wrong
    print("\nBRANCHES vs %s  (by CONTENT, not commit reachability)" % base)
    _, refs = git("for-each-ref", "--format=%(refname:short)", "refs/heads/")
    rows = []
    for b in [r for r in refs.splitlines() if r.strip()]:
        rc, cnt = git("rev-list", "--count", "%s..%s" % (base, b))
        if not cnt or cnt == "0":
            continue
        state = content_in_main(b, base)
        _, last = git("log", "-1", "--format=%cs", b)
        rows.append((state, int(cnt), b, last))
    order = {"ahead": 0, "conflicts": 1, "unknown": 2, "merged": 3}
    rows.sort(key=lambda r: (order.get(r[0], 9), -r[1]))
    real = [r for r in rows if r[0] in ("ahead", "conflicts")]
    absorbed = [r for r in rows if r[0] == "merged"]
    print("  %d branch(es) look ahead by commit count; only %d actually carry unmerged CONTENT."
          % (len(rows), len(real)))
    if absorbed:
        print("  %d are squash-merge leftovers — content is already in %s, safe to delete."
              % (len(absorbed), base))
    for state, cnt, b, last in real[:20]:
        flag = "CONFLICTS" if state == "conflicts" else "unmerged "
        print("  %s %-46s %3d commit(s)  last %s" % (flag, b, cnt, last))
    if len(real) > 20:
        print("  … and %d more" % (len(real) - 20))

    # 4. open PRs
    prs = open_prs()
    print("\nOPEN PRs")
    if prs is None:
        print("  (gh unavailable — skipped)")
    elif not prs:
        print("  none")
    else:
        for p in prs:
            print("  #%-5s %-11s %-9s %-34s %s" % (p["number"], p.get("mergeable"),
                  p.get("mergeStateStatus"), p["headRefName"][:34],
                  ("DRAFT " if p.get("isDraft") else "") + p["title"][:40]))

    print("\nNEXT")
    print("  integrate : python3 tools/release_train.py integrate")
    print("  deploy    : python3 tools/release_train.py deploy   (separate and explicit, by design)")
    return 1 if hazards else 0


# --- reconcile -------------------------------------------------------------------------------
# Branch-name shapes that are scaffolding by intent rather than work worth reviving. Matching the
# NAME only proposes a category — the report still shows the diff so a human decides. Nothing here
# ever deletes anything.
_SCAFFOLD = ("debug/", "tmp/", "scratch/", "wip/", "test/")


def branch_facts(b, base):
    """Everything needed to judge one branch in a few seconds."""
    state = content_in_main(b, base)
    _, cnt = git("rev-list", "--count", "%s..%s" % (base, b))
    _, last = git("log", "-1", "--format=%cs", b)
    _, who = git("log", "-1", "--format=%an", b)
    _, subj = git("log", "-1", "--format=%s", b)
    _, files = git("diff", "--name-only", "%s...%s" % (base, b))
    fl = [f for f in files.splitlines() if f.strip()]
    # Do the files it touches still exist upstream? All-missing usually means the area was
    # restructured or removed, which is a strong hint the branch is obsolete rather than pending.
    alive = 0
    for f in fl:
        if git("cat-file", "-e", "%s:%s" % (base, f))[0] == 0:
            alive += 1
    _, pr = sh(["gh", "pr", "list", "--head", b, "--state", "all", "--limit", "1",
                "--json", "number,state"])
    try:
        prinfo = (json.loads(pr) or [None])[0]
    except Exception:                                         # noqa: BLE001
        prinfo = None
    return {"branch": b, "state": state, "commits": int(cnt or 0), "last": last, "author": who,
            "subject": subj, "files": fl, "alive": alive, "pr": prinfo}


def classify(f):
    """A recommendation, always conservative: never propose deleting unrecovered content."""
    if f["state"] == "merged":
        return ("ABSORBED", "content is already in the base — safe to delete")
    if f["pr"] and f["pr"].get("state") == "OPEN":
        return ("IN FLIGHT", "open PR #%s — leave to its session" % f["pr"]["number"])
    if any(f["branch"].startswith(p) for p in _SCAFFOLD):
        return ("SCAFFOLD", "named as throwaway — confirm, then delete")
    if f["files"] and f["alive"] == 0:
        return ("OBSOLETE?", "every file it touches is gone from the base — likely restructured away")
    if f["state"] == "conflicts":
        return ("CONFLICTED", "real content, but drifted — rebase or cherry-pick, do not merge")
    return ("REVIVABLE", "real content, still applies cleanly — could open a PR today")


def reconcile(args):
    base = "origin/" + default_branch()
    git("fetch", "--quiet", "origin")
    _, refs = git("for-each-ref", "--format=%(refname:short)", "refs/heads/")
    branches = [r for r in refs.splitlines() if r.strip() and not r.startswith(INTEGRATION_PREFIX)]

    facts = []
    for b in branches:
        rc, cnt = git("rev-list", "--count", "%s..%s" % (base, b))
        if not cnt or cnt == "0":
            continue
        facts.append(branch_facts(b, base))

    buckets = {}
    for f in facts:
        cat, why = classify(f)
        f["why"] = why
        buckets.setdefault(cat, []).append(f)

    print("RECONCILE — %d branch(es) ahead of %s\n" % (len(facts), base) + "=" * 78)
    order = ["IN FLIGHT", "REVIVABLE", "CONFLICTED", "OBSOLETE?", "SCAFFOLD", "ABSORBED"]
    for cat in order:
        rows = buckets.get(cat, [])
        if not rows:
            continue
        print("\n%s — %d  (%s)" % (cat, len(rows), rows[0]["why"]))
        for f in sorted(rows, key=lambda x: x["last"], reverse=True):
            print("  %-46s %2d commit(s)  %s  %s"
                  % (f["branch"][:46], f["commits"], f["last"], f["author"][:18]))
            print("      %s" % f["subject"][:88])
            if args.files and f["files"]:
                shown = ", ".join(f["files"][:5])
                print("      touches %d file(s): %s%s"
                      % (len(f["files"]), shown[:96], " …" if len(f["files"]) > 5 else ""))

    print("\n" + "=" * 78)
    print("SUGGESTED ORDER OF OPERATIONS")
    if buckets.get("ABSORBED"):
        print("  1. delete the %d ABSORBED branches — their content is provably in %s:"
              % (len(buckets["ABSORBED"]), base))
        print("     git branch -D %s"
              % " ".join(f["branch"] for f in buckets["ABSORBED"][:6])
              + (" …" if len(buckets["ABSORBED"]) > 6 else ""))
    if buckets.get("SCAFFOLD"):
        print("  2. confirm and delete the %d SCAFFOLD branches" % len(buckets["SCAFFOLD"]))
    if buckets.get("REVIVABLE"):
        print("  3. the %d REVIVABLE branches still apply cleanly — decide keep or drop while that"
              % len(buckets["REVIVABLE"]))
        print("     is still true; every day of base drift turns one of these into a CONFLICTED one")
    if buckets.get("CONFLICTED"):
        print("  4. the %d CONFLICTED ones need a human per branch. Cheapest triage is to read the"
              % len(buckets["CONFLICTED"]))
        print("     subject line and decide whether the work still matters BEFORE resolving anything")
    print("\n  Nothing here has been changed. This command only reads.")
    return 0


# --- integrate -------------------------------------------------------------------------------
def checks(cwd, py=None):
    """Everything that can be proven without credentials. A suite that can't run for want of an
    optional dep is reported as SKIP, never counted as a pass — a check that quietly didn't run is
    the same lie as a guard that quietly degrades."""
    py = py or checker_python()
    results = []
    rc, out = sh([py, "tools/smoke_check.py"], cwd=cwd, timeout=300)
    results.append(("smoke_check", "PASS" if rc == 0 else "FAIL", out.splitlines()[-1] if out else ""))
    tests = sorted(f for f in os.listdir(os.path.join(cwd, "unifyd")) if f.endswith("_test.py"))
    for t in tests:
        try:
            rc, out = sh([py, os.path.join("unifyd", t)], cwd=cwd, timeout=600)
        except subprocess.TimeoutExpired:
            results.append((t, "TIMEOUT", ""))
            continue
        tail = out.splitlines()[-1] if out else ""
        if "ModuleNotFoundError" in out or "skipping" in out.lower():
            results.append((t, "SKIP", tail))
        else:
            results.append((t, "PASS" if rc == 0 else "FAIL", tail))
    return results


def attribute(base, prs, names, py):
    """Which PR first breaks each failing check.

    `integrate` merges in sequence, so "the change set broke it" is true but not actionable — the
    owner needs to know WHICH pull request, and whether it was already broken on that PR's own
    branch (their defect) rather than only after merging with a moved base (a rebase problem).
    Only the already-failing checks are re-run, so this costs a few seconds, not another full sweep.
    """
    out = {}
    probe = scratch("_attribute")
    for p in prs:
        if os.path.isdir(probe):
            git("worktree", "remove", "--force", probe)
        if git("worktree", "add", "--detach", probe, base)[0] != 0:
            break
        merged_ok = git("merge", "--no-edit", "origin/" + p["headRefName"], cwd=probe)[0] == 0
        for n in list(names):
            if n in out or not merged_ok:
                continue
            path = "tools/smoke_check.py" if n == "smoke_check" else os.path.join("unifyd", n)
            if not os.path.exists(os.path.join(probe, path)):
                continue
            try:
                rc, _ = sh([py, path], cwd=probe, timeout=600)
            except subprocess.TimeoutExpired:
                rc = 1
            if rc != 0:
                # Also broken on the PR's own tip? Then it is theirs, not a merge artefact.
                tip = scratch("_tip")
                own = None
                reset_scratch(tip)
                if git("worktree", "add", "--detach", tip, "origin/" + p["headRefName"])[0] == 0:
                    if os.path.exists(os.path.join(tip, path)):
                        try:
                            rc2, _ = sh([py, path], cwd=tip, timeout=600)
                            own = rc2 != 0
                        except subprocess.TimeoutExpired:
                            own = True
                    git("worktree", "remove", "--force", tip)
                out[n] = (p, own)
    if os.path.isdir(probe):
        git("worktree", "remove", "--force", probe)
    return out


def baseline_failures(base, names, py):
    """Which of these checks ALSO fail on the base branch?

    Without this the report cannot distinguish "your merge broke it" from "it was already broken",
    and those need completely different responses. Measured live: snowflake_load_test failed on an
    integrated tree, and the useful fact was that it already failed on the contributing PR's own
    branch — so it was never an integration problem at all.
    """
    if not names:
        return set()
    probe = scratch("_baseline")
    reset_scratch(probe)
    rc, _ = git("worktree", "add", "--detach", probe, base)
    if rc != 0:
        return set()
    bad = set()
    try:
        for n in names:
            path = "tools/smoke_check.py" if n == "smoke_check" else os.path.join("unifyd", n)
            if not os.path.exists(os.path.join(probe, path)):
                continue                      # the check itself is new in this change set
            try:
                rc, _ = sh([py, path], cwd=probe, timeout=600)
            except subprocess.TimeoutExpired:
                rc = 1
            if rc != 0:
                bad.add(n)
    finally:
        git("worktree", "remove", "--force", probe)
    return bad


def integrate(args):
    base = "origin/" + default_branch()
    got, why = acquire_lock("integrate")
    if not got:
        print("!! another release train is running: %s" % why)
        return 2
    try:
        git("fetch", "--quiet", "origin")
        prs = open_prs()
        if prs is None:
            print("!! gh unavailable — cannot enumerate PRs")
            return 2
        prs = [p for p in prs if not p.get("isDraft")]
        if args.only:
            want = {int(x) for x in args.only.split(",")}
            prs = [p for p in prs if p["number"] in want]
        if not prs:
            print("nothing to integrate")
            return 0
        prs.sort(key=lambda p: p["number"])

        # Integration branches are throwaway by construction — reap the previous ones so they
        # don't silently become 50 more branches for the next survey to explain.
        _, old = git("for-each-ref", "--format=%(refname:short)", "refs/heads/" + INTEGRATION_PREFIX + "*")
        for b in [x for x in old.splitlines() if x.strip()]:
            git("branch", "-D", b)

        branch = INTEGRATION_PREFIX + time.strftime("%Y%m%d-%H%M%S")
        wt = scratch("_integration")
        reset_scratch(wt)
        rc, out = git("worktree", "add", "-b", branch, wt, base)
        if rc != 0:
            print("!! could not create integration worktree: %s" % out)
            return 2
        print("integration branch %s (from %s)\n" % (branch, base))

        merged, conflicted = [], None
        for p in prs:
            ref = "origin/" + p["headRefName"]
            rc, out = git("merge", "--no-ff", "-m", "integrate #%d %s" % (p["number"], p["title"]),
                          ref, cwd=wt)
            if rc != 0:
                _, files = git("diff", "--name-only", "--diff-filter=U", cwd=wt)
                conflicted = (p, [f for f in files.splitlines() if f.strip()])
                break
            merged.append(p)
            print("  merged  #%-5s %s" % (p["number"], p["title"][:60]))

        if conflicted:
            p, files = conflicted
            print("\n!! CONFLICT integrating #%d (%s)" % (p["number"], p["headRefName"]))
            print("   %d file(s):" % len(files))
            for f in files:
                # Who else touched this file recently — the other session you need to talk to.
                _, who = git("log", "-3", "--format=%an %cs %s", base, "--", f, cwd=wt)
                print("     %s" % f)
                for line in who.splitlines()[:3]:
                    print("        last on %s: %s" % (default_branch(), line[:88]))
            print("\n   The merge is LEFT IN PLACE at %s so it can be resolved:" % wt)
            print("     cd '%s' && git status" % wt)
            print("   Nothing has touched %s. Resolve, commit, then re-run integrate." % default_branch())
            print("   To abandon: git -C '%s' merge --abort" % wt)
            return 3

        py = checker_python()
        vname = os.path.basename(os.path.dirname(os.path.dirname(py))) or "system"
        print("\nchecks on the integrated tree  (python: %s)" % vname)
        res = checks(wt, py)
        width = max(len(r[0]) for r in res)
        bad = [r for r in res if r[1] in ("FAIL", "TIMEOUT")]
        skipped = [r for r in res if r[1] == "SKIP"]
        for name, status, tail in res:
            if status != "PASS" or args.verbose:
                print("  %-8s %-*s %s" % (status, width, name, tail[:60]))
        print("  %d passed, %d failed, %d skipped"
              % (len(res) - len(bad) - len(skipped), len(bad), len(skipped)))

        # Pre-existing or introduced? Different problem, different owner.
        pre = baseline_failures(base, [b[0] for b in bad], py) if bad else set()
        introduced = [b for b in bad if b[0] not in pre]

        print("\nRESULT")
        if bad:
            for name, _st, tail in bad:
                origin_note = ("ALREADY FAILS on %s — pre-existing, not this change set" % base
                               if name in pre else "INTRODUCED by this change set")
                print("  %-28s %s" % (name, origin_note))
            if introduced:
                blame = attribute(base, merged, [b[0] for b in introduced], py)
                for name, _st, _t in introduced:
                    hit = blame.get(name)
                    if not hit:
                        continue
                    p, own = hit
                    where = ("fails on that branch's own tip too — the PR's own defect, not a "
                             "merge artefact" if own else
                             "passes on that branch alone — it only breaks against the current "
                             "base, so that branch needs a rebase")
                    print("  %-28s first broken by #%d (%s)\n%s%s"
                          % (name, p["number"], p["headRefName"], " " * 32, where))
                print("\n  NOT READY — %d check(s) introduced by this integration." % len(introduced))
                print("  The integration branch is kept at %s for inspection." % wt)
                return 1
            print("\n  All %d failure(s) are pre-existing on %s — this integration introduces none."
                  % (len(bad), base))
            print("  They still need owners, but they do not block this train.")
        print("  %d PR(s) integrate cleanly and all runnable checks pass." % len(merged))
        print("  Branch: %s" % branch)
        print("  This has NOT touched %s and has NOT deployed." % default_branch())
        print("  To ship: merge the PRs normally, then `python3 tools/release_train.py deploy`.")
        return 0
    finally:
        release_lock()


# --- fly.toml [[vm]] drift -------------------------------------------------------------------
# `flyctl deploy` re-applies the [[vm]] block, so a `flyctl machine update` scale-up performed
# outside git is silently reverted by the next unrelated deploy. Reported by the Hoodie Relations
# session with a real case: a machine raised to shared-cpu-4x/8192MB to stop `next build` being
# OOM-killed was put back to shared-cpu-2x/4096MB by a deploy shipping an entrypoint change. The
# deploy reported success and said nothing. The symptom was "server unreachable", so the time went
# on flyctl rate-limits and WireGuard before `free -m` reading 3916MB gave it away.
#
# On a multi-machine app a silent downsize is a performance regression. On a single machine with
# --ha=false it is an outage. Same blind spot as the file clobber: releases green, runtime wrong.
_SIZE_CPUS = {"shared-cpu-1x": (1, "shared"), "shared-cpu-2x": (2, "shared"),
              "shared-cpu-4x": (4, "shared"), "shared-cpu-8x": (8, "shared"),
              "performance-1x": (1, "performance"), "performance-2x": (2, "performance"),
              "performance-4x": (4, "performance"), "performance-8x": (8, "performance"),
              "performance-16x": (16, "performance")}


def mem_mb(v):
    """fly.toml memory -> MB. THE UNIT MISMATCH IS THE WHOLE TRAP: fly.toml writes `8gb`, the CLI
    and machine listing report `8192`. Compare those as strings and the check cries drift on every
    single deploy, which means it gets ignored inside a day and is worse than not existing."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    t = str(v).strip().lower().replace(" ", "")
    try:
        if t.endswith("gb"):
            return int(float(t[:-2]) * 1024)
        if t.endswith("mb"):
            return int(float(t[:-2]))
        if t.endswith("g"):
            return int(float(t[:-1]) * 1024)
        return int(float(t))                      # bare number = MB, as the CLI reports it
    except ValueError:
        return None


def _vm_block_fallback(path, log=print):
    """Parse just the [[vm]] block without tomllib.

    tomllib is stdlib only from 3.11, and this repo's venv is 3.9 — so on the interpreter the check
    sweep actually uses, the tomllib path raised, declared_vm returned None, and the drift check
    SILENTLY DID NOTHING. A guard that quietly stops guarding is the failure mode this codebase has
    a standing rule against, so it now degrades to a small hand parser instead of to nothing.
    """
    vm, in_block = {}, False
    try:
        with open(path, "r") as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                if line.startswith("["):
                    in_block = line.replace(" ", "") in ("[[vm]]", "[vm]")
                    continue
                if in_block and "=" in line:
                    k, _, v = line.partition("=")
                    vm[k.strip()] = v.strip().strip("'\"")
    except OSError as e:
        log("  [vm-drift] cannot read %s: %s" % (path, str(e)[:60]))
        return None
    return vm or None


def declared_vm(wt, log=print):
    """The [[vm]] the deploy would apply. None when fly.toml declares none."""
    path = os.path.join(wt, "fly.toml")
    if not os.path.exists(path):
        return None
    vm = None
    try:
        import tomllib
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
        vms = cfg.get("vm")
        vm = (vms[0] if isinstance(vms, list) and vms else vms) or None
    except ImportError:
        vm = _vm_block_fallback(path, log)        # python < 3.11
    except Exception as e:                        # noqa: BLE001 — malformed toml
        log("  [vm-drift] fly.toml did not parse (%s) — falling back" % str(e)[:60])
        vm = _vm_block_fallback(path, log)
    if not isinstance(vm, dict):
        return None
    size = str(vm.get("size", "")).strip().lower()
    cpus, kind = _SIZE_CPUS.get(size, (vm.get("cpus"), vm.get("cpu_kind")))
    return {"size": size, "cpus": cpus, "cpu_kind": kind,
            "memory_mb": mem_mb(vm.get("memory") or vm.get("memory_mb"))}


def vm_drift(app, wt, log=print):
    """(drift, what_was_inspected) for machines this deploy would RESIZE.

    Returns the inspection summary as well as the findings, because SILENCE ON A PASS IS
    INDISTINGUISHABLE FROM NOT HAVING RUN. This check has already been silently inert once — the
    tomllib import failed on python 3.9 and it reported "no drift" for every deploy. A check that
    cannot tell "I ran and found nothing" from "I didn't run" converts an open question into false
    confidence, so it now states what it compared.

    Compared against the RUNNING machines, not the previous fly.toml — the drift is created outside
    git (`flyctl machine update`), so git cannot see it. Only machines in a process group are
    considered: those are what a deploy touches. The dispatcher deliberately has none, which is why
    it needs a separate re-pin.
    """
    want = declared_vm(wt, log=log)
    if not want:
        return [], "NOT CHECKED — fly.toml declares no [[vm]] block"
    if not want.get("memory_mb"):
        return [], "NOT CHECKED — could not read a memory value from [[vm]]"
    fly = os.path.expanduser("~/.fly/bin/flyctl")
    fly = fly if os.path.exists(fly) else "flyctl"
    rc, out = sh([fly, "machines", "list", "-a", app, "--json"], timeout=180)
    if rc != 0:
        return [], "NOT CHECKED — could not list machines for %s" % app
    try:
        machines = json.loads(out)
    except Exception:                             # noqa: BLE001
        return [], "NOT CHECKED — machine list did not parse"
    drift, inspected = [], []
    for m in machines:
        cfg = m.get("config", {}) or {}
        if not (cfg.get("metadata", {}) or {}).get("fly_process_group"):
            continue                              # not a deploy target
        if m.get("state") != "started":
            continue
        g = cfg.get("guest", {}) or {}
        inspected.append("%s %sMB/%scpu vs declared %sMB/%scpu"
                         % (m["id"], g.get("memory_mb"), g.get("cpus"),
                            want["memory_mb"], want["cpus"]))
        if want["memory_mb"] and g.get("memory_mb") and g["memory_mb"] != want["memory_mb"]:
            drift.append((m["id"], "memory_mb", g["memory_mb"], want["memory_mb"]))
        if want["cpus"] and g.get("cpus") and g["cpus"] != want["cpus"]:
            drift.append((m["id"], "cpus", g["cpus"], want["cpus"]))
    if not inspected:
        return [], "NOT CHECKED — no started process-group machines to compare"
    return drift, "compared %d machine(s): %s" % (len(inspected), "; ".join(inspected))


# --- deploy ----------------------------------------------------------------------------------
def deploy(args):
    """The only command that can affect production. Refuses anything but a clean origin/main."""
    base_name = default_branch()
    base = "origin/" + base_name
    got, why = acquire_lock("deploy")
    if not got:
        print("!! another release train is running: %s" % why)
        return 2
    try:
        git("fetch", "--quiet", "origin")
        wt = scratch("_deploy")
        reset_scratch(wt)
        rc, out = git("worktree", "add", "--detach", wt, base)
        if rc != 0:
            print("!! could not create the deploy checkout: %s" % out)
            return 2

        # Prove what we are about to ship, rather than trusting the caller's cwd.
        _, head = git("rev-parse", "HEAD", cwd=wt)
        _, want = git("rev-parse", base)
        if head != want:
            print("!! deploy checkout is at %s, expected %s — refusing" % (head[:8], want[:8]))
            return 2
        if dirty(wt):
            print("!! deploy checkout is not clean — refusing")
            return 2
        _, subject = git("log", "-1", "--format=%h %an %cs %s", cwd=wt)
        print("deploying %s @ %s\n  %s" % (base, head[:8], subject))

        app = os.environ.get("FLY_APP", "hoodie-suite")
        drift, inspected = vm_drift(app, wt)
        # Always say what was inspected. A guard that only speaks up when it finds something is
        # indistinguishable from a guard that isn't running.
        print("\n  [[vm]] check: %s" % inspected)
        shrink = [d for d in drift if d[3] < d[2]]
        if not drift and not inspected.startswith("NOT CHECKED"):
            print("  [[vm]] check: no resize — the deploy leaves machine sizing unchanged")
        if drift:
            print("\n  fly.toml [[vm]] would RESIZE running machines:")
            for mid, field, running, declared in drift:
                arrow = "SHRINK" if declared < running else "grow"
                print("    %-6s %s  %s: %s -> %s" % (arrow, mid, field, running, declared))
        if shrink and not args.allow_resize:
            print("\n  REFUSING: this deploy would SHRINK a running machine.")
            print("  `flyctl deploy` re-applies [[vm]], so a scale-up done with `flyctl machine")
            print("  update` is reverted silently — on a single-machine app that is an outage, and")
            print("  nothing in `flyctl releases` will show it.")
            print("  Either pin the intended size in fly.toml, or re-run with --allow-resize.")
            return 2

        # Did the registry move? If so the hourly dispatcher must be re-pinned afterwards or it
        # keeps running a stale image and never dispatches the new source.
        _, changed = git("diff", "--name-only", "HEAD~5..HEAD", cwd=wt)
        registry_moved = any("source_registry.py" in l for l in changed.splitlines())

        if args.dry_run:
            print("\n--dry-run: would run `flyctl deploy --remote-only --ha=false` in %s" % wt)
            if registry_moved:
                print("--dry-run: source_registry.py moved → would then run tools/repin_dispatcher.sh")
            return 0

        fly = os.path.expanduser("~/.fly/bin/flyctl")
        fly = fly if os.path.exists(fly) else "flyctl"
        rc, out = sh([fly, "deploy", "--remote-only", "--ha=false"], cwd=wt, timeout=1800)
        print(out[-3000:])
        if rc != 0:
            print("\n!! deploy FAILED")
            return 1

        rc, rel = sh([fly, "releases", "-a", os.environ.get("FLY_APP", "hoodie-suite")], timeout=120)
        print("\nreleases:\n%s" % "\n".join(rel.splitlines()[:4]))
        if registry_moved:
            print("\nsource_registry.py moved in this range — re-pinning the dispatcher")
            rc, out = sh(["bash", os.path.join("tools", "repin_dispatcher.sh")], cwd=wt, timeout=600)
            print(out[-1500:])
        return 0
    finally:
        release_lock()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("survey", help="read-only truth report across worktrees, branches, PRs")
    s.set_defaults(fn=survey)
    i = sub.add_parser("integrate", help="merge open PRs onto an integration branch and test")
    i.add_argument("--only", help="comma-separated PR numbers")
    i.add_argument("--verbose", action="store_true")
    i.set_defaults(fn=integrate)
    r = sub.add_parser("reconcile", help="classify every branch ahead of base and recommend an action")
    r.add_argument("--files", action="store_true", help="also list the files each branch touches")
    r.set_defaults(fn=reconcile)
    d = sub.add_parser("deploy", help="deploy origin/<default> from a clean checkout")
    d.add_argument("--dry-run", action="store_true")
    d.add_argument("--allow-resize", action="store_true",
                   help="proceed even if fly.toml would shrink a running machine")
    d.set_defaults(fn=deploy)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
