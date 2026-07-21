# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

<!-- SEED NOTE: this file was seeded from hoodie-suite/canon-seed/. Fill the "What this is" section
     from the repo's actual phase-1 state, keep the LAWS below verbatim (they must not drift from
     hoodie-suite/MDM_FLOW.md — that doc is the canon of record), and delete this comment. -->

## What this is

**hoodie-canon** — the 7-phase, agent-integrated rebuild of the end-to-end MDM pipeline:
land → clean → resolve → survive → verify → serve, with **agents doing the work and human
involvement limited until absolutely necessary**. The engine ideas and the design canon it builds
from live in `hoodie-suite` (`unifyd/flow.py`, `MDM_FLOW.md`) — read those before designing here.

<!-- Describe the current phase, layout, and dev loop here from the repo's real state. -->

## The laws (must not drift — canonical statement: hoodie-suite/MDM_FLOW.md)

1. **The first law: never fix the row — fix the rule.** The golden record is a derivation, not a
   document. Every steward/agent action is a rule that re-materializes on rebuild; a one-off
   correction is an override rule with provenance (who · when · why · evidence) and a scope of one.
   No surface may edit a mastered value directly.
2. **The automatch standard: 90–95% — accuracy-first, by definition.** NOT a quota. **Accuracy is
   the binding constraint; automation is maximized subject to it.** 90–95% is the high end of the
   range where accuracy stays front and center. The rate is **earned, never bought**: it rises
   through better tiers (normalizers, evidence, whitelisted authorities) — never by loosening match
   thresholds. A decision the cascade cannot make accurately goes to the human — that is the design
   working, not failing. Measure the pair per entity per build: `auto / claude-verified /
   oracle-resolved / human` shares **and** false-merge precision by audit sampling. Either number
   moving the wrong way is a regression.
3. **The verification cascade.** T0 deterministic → T1 Claude-verify (fetch the fact from a
   whitelist of authoritative sources; verdict + evidence, never a vote) → T2 external oracle
   (e.g. Google Places = oracle-not-store: persist only the decision + durable id, never the
   payload) → T3 human, last and rarest.
4. **Real and synthetic never commingle.** Separate namespaces; a build reads exactly one; nothing
   synthetic is ever servable to a consumer; synthetic surfaces wear a loud badge.
5. **Identity from public first principles.** No proprietary vendor IDs in identity — public,
   observable, or open-standard signals only (name/address/geo/license, UPC/GTIN, TTB filing).
   Stable Hoodie IDs (HO-…) minted from a registry, never reminted on rebuild.
6. **Catch-alls are populated-but-empty.** "other"/"misc"/"unknown" never count as informative;
   catch-all density is a verify trigger (≥40% of *responded* fields once identifier-level), not
   acceptable data.
7. **The engine is domain-agnostic; a domain is a config pack.** Bev-alc is unique in field
   requirements, not tool capacity. A new domain (the lettuce test) is config, not a fork.

## Ticket discipline (HARD RULE)

Every non-trivial unit of work moves through a ticket. **No implementation without a ticket; no
ticket left stale.** The single source of truth is **`tickets.json`** (repo root); the board is
`tickets.html` (serve the repo root and open it). Ticket prefix: `HC-`. A ticket must carry enough
detail that a lead engineer can pick it up cold.

1. **Before writing code:** create the ticket (id `HC-<next_id>`, bump `meta.next_id`). Required
   fields: `title`, `type` (`feat|fix|chore|docs`), `status`, `size` (S/M/L/XL — XL must be split
   before starting), `priority`, `summary`, `context` (why / the decision behind it), `acceptance`
   (testable criteria), and a `verification_plan`. Trivial exceptions (typo-level) may skip a ticket;
   when in doubt, ticket it.
2. **Pipeline:** `backlog → ready → in-progress → in-review → done`. Update `status` as work moves —
   `in-progress` when you start, `in-review` when pushed and awaiting review/verification, `done`
   only when acceptance criteria are verified (evidence recorded in `verification`).
3. **On completion:** fill `commits` (short SHAs), `files`, `implementation` (concrete notes — key
   functions, decisions, gotchas), and replace `verification_plan` with `verification` (what was
   actually run/observed). Update `updated`.
4. **Commits reference tickets** — mention the id (e.g. `HC-003`) in the commit body when work maps
   to a ticket.
5. Discovered follow-up work becomes a **new ticket** (link it in `followups`), not a mental note.
6. **Phase mapping:** give each of the 7 phases an epic-level ticket; work tickets link their phase
   in `context`. A phase is `done` only when its acceptance is verified — including its automatch /
   precision numbers where applicable.

(The same rule governs `hoodie-suite` (`HS-`), `hoodie-backend` (`HB-`), `hoodie-app` (`HA-`).)

## Git conventions

- **Never commit directly to `main`** — feature branches, conventional commits (`feat:`, `fix:`,
  `chore:`, `docs:`), short body on non-trivial diffs.
- Never commit secrets.
