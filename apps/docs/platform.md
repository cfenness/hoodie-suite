# The platform — repos & how they connect

One product, four repos, one discipline. Every repo carries the same ticket hard rule (its own
`tickets.json` + board) and the same laws, synced from this repo's `MDM_FLOW.md`.

## The estate

**hoodie-suite** (this repo) — the prototype suite AND the engine host.
- `index.html` + `apps/*.html` — the dependency-free static suite (launcher + single-file apps).
- `unifyd/` — **the owned layer**: scrapers/connectors, the warehouse (Parquet + DuckDB), the MDM
  flow engine (`flow.py`), and the Flask server that serves both the suite and `/api/*` on Fly
  (`hoodie-suite.fly.dev`, behind a Google OIDC gate). The engine is never web-served as source.
- Boards & spaces: `#tickets` (HS-), `#roadmap`, `#docs` (this handbook), `#mdm` (the MDM console,
  including the Flow workbench).

**hoodie-backend** — the two-track backend prototype. Track 1: Firebase Functions AI proxy +
Firestore user-state persistence (reviewable, not deployed). Track 2: DuckDB analytical data layer,
proven at 1.6M rows. **The load-bearing rule: depletion data never goes in Firestore — analytics is
DuckDB/SQL.** Board: `tickets.html` (HB-).

**hoodie-app** — the production frontend monorepo. `packages/core` (@hoodie/core: domain types +
a typed client GENERATED from the engine's served OpenAPI spec), `apps/mobile` (Expo — the primary
surface: Prism, Hoodie Intelligence, Accounts), `apps/web` (Next stub). Frontend only — it talks to
the suite's engine over `/api/*`. Board: `tickets.html` (HA-).

**hoodie-canon** — the 7-phase, agent-integrated rebuild of the end-to-end MDM pipeline (land →
clean → resolve → survive → verify → serve), human involvement limited until absolutely necessary.
Seeded from `canon-seed/` in this repo (laws + ticket rule, HC-). North star: **90–95% automatch,
accuracy-first** — see the Data page.

## How data flows

```
sources (state registries, TTB COLA, retailers, census…)
   → unifyd connectors → warehouse Parquet (real/ vs synthetic/ — never commingled)
   → the flow engine (clean → union → resolve → survive) → golden dim_ tables + stable Hoodie IDs
   → /api/serve/* (REAL ONLY, by construction) → hoodie-app + customers
```

## Canonical deep docs (read these, don't re-derive)

- `MDM_FLOW.md` — the MDM design canon: the laws, the verification cascade, the automatch standard.
- `BACKEND_DESIGN.md` — the backend architecture (star schema, warehouse, Fly).
- `SPINE.md` — the shared hierarchy/context contract every app reads.
- Per-repo `CLAUDE.md` — orientation + the rules that bind work in that repo.
