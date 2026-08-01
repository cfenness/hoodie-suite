# Handoff — Hoodie Cockpit

**Branch:** `feat/hoodie-cockpit` · **PR:** [#712](https://github.com/cfenness/hoodie-suite/pull/712)
· current HEAD `9c02e3c` · not deployed (merging ships nothing here)

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
| `agent_chats.py` | claims, deploy lease, anti-clobber, chat transcripts, tactic-savings rollup | 61 |
| `agent_roles.py` | PM / engineer / QA / lead-reviewer crews | 63 |
| `agent_checks.py` | the deterministic checker | 43 |
| `agent_exec.py` | the Claude Code CLI seam + run ledger; explicit metered-API opt-in | 69 |
| `agent_import_chat.py` | scoped claude.ai export intake | 34 |
| `agent_mine.py` | mine stated rules from transcripts | 38 |
| `agent_tickets.py` | ticket + epic lifecycle: PM draft → editable criteria → crew run → docs; forward-only status; Jira-parity export; structured verdicts; cost receipt; evidence attachments | 138 |

`agent_tickets.derive_title()` treats a model's own markdown headings as section labels, never a
title: it prefers the prose under a heading named `outcome`, then an inline `Outcome:` label, then
the first non-heading line — found live on the first two real tickets created through the panel,
not in a fixture.

**Storage (v2): one JSON file per ticket, tracked by git.** Tickets live at
`unifyd/cockpit_tickets/tickets/<id>.json` and epics at `unifyd/cockpit_tickets/epics/<id>.json` —
inside the repo and **committed**, not `unifyd/agent_state/cockpit/`, which is `.gitignore`d (the
same bucket as scrape caches and run logs). This is the headline change: a ticket now survives across
machines and worktrees the normal way — `git commit`, `git push`, `git log` — instead of living only
on whichever Mac created it. See the "Ticket storage, Jira parity, and PR linkage" section below.

Surface: `apps/cockpit.html` · `apps/md-viewer.html` (live-updating ticket body viewer) · endpoints
`/api/cockpit/*` plus the ticket routes `/api/cockpit/tickets` (POST create / GET list),
`/api/cockpit/tickets/<id>` (GET / PATCH), `/api/cockpit/tickets/<id>/raw`,
`/api/cockpit/tickets/<id>/run`, `/api/cockpit/tickets/<id>/docs`, `/api/cockpit/tickets/<id>/link-pr`,
`/api/cockpit/tickets/export?format=json|jira-csv`, plus the epic routes `/api/cockpit/epics`
(POST create / GET list-with-rollup), `/api/cockpit/epics/<id>` (GET / PATCH), plus
`/api/cockpit/tactics-savings` (below) — all in `unifyd/server.py:7557–7900+` · agent definitions
`.claude/agents/hoodie-{pm,qa,reviewer}.md` (generated — see below).

---

## Ticket storage, Jira parity, and PR linkage

**One file per ticket, deliberately not one shared index.** A single `tickets.json` array (or an
index file alongside per-ticket bodies) means two chats editing *different* tickets collide on the
*same* file — the shared-mutable-file contention `agent_chats.py`'s whole anti-clobber system exists
to guard against (see "Anti-clobber is mechanical" above). One file per ticket makes that collision
class impossible; `list_tickets()` just globs the directory. It also collapses the old two-file
shape (a JSON index row plus a separate raw-markdown body file, which could desync) into one
structured record — `render_markdown()` renders `description_md` + `activity[]` into the same
markdown the old body file held, on demand, so `apps/md-viewer.html` and the `/raw` endpoint see no
difference.

**Jira-parity fields.** `issue_type` (story/bug/task/chore), `priority` (lowest–highest), `labels`,
`story_points`, and `epic_id` put a ticket on the same footing as a real Jira issue; `epics/<id>.json`
gives tickets somewhere to roll up (`epic_rollup()` — ticket counts by status + summed story points,
what the Epics sub-view renders per row).

**CSV/JSON export — a bridge, not a sync.** `GET /api/cockpit/tickets/export?format=jira-csv` writes
`agent_tickets.jira_csv()` in Jira's standard bulk-CSV-importer column shape (Summary/Issue
Type/Priority/Labels/Epic Link/Story Points/Description/Status) — a direct drag-and-drop import.
`format=json` returns the full-fidelity records. This is explicitly a **stopgap**: one-way, pull-based,
nothing pushes back from Jira, and there is no live two-way sync — that's EPIC-4 (T-4.2), not built.
The export exists so the shape converges with what a real sync would eventually push, not so the sync
itself can be skipped.

