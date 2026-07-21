# Process — tickets, propose→confirm, agentic review & QA

The operating discipline, identical across all repos (HS- suite · HB- backend · HA- app ·
HC- canon). Canonical text: each repo's `CLAUDE.md` "Ticket discipline (HARD RULE)".

## The ticket rule (one paragraph)

Every non-trivial unit of work moves through a ticket in the repo's `tickets.json`, rendered on its
board. A ticket carries enough detail that a lead engineer picks it up cold: summary, context/why,
testable acceptance criteria, implementation notes, verification EVIDENCE (observed outputs, not
intentions), files, commits. Follow-ups become tickets, never mental notes.

## Propose → Confirm (the scope gate)

Before implementation, Claude **drafts the ticket and returns it in chat** — title, size,
acceptance criteria, verification plan — as status `proposed`. The user confirms or adjusts in
plain language; Claude updates the ticket; only a confirmed ticket is built. Why: decisions stay at
the user level without the user writing tickets, and scope is agreed BEFORE tokens are spent
building the wrong thing. Skips: an explicit "just do it", work the user already specified in
detail in chat (record that as the confirmation), typo-level fixes.

## The pipeline

`proposed → backlog → ready → in-progress → in-review → done`

- `proposed` — drafted, awaiting user confirmation/adjustment.
- `backlog` — confirmed idea, not scheduled. `ready` — next up, spec complete.
- `in-progress` — being built. `in-review` — pushed, gates running.
- `done` — acceptance verified with recorded evidence, gates passed.

## Agentic review & QA (the quality gates: in-review → done)

Run as **parallel fresh-context agents** at professional-QA-team throughput — the per-ticket
workload is DEFINED, not discretionary:

1. **Agentic review** — every changed hunk read adversarially (correctness, first-law violations,
   silent data loss, law drift, injection/escaping, failure paths); every owner-authored
   `review_notes` item explicitly addressed; engine+UI diffs get one reviewer per surface in
   parallel. Findings triaged **blocker/major/minor** into the ticket's `review` field. Blockers
   stop the close. "None found" is a valid finding; an unrun review is not.
2. **Agentic QA** — every acceptance criterion EXECUTED; every owner-authored `qa_checks` item
   EXECUTED with per-item PASS/FAIL + observations in `qa_results`; the FULL self-test suite run;
   UI driven in a real browser with at least one negative/edge probe per surface; renders REVIEWED.

The owner writes `qa_checks` and `review_notes` in plain English on any ticket (inline, in the
board's editor) — the agents are REQUIRED to execute each item. A surviving finding becomes a fix
or a new ticket — never a silent pass.

## Editing tickets inline

The ticket STRUCTURE is `apps/tickets.schema.json` — add/remove/reorder sections there and every
ticket drawer follows, no code change. With the engine up, the board's drawer has an **edit** mode:
change any section, add/remove items, save → writes `apps/tickets.json` on disk (commit the file
for durability; the board reminds you).

## Docs-as-code

This handbook (`apps/docs/`) updates in the SAME commit as any behavior/contract/process change.
Audience pages orient and point; the canonical deep docs stay canonical. New page = one .md + one
`index.json` entry.

## Sizing & priorities

S ≤ half a day · M half-day–2 days · L 2–5 days · XL must be split before starting.
P1 do-next · P2 scheduled · P3 opportunistic.
