# Engineering guide — dev loops, contracts, deploy

## Dev loops

**Suite + engine (hoodie-suite):**
- Static suite: `python3 -m http.server 8000` from the repo root (iframes + fetch need a real
  origin; `file://` won't work).
- Engine: `python unifyd/server.py` (port 8765; the OIDC gate is OFF locally when Google creds are
  absent). Seed a synthetic book: `POST /api/seed/build`. The all-in-one mode (`SUITE_ROOT` env)
  serves the static suite AND `/api/*` from one origin — this is exactly how Fly runs it.
- Engine deps are lazy: stdlib-only until a connector/warehouse call needs `duckdb`/`pyarrow`/`flask`.
- Self-tests are executable modules: `python3 unifyd/flow.py`, `python3 unifyd/derive.py` — run them
  before pushing engine changes (the agentic QA gate requires it).

**App (hoodie-app):** `npm install` → `npm run mobile` (Expo; point at the engine with
`EXPO_PUBLIC_API_URL`, LAN IP for a physical device) or `npm run web`. `npm run typecheck` must be
green before a PR.

**Backend (hoodie-backend):** open `data-layer/duckdb-data-layer.html` in a browser (Track 2);
Track 1 deploys via Firebase when HB-003 lands. Secrets only via `.env` (never committed).

## Contracts (the durable part)

- **`/api/*` is the product boundary.** The engine serves its OpenAPI spec; `@hoodie/core`
  GENERATES the typed client from the running API — backend↔app drift is a build error.
- **The star schema**: `dim_product / dim_account / dim_date / fact_depletion`; every analytics
  surface is a query over it (`/api/book/*`). Synthetic today, real later — the schema doesn't move.
- **The masters**: the flow engine materializes `dim_<entity>` with a `hoodie_id` column (stable
  across rebuilds, per-mode registry). Consumers join on Hoodie IDs via `/api/serve/*` (real-only).
- **Warehouse**: Parquet in object storage (Tigris on Fly; local dir in dev), queried in place by
  DuckDB. Mode namespaces: `real/` vs `synthetic/` — a build reads exactly one.

## Deploy

- **Production is Fly** (`hoodie-suite.fly.dev`): push to `main` triggers `.github/workflows/
  deploy-fly.yml` IF the `FLY_API_TOKEN` secret is set; otherwise deploy by hand
  (`flyctl deploy --ha=false`). After merging, CONFIRM changes are live — they don't ship until a
  Fly deploy runs.
- Static serving enforces a top-level allowlist (`_SUITE_OK_TOP` in `server.py`) — the engine,
  secrets, and dotfiles are in the image but never web-served. New top-level public dirs must be
  added there deliberately (the handbook lives under `apps/` to avoid that).
- **Never commit to `main` directly** — every push to main auto-deploys production.

## Auth

Google OIDC gate over the whole origin (email allowlist, `unifyd/auth_gate.py`); `/api/*` 401s
unauthenticated; native apps exchange a Google ID token for a bearer. Health, `/auth/*`,
`robots.txt` are the only public routes. Rate limit on `/api/*` (default 600/60s per IP).

## Where to look first

`server.py` route sections are labeled (`── MDM Flow ──`, serve, book, cola…); the flow engine is
`unifyd/flow.py` (read its docstring — the node model in one screen); UI conventions live in
`apps/mdm.html` (the console shell pattern) and `apps/mdm-flow.html` (the workbench pattern).
