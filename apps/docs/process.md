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

1. **Agentic review** — an adversarial pass over the diff by a FRESH context (not the author's):
   the `/code-review` skill or a spawned reviewer agent, hunting correctness bugs, first-law
   violations, silent data loss, law drift. Findings land in the ticket's `review` field —
   "none found" is a valid finding; an unrun review is not.
2. **Agentic QA** — verification executed, not asserted: engine changes run self-tests + end-to-end
   smoke; UI changes are driven in a real browser (playwright) and the rendered result is REVIEWED
   (screenshots read, not just non-erroring). Evidence goes in `verification` as observed outputs.

A surviving finding becomes a fix on the same ticket or a new ticket — never a silent pass. The
session that wrote the code may run the gates only via fresh-context agents.

## Docs-as-code

This handbook (`apps/docs/`) updates in the SAME commit as any behavior/contract/process change.
Audience pages orient and point; the canonical deep docs stay canonical. New page = one .md + one
`index.json` entry.

## Sizing & priorities

S ≤ half a day · M half-day–2 days · L 2–5 days · XL must be split before starting.
P1 do-next · P2 scheduled · P3 opportunistic.
