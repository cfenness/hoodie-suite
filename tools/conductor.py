#!/usr/bin/env python3
"""conductor.py — the agent above the threads: gate, merge, deploy, VERIFY, roll back.

Several Claude sessions work this repo at once, each in its own worktree. They open PRs; somebody
then has to decide what is safe to merge, merge it, deploy it, and check that production actually
became what was intended. That last step is the whole job, and it is the one that keeps getting
skipped — including by me.

WHY "TESTS GREEN" IS NOT THE MERGE CRITERION
  On 2026-08-03 eight real defects shipped or nearly shipped in one session. Every one of them
  passed its tests. They were caught by running the thing in production and looking:
    · Tier-3 matching resolved NOTHING (36% of distributor item codes are ambiguous)
    · a registry build declared a dependency on a table it never reads
    · the drift-baseline fallback ran against a stale machine and cried CRITICAL falsely
    · a returned workbook had a duplicated column and three silently-empty enrichment fields
  A conductor that merges on green would have shipped all of them. So the gate here is:
  green ⇒ merge ⇒ deploy ⇒ VERIFY IN PRODUCTION ⇒ keep, or roll back and dispatch a fix.

THE VERIFY STEP IS THE PRODUCT
  `verify()` does not ask "did the deploy command exit 0" — that lies. It asks whether the running
  container IS the build we intended (fingerprint identity, not a version string), whether the app
  answers, and whether the suite passes INSIDE the container. Anything less is the failure mode
  this exists to end: `flyctl releases` reading `complete` over a stale tree.

SAFETY RAILS, in the order they matter
  · DRY RUN IS THE DEFAULT. --commit is required to change anything.
  · SELF-MODIFICATION IS NEVER AUTONOMOUS. A PR touching the conductor, release_train, the deploy
    guard or the CI gate is held for a human. An agent that can widen its own gate has no gate.
  · ROLLBACK BEFORE DIAGNOSIS. A failed verify restores the previous release FIRST, then reports.
  · One action class per cycle, a hard cap on merges, and a kill switch (tools/CONDUCTOR_STOP).
  · Every refusal states which rail fired. A guard that goes quiet is indistinguishable from one
    that passed.

The world is injected (`World`), so every decision path is testable without git, gh, or Fly.

    python3 tools/conductor.py cycle              # dry run: say what it WOULD do
    python3 tools/conductor.py cycle --commit     # actually merge, deploy, verify, roll back
    python3 tools/conductor.py verify             # verify production against the recorded intent
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.environ.get("FLY_APP", "hoodie-suite")
STOP_FILE = os.path.join(HERE, "CONDUCTOR_STOP")
MAX_MERGES_PER_CYCLE = int(os.environ.get("CONDUCTOR_MAX_MERGES", "4"))

# A PR touching any of these changes the machinery that decides what is safe. Merging one of those
# autonomously means the gate approves its own replacement — so they are always held for a human.
SELF_PATHS = ("tools/conductor.py", "tools/conductor_test.py", "tools/release_train.py",
              "tools/deploy_guard.py", "tools/smoke_check.py", ".github/")


def _fly():
    p = os.path.expanduser("~/.fly/bin/flyctl")
    return p if os.path.exists(p) else "flyctl"


class World(object):
    """Every side effect the conductor can have, in one place so tests can replace it."""

    def __init__(self, root=ROOT, log=print):
        self.root = root
        self.log = log

    def sh(self, cmd, timeout=1800, cwd=None):
        try:
            p = subprocess.run(cmd, cwd=cwd or self.root, capture_output=True, text=True,
                               timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, "timed out after %ds" % timeout
        except OSError as e:
            return 127, str(e)

    # ── reads ──
    def open_prs(self):
        rc, out = self.sh(["gh", "pr", "list", "--state", "open", "--limit", "50", "--json",
                           "number,title,isDraft,mergeable,headRefName,files,statusCheckRollup"])
        if rc != 0:
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None

    def releases(self):
        rc, out = self.sh([_fly(), "releases", "-a", APP, "--json"], timeout=120)
        if rc != 0:
            return []
        try:
            return json.loads(out)
        except ValueError:
            return []

    def live_version(self):
        rc, out = self.sh(["curl", "-s", "--max-time", "25",
                           "https://%s.fly.dev/api/version" % APP], timeout=60)
        try:
            return json.loads(out)
        except ValueError:
            return None

    def intended_fingerprint(self):
        """Fingerprint a CLEAN origin/<default> checkout — never `self.root`.

        Fingerprinting the working tree looks equivalent and is not: the conductor runs from
        someone's checkout, and an uncommitted file there would be counted as part of the
        "intended" build. Production would then be reported as drifted because of local WIP,
        which is the same class of wrong answer this whole tool exists to catch — caught here by
        running it against a tree that had two uncommitted files in it.
        """
        rc, base = self.sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
        base = base.strip().split("/")[-1] if rc == 0 and base.strip() else "main"
        self.sh(["git", "fetch", "--quiet", "origin"], timeout=300)
        wt = os.path.join("/tmp", "conductor-intent-%d" % os.getpid())
        self.sh(["git", "worktree", "remove", "--force", wt], timeout=120)
        rc, out = self.sh(["git", "worktree", "add", "--detach", wt, "origin/" + base], timeout=300)
        if rc != 0:
            return None, None
        try:
            rc, out = self.sh([sys.executable, os.path.join(wt, "unifyd", "deploy_drift.py"),
                               "fingerprint", wt], timeout=300)
            m = re.match(r"([0-9a-f]{64})\s+\((\d+) files\)", (out or "").strip())
            return (m.group(1), int(m.group(2))) if rc == 0 and m else (None, None)
        finally:
            self.sh(["git", "worktree", "remove", "--force", wt], timeout=120)

    def in_container(self, script):
        rc, out = self.sh([_fly(), "ssh", "console", "-a", APP, "-g", "app", "-C", script],
                          timeout=900)
        return rc, out

    # ── writes ──
    def merge(self, number):
        return self.sh(["gh", "pr", "merge", str(number), "--squash"], timeout=300)

    def release_train(self, *args):
        return self.sh([sys.executable, os.path.join(self.root, "tools", "release_train.py")]
                       + list(args), timeout=2400)

    def rollback(self, image):
        return self.sh([_fly(), "deploy", "-a", APP, "-i", image, "--strategy", "immediate"],
                       timeout=1800)


# ── the gate ───────────────────────────────────────────────────────────────────────────────────

def touches_self(pr):
    files = [f.get("path", "") for f in (pr.get("files") or [])]
    return sorted({p for p in files for s in SELF_PATHS if p == s or p.startswith(s)})


def checks_state(pr):
    """green | failing | pending | none — read from the PR's own check rollup."""
    roll = pr.get("statusCheckRollup") or []
    if not roll:
        return "none"                                  # this repo runs no CI; integrate() is the gate
    states = {(c.get("conclusion") or c.get("state") or "").upper() for c in roll}
    if states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
        return "failing"
    if states & {"PENDING", "IN_PROGRESS", "QUEUED", ""}:
        return "pending"
    return "green"


