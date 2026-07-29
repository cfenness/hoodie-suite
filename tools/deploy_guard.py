#!/usr/bin/env python3
"""deploy_guard.py — refuse `flyctl deploy` from anything but a clean origin/main.

`flyctl deploy` ships the WORKING TREE, not a branch. With several Claude Code sessions each in
their own worktree, that is a live clobber: on 2026-07-28 a merged feature was deployed, then
silently wiped from production by a later deploy from a worktree that predated the merge. Four
consecutive releases all reported "complete" while `/app/unifyd/locator_signal.py` was simply
absent from the running container. `flyctl releases` cannot show you that.

The rule was already written down. Written-down rules lose to muscle memory at 2am, so this makes
it mechanical: a shim on PATH ahead of the real flyctl that checks the tree before a deploy and
gets out of the way for everything else.

DESIGN RULE — FAIL OPEN. This sits in front of every flyctl invocation on the machine. It refuses
ONLY on a positive determination that the tree is unsafe; any internal error, any unexpected state,
anything it cannot establish, and it allows the command through with a warning. A guard that breaks
your tooling gets uninstalled, and then it is guarding nothing.

    python3 tools/deploy_guard.py check [cwd]     # exit 0 = safe, 3 = refuse, 1 = could not tell
    python3 tools/deploy_guard.py install         # install the shim at ~/.fly/bin/flyctl
    python3 tools/deploy_guard.py uninstall       # put the original back

Escape hatch: HOODIE_DEPLOY_OK=1 bypasses the check for one invocation (and says so loudly).

TWO THINGS HERE EXIST BECAUSE THE FIRST VERSION FAILED CLOSED — caught and reported by the Hoodie
Relations session before it bit anyone:

  1. EXIT 3, NOT 2, MEANS REFUSED. python3 exits **2** when it cannot open the script at all, so
     using 2 for "refused" made a MISSING GUARD indistinguishable from a policy decision. The shim
     would have blocked every deploy on the machine — including an emergency one — and reported it
     as a refusal.
  2. INSTALL COPIES ITSELF NEXT TO THE SHIM. The first version baked __file__ into the shim, which
     pointed inside a `.claude/worktrees/...` worktree that Claude Code removes on session exit.
     The guard now lives at ~/.fly/bin/hoodie_deploy_guard.py, so it depends on no checkout, no
     branch, and no session. The shim also `test -f`s it and allows if it is gone.
"""
import os
import subprocess
import sys

FLY_DIR = os.path.expanduser("~/.fly/bin")
SHIM = os.path.join(FLY_DIR, "flyctl")
REAL = os.path.join(FLY_DIR, "flyctl-real")

SHIM_SRC = '''#!/bin/sh
# Installed by hoodie-suite tools/deploy_guard.py — refuses `flyctl deploy` from a tree that is not
# a clean origin/main. Every other subcommand passes straight through. Fails OPEN.
# Uninstall: python3 tools/deploy_guard.py uninstall
GUARD="%(guard)s"
REAL="%(real)s"
if [ "$1" = "deploy" ]; then
  if [ -n "$HOODIE_DEPLOY_OK" ]; then
    echo "deploy-guard: BYPASSED via HOODIE_DEPLOY_OK — you are shipping this working tree." >&2
  elif [ ! -f "$GUARD" ]; then
    # The guard script is gone (repo moved, worktree pruned). ALLOW — a guard that cannot run must
    # never be mistaken for a guard that said no.
    echo "deploy-guard: guard script missing at $GUARD — allowing. Reinstall:" >&2
    echo "  python3 tools/deploy_guard.py install" >&2
  else
    python3 "$GUARD" check "$PWD"
    # ONLY 3 means refused. Python exits 2 when it cannot open the script, 1 for an unhandled
    # error — neither is a policy decision, and both must fall through to the real binary.
    [ $? -eq 3 ] && exit 1
  fi
fi
exec "$REAL" "$@"
'''