**PR linkage.** `pr` is plain data on the ticket record (`number`/`url`/`branch`/`state`/`repo`),
settable by hand via `PATCH /api/cockpit/tickets/<id>` with a `pr` object, or auto-populated by
`POST /api/cockpit/tickets/<id>/link-pr`, which shells out to `gh pr view --json ...` in the ticket's
own worktree. That route is **best-effort local `gh`, not a GitHub API integration** — it needs a
real git checkout and local `gh` auth, so it's Mac-only (`_on_fly()`-gated, same reasoning as chat and
crew dispatch), and it fails to a plain "not linked" rather than erroring the ticket. `agent_tickets.py`
itself has no network or git access of its own (stdlib-only, unit-tested standalone) — `set_pr`/
`set_jira` just write the fields server.py's routes hand them.

**Structured verdicts + the Activity panel (T-2.1).** `agent_tickets.extract_verdict_json()` pulls
the LAST fenced ```` ```json ```` block out of a QA/reviewer report and parses it — `ROLES[qa]`/
`ROLES[reviewer]` in `agent_roles.py` now end their system prompts asking for one (QA: pass/fail/
untested per criterion + evidence + severity; reviewer: ship/ship-with-followups/blocked + named
blockers), **in addition to** the full prose report, never instead of it. `add_activity(verdict=...)`
stores it on the activity entry; a missing or unparseable block just means `verdict: None` — the
prose still lands either way, nothing is ever silently dropped. `apps/cockpit.html`'s ticket detail
renders this as an **Activity** section (fixing a real gap: reports were landing in `t.activity` but
the panel never rendered that array) — verdict pills, per-criterion QA rows, reviewer blockers, and
the full report always one click away behind "Full report" rather than hidden.

**Cost receipt (T-2.3) — tokens and burn, deliberately not dollars.** `agent_tickets.
ticket_receipt(rec)` itemizes every stage that spent real subscription burn: model, effort, tactics,
actual input/output tokens, and the router's own relative `burn_index`. It does **not** report a
dollar figure — `agent_router.py`'s own docstring is explicit that this engine runs on a flat-fee
Claude subscription, not per-token API billing ("the objective is NOT minimize dollars"), so
inventing one would be exactly the kind of unsupported claim this repo's standing rules forbid.
`GET /api/cockpit/tickets/<id>` includes the computed `receipt`.

**Tactic savings, read back not re-estimated.** `agent_chats.tactics_savings()` (`GET
/api/cockpit/tactics-savings`) sums `filler_removed` — `cavemanize()`'s exact, deterministic word
count — off every stored chat turn's `route` snapshot, and tallies how often each tactic (caveman/
terse/scope/evidence/noverify) was applied. It reports usage counts only for the non-caveman
tactics, never a fabricated per-turn savings number for them, since they shape the model's output
rather than stripping anything measurable pre-send. The same `filler_removed` field now flows
through the ticket cost receipt too, though it's currently always 0 there — `run_crew()`'s stages
build their own route dicts with `tactics=[]` (crew stages use role-based system prompts, not the
class-based tactic system), so this is a real-but-currently-empty field, wired for when/if that
changes.

**Evidence attachments (T-2.2).** `add_activity(attachment=...)` stores a plain, caller-owned dict
referencing evidence that lives elsewhere. `server.py`'s `preview-snapshot` and `preview-diff` routes
accept an optional `ticket_id` and, when given, land a `snapshot` (the `preview_shot.py` key) or
`visual_diff` (both keys + `diff_pct`) activity entry — `preview_shot.py` itself stays a plain
URL-keyed store with no ticket awareness; the association lives entirely on the ticket. Diffs store
metadata only, not the overlay image — re-deriving it is one more POST with the same two keys.

**Crew findings now write back to memory.** `POST /api/cockpit/tickets/<id>/run` calls
`agent_memory.remember_answer()` for each stage's result (`server.py:7697–7704`) — the *same*
write-back mechanism `/api/cockpit/chat` already uses on a model-answered miss (see "3. Nothing is
served without a verdict" above), a second caller of it, not new machinery. Without this a ticket's
engineer/QA/reviewer findings vanished the moment the ticket closed; now a later ticket or chat on the
same subject can hit the fact store instead of re-deriving what the crew already found.

---

## The four things that are load-bearing

**1. Subscription rail, never metered — an explicit opt-in, not an ambient fallback.** The engine is
`claude -p` driven headlessly, authenticating from the OAuth login in `~/.claude.json` (token itself
in the macOS Keychain, service `Claude Code-credentials`). This is a *different billing rail* from
the ~17 `unifyd/` modules that use `anthropic` + `ANTHROPIC_API_KEY`. Dispatch is local-only —
`/api/cockpit/run` and `/chat` refuse when `FLY_APP_NAME` is set, because the credential exists only
on the Mac.

An earlier version of this point made `agent_exec` **hard-refuse** every dispatch whenever
`ANTHROPIC_API_KEY` was merely *present* in the environment — which punished the wrong thing: any
unrelated tool exporting that variable into the shell blocked every run until someone noticed and
unset it by hand, and it was never actually a deliberate safeguard (working around the refusal to
use the key on purpose defeated its own point). The real fix is `agent_exec.metered_allowed()`
(`unifyd/agent_state/cockpit/exec_settings.json`, machine-local, **defaults False**) — the ONE thing
that decides behavior now:
- **Off (default):** `dispatch()`'s `_dispatch_env()` strips `ANTHROPIC_API_KEY` from the *subprocess*
  environment before invoking `claude` — the run proceeds normally on the subscription regardless of
  what's ambiently set. `auth_mode()` reports this as `subscription (ANTHROPIC_API_KEY present but
  ignored)` with a low-key `note`, deliberately not a `warning` (the Cockpit only surfaces `warning`
  as a "needs your attention" item, and this is a handled, working state).
- **On:** the environment is left untouched, the CLI's own normal OAuth-vs-API-key precedence
  applies, and `auth_mode()` reports `api-key (METERED — explicitly enabled)` *with* a `warning` —
  real spend is now possible. Toggle via the Cockpit UI (Standing panel, below the tiles) or
  `POST /api/cockpit/metered {metered_allowed: bool}`.

`_dispatch_env(auth, base_env=None)` is a pure function (env in, env out, no subprocess) specifically
so this policy is unit-tested without this module's test suite ever dispatching for real — see its
own docstring and `agent_exec_test.py`'s "5c/5d" sections.

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

**4. A failed dispatch is never a quiet miss.** `agent_exec.dispatch()` sets `rec["error"]` for a
non-JSON payload, a non-zero exit, or the CLI's own `is_error` flag — from the actual stderr/stdout,
never a placeholder — and never reports a result alongside it. Callers decide success by
`rec.get("error")` alone (`run_crew()` stops at the first failing stage; `api_cockpit_chat` sets
`answered`/`error`), so before this a real failure read as an ordinary empty answer and a crew ran
straight past a stage that had actually died.

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
| 8 legacy modules untested | `publix`, `instacart`, `browser_warm`, `menu_site`, `off_premise`, `doordash_discover`, `server`, `source_registry`. Pre-existing; separate lane. |
| Ask is model-or-facts, not both | On a hit the model is never consulted. Fine for lookups; a hybrid may be better for judgement questions. |
| Ticket runs are operator-triggered | `POST /api/cockpit/tickets/<id>/run` dispatches engineer/QA/reviewer via `run_crew()` (`server.py:7460`) end-to-end, but nothing schedules it — a human still clicks Run Crew from the ticket panel. The PM stage is deliberately skipped there (a human already filled that role by editing the criteria). |
| Third-party checkers | Deliberately not built. Recommendation: wire **GPT alone first**, instrument finding-attribution, and read the overlap rate after ~10 reviews before adding Gemini — turn "do we need two?" into a number. Both are metered, so it's a deliberate exception to the no-variable-cost rule, defensible scoped to `correctness=max` lanes. |
| Export was `batch-0000` | Check for further batches from the claude.ai data export. |
| Jira export is one-way, not a sync | `jira_csv()`/`format=json` on `/api/cockpit/tickets/export` is a pull-based CSV/JSON bridge only — no push from Jira, no live sync. Real two-way integration is EPIC-4 (T-4.2), not started. |

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
