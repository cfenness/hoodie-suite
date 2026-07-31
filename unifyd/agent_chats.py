#!/usr/bin/env python3
"""agent_chats.py — CONCURRENT CHATS THAT DON'T CLOBBER EACH OTHER.

The measurement that motivates every line here, taken live on this machine:
    57 git worktrees.  54 of them would REVERT origin/main if deployed as-is.
Fifty-four loaded guns. `flyctl deploy` ships the WORKING TREE, not a branch, so any one of those
54 shipping would silently un-deploy whatever landed in main since it branched — with every release
still reading `complete`. That has already happened twice in this repo (the GS1 work, then the
locator work), and it is the failure this module exists to make impossible rather than unlikely.

THE GUARANTEE, AND ITS LIMIT — stated plainly so nobody over-trusts it:
  • MECHANICAL (this module enforces it): no tree ships if it does not contain origin/main and every
    sibling chat's merged commit. That is pure git ancestry — `merge-base --is-ancestor` — so it is
    deterministic, needs no cooperation from the other chats, and cannot be talked out of.
  • ADVISORY (this module surfaces it): overlapping file claims between chats. Two chats editing the
    same file is a conflict a human resolves; the value is knowing BEFORE the second one commits.
  • NOT SOLVED, and don't pretend otherwise: semantic conflict. If the ubereats chat changes a
    function the dashboard chat reads, ancestry is clean and claims may not overlap, yet the result
    is broken. Claims + the shared fact store give awareness; they don't give correctness.

"Self-healing" here means a verify-and-reconcile loop, not magic: every deploy records what SHOULD
be in it, and the survival check re-asserts that afterwards. A clobber becomes a detected, named,
fixable event instead of something discovered hours later by grepping a container.

WHY LEASES EXPIRE:
The deploy lease is a lock, and a lock with no expiry is a lock that eventually wedges everything —
one crashed chat and nobody can ship. Every lease carries a TTL and is stealable once stale.

    python3 unifyd/agent_chats.py --sync          # adopt worktrees as chats
    python3 unifyd/agent_chats.py --readiness     # who could ship safely right now
"""
import argparse
import json
import os
import re
import subprocess
import sqlite3
import sys
import uuid
import time
from fnmatch import fnmatch

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATE = os.path.join(HERE, "agent_state", "cockpit")
DB = os.path.join(STATE, "chats.db")

LEASE_TTL = 900          # 15 min: long enough for a real deploy, short enough to self-heal
ACTIVE, MERGED, ARCHIVED = "active", "merged", "archived"