def sh(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def check(cwd):
    """0 = safe to deploy, 3 = refuse, 1 = could not determine (caller must allow).

    3 rather than 2 because python3 itself exits 2 on "can't open file" — see the module docstring.
    """
    rc, top, _ = sh(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    if rc != 0 or not top:
        print("deploy-guard: not a git repo — allowing (cannot judge what you're shipping).",
              file=sys.stderr)
        return 1

    rc, remote, _ = sh(["git", "remote", "get-url", "origin"], cwd=top)
    if rc != 0 or "hoodie-suite" not in (remote or ""):
        return 1                                   # some other repo; not ours to police

    sh(["git", "fetch", "--quiet", "origin"], cwd=top)
    rc, base, _ = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=top)
    base = base.split("/")[-1] if rc == 0 and base else "main"

    rc, head, _ = sh(["git", "rev-parse", "HEAD"], cwd=top)
    rc2, want, _ = sh(["git", "rev-parse", "origin/%s" % base], cwd=top)
    rc3, dirty, _ = sh(["git", "status", "--porcelain"], cwd=top)
    if rc or rc2 or rc3:
        print("deploy-guard: could not read git state — allowing.", file=sys.stderr)
        return 1

    problems = []
    if head != want:
        rc, ahead, _ = sh(["git", "rev-list", "--count", "origin/%s..HEAD" % base], cwd=top)
        rc, behind, _ = sh(["git", "rev-list", "--count", "HEAD..origin/%s" % base], cwd=top)
        rc, br, _ = sh(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=top)
        problems.append("HEAD is '%s' (%s ahead, %s behind origin/%s), not origin/%s"
                        % (br, ahead or "?", behind or "?", base, base))
    n = len([l for l in dirty.splitlines() if l.strip()])
    if n:
        problems.append("%d uncommitted change(s) — a deploy ships them" % n)

    if not problems:
        # State the pass. Silence here would be indistinguishable from the guard not running at
        # all — which this guard has already done once, when a missing script exited 2 and was read
        # as a refusal.
        print("deploy-guard: OK — HEAD is origin/%s (%s) and the tree is clean."
              % (base, head[:8]), file=sys.stderr)
        return 0

    print("", file=sys.stderr)
    print("  REFUSED: flyctl deploy ships this WORKING TREE, not a branch.", file=sys.stderr)
    for p in problems:
        print("    - %s" % p, file=sys.stderr)
    print("", file=sys.stderr)
    print("  This has already cost a production feature: on 2026-07-28 a deploy from a stale", file=sys.stderr)
    print("  worktree silently wiped a merged file from the running container, while every", file=sys.stderr)
    print("  release read 'complete'.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Ship the merged state instead:", file=sys.stderr)
    print("      python3 tools/release_train.py deploy", file=sys.stderr)
    print("", file=sys.stderr)
    print("  It builds a clean checkout at origin/%s, verifies it, deploys, confirms the release" % base,
          file=sys.stderr)
    print("  landed, and re-pins the dispatcher if the source registry moved.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Deliberately shipping this tree anyway:  HOODIE_DEPLOY_OK=1 flyctl deploy ...", file=sys.stderr)
    print("", file=sys.stderr)
    return 3


INSTALLED_GUARD = os.path.join(FLY_DIR, "hoodie_deploy_guard.py")


def install():
    """Copy this script next to the shim and point the shim at the COPY.

    Never reference the repo: the installing session is usually in a `.claude/worktrees/...`
    worktree that gets removed on exit, and the primary checkout may be on a branch that predates
    this file. A guard whose own path can vanish is a guard that will one day refuse everything.
    """
    import shutil
    src = os.path.abspath(__file__)
    if not os.path.exists(SHIM):
        print("no flyctl at %s — nothing to wrap" % SHIM)
        return 1
    if os.path.exists(REAL):
        print("already installed (%s exists); refreshing the shim" % REAL)
    else:
        os.rename(SHIM, REAL)
    shutil.copyfile(src, INSTALLED_GUARD)
    os.chmod(INSTALLED_GUARD, 0o755)
    with open(SHIM, "w") as f:
        f.write(SHIM_SRC % {"guard": INSTALLED_GUARD, "real": REAL})
    os.chmod(SHIM, 0o755)
    print("installed: %s now guards deploys" % SHIM)
    print("  guard copy : %s  (no repo/worktree dependency)" % INSTALLED_GUARD)
    print("  real binary: %s" % REAL)
    print("uninstall: python3 tools/deploy_guard.py uninstall")
    return 0


def uninstall():
    if not os.path.exists(REAL):
        print("not installed (no %s)" % REAL)
        return 1
    os.replace(REAL, SHIM)
    os.chmod(SHIM, 0o755)
    try:
        os.unlink(INSTALLED_GUARD)
    except OSError:
        pass
    print("uninstalled: %s restored" % SHIM)
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    try:
        return check(sys.argv[2] if len(sys.argv) > 2 else os.getcwd())
    except Exception as e:                          # noqa: BLE001 — never block on our own bug
        print("deploy-guard: internal error (%s) — allowing." % str(e)[:80], file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