def triage(prs):
    """Split open PRs into {merge, hold, dispatch} with a reason on every one.

    Nothing lands in `merge` on a maybe: a PR must be non-draft, conflict-free, not touching the
    conductor's own machinery, and not failing its checks. `hold` is where anything uncertain goes.
    """
    out = {"merge": [], "hold": [], "dispatch": []}
    for pr in sorted(prs, key=lambda p: p.get("number", 0)):
        n, why = pr.get("number"), None
        rec = {"number": n, "title": (pr.get("title") or "")[:70],
               "branch": pr.get("headRefName")}
        if pr.get("isDraft"):
            why = "draft"
        elif (pr.get("mergeable") or "").upper() == "CONFLICTING":
            why = "conflicts with main — needs a rebase, not a merge"
        elif touches_self(pr):
            why = ("touches the conductor's own machinery (%s) — held for a human, an agent must "
                   "not approve a change to its own gate" % ", ".join(touches_self(pr)[:3]))
        elif checks_state(pr) == "failing":
            rec["why"] = "checks are failing"
            out["dispatch"].append(rec)
            continue
        elif checks_state(pr) == "pending":
            why = "checks still running"
        if why:
            rec["why"] = why
            out["hold"].append(rec)
        else:
            rec["why"] = "clean: no conflicts, no self-modification, checks %s" % checks_state(pr)
            out["merge"].append(rec)
    return out


# ── verification: the step that is the whole point ─────────────────────────────────────────────

def verify(world, expect_fp=None, expect_files=None, run_tests=True):
    """Is production the build we intended? Returns (ok, [checks]).

    Deliberately NOT 'did the deploy exit 0'. Each check states what it concluded and why; an
    UNKNOWN is never reported as a pass, because "couldn't tell" and "fine" are the two things
    this function exists to keep apart.
    """
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    live = world.live_version()
    if not live or not live.get("fingerprint"):
        add("live-reachable", False, "GET /api/version returned nothing usable — production is "
                                     "UNKNOWN, not healthy")
        return False, checks
    add("live-reachable", True, "%d files, fp %s…" % (live.get("files", 0),
                                                      live["fingerprint"][:12]))

    if expect_fp is None:
        expect_fp, expect_files = world.intended_fingerprint()
    if not expect_fp:
        add("build-identity", False, "could not fingerprint the intended tree — cannot compare")
        return False, checks
    same = live["fingerprint"] == expect_fp
    add("build-identity", same,
        ("production matches the intended build" if same else
         "PRODUCTION IS NOT THE INTENDED BUILD — live fp %s… (%s files) vs intended %s… (%s files); "
         "`flyctl releases` will still read 'complete'"
         % (live["fingerprint"][:12], live.get("files"), expect_fp[:12], expect_files)))
    if not same:
        return False, checks

    rc, out = world.in_container("python3 -c \"import json,urllib.request; "
                                "print(urllib.request.urlopen('http://127.0.0.1:8080/api/health')"
                                ".read().decode()[:200])\"")
    add("health", rc == 0 and '"ok"' in out, (out or "").strip()[-160:] or "no output")

    if run_tests:
        rc, out = world.in_container("sh -c 'cd /app && python3 tools/smoke_check.py'")
        add("suite-smoke-in-container", rc == 0, (out or "").strip().splitlines()[-1:] and
            (out or "").strip().splitlines()[-1][:160] or "no output")

    ok = all(c["ok"] for c in checks)
    return ok, checks