def _git(*args, cwd=None):
    """Run git and return stripped stdout, or None on any failure. Never raises — a coordination
    layer that dies because one worktree is in a weird state is worse than one that reports unknown."""
    try:
        p = subprocess.run(["git"] + list(args), cwd=cwd or REPO,
                           capture_output=True, text=True, timeout=30)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def _conn(path=None):
    os.makedirs(STATE, exist_ok=True)
    c = sqlite3.connect(path or DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
    -- `id` is OURS and stable for the life of the conversation; `session_id` is the CLI's current
    -- session and is the handle `--resume` takes. These are deliberately two columns, correcting an
    -- earlier design that conflated them:
    --   • a FORK gives the CLI a new session id while the conversation continues unchanged, so tying
    --     chat identity to the session id would split one chat into two every time you branched;
    --   • a chat answered entirely from the fact store never has a CLI session at all, and with the
    --     ids conflated it had no identity — every such conversation collapsed into one shared
    --     "unassigned" transcript (observed).
    CREATE TABLE IF NOT EXISTS chats (
      id        TEXT PRIMARY KEY,      -- our stable conversation id (uuid), or a worktree key
      session_id TEXT,                 -- current Claude Code session id, for --resume
      subject   TEXT NOT NULL DEFAULT '',
      worktree  TEXT,
      branch    TEXT,
      status    TEXT NOT NULL DEFAULT 'active',
      claims    TEXT NOT NULL DEFAULT '[]',   -- JSON list of paths/globs
      merged_sha TEXT,                        -- set when this chat's work landed in main
      created   REAL, last REAL, turns INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS lease (
      name TEXT PRIMARY KEY, holder TEXT, acquired REAL, ttl REAL, note TEXT
    );
    CREATE TABLE IF NOT EXISTS deploys (
      id INTEGER PRIMARY KEY, ts REAL, chat_id TEXT, deployed_sha TEXT,
      expected TEXT,          -- JSON: {chat_id: sha} that MUST be ancestors of deployed_sha
      verdict TEXT, detail TEXT
    );
    -- OUR OWN transcript, deliberately, even though Claude Code already persists each session.
    -- The CLI's session store is an internal format keyed for resume, not a readable log, and the run
    -- ledger records what a turn COST rather than what was said. To render an ongoing conversation the
    -- surface needs the words — and owning them also means a chat stays readable after its CLI session
    -- is pruned.
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL,
      text TEXT NOT NULL, ts REAL NOT NULL,
      route TEXT,             -- JSON snapshot of the routing decision for this turn
      source TEXT,            -- 'facts' (free, from the store) | 'model' | 'error'
      tokens INTEGER
    );
    CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
    """)
    # `CREATE TABLE IF NOT EXISTS` does NOT add a column to a table that already exists, so a db
    # created before session_id existed would keep working right up until the first UPDATE naming it,
    # then fail at runtime with "no such column" — on the deploy-coordination path. Add it in place.
    cols = {r["name"] for r in c.execute("PRAGMA table_info(chats)").fetchall()}
    if "session_id" not in cols:
        with c:
            c.execute("ALTER TABLE chats ADD COLUMN session_id TEXT")
    return c


def add_message(chat_id, role, text, route=None, source=None, tokens=None, db=None):
    c = _conn(db)
    with c:
        c.execute("""INSERT INTO messages (chat_id, role, text, ts, route, source, tokens)
                     VALUES (?,?,?,?,?,?,?)""",
                  (chat_id, role, text, time.time(),
                   json.dumps(route) if route else None, source, tokens))
    return True


def transcript(chat_id, limit=200, db=None):
    """Oldest-first, so the page can append rather than reverse — a conversation reads forward."""
    c = _conn(db)
    rows = c.execute("""SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?""",
                     (chat_id, int(limit))).fetchall()
    out = []
    for r in reversed(rows):
        d = dict(r)
        if d.get("route"):
            try:
                d["route"] = json.loads(d["route"])
            except Exception:
                d["route"] = None
        out.append(d)
    return out


def recent_chats(limit=12, db=None):
    """Chats that have actually been TALKED to, newest first — the switcher's list.

    Distinct from `chats()`, which returns all 57 adopted worktrees. A worktree you've never spoken to
    is a deploy-safety subject, not a conversation; mixing them would bury the three chats you're
    actually holding in your head under fifty you aren't."""
    c = _conn(db)
    rows = c.execute("""SELECT m.chat_id, MAX(m.id) AS last_id, COUNT(*) AS turns,
                               MAX(m.ts) AS last_ts
                        FROM messages m GROUP BY m.chat_id
                        ORDER BY last_id DESC LIMIT ?""", (int(limit),)).fetchall()
    out = []
    for r in rows:
        ch = get(r["chat_id"], db=db) or {}
        first = c.execute("""SELECT text FROM messages WHERE chat_id=? AND role='user'
                            ORDER BY id ASC LIMIT 1""", (r["chat_id"],)).fetchone()
        out.append(dict(chat_id=r["chat_id"], turns=r["turns"], last_ts=r["last_ts"],
                        subject=ch.get("subject") or (first["text"][:70] if first else ""),
                        branch=ch.get("branch"), worktree=ch.get("worktree")))
    return out


def tactics_savings(db=None, limit=5000):
    """What the caveman/terse/etc. tactics actually saved, read back off what was ACTUALLY stored per
    turn (`add_message`'s `route` snapshot) — not a fresh estimate. Deliberately narrow about what it
    claims: `cavemanize()`'s `filler_removed` is an exact word count (real, no approximation needed),
    so that's summed and reported as-is. The other tactics (terse/scope/evidence/noverify) don't
    strip anything deterministic pre-send — their effect is on the MODEL's output shape, not
    measurable per turn — so this only reports how often each was applied (`tactic_counts`), never a
    fabricated per-turn savings number for them. agent_router.py's own docstring already carries the
    one honest AGGREGATE claim that exists for `terse` ("~20% output trim in measurement") — that's a
    separate, already-labeled historical figure, not something recomputed here."""
    c = _conn(db)
    rows = c.execute("SELECT route FROM messages WHERE route IS NOT NULL ORDER BY id DESC LIMIT ?",
                     (int(limit),)).fetchall()
    turns_with_route, filler_words_removed, tactic_counts = 0, 0, {}
    for r in rows:
        try:
            rt = json.loads(r["route"])
        except Exception:
            continue
        if not isinstance(rt, dict):
            continue
        turns_with_route += 1
        filler_words_removed += int(rt.get("filler_removed") or 0)
        for t in (rt.get("tactics") or []):
            tactic_counts[t] = tactic_counts.get(t, 0) + 1
    return dict(turns_with_route=turns_with_route, filler_words_removed=filler_words_removed,
               tactic_counts=tactic_counts)


# ── chats ────────────────────────────────────────────────────────────────────────────────────────
def new_chat_id():
    """Mint a stable conversation id. Ours, not the CLI's — see the schema note on `session_id`."""
    return "chat:" + uuid.uuid4().hex[:12]


def upsert(chat_id, subject=None, worktree=None, branch=None, status=None,
           claims=None, merged_sha=None, session_id=None, db=None, bump=False):
    c = _conn(db)
    now = time.time()
    row = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if row is None:
        c.execute("""INSERT INTO chats (id, session_id, subject, worktree, branch, status, claims,
                                        merged_sha, created, last, turns)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (chat_id, session_id, subject or "", worktree, branch, status or ACTIVE,
                   json.dumps(claims or []), merged_sha, now, now, 1 if bump else 0))
    else:
        c.execute("""UPDATE chats SET subject=?, worktree=?, branch=?, status=?, claims=?,
                                      merged_sha=?, session_id=?, last=?, turns=turns+?
                     WHERE id=?""",
                  (subject if subject is not None else row["subject"],
                   worktree if worktree is not None else row["worktree"],
                   branch if branch is not None else row["branch"],
                   status or row["status"],
                   json.dumps(claims) if claims is not None else row["claims"],
                   merged_sha if merged_sha is not None else row["merged_sha"],
                   session_id if session_id is not None else row["session_id"],
                   now, 1 if bump else 0, chat_id))
    c.commit()
    return get(chat_id, db=db)


def get(chat_id, db=None):
    r = _conn(db).execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["claims"] = json.loads(d.get("claims") or "[]")
    return d


def chats(status=None, db=None):
    c = _conn(db)
    rows = c.execute("SELECT * FROM chats" + (" WHERE status=?" if status else "")
                     + " ORDER BY last DESC", (status,) if status else ()).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["claims"] = json.loads(d.get("claims") or "[]")
        out.append(d)
    return out


def sync_worktrees(db=None):
    """Adopt every git worktree as a candidate chat.

    Worktrees ARE the concurrent work here — 57 of them right now — so discovering them beats asking
    the operator to register anything. Auto-discovery also means a chat started outside the Cockpit
    still participates in the deploy guarantee, which matters: a coordination layer only the
    well-behaved join protects nobody."""
    out = _git("worktree", "list", "--porcelain")
    if not out:
        return 0
    n, cur = 0, {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if cur.get("worktree"):
                wt = cur["worktree"]
                key = "wt:" + os.path.basename(wt.rstrip("/"))
                br = (cur.get("branch") or "").replace("refs/heads/", "") or "(detached)"
                existing = get(key, db=db)
                upsert(key, subject=(existing or {}).get("subject") or br,
                       worktree=wt, branch=br,
                       status=(existing or {}).get("status") or ACTIVE, db=db)
                n += 1
            cur = {}
            continue
        k, _, v = line.partition(" ")
        if k in ("worktree", "HEAD", "branch"):
            cur[k] = v
    return n


# ── claims (advisory) ────────────────────────────────────────────────────────────────────────────
def _norm(p):
    return (p or "").strip().lstrip("./")


def claim(chat_id, paths, db=None):
    """Declare the paths a chat is touching. Globs allowed (`unifyd/ue_*.py`)."""
    return upsert(chat_id, claims=[_norm(p) for p in paths if _norm(p)], db=db)


def _hits(a, b):
    """Do two claim patterns refer to overlapping ground? Directory prefixes count: a chat claiming
    `unifyd/` overlaps one claiming `unifyd/ubereats.py`, which is the case that actually bites."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or fnmatch(a, b) or fnmatch(b, a):
        return True
    for x, y in ((a, b), (b, a)):
        if x.endswith("/") and y.startswith(x):
            return True
        if y.startswith(x.rstrip("/") + "/"):
            return True
    return False


def overlaps(chat_id, db=None):
    """Other ACTIVE chats whose claims intersect this one's. Advisory, and deliberately so — the
    point is to warn both sides before the second commit, not to block editing."""
    me = get(chat_id, db=db)
    if not me:
        return []
    out = []
    for other in chats(status=ACTIVE, db=db):
        if other["id"] == chat_id:
            continue
        shared = sorted({"%s ~ %s" % (m, o) for m in me["claims"] for o in other["claims"]
                         if _hits(m, o)})
        if shared:
            out.append(dict(chat_id=other["id"], subject=other["subject"],
                            branch=other["branch"], shared=shared))
    return out


# ── the mechanical anti-clobber check ────────────────────────────────────────────────────────────
def _sha(ref, cwd=None):
    return _git("rev-parse", ref, cwd=cwd)


def _contains(ancestor, descendant, cwd=None):
    """True iff `descendant` already contains `ancestor`. None when git CANNOT ANSWER.

    This is the whole guarantee in one call. If origin/main is NOT an ancestor of the tree you are
    about to ship, shipping it removes those commits from production — no judgment involved.

    THE EXIT CODES ARE NOT BOOLEAN, and treating them as one is a real bug (caught by the test):
      0        -> yes, it is an ancestor
      1        -> no, it is not          <- a genuine answer
      128/else -> git could not resolve a rev (wrong repo, unknown sha, corrupt worktree)
    An earlier version returned `returncode == 0`, which folded 128 into "not an ancestor" — so a sha
    from a DIFFERENT repository read as a clobber, and the survival check reported a commit missing
    from itself. Unknown must stay unknown so callers can fail open on it instead of raising a false
    alarm; a guard that cries clobber on every unresolvable ref is a guard that gets ignored."""
    if not ancestor or not descendant:
        return None
    try:
        p = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant],
                           cwd=cwd or REPO, capture_output=True, text=True, timeout=30)
        if p.returncode == 0:
            return True
        if p.returncode == 1:
            return False
        return None                      # git could not answer — not the same as "no"
    except Exception:
        return None


