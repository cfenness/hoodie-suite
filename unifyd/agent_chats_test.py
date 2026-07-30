#!/usr/bin/env python3
"""agent_chats_test.py — pins the anti-clobber guarantee against REAL git repos, not mocks.

This module makes a promise: no tree ships if it would revert someone. A promise like that has to be
tested against actual git ancestry, because the failure it prevents (54 of 57 worktrees able to
silently un-deploy production) is invisible in every other signal — the release reads `complete`,
the branch is merged, `git log` on main looks right, and production is missing the code.

So these tests build throwaway repos with real commits and real divergence, and assert the verdict.

    python3 unifyd/agent_chats_test.py
"""
import os
import shutil
import subprocess
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


def sh(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, timeout=30)


def build_repo(root):
    """An origin + a clone whose HEAD deliberately predates origin/main — the clobber setup."""
    origin, work = os.path.join(root, "origin"), os.path.join(root, "work")
    os.makedirs(origin)
    sh("git", "init", "-q", "--bare", "--initial-branch=main", cwd=origin)
    seed = os.path.join(root, "seed")
    sh("git", "clone", "-q", origin, seed)
    for k, v in (("user.email", "t@t.t"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=seed)
    open(os.path.join(seed, "a.txt"), "w").write("one\n")
    sh("git", "add", "-A", cwd=seed); sh("git", "commit", "-qm", "base", cwd=seed)
    sh("git", "push", "-q", "origin", "main", cwd=seed)
    # clone at base -> this is the "stale worktree"
    sh("git", "clone", "-q", origin, work)
    for k, v in (("user.email", "t@t.t"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=work)
    # advance origin/main beyond what `work` has
    open(os.path.join(seed, "b.txt"), "w").write("two\n")
    sh("git", "add", "-A", cwd=seed); sh("git", "commit", "-qm", "landed-later", cwd=seed)
    sh("git", "push", "-q", "origin", "main", cwd=seed)
    sh("git", "fetch", "-q", "origin", "main", cwd=work)     # work now KNOWS it's behind
    return origin, seed, work


def stale_clone(root, origin, name):
    """A clone parked at origin/main's PARENT — i.e. deliberately behind by one commit.

    Each section gets its own, because threading one mutable `work` repo through every section made
    the later assertions depend on earlier merges. A test that only passes in one order is pinning
    the order, not the behaviour."""
    d = os.path.join(root, name)
    sh("git", "clone", "-q", origin, d)
    for k, v in (("user.email", "t@t.t"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=d)
    sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=d)
    return d


def main():
    print("agent_chats — concurrent chats that don't clobber")
    import agent_chats as C

    root = tempfile.mkdtemp(prefix="cockpit-chats-")
    db = os.path.join(root, "c.db")
    origin, seed, work = build_repo(root)

    # --- 1. THE GUARANTEE: a stale tree is refused, and names what it would revert -------------
    r = C.deploy_readiness(worktree=work, db=db)
    check("stale tree is NOT deploy-ready", r["ready"] is False, r)
    check("blocker explains the revert", any("revert" in b.lower() for b in r["blockers"]),
          r["blockers"])
    check("names the commits that would be lost", len(r["would_revert"]) >= 1, r["would_revert"])
    check("the named commit is the one that landed later",
          any("landed-later" in l for l in r["would_revert"]), r["would_revert"])

    # After merging main in, the same tree becomes shippable. The check must be able to say YES —
    # a guard that never passes gets uninstalled, which is how you end up with no guard at all.
    sh("git", "merge", "-q", "--no-edit", "origin/main", cwd=work)
    r2 = C.deploy_readiness(worktree=work, db=db)
    check("after merging origin/main the tree IS ready", r2["ready"] is True, r2)
    check("nothing left to revert", r2["would_revert"] == [], r2["would_revert"])

    # --- 2. dirty trees are blocked (a deploy ships the TREE, not the branch) --------------------
    open(os.path.join(work, "uncommitted.txt"), "w").write("wip\n")
    r3 = C.deploy_readiness(worktree=work, db=db)
    check("dirty tree is refused", r3["ready"] is False, r3)
    check("dirty blocker explains that a deploy ships the tree",
          any("dirty" in b.lower() for b in r3["blockers"]), r3["blockers"])
    os.remove(os.path.join(work, "uncommitted.txt"))
    check("removing the dirt restores readiness",
          C.deploy_readiness(worktree=work, db=db)["ready"] is True)

    # --- 3. sibling merges: NOT redundant with the origin/main check -----------------------------
    # A sibling's merge can be newer than the origin/main this tree fetched, so checking main alone
    # can still pass a tree that reverts the sibling. This is that case.
    open(os.path.join(seed, "c.txt"), "w").write("three\n")
    sh("git", "add", "-A", cwd=seed); sh("git", "commit", "-qm", "sibling-work", cwd=seed)
    sib_sha = sh("git", "rev-parse", "HEAD", cwd=seed).stdout.strip()
    sh("git", "push", "-q", "origin", "main", cwd=seed)
    C.upsert("chat:sibling", subject="ubereats scrape", merged_sha=sib_sha, db=db)
    r4 = C.deploy_readiness(worktree=work, chat_id="chat:me", db=db)
    check("a sibling's merged work blocks a tree that predates it", r4["ready"] is False, r4)
    check("the blocker names the sibling chat",
          any("chat:sibling" in b for b in r4["blockers"]), r4["blockers"])
    # UNKNOWN IS NOT SAFE: the sibling's sha is not in `work`'s object graph (never fetched), so git
    # cannot answer. That must BLOCK, not pass — otherwise the sibling check is a silent no-op.
    check("an unverifiable sibling blocks rather than passing",
          any("cannot verify" in b for b in r4["blockers"]), r4["blockers"])
    # Once the sha is actually reachable, the verdict becomes a real yes/no again.
    sh("git", "fetch", "-q", "origin", "main", cwd=work)
    r4b = C.deploy_readiness(worktree=work, chat_id="chat:me", db=db)
    check("after fetching, the sibling check gives a real revert verdict",
          r4b["ready"] is False and any("would revert" in b for b in r4b["blockers"]),
          r4b["blockers"])
    sh("git", "merge", "-q", "--no-edit", "origin/main", cwd=work)
    r4c = C.deploy_readiness(worktree=work, chat_id="chat:me", db=db)
    check("merging the sibling's work in clears the blocker", r4c["ready"] is True, r4c)

    # --- 4. claims are advisory, and overlap is detected ----------------------------------------
    C.claim("chat:ue", ["unifyd/ubereats.py", "unifyd/ue_crawl.py"], db=db)
    C.claim("chat:dash", ["apps/collect.html"], db=db)
    C.claim("chat:wide", ["unifyd/"], db=db)
    check("disjoint claims do not collide",
          not any(o["chat_id"] == "chat:dash" for o in C.overlaps("chat:ue", db=db)),
          C.overlaps("chat:ue", db=db))
    # The case that actually bites: a chat claiming a whole directory vs one claiming a file in it.
    check("a directory claim overlaps a file inside it",
          any(o["chat_id"] == "chat:wide" for o in C.overlaps("chat:ue", db=db)),
          C.overlaps("chat:ue", db=db))
    check("overlap is symmetric",
          any(o["chat_id"] == "chat:ue" for o in C.overlaps("chat:wide", db=db)))
    check("glob claims match", C._hits("unifyd/ue_*.py", "unifyd/ue_crawl.py"))
    check("unrelated paths don't match", not C._hits("apps/a.html", "unifyd/b.py"))
    check("empty claims never match", not C._hits("", "unifyd/x.py"))

    # --- 5. the lease is a GATE, not just a mutex ------------------------------------------------
    lease_wt = stale_clone(root, origin, "lease-wt")
    C.upsert("chat:stale", worktree=lease_wt, db=db)
    # `work` currently predates the sibling merge, so the lease must be refused on readiness alone.
    got = C.acquire_deploy_lease("chat:stale", db=db)
    check("lease is refused to a not-ready tree (gate, not mutex)", got["ok"] is False, got)
    check("refusal explains why", "not deploy-ready" in (got.get("reason") or ""), got)

    ok = C.acquire_deploy_lease("chat:free", db=db, require_ready=False)
    check("lease can be acquired when readiness is waived", ok["ok"] is True, ok)
    second = C.acquire_deploy_lease("chat:other", db=db, require_ready=False)
    check("a second chat cannot hold the lease", second["ok"] is False, second)
    check("holder is reported", C.lease_state(db=db)["holder"] == "chat:free")
    check("re-acquiring by the SAME holder is allowed (idempotent)",
          C.acquire_deploy_lease("chat:free", db=db, require_ready=False)["ok"] is True)
    check("a non-holder cannot release someone else's lease",
          C.release_deploy_lease("chat:other", db=db)["ok"] is False)
    check("the holder can release", C.release_deploy_lease("chat:free", db=db)["ok"] is True)
    check("after release the lease is free", C.lease_state(db=db)["held"] is False)

    # A lock with no expiry wedges everything the first time a chat crashes.
    C.acquire_deploy_lease("chat:crashed", db=db, require_ready=False, ttl=0.01)
    import time as _t; _t.sleep(0.05)
    st = C.lease_state(db=db)
    check("an expired lease reads stale and not held", st["held"] is False and st["stale"] is True, st)
    check("a stale lease can be taken over",
          C.acquire_deploy_lease("chat:next", db=db, require_ready=False)["ok"] is True)
    C.release_deploy_lease("chat:next", db=db)

    # --- 6. survival check: the reconcile half of "self-heal" -----------------------------------
    surv_wt = stale_clone(root, origin, "surv-wt")
    behind = sh("git", "rev-parse", "HEAD", cwd=surv_wt).stdout.strip()
    C.record_deploy("chat:me", behind, db=db, cwd=surv_wt)
    v = C.verify_survival(db=db)
    check("deploying a tree missing a sibling's work is detected as CLOBBERED",
          v["verdict"] == "clobbered", v)
    check("the clobber names who got reverted",
          any(m["who"] == "chat:sibling" for m in v["missing"]), v["missing"])
    check("...and reports not-ok", v["ok"] is False, v)

    sh("git", "fetch", "-q", "origin", "main", cwd=surv_wt)
    sh("git", "merge", "-q", "--no-edit", "origin/main", cwd=surv_wt)
    good = sh("git", "rev-parse", "HEAD", cwd=surv_wt).stdout.strip()
    C.record_deploy("chat:me", good, db=db, cwd=surv_wt)
    v2 = C.verify_survival(db=db)
    check("a tree containing everything verifies INTACT", v2["verdict"] == "intact", v2)
    check("intact reports ok", v2["ok"] is True, v2)

    # --- 7. chat registry basics -----------------------------------------------------------------
    C.upsert("chat:x", subject="hello", db=db, bump=True)
    C.upsert("chat:x", db=db, bump=True)
    check("turns increment on bump", C.get("chat:x", db=db)["turns"] == 2, C.get("chat:x", db=db))
    check("upsert preserves subject when not passed",
          C.get("chat:x", db=db)["subject"] == "hello")
    check("get on unknown chat returns None", C.get("nope", db=db) is None)
    check("claims round-trip as a list", isinstance(C.get("chat:ue", db=db)["claims"], list))
    check("status filter works", all(c["status"] == C.ACTIVE for c in C.chats(status=C.ACTIVE, db=db)))

    # --- 7b. chat identity is OURS, not the CLI's ------------------------------------------------
    # The bug this pins: chat identity was originally the CLI session id. A chat answered entirely
    # from the fact store has no CLI session, so every such conversation collapsed into one shared
    # "unassigned" transcript and two unrelated chats merged. And a FORK mints a new session id, which
    # would have split one ongoing conversation into two.
    c1, c2 = C.new_chat_id(), C.new_chat_id()
    check("minted chat ids are distinct", c1 != c2, (c1, c2))
    check("minted ids are namespaced", c1.startswith("chat:"), c1)
    C.upsert(c1, subject="ubereats scrape", db=db)
    C.upsert(c2, subject="scrape dashboard", db=db)
    C.add_message(c1, "user", "how does the zone gate work", db=db)
    C.add_message(c1, "assistant", "from stored findings", source="facts", tokens=0, db=db)
    C.add_message(c2, "user", "the sidebar stacks", db=db)
    check("two chats keep separate transcripts",
          len(C.transcript(c1, db=db)) == 2 and len(C.transcript(c2, db=db)) == 1,
          (len(C.transcript(c1, db=db)), len(C.transcript(c2, db=db))))
    check("a fact-answered turn is recorded as free",
          C.transcript(c1, db=db)[1]["source"] == "facts" and
          C.transcript(c1, db=db)[1]["tokens"] == 0, C.transcript(c1, db=db)[1])
    check("transcript reads oldest-first (a conversation reads forward)",
          C.transcript(c1, db=db)[0]["role"] == "user", C.transcript(c1, db=db)[0])

    C.upsert(c1, session_id="sess-A", db=db)
    check("session id is stored on the chat", C.get(c1, db=db)["session_id"] == "sess-A")
    C.upsert(c1, session_id="sess-B", db=db)          # a fork mints a new CLI session
    g = C.get(c1, db=db)
    check("a fork changes the session but NOT the chat id", g["id"] == c1 and g["session_id"] == "sess-B", g)
    check("...and the transcript survives the fork", len(C.transcript(c1, db=db)) == 2)
    check("subject survives an update that omits it", g["subject"] == "ubereats scrape", g)

    rc = C.recent_chats(db=db)
    ids = [r["chat_id"] for r in rc]
    check("recent_chats lists only chats with messages", c1 in ids and c2 in ids, ids)
    check("...and excludes adopted worktrees never spoken to",
          not any(i.startswith("wt:") for i in ids), ids)
    check("recent_chats is newest-first", ids.index(c2) < ids.index(c1), ids)
    check("recent_chats reports turn counts",
          next(r for r in rc if r["chat_id"] == c1)["turns"] == 2, rc)

    # --- 8. never raises on a non-repo path -----------------------------------------------------
    junk = os.path.join(root, "not-a-repo")
    os.makedirs(junk)
    r5 = C.deploy_readiness(worktree=junk, db=db)
    check("a non-repo path is refused, not crashed", r5["ready"] is False, r5)
    check("_contains returns None (unknown), NOT False, on unresolvable revs",
          C._contains("deadbeef", "cafebabe", cwd=junk) is None,
          C._contains("deadbeef", "cafebabe", cwd=junk))
    check("an unresolvable expectation reads `unknown`, never `clobbered`",
          (lambda: (C.record_deploy("chat:ghost", "deadbeefdeadbeef", db=db, cwd=junk),
                    C.verify_survival(db=db))[1]["verdict"] == "unknown")())

    shutil.rmtree(root, ignore_errors=True)
    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