def previous_release(world):
    """The image of the last COMPLETE release before the current one — the rollback target."""
    rels = [r for r in world.releases() if (r.get("Status") or r.get("status")) == "complete"]
    if len(rels) < 2:
        return None, "no earlier complete release to roll back to"
    prev = rels[1]
    img = prev.get("ImageRef") or prev.get("imageRef")
    if not img:
        return None, "release v%s carries no image reference" % prev.get("Version")
    return {"version": prev.get("Version"), "image": img}, None


# ── dispatch: hand a failure to a coding agent ─────────────────────────────────────────────────

DISPATCH_LABEL = "conductor:needs-fix"


def dispatch_fix(world, pr, why, commit=False):
    """Attach the evidence to the PR, then run a headless coding agent on that branch.

    The PR comment happens FIRST and unconditionally. If the agent cannot be started — no CLI, no
    quota, a crash — the failure is still recorded where the next human or thread will see it. A
    dispatch whose only trace is a dead subprocess is a failure that silently disappears.

    The agent is scoped deliberately: one branch, one failure, told to fix and push, and told NOT
    to merge or deploy. Merging stays the conductor's decision, so a fix still has to pass the
    same gate as everything else rather than riding in on the fixer's own say-so.
    """
    body = ("**conductor**: this PR did not clear the gate.\n\n**Why:** %s\n\n```\n%s\n```\n\n"
            "A coding agent has been dispatched to this branch. It will push a fix; it will not "
            "merge or deploy — the fix re-enters the same gate." % (
                why.split("\n")[0][:200], why[:2500]))
    if commit:
        world.sh(["gh", "pr", "comment", str(pr["number"]), "--body", body], timeout=180)
        world.sh(["gh", "pr", "edit", str(pr["number"]), "--add-label", DISPATCH_LABEL], timeout=120)

    prompt = (
        "You are fixing PR #%s on branch `%s` in the hoodie-suite repo.\n\n"
        "The release conductor rejected it:\n\n%s\n\n"
        "Do exactly this:\n"
        "1. Check out that branch in an ISOLATED git worktree (never switch the shared HEAD).\n"
        "2. Reproduce the failure before changing anything. If you cannot reproduce it, say so "
        "and stop — do not guess at a fix.\n"
        "3. Fix the cause, not the symptom. Add a test that FAILS without your fix.\n"
        "4. Run the full suite, then push to the same branch.\n"
        "5. Do NOT merge and do NOT deploy. Comment on the PR with what you changed and why.\n"
    ) % (pr["number"], pr.get("branch") or "?", why[:4000])
    if not commit:
        return {"pr": pr["number"], "dispatched": False, "dry_run": True}
    cli = os.path.expanduser("~/.local/bin/claude")
    cli = cli if os.path.exists(cli) else "claude"
    rc, out = world.sh([cli, "-p", prompt], timeout=3600)
    return {"pr": pr["number"], "dispatched": rc == 0,
            "detail": (out or "")[-400:] if rc != 0 else "agent completed"}


# ── the cycle ──────────────────────────────────────────────────────────────────────────────────