def deploy_readiness(worktree=None, chat_id=None, db=None, fetch=False):
    """Can this tree ship without reverting anyone? Returns a verdict that NAMES what would be lost.

    Two conditions, both ancestry:
      1. the tree contains origin/main
      2. the tree contains every sibling chat's merged commit
    (2) is not redundant with (1): a sibling's merge may be newer than the origin/main you last
    fetched, so checking main alone can still pass a tree that reverts a sibling."""
    wt = worktree
    if not wt and chat_id:
        wt = (get(chat_id, db=db) or {}).get("worktree")
    wt = wt or REPO
    if fetch:
        _git("fetch", "origin", "main", cwd=wt)

    head = _sha("HEAD", cwd=wt)
    main = _sha("origin/main", cwd=wt)
    dirty = _git("status", "--porcelain", cwd=wt)
    blockers, warnings = [], []

    if dirty:
        n = len([l for l in dirty.splitlines() if l.strip()])
        blockers.append("working tree is dirty (%d uncommitted path%s) — a deploy ships the TREE, "
                        "so uncommitted work goes to production unreviewed"
                        % (n, "" if n == 1 else "s"))
    if not head or not main:
        return dict(ready=False, worktree=wt, head=head, main=main,
                    blockers=["cannot resolve HEAD or origin/main in this worktree"],
                    warnings=[], would_revert=[])

    would_revert = []
    if _contains(main, head, cwd=wt) is False:
        lost = _git("log", "--oneline", "--no-decorate", "-20", "%s..%s" % (head, main), cwd=wt)
        lines = [l for l in (lost or "").splitlines() if l.strip()]
        would_revert = lines
        blockers.append("tree does not contain origin/main — deploying would REVERT %d commit%s"
                        % (len(lines), "" if len(lines) == 1 else "s"))

    # UNKNOWN IS NOT SAFE. A sibling's merged sha is often absent from a stale worktree's object
    # graph (it was never fetched), and git then answers "cannot resolve" rather than yes/no. Letting
    # that pass made the entire sibling check a no-op that still reported READY — a fail-open hole,
    # and the exact quiet-degrade shape this repo treats as worse than a loud failure. So an
    # unverifiable sibling BLOCKS, and says how to resolve it.
    for sib in chats(db=db):
        if sib["id"] == chat_id or not sib.get("merged_sha"):
            continue
        who = "%s (%s)" % (sib["id"], sib["subject"] or sib["branch"])
        got = _contains(sib["merged_sha"], head, cwd=wt)
        if got is False:
            blockers.append("tree predates merged work from chat %s @ %s — deploying would revert it"
                            % (who, (sib["merged_sha"] or "")[:12]))
        elif got is None:
            blockers.append("cannot verify against chat %s @ %s — that commit is not in this "
                            "worktree's object graph (git fetch origin, then re-check). Unverified "
                            "is treated as unsafe." % (who, (sib["merged_sha"] or "")[:12]))

    for other in chats(status=ACTIVE, db=db):
        if other["id"] != chat_id and other.get("worktree") and other["worktree"] != wt:
            pass   # presence alone isn't a blocker; overlapping claims are surfaced separately
    if chat_id:
        ov = overlaps(chat_id, db=db)
        for o in ov:
            warnings.append("chat %s (%s) claims overlapping paths: %s"
                            % (o["chat_id"], o["subject"], ", ".join(o["shared"][:3])))

    return dict(ready=not blockers, worktree=wt, head=head, main=main,
                blockers=blockers, warnings=warnings, would_revert=would_revert)


