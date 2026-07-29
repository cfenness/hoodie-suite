# Release train

Safe integration and deploy when several Claude Code sessions work this repo at once.

## Why

Each session gets its own worktree on its own branch. That is the right isolation for **editing**
and the wrong one for **shipping** — nothing reconciles the branches, and `flyctl deploy` ships the
**local tree**, not `main`. Both failure modes have already happened here:

- A deploy run from a worktree, or from a primary checkout parked on a feature branch, pushes that
  branch to production and silently reverts whatever another session shipped.
- A branch looks unmerged forever because `git rev-list main..branch` counts **commits**, and a
  squash-merge leaves the commits behind after the content has landed. You cannot tell stranded
  work from merged work by commit count — so both get ignored, and real work rots.

The first survey of this repo found 65 worktrees, a primary checkout 31 commits behind `main` on an
unmerged feature branch with 20 uncommitted files, and 43 branches "ahead" of `main` — of which only
32 carried real content and 26 had drifted into conflict.

## Commands

```bash
python3 tools/release_train.py survey
```

Read-only. Touches nothing. Reports, in order of danger:

1. **Deploy hazards** — is the *primary* checkout on the default branch, clean, and up to date?
   (Checked against the primary worktree no matter which worktree you run from.) Is another
   release train already holding the lock?
2. **Worktrees** — how many, which are prunable, which hold uncommitted work.
3. **Branches by content, not commit reachability** — each branch is classified `unmerged`,
   `CONFLICTS`, or squash-merge leftover, by merging it into a scratch tree and comparing to the
   base tree. This is the check that tells stranded work from noise.
4. **Open PRs** with mergeability.

Exits non-zero when hazards are present, so it can gate other automation.

```bash
python3 tools/release_train.py integrate [--only 644,645] [--verbose]
```

Creates `integration/<timestamp>` from `origin/<default>` in a throwaway worktree, merges each open
non-draft PR in number order, then runs `tools/smoke_check.py` and every `unifyd/*_test.py`.

- **On conflict it stops** and reports the conflicting files plus who last touched each on the
  default branch — i.e. which other session you need to talk to. The half-merged worktree is left
  in place so the conflict can be resolved. `main` is never touched.
- A suite that cannot run for want of an optional dependency is reported **SKIP**, never counted as
  a pass. A check that quietly did not run is the same lie as a guard that quietly degrades. To keep
  skips rare it picks the most capable interpreter available (probing for `duckdb`, `pyarrow`,
  `flask`, `numpy`; override with `RT_PYTHON`) — on a bare `python3` the first run skipped 22 of 38
  suites, which proves almost nothing.
- Every failure is labelled **INTRODUCED by this change set** or **ALREADY FAILS on the base**, by
  re-running just the failing checks against a clean base checkout. Those need different owners and
  different urgency, and the train only blocks on the introduced ones.

Integrate never merges to the default branch and never deploys.

```bash
python3 tools/release_train.py deploy [--dry-run]
```

The only command that can affect production, and it is always explicit.

- Builds a **fresh detached checkout at `origin/<default>`** rather than trusting the caller's cwd,
  then proves `HEAD == origin/<default>` and the tree is clean before doing anything.
- `flyctl deploy --remote-only --ha=false`, then prints `flyctl releases` so the release is
  *verified*, not assumed.
- If `source_registry.py` moved in the deployed range it runs `tools/repin_dispatcher.sh`, because
  the hourly dispatcher machine is not updated by a deploy and would otherwise keep running a stale
  registry and never dispatch the new source.

## What it caught on its first run

- The primary checkout sitting on an unmerged feature branch, 31 commits behind `main`, with 20
  uncommitted files — a `flyctl deploy` from there would have shipped that branch to production.
- 43 branches "ahead" of `main` reduced to the 32 that carry real content, with 11 identified as
  squash-merge leftovers that are safe to delete, and 26 flagged as having drifted into conflict.
- `snowflake_load_test` failing on PR #644 — which GitHub reports as `CLEAN`, because CI does not
  run that guard. The baseline check then showed it fails on that PR's own branch tip too, so it is
  the PR's own defect rather than an integration problem.

```bash
python3 tools/release_train.py reconcile [--files]
```

Classifies every branch ahead of the base and recommends an action. Read-only; it never deletes.

| Bucket | Meaning |
|---|---|
| `IN FLIGHT` | has an open PR — leave it to its session |
| `REVIVABLE` | real content that still applies cleanly — decide now, while that is still true |
| `CONFLICTED` | real content that has drifted — rebase or cherry-pick, never merge |
| `OBSOLETE?` | every file it touches is gone from the base — the area was probably restructured away |
| `SCAFFOLD` | named `debug/`, `tmp/`, `wip/`… — throwaway by intent, confirm then delete |
| `ABSORBED` | content is provably already in the base — safe to delete |

A branch is only ever proposed for deletion when its content is provably in the base or its name
declares it disposable. Everything else is surfaced with its subject line so a human can judge in
seconds whether the work still matters — which is much cheaper than resolving a conflict first and
discovering afterwards that nobody wanted it.

## Read config from the base, not from a working tree

The first time this tool was used, its own author reported a false "policy contradiction" after
listing `.github/workflows/` in the primary checkout — which was 31 commits behind and still had
six workflow files that `main` had long since deleted. Use `git show origin/main:<path>`. The whole
point of the survey is that a working tree is not the truth.

## Locking

`survey` is lock-free. `integrate` and `deploy` take an exclusive lock in the **common git dir**,
which every worktree shares — so it actually excludes the other sessions rather than only itself.
Locks older than `RT_LOCK_STALE_SEC` (default 3600) are treated as abandoned and reclaimed.

## What this deliberately does not do

It does not resolve conflicts, decide whether two changes belong together, or deploy on its own
judgment. Those are judgment calls. Everything here is the mechanical part — the rules that are
easy to state, easy to skip at 2am, and expensive to get wrong.
