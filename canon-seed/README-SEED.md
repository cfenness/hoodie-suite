# canon-seed — transplant kit for `hoodie-canon`

The ticket discipline + the MDM laws, packaged to drop into the `hoodie-canon` repo in one step.
Built here because this session couldn't reach the canon repo directly (repo-add pending approval);
the canon-building session — or anyone with the repo checked out — installs it with:

```bash
# from the hoodie-canon repo root, with hoodie-suite checked out alongside:
cp ../hoodie-suite/canon-seed/CLAUDE.md ../hoodie-suite/canon-seed/tickets.json ../hoodie-suite/canon-seed/tickets.html .
```

**If `CLAUDE.md` already exists in canon: MERGE, don't clobber** — keep canon's own orientation and
append the "The laws" + "Ticket discipline (HARD RULE)" sections from the seed verbatim.

Then do ticket **HC-001** (already in the seeded `tickets.json`): fill the orientation from the
repo's real state and **backfill phase 1 from actual git history** — done tickets with evidence,
the remaining phases as epics. Never invent backfill; it must be written by a session that can see
the repo.

Two invariants the seed encodes — do not weaken them in transit:

- **The laws are verbatim-synced to `hoodie-suite/MDM_FLOW.md`** (the canon of record). If a law
  needs to change, change it there first, then propagate.
- **Accuracy-first automatch**: 90–95% is the high end of what accuracy permits, earned through
  better tiers, never bought with looser thresholds; measured as the pair (automatch shares +
  false-merge precision). The measurement harness comes before the rate.

Delete this file from the canon repo after install (it's install instructions, not product).