def fleet_readiness(db=None):
    """Readiness across every known chat — the 54-of-57 number, live."""
    out = []
    for ch in chats(db=db):
        if not ch.get("worktree") or not os.path.isdir(ch["worktree"]):
            continue
        r = deploy_readiness(worktree=ch["worktree"], chat_id=ch["id"], db=db)
        out.append(dict(chat_id=ch["id"], subject=ch["subject"], branch=ch["branch"],
                        ready=r["ready"], blockers=r["blockers"],
                        reverts=len(r["would_revert"])))
    return out


# ── deploy lease ─────────────────────────────────────────────────────────────────────────────────
def lease_state(name="deploy", db=None):
    r = _conn(db).execute("SELECT * FROM lease WHERE name=?", (name,)).fetchone()
    if not r or not r["holder"]:
        return dict(held=False, holder=None)
    age = time.time() - (r["acquired"] or 0)
    stale = age > (r["ttl"] or LEASE_TTL)
    return dict(held=not stale, holder=r["holder"], age=round(age, 1),
                ttl=r["ttl"], stale=stale, note=r["note"])


def acquire_deploy_lease(chat_id, db=None, ttl=LEASE_TTL, note=None, require_ready=True):
    """Serialize deploys to one chat at a time.

    `require_ready=True` is the point: the lease is not just a mutex, it is a GATE. You cannot hold
    the deploy lease with a tree that would revert someone — which is what turns "please rebase
    first" into something that doesn't depend on remembering."""
    st = lease_state(db=db)
    if st["held"] and st["holder"] != chat_id:
        return dict(ok=False, reason="deploy lease held by %s (%.0fs old, ttl %.0fs)"
                    % (st["holder"], st["age"], st["ttl"]), lease=st)
    if require_ready:
        r = deploy_readiness(chat_id=chat_id, db=db, fetch=True)
        if not r["ready"]:
            return dict(ok=False, reason="not deploy-ready", readiness=r)
    c = _conn(db)
    with c:
        c.execute("""INSERT INTO lease (name, holder, acquired, ttl, note) VALUES ('deploy',?,?,?,?)
                     ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,
                       acquired=excluded.acquired, ttl=excluded.ttl, note=excluded.note""",
                  (chat_id, time.time(), ttl, note))
    return dict(ok=True, lease=lease_state(db=db))


