"""Offline test for the conductor: python3 tools/conductor_test.py

The conductor merges and deploys to production without a human, so what matters is not that it
works when everything is fine — it is what it REFUSES to do, and whether it can tell "production
is broken" from "I could not tell". Both of those are pinned here.

Every path runs against an injected World: no git, no gh, no Fly, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if not cond:
        FAILED.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — %s" % detail) if not cond else ""))


def pr(n, **kw):
    d = {"number": n, "title": "pr %d" % n, "isDraft": False, "mergeable": "MERGEABLE",
         "headRefName": "b%d" % n, "files": [{"path": "unifyd/thing.py"}], "statusCheckRollup": []}
    d.update(kw)
    return d


class FakeWorld(object):
    """Records every write. Reads are configured per test."""

    def __init__(self, **kw):
        self.calls = []
        self.prs = kw.get("prs", [])
        self._live = kw.get("live", {"fingerprint": "a" * 64, "files": 100})
        self._intended = kw.get("intended", ("a" * 64, 100))
        self._rels = kw.get("releases", [{"Version": 9, "Status": "complete", "ImageRef": "img:9"},
                                         {"Version": 8, "Status": "complete", "ImageRef": "img:8"}])
        self._container = kw.get("container", (0, '{"ok": true}'))
        self._integrate_rc = kw.get("integrate_rc", 0)
        self._integrate_out = kw.get("integrate_out", "integrate output")
        self._deploy_rc = kw.get("deploy_rc", 0)
        self._rollback_rc = kw.get("rollback_rc", 0)
        self.log = lambda *a: None

    def open_prs(self):
        return self.prs

    def live_version(self):
        return self._live

    def intended_fingerprint(self):
        return self._intended

    def releases(self):
        return self._rels

    def in_container(self, script):
        self.calls.append(("in_container", script[:40]))
        return self._container

    def reset_integration_worktree(self):
        self.calls.append(("reset_integration",))
        return 0, "reset"

    def merge(self, n):
        self.calls.append(("merge", n))
        return 0, "merged"

    def release_train(self, *args):
        self.calls.append(("release_train",) + args)
        if args[0] == "integrate":
            return self._integrate_rc, self._integrate_out
        return self._deploy_rc, "deploy output"

    def rollback(self, image):
        self.calls.append(("rollback", image))
        return self._rollback_rc, "rolled back"

    def did(self, kind):
        return [c for c in self.calls if c[0] == kind]


# ── triage: what it refuses to merge ──────────────────────────────────────────────────────────
print("triage holds everything uncertain, and says why")
t = C.triage([
    pr(1),
    pr(2, isDraft=True),
    pr(3, mergeable="CONFLICTING"),
    pr(4, files=[{"path": "tools/conductor.py"}]),
    pr(5, statusCheckRollup=[{"conclusion": "FAILURE"}]),
    pr(6, statusCheckRollup=[{"state": "PENDING"}]),
    pr(7, files=[{"path": "tools/release_train.py"}, {"path": "unifyd/x.py"}]),
])
ids = {b: {p["number"] for p in t[b]} for b in t}
check("a clean PR is mergeable", ids["merge"] == {1}, ids)
check("a draft is held", 2 in ids["hold"])
check("a conflicting PR is held, not merged", 3 in ids["hold"])
check("a PR touching the conductor is HELD", 4 in ids["hold"], ids)
check("a PR touching release_train is HELD", 7 in ids["hold"], ids)
check("a failing PR goes to dispatch, not merge", ids["dispatch"] == {5}, ids)
check("a pending PR is held", 6 in ids["hold"])
check("every decision carries a reason", all(p.get("why") for b in t for p in t[b]))
selfheld = next(p for p in t["hold"] if p["number"] == 4)
check("the self-modification reason names the rail",
      "own gate" in selfheld["why"], selfheld["why"])

# ── verify: UNKNOWN is never a pass ───────────────────────────────────────────────────────────
print("\nverify separates 'broken' from 'could not tell'")
ok, ch = C.verify(FakeWorld(live=None))
check("unreachable production does NOT verify", not ok)
check("…and says it is UNKNOWN, not healthy", "UNKNOWN" in ch[0]["detail"], ch)

ok, ch = C.verify(FakeWorld(intended=(None, None)))
check("an unfingerprintable tree does not verify", not ok)
check("…and says it could not compare", "cannot compare" in ch[-1]["detail"], ch)

ok, ch = C.verify(FakeWorld(live={"fingerprint": "b" * 64, "files": 99},
                            intended=("a" * 64, 100)))
check("a fingerprint mismatch fails", not ok)
check("…and names the stale-tree failure mode",
      "still read 'complete'" in ch[-1]["detail"], ch[-1]["detail"])

w = FakeWorld(container=(1, "smoke failed"))
ok, ch = C.verify(w)
check("a failing in-container check fails verification", not ok, ch)
check("verification actually runs inside the container", w.did("in_container"), w.calls)

ok, ch = C.verify(FakeWorld())
check("a matching, healthy build verifies", ok, ch)

# ── the cycle: dry run changes nothing ────────────────────────────────────────────────────────
print("\ndry run is the default and writes nothing")
w = FakeWorld(prs=[pr(1), pr(2)])
rep = C.cycle(w, commit=False)
check("dry run merges nothing", not w.did("merge"), w.calls)
check("dry run deploys nothing", not w.did("release_train"), w.calls)
check("dry run still reports what it would merge", len(rep["merged"]) == 2, rep["merged"])

# ── the cycle: integration failure blocks the merge ───────────────────────────────────────────
print("\nintegration is the gate before merging, not after")
w = FakeWorld(prs=[pr(1), pr(2)], integrate_rc=1)
seen = []
rep = C.cycle(w, commit=True, dispatch=lambda p, why: seen.append((p["number"], why[:20])))
check("a failed integration merges NOTHING", not w.did("merge"), w.calls)
check("…and never reaches deploy", not [c for c in w.did("release_train") if c[1] == "deploy"])
check("…and dispatches a fix for each PR", len(seen) == 2, seen)

# ── the cycle: post-deploy verification failure rolls back ────────────────────────────────────
print("\na failed verification rolls back BEFORE it diagnoses")
w = FakeWorld(prs=[pr(1)], live={"fingerprint": "b" * 64, "files": 99}, intended=("a" * 64, 100))
seen = []
rep = C.cycle(w, commit=True, dispatch=lambda p, why: seen.append(p["number"]))
check("it merged and deployed", w.did("merge") and w.did("release_train"))
check("it ROLLED BACK on a failed verify", w.did("rollback"), w.calls)
check("…to the previous complete release", w.did("rollback")[0][1] == "img:8", w.did("rollback"))
check("…and recorded it", rep["rolled_back"] == {"to": 8, "ok": True}, rep["rolled_back"])
check("…and dispatched a fix", seen == [1], seen)

print("\nit refuses to deploy with no rollback target")
w = FakeWorld(prs=[pr(1)], releases=[{"Version": 9, "Status": "complete", "ImageRef": "img:9"}])
rep = C.cycle(w, commit=True)
check("no earlier release ⇒ no deploy", not [c for c in w.did("release_train") if c[1] == "deploy"],
      w.calls)
check("…and the refusal says why",
      any("REFUSING" in s["detail"] for s in rep["steps"]), rep["steps"])

# ── kill switch + cap ─────────────────────────────────────────────────────────────────────────
print("\nthe kill switch and the merge cap")
open(C.STOP_FILE, "w").close()
try:
    w = FakeWorld(prs=[pr(1)])
    rep = C.cycle(w, commit=True)
    check("the kill switch stops everything", not w.calls, w.calls)
    check("…and says so", rep["steps"][0]["step"] == "kill-switch", rep["steps"])
finally:
    os.remove(C.STOP_FILE)

w = FakeWorld(prs=[pr(i) for i in range(1, 8)])
rep = C.cycle(w, commit=False, max_merges=2)
check("the per-cycle merge cap is honoured", len(rep["merged"]) == 2, rep["merged"])

# ── dispatch: the evidence must survive the agent failing ─────────────────────────────────────
print("\ndispatch records the failure even when the agent cannot run")


class DispatchWorld(FakeWorld):
    def __init__(self, agent_rc=0, **kw):
        FakeWorld.__init__(self, **kw)
        self.agent_rc = agent_rc

    def sh(self, cmd, timeout=1800, cwd=None):
        self.calls.append(("sh", cmd[0], cmd[1] if len(cmd) > 1 else ""))
        if "claude" in cmd[0]:
            return self.agent_rc, "agent output"
        return 0, ""


w = DispatchWorld(agent_rc=1)                      # the coding agent fails to run
r = C.dispatch_fix(w, {"number": 42, "branch": "b42"}, "integration failed: boom", commit=True)
cmds = [c for c in w.calls if c[0] == "sh"]
check("the PR comment is posted before the agent runs",
      cmds[0][1] == "gh" and cmds[0][2] == "pr", cmds[:2])
check("a label is applied so it is findable", any(c[2] == "pr" for c in cmds[1:2]), cmds)
check("a failed agent is reported, not swallowed", r["dispatched"] is False, r)

w = DispatchWorld(agent_rc=0)
r = C.dispatch_fix(w, {"number": 43, "branch": "b43"}, "verify failed", commit=True)
check("a successful dispatch is reported", r["dispatched"] is True, r)
check("the agent is actually invoked", any("claude" in c[1] for c in w.calls), w.calls)

w = DispatchWorld()
r = C.dispatch_fix(w, {"number": 44, "branch": "b44"}, "x", commit=False)
check("dry run dispatches nothing", not w.calls and r["dry_run"], w.calls)

# ── infra vs code: the failure the FIRST LIVE RUN produced ────────────────────────────────────
# Verbatim from that cycle. release_train merged three PRs onto the integration branch, then died
# on leftover worktree state — and said so. The conductor read it as "these PRs are broken" and
# dispatched a coding agent at #764, which had merged cleanly, to fix something no PR change could.
LIVE_FAILURE = """integration branch integration/20260804-115918 (from origin/main)

  merged  #745   feat(scrape): metro-area scoping for ue_catalog.py
  merged  #762   feat(scrape): explicit outlets= override for toast.py pull_menus()
  merged  #764   feat(scrape): remove BrightData from menu_site.py