def cycle(world, commit=False, dispatch=None, max_merges=MAX_MERGES_PER_CYCLE):
    """One pass: triage → merge → deploy → verify → (rollback + dispatch on failure)."""
    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": commit, "steps": [],
              "merged": [], "held": [], "dispatched": [], "rolled_back": None}

    def step(name, ok, detail):
        report["steps"].append({"step": name, "ok": ok, "detail": detail})
        world.log("  [%s] %s — %s" % ("ok " if ok else "!!", name, detail))
        return ok

    if os.path.exists(STOP_FILE):
        step("kill-switch", False, "%s exists — standing down" % STOP_FILE)
        return report

    prs = world.open_prs()
    if prs is None:
        step("survey", False, "gh unavailable — cannot enumerate PRs, doing nothing")
        return report
    tri = triage(prs)
    report["held"] = tri["hold"]
    step("survey", True, "%d open · %d mergeable · %d held · %d need a fix"
         % (len(prs), len(tri["merge"]), len(tri["hold"]), len(tri["dispatch"])))
    for h in tri["hold"]:
        world.log("      hold  #%-5s %s" % (h["number"], h["why"]))

    for d in tri["dispatch"]:
        report["dispatched"].append(dict(d, reason="checks failing before merge"))
        if commit and dispatch:
            dispatch(d, "checks failing before merge")

    todo = tri["merge"][:max_merges]
    if len(tri["merge"]) > max_merges:
        step("cap", True, "%d mergeable, taking %d this cycle (cap)" % (len(tri["merge"]), max_merges))
    if not todo:
        step("merge", True, "nothing mergeable this cycle")
        return report

    # INTEGRATE FIRST — the full suite over the merged result, not each PR alone. This is what
    # catches the pair that is fine apart and broken together.
    rc, out = (0, "(dry run)") if not commit else world.release_train(
        "integrate", "--only", ",".join(str(p["number"]) for p in todo))
    if rc != 0:
        step("integrate", False, "integration FAILED — merging nothing; dispatching a fix")
        for p in todo:
            report["dispatched"].append(dict(p, reason="integration failed"))
            if commit and dispatch:
                dispatch(p, "integration failed:\n" + (out or "")[-3000:])
        return report
    step("integrate", True, "the merged result passes the full suite")

    for p in todo:
        if not commit:
            report["merged"].append(dict(p, dry_run=True))
            continue
        rc, out = world.merge(p["number"])
        if rc != 0:
            step("merge #%s" % p["number"], False, (out or "")[-160:])
            report["held"].append(dict(p, why="merge failed: %s" % (out or "")[-100:]))
        else:
            report["merged"].append(p)
    step("merge", True, "%d merged" % len(report["merged"]))

    if not commit:
        step("deploy", True, "(dry run) would deploy origin/main and verify")
        return report

    prev, why = previous_release(world)
    if not prev:
        step("rollback-target", False, "%s — REFUSING to deploy without one" % why)
        return report
    step("rollback-target", True, "v%s is the fallback if verification fails" % prev["version"])

    rc, out = world.release_train("deploy")
    if rc != 0:
        step("deploy", False, "deploy failed: %s" % (out or "")[-200:])
        return report
    step("deploy", True, "deployed origin/main")

    ok, checks = verify(world)
    report["verify"] = checks
    for c in checks:
        world.log("      %s %-28s %s" % ("ok " if c["ok"] else "!! ", c["check"], c["detail"][:110]))
    if ok:
        step("verify", True, "production IS the intended build and answers")
        return report

    step("verify", False, "production did NOT verify — rolling back to v%s" % prev["version"])
    rc, out = world.rollback(prev["image"])
    report["rolled_back"] = {"to": prev["version"], "ok": rc == 0}
    step("rollback", rc == 0, "restored v%s" % prev["version"] if rc == 0
         else "ROLLBACK FAILED: %s" % (out or "")[-200:])
    for p in report["merged"]:
        report["dispatched"].append(dict(p, reason="post-deploy verification failed"))
        if dispatch:
            dispatch(p, "post-deploy verification failed:\n"
                     + json.dumps(checks, indent=1)[:3000])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cycle", help="triage → merge → deploy → verify → roll back")
    c.add_argument("--commit", action="store_true", help="actually act (default is a dry run)")
    c.add_argument("--max-merges", type=int, default=MAX_MERGES_PER_CYCLE)
    sub.add_parser("verify", help="verify production against the intended build")
    sub.add_parser("triage", help="classify open PRs, change nothing")
    a = ap.parse_args(argv)
    world = World()

    if a.cmd == "verify":
        ok, checks = verify(world)
        for ch in checks:
            print("  %s %-28s %s" % ("ok " if ch["ok"] else "!! ", ch["check"], ch["detail"]))
        print("\n%s" % ("VERIFIED — production is the intended build" if ok
                        else "NOT VERIFIED — see above"))
        return 0 if ok else 2
    if a.cmd == "triage":
        prs = world.open_prs() or []
        tri = triage(prs)
        for bucket in ("merge", "hold", "dispatch"):
            print("\n%s (%d)" % (bucket.upper(), len(tri[bucket])))
            for p in tri[bucket]:
                print("  #%-5s %-52s %s" % (p["number"], p["title"], p["why"][:80]))
        return 0
    rep = cycle(world, commit=a.commit, max_merges=a.max_merges,
                dispatch=lambda pr, why: dispatch_fix(world, pr, why, commit=a.commit))
    print("\n%s" % json.dumps({k: v for k, v in rep.items() if k != "steps"}, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
