# Handoff — Hoodie Cockpit

**Branch:** `feat/hoodie-cockpit` · **PR:** [#708](https://github.com/cfenness/hoodie-suite/pull/708)
· 7 commits · **416 checks passing** · not deployed (merging ships nothing here)

The surface built around one operator: route a task to the right model, answer from stored findings
instead of re-deriving them, run a role crew when the work earns it, and make concurrent chats
mechanically unable to clobber each other's deploys.

---

## Why it exists — measured, not asserted

228 local Claude Code sessions, 34.5B tokens, price-weighted (opus 5/25, cache-read 0.1×, write 1.25×):

| | |
|---|---|
| **89.6%** of spend | context handling — 62.7% re-reading history + 26.9% cache writes |
| 10.3% | output |
| 0.2% | fresh input |

Attributed to the prompt that started each agentic loop:

| class | prompts | asst msgs | share of cost |
|---|---|---|---|
| **triage** | 1,639 | **45,301** | **66.9%** |
| master | 170 | 5,986 | 9.0% |
| surface | 122 | 6,023 | 8.1% |
| scrape | 130 | 5,437 | 7.9% |

Quick lookups average **27.6 assistant messages** each because the answer is re-derived every time.
Confirmed on live traffic afterwards: a trivial haiku turn cost 45,507 context tokens for a 42-token
answer; a real question cost 29,076 cache-read for 78 tokens. **Retrieval, not a cheaper model, is
the lever.** Model routing alone is worth ~58.5% token-weighted.

---

## Modules (`unifyd/`, all stdlib-only)

| module | does | tests |
|---|---|---|
| `agent_router.py` | task → model / effort / tactic / thread action | 95 |
| `agent_memory.py` | SQLite FTS5 fact store: staleness + relevance gate + write-back | 76 |
| `agent_chats.py` | claims, deploy lease, anti-clobber, chat transcripts | 57 |
| `agent_roles.py` | PM / engineer / QA / lead-reviewer crews | 63 |
| `agent_checks.py` | the deterministic checker | 43 |
| `agent_exec.py` | the Claude Code CLI seam + run ledger | 48 |
| `agent_import_chat.py` | scoped claude.ai export intake | 34 |
| `agent_mine.py` | mine stated rules from transcripts | **none — see Open** |

Surface: `apps/cockpit.html` · endpoints `/api/cockpit/*` in `unifyd/server.py` · agent definitions
`.claude/agents/hoodie-{pm,qa,reviewer}.md` (generated — see below).

---

## The three things that are load-bearing

**1. Subscription rail, never metered.** The engine is `claude -p` driven headlessly, authenticating
from the OAuth login in `~/.claude.json` (token itself in the macOS Keychain, service
`Claude Code-credentials`). This is a *different billing rail* from the ~17 `unifyd/` modules that use
`anthropic` + `ANTHROPIC_API_KEY`. `agent_exec` **refuses to run** if it sees a stray API key rather
than silently switching rails. Dispatch is local-only — `/api/cockpit/run` and `/chat` refuse when
`FLY_APP_NAME` is set, because the credential exists only on the Mac.

> **Auth gotcha, cost ~45 min:** `claude auth status` reports `loggedIn: true` from the mere *presence*
> of a credential, so a stale one makes plain `/login` skip the browser flow entirely. Recovery is
> `claude auth logout` then `claude auth login`, run **separately** — chained with `&&`, the second
> never runs if the first prompts. Liveness is only knowable by actually running.

**2. Anti-clobber is mechanical.** Measured live: **57 worktrees, 54 of which would revert
`origin/main` if deployed as-is.** `deploy_readiness()` blocks any tree not containing `origin/main`
*and* every sibling chat's merged commit, naming the commits that would be lost. The deploy lease is a
**gate**, not a mutex — it cannot be held by a not-ready tree. **`unknown` is treated as unsafe**: an
unverifiable sibling blocks rather than passes, which is what made the check a silent no-op before.

*Honest limit:* this guarantees **no silent reverts**. It does not solve semantic conflict — claims
give awareness, not correctness.

**3. Nothing is served without a verdict.** Every fact hashes the file it came from; a changed file
reads `stale`, a missing one `unverifiable`, and neither is ever a hit. Written-back model answers are
`inferred`, never dressed as declared truth.

---

## What the crew found (why it exists)

An independent PM → QA → lead-review pass over `agent_memory.py` found **four real bugs in a module
with 65 passing tests**, and the reviewer escalated one to a blocker. The diagnosis that mattered:

> *"`_TOK_STOP` already concedes the design is patch-by-observation; `and` was added after a live false
> hit, `data` and `note` weren't."*

That was a correct indictment of how I'd been fixing it — reactively, always one incident behind. The
rule now is **coverage**: the query must name enough of the subject to identify it (2+ of its tokens,
or all when it has one), plus a claim alone never qualifies on a single token because *a property with
no subject identifies nothing*. Store-size independent, no maintained word list.

**A reviewer is never weaker than the author** (`crew_for()` floors QA and reviewer at the author's
tier). Haiku reviewing Opus output returns a confident approval that means nothing — worse than no
review, because now there's a green check on it.

---

## Deterministic checks come first

`agent_checks.py` runs the repo's own verification over the changed paths and hands the results to the
crew with an explicit *"do not repeat these"*. Two rules it won't bend: a check that could not run
reports **`skipped`, never `pass`**; evidence is the **actual output**, never a summary. Missing
coverage is itself a finding — its first run named three of my own untested modules, which is why they
now have suites.

---

## Open

| item | note |
|---|---|
| **`agent_mine.py` has no paired test** | Flagged by the checker. The clustering and scope logic are pure and testable. |
| 8 legacy modules untested | `publix`, `instacart`, `browser_warm`, `menu_site`, `off_premise`, `doordash_discover`, `server`, `source_registry`. Pre-existing; separate lane. |
| Ask is model-or-facts, not both | On a hit the model is never consulted. Fine for lookups; a hybrid may be better for judgement questions. |
| Crew stages are manual | `crew_for()` plans and prices; running the stages is still hand-driven. The agent definitions make it scriptable either way. |
| Third-party checkers | Deliberately not built. Recommendation: wire **GPT alone first**, instrument finding-attribution, and read the overlap rate after ~10 reviews before adding Gemini — turn "do we need two?" into a number. Both are metered, so it's a deliberate exception to the no-variable-cost rule, defensible scoped to `correctness=max` lanes. |
| Export was `batch-0000` | Check for further batches from the claude.ai data export. |

---

## Running it

```bash
python3 unifyd/agent_memory.py --harvest        # seed 414 facts from source_registry
python3 unifyd/agent_checks.py                  # deterministic checks over your changes
python3 unifyd/agent_chats.py --readiness       # who could ship without reverting anyone
python3 unifyd/agent_roles.py --task "..."      # plan and price a crew
```

The page needs the local agent (`.claude/launch.json` → `cockpit-verify`, port 8918). It degrades to
measured fallback figures with the agent offline — date-stamped so they can't silently age.