!! MERGE FAILED integrating #765 (feat/naop-checkpoint-and-session) - this is NOT a content conflict
   git said:
     fatal: stash failed

   No files are in conflict, so there is nothing to resolve by hand.
   Nothing has touched main.
"""

print("\nan infrastructure failure escalates and dispatches NOBODY")
kind, reason, implicated = C.classify_integrate_failure(LIVE_FAILURE)
check("the live failure is classified as infra", kind == "infra", (kind, reason))
check("…naming the actual cause", "stash failed" in reason, reason)
check("…and it still identifies the PR git choked on", implicated == [765], implicated)

w = FakeWorld(prs=[pr(764), pr(765)], integrate_rc=1)
w._integrate_out = LIVE_FAILURE
seen = []
rep = C.cycle(w, commit=True, dispatch=lambda p, why: seen.append(p["number"]))
check("an infra failure dispatches NOBODY", seen == [], seen)
check("…merges nothing", not w.did("merge"), w.calls)
check("…and escalates instead", rep.get("escalated") and "no PR change would fix" in
      rep["escalated"]["reason"], rep.get("escalated"))

print("\na real code failure dispatches ONLY the implicated PR")
w = FakeWorld(prs=[pr(1), pr(2), pr(3)], integrate_rc=1)
w._integrate_out = ("running suite…\n!! MERGE FAILED integrating #2 (b2)\n"
                    "   2 tests failed in unifyd/thing_test.py\n")
seen = []
rep = C.cycle(w, commit=True, dispatch=lambda p, why: seen.append(p["number"]))
check("only the named PR is dispatched", seen == [2], seen)
check("…not the healthy ones in the batch", 1 not in seen and 3 not in seen, seen)

print("\nwhen no PR is named, the whole batch is suspect — and it says so")
w = FakeWorld(prs=[pr(1), pr(2)], integrate_rc=1)
w._integrate_out = "the suite failed but nothing identified a PR"
seen = []
rep = C.cycle(w, commit=True, dispatch=lambda p, why: seen.append(p["number"]))
check("an unattributed code failure dispatches the batch", sorted(seen) == [1, 2], seen)

print("\nthe integration worktree is reset before every integrate")
w = FakeWorld(prs=[pr(1)])
C.cycle(w, commit=True)
check("reset happens, and before the integrate", w.calls[0][0] == "reset_integration", w.calls[:2])
w = FakeWorld(prs=[pr(1)])
C.cycle(w, commit=False)
check("…but never on a dry run", not w.did("reset_integration"), w.calls)

print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
sys.exit(1 if FAILED else 0)