def release_deploy_lease(chat_id, db=None):
    st = lease_state(db=db)
    if st["holder"] and st["holder"] != chat_id and st["held"]:
        return dict(ok=False, reason="lease held by %s, not %s" % (st["holder"], chat_id))
    c = _conn(db)
    with c:
        c.execute("UPDATE lease SET holder=NULL, note=NULL WHERE name='deploy'")
    return dict(ok=True)


# ── survival check (the reconcile half of "self-heal") ───────────────────────────────────────────
def record_deploy(chat_id, deployed_sha, db=None, cwd=None):
    """Write down what this deploy is REQUIRED to contain, so the claim can be re-checked later.

    Recording expectations before the fact is what makes the later check meaningful — reconstructing
    "what should have been in there" after a suspected clobber is exactly the archaeology that cost
    hours the last two times.

    `cwd` names WHICH REPOSITORY these shas live in, and it is stored alongside them. Without it the
    later verification defaulted to the primary checkout and compared shas across unrelated repos —
    which produced a confident, entirely bogus 'clobbered' verdict (caught by the test). Ancestry is
    only meaningful within one object graph, so the graph has to be recorded, not assumed."""
    repo = cwd or REPO
    expected = {ch["id"]: ch["merged_sha"] for ch in chats(db=db) if ch.get("merged_sha")}
    main = _sha("origin/main", cwd=repo)
    if main:
        expected["origin/main"] = main
    c = _conn(db)
    with c:
        c.execute("INSERT INTO deploys (ts, chat_id, deployed_sha, expected, verdict, detail) "
                  "VALUES (?,?,?,?,?,?)",
                  (time.time(), chat_id, deployed_sha,
                   json.dumps(dict(repo=repo, shas=expected)), "pending", None))
    return dict(ok=True, deployed_sha=deployed_sha, expected=expected, repo=repo)


def verify_survival(deploy_id=None, db=None):
    """Re-assert that the deployed commit contains everything it was supposed to.

    Pure git ancestry against the recorded expectations — no container access needed, which means it
    works even after the machine has been replaced. An in-container marker grep is still the right
    SECOND layer (it proves the image was built from that tree), but ancestry is the cheap first one
    and catches the clobber class outright.

    `unknown` is its own verdict, distinct from `clobbered`. If git cannot resolve a recorded sha
    (the worktree is gone, the branch was pruned) that is missing INFORMATION, not evidence of a
    revert — reporting it as a clobber would train you to ignore the alarm."""
    c = _conn(db)
    row = (c.execute("SELECT * FROM deploys WHERE id=?", (deploy_id,)).fetchone() if deploy_id
           else c.execute("SELECT * FROM deploys ORDER BY id DESC LIMIT 1").fetchone())
    if not row:
        return dict(ok=False, verdict="none", reason="no deploy recorded", missing=[], unknown=[])
    blob = json.loads(row["expected"] or "{}")
    # Tolerate the pre-`repo` record shape so old ledger rows still verify.
    repo = blob.get("repo") or REPO
    expected = blob.get("shas") if "shas" in blob else blob
    dsha, missing, unknown = row["deployed_sha"], [], []
    for who, sha in (expected or {}).items():
        got = _contains(sha, dsha, cwd=repo)
        if got is False:
            missing.append(dict(who=who, sha=(sha or "")[:12]))
        elif got is None:
            unknown.append(dict(who=who, sha=(sha or "")[:12]))
    verdict = "clobbered" if missing else ("unknown" if unknown else "intact")
    bits = []
    if missing:
        bits.append("missing: " + ", ".join("%s@%s" % (m["who"], m["sha"]) for m in missing))
    if unknown:
        bits.append("unresolvable: " + ", ".join("%s@%s" % (u["who"], u["sha"]) for u in unknown))
    detail = ("deployed %s — %s" % ((dsha or "")[:12], "; ".join(bits))) if bits else None
    with c:
        c.execute("UPDATE deploys SET verdict=?, detail=? WHERE id=?", (verdict, detail, row["id"]))
    return dict(ok=not missing, verdict=verdict, deployed_sha=dsha, repo=repo,
                missing=missing, unknown=unknown, detail=detail)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cockpit chat coordination.")
    ap.add_argument("--sync", action="store_true", help="adopt git worktrees as chats")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--readiness", action="store_true", help="fleet deploy readiness")
    ap.add_argument("--verify", action="store_true", help="verify the last deploy survived")
    a = ap.parse_args(argv)
    if a.sync:
        print("adopted %d worktrees as chats" % sync_worktrees())
    if a.list:
        for ch in chats():
            print("%-34s %-11s %-28s %s" % (ch["id"][:34], ch["status"],
                                            (ch["branch"] or "")[:28], ch["subject"][:40]))
    if a.readiness:
        rows = fleet_readiness()
        ready = [r for r in rows if r["ready"]]
        print("fleet: %d chats | %d ready to ship | %d would revert something"
              % (len(rows), len(ready), len(rows) - len(ready)))
        for r in sorted(rows, key=lambda x: -x["reverts"])[:12]:
            print("  %-26s %-30s %s" % (r["chat_id"][:26], (r["branch"] or "")[:30],
                                        "READY" if r["ready"] else r["blockers"][0][:76]))
    if a.verify:
        print(json.dumps(verify_survival(), indent=2))
    if not any((a.sync, a.list, a.readiness, a.verify)):
        print(json.dumps(dict(chats=len(chats()), lease=lease_state()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
