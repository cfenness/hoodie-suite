# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hoodie Suite is a **dependency-free static site**: a launcher shell (`index.html`)
plus a set of self-contained single-file HTML apps under `apps/`. There is no build
step, no package manager, no test runner, no transpilation. Each app is shipped as-is.

To preview locally, serve the directory over HTTP (iframes + `fetch` need a real
origin, so opening `index.html` via `file://` will not load apps or the hierarchy):

```bash
python3 -m http.server 8000   # then open http://localhost:8000
```

## Architecture

### The shell (`index.html`)
The launcher and host. Two things matter:

- **The `APPS` array** (near the top of the inline `<script>`, ~line 238) is the
  single source of truth for what surfaces exist. Each entry is
  `{id, name, desc, group, status, file, mark}`. The sidebar, the filterable grid,
  and hash routing (`#<id>` → loads `file` into the `#frame` iframe) are all derived
  from it. **Adding an app = drop the HTML in `apps/` + add one line to `APPS`.**
  Optionally add a glyph to the `LOGOS` map keyed by the same `id`.
- `status` is one of `live | beta | internal` (drives styling/pills). `group` must be
  one of the `GROUPS` list; groups with no apps are hidden automatically.
- **Composite surfaces:** an app can host other apps as tabs. `apps/sources.html`
  is the reference — a shared tab bar iframing `source-analyzer.html` (Analyze) and
  `pulls-tracker.html` (Catalog), each kept intact. Cross-tab actions go over
  `postMessage`; shared state (e.g. tracked-source URLs) rides same-origin
  `localStorage`. Those two apps are surfaced only through `sources.html`, not as
  their own `APPS` tiles.

### The spine (`spine/spine.js`)
The shared backbone every app reads instead of re-solving. Read `SPINE.md` for the
full contract. The shape:

- The shell calls `HoodieSpine.host({ hierarchy })` and owns the canonical
  **hierarchy** and the shared **context** (`scope`, `account`, `dateRange.basis`,
  `metric`). It broadcasts context to all iframes and routes cross-app `nav` requests.
- An app calls `HoodieSpine.connect({ name, onContext })` (~10 lines, see
  `apps/spine-adapter.html` for a live reference). It then inherits shared context,
  can push changes with `spine.setContext(patch)`, and deep-link siblings with
  `spine.navigate(appId, scope)`.
- Transport is `window.postMessage`; every message is
  `{ __spine: "hoodie:spine", kind, payload }` with `kind` ∈
  `ready | context | setContext | nav`. Apps that haven't adopted the adapter simply
  ignore the messages — adoption is incremental and nothing breaks meanwhile.
- The hierarchy is a tree of levels `portfolio → brandFamily → brand →
  productFamily → sku`; node shape and helpers (`HoodieSpine.util.find/path/levels`)
  are in `spine/spine.js`. The shell loads `spine/hierarchy.sample.json` at runtime
  and falls back to an inline `HIER` if the fetch fails.

### The Unifyd engine (`unifyd/`)
The **owned layer** — the scrapers and pipeline that produce master data, consolidated
from the former standalone `unifyd-scraper/` project. It is NOT part of the static site
and is **excluded from deploy** (along with `*.py`, `cloudfront/`, and the docs).

**Standing rules (scraper hygiene — load-bearing, learned the hard way):**
- **One active scraper per source.** `unifyd/source_registry.py` is the single source
  of truth for which module + entrypoint is live for each source. When you replace a
  scraper with a better iteration, repoint the registry AND `git mv` the old module to
  `unifyd/_archive/` with a note (see `unifyd/_archive/README.md`). Never leave two
  iterations of the same source side-by-side in `unifyd/` — that's how the thin
  `kroger_api` got run instead of the real `kroger_atlas` inventory bypass. Archive,
  don't delete (the work is expensive to re-derive). *Parked* unfinished work with no
  active replacement stays in `unifyd/`; only *superseded* iterations get archived.
- **Never clobber a catalog.** `warehouse.write_parquet` refuses to overwrite a
  populated table with 0 rows (raises unless `allow_empty=True`) — an empty write is a
  failed scrape, not a rebuild. Persistent catalogs use `write_accumulate` (merge).
- **Creds-gated sources declare `requires=[env]`** in the registry; `run_sources.py`
  reports them `no-creds` (skipped, honest) instead of running them to failure.

- `unifyd/server.py` — a local Flask agent (`python unifyd/server.py`, port 8765) that
  serves `hoodie_mdm.html` and runs real pulls on `/api/run`. Endpoints: `/api/health`,
  `/api/datasets` (supports `?q=` / `?dataset=` for scoped queries), `/api/runs`, `/api/run`,
  `/api/hierarchy` (scope tree — **derived from the pulled data** when present, else the
  `unifyd/hierarchy.json` seed). State persists to `unifyd/agent_state/`.
- `unifyd/ttb_cola_scraper.py` — the TTB COLA registry scraper (date-chunked search,
  pagination, optional `--detail`/`--ocr`/`--resume`). **Self-healing parse:** it locates
  the results table and rows by the stable `ttbid=` identifier (not a page name, so URL/path
  changes don't break it) and maps columns by header name. When it *can't* map — no table,
  0 rows from a populated table, empty fields, or unrecognized headers — the run is marked
  `degraded` with `warnings[]` (via `/api/runs`) instead of silently emitting bad data.
  An optional **AI auto-fixer** (`unifyd/self_heal.py`, off unless `AGENT_SELF_HEAL=1` +
  `ANTHROPIC_API_KEY`) has an LLM re-derive unrecognized column indices and retries the parse.
  `unifyd/fixtures/` holds captured pages to confirm the parser against (it currently passes
  `cola_debug.html` → 20 rows, all fields). **TTB is TLS-blocked from sandboxes — first run live.**
- `unifyd/abc_fws_scraper.py` — ABC FWS (abcfws.com, **BigCommerce**) **STORE-LEVEL** inventory
  tracker (connId `abc-fws`, in Hoodie Pulls). The store is a BigCommerce product option:
  each product page lists ~133 store options (`ABC #003 - OBT` / `Online`) and
  `available_variant_values` names the in-stock store-values — so we read **per-store in/out
  + the chain price from the allowed product page (no robots-disallowed AJAX)**. Snapshot
  keyed `sku|storeValue`; diff catches per-store in/out transitions + price moves. Polite
  (robots 10s crawl-delay, product pages only, honest UA), stdlib-only, self-reports
  `degraded` if the store-option / `available_variant_values` selectors drift. Validated
  live (~13.9k products via sitemap). `unifyd/schedule_pull.py` runs any connId on a cadence
  locally (`python unifyd/schedule_pull.py abc-fws --every 24h`).
- `unifyd/pull_sources.py` — agent-less batch pull (Florida is live/tested; COLA needs
  `requests`+`bs4`). Emits `out/datasets.js` + `out/runs.json`.
- `unifyd/hoodie_mdm.html` — the MDM control plane the agent serves. Reads `/api/*` when
  the agent is up, falls back to an embedded `const DATASETS` preview otherwise.
- **Runtime is git-ignored:** `agent_state/`, `cola_out/`, `out/`, `__pycache__/`.
- **Promoted to the suite:** `apps/mdm.html` is the canonical MDM surface. It is now a
  **composite console** (same pattern as `sources.html`) with four tabs, each an existing
  app iframed and lazy-mounted: **Master** (`apps/mdm-master.html` — the engine's
  `hoodie_mdm.html` re-served under suite chrome + spine, `/api/*` with an embedded
  `DATASETS` fallback), **Catalog** (`apps/catalog.html`), **Pulls** (`apps/pulls.html`),
  **Ingestion** (`apps/ttb-ingestion.html`). Those four are surfaced only through
  `mdm.html`, not as their own `APPS` tiles. `unifyd/hoodie_mdm.html` remains the engine's
  local console (served by `server.py`); it and `mdm-master.html` share the `/api/*` contract.

### Backend on-ramp (the engine is the first slice)
The contract is designed so the message protocol does **not** change when a backend
arrives — only the data source. `host()` will fetch `/api/hierarchy` instead of the
sample file; apps keep calling `applyContext(ctx)` but fetch `/api/<entity>?scope=...`
inside. The intended shape is API Gateway + Lambda behind a `/api/*` CloudFront
behavior on the same domain. See the "backend on-ramp" sections of `README.md` and
`SPINE.md` before adding any server code.

## Deploy

**Production is Fly.io** — `hoodie-suite.fly.dev`, one all-in-one machine serving the
static suite **and** `/api` (see `DEPLOY-FLY.md`, `fly.toml`, `Dockerfile`). `main` is
production; there is no staging branch.

- **Auto-deploy (Fly):** push to `main` triggers `.github/workflows/deploy-fly.yml`
  (`flyctl deploy --remote-only`). **It only runs when the `FLY_API_TOKEN` repo secret
  is set** (`flyctl tokens create deploy` → add to GitHub → Settings → Secrets). Without
  that secret the job skips, and deploys must be done by hand — the common gotcha:
  merging a PR then finding the live site unchanged because nobody ran a deploy.
- **Manual deploy (Fly):** `flyctl deploy --ha=false` from the repo root (flyctl at
  `~/.fly/bin`). This is the fallback and how the site was updated before the workflow.
- **What ships:** the Dockerfile copies the repo; the engine (`unifyd/`, `*.py`, secrets,
  dotfiles) is present in the image but **never web-served** — the static file route
  enforces a `_SUITE_OK_TOP` allowlist on the resolved path.
- **Legacy S3/CloudFront** (`deploy.yml`, `deploy.sh`, `cloudfront/`) is **DORMANT** —
  kept for reference only; it does not run on push. Ignore it unless deliberately
  resuming S3 serving.

**After merging suite changes, confirm they're live** (they don't ship until a Fly
deploy runs): `curl -s https://hoodie-suite.fly.dev/robots.txt` and check the launcher
reflects the change, or `flyctl releases -a hoodie-suite`.

## Git conventions

- **Never commit directly to `main`** — always work on a feature branch. `main` is
  production (every push auto-deploys), so this is load-bearing, not stylistic.
- **Branch names:** `feat/short-description`, `fix/short-description`,
  `chore/short-description`.
- **Commits:** conventional commits (`feat:`, `fix:`, `refactor:`, `chore:`,
  `docs:`). For a non-trivial diff, add a short body listing the key changes.
- **"ship it" / "commit and push"** means: stage the relevant files, commit, push the
  branch, and open a PR — all in one step.
- **Before committing:** review the diff; exclude debug code, temp files, `.env`, and
  anything matching `.gitignore`.
- **Never force-push or hard-reset without explicit confirmation.**

## Security context

The apps include a CRM and a master-data console containing proprietary/possibly-real
data. The deploy model is a **private S3 bucket served only through CloudFront** (OAC),
with an optional shared-password gate in `cloudfront/basic-auth.js` (a CloudFront
Function on the viewer-request event). Do not switch to a public S3 website. Full
setup steps are in `README.md`.

**We must not be scrapeable.** On the live Fly deploy the primary defense is the
**Google OIDC gate** (`unifyd/auth_gate.py`): a `before_request` that redirects any
unauthenticated HTML request to `/auth/login` and 401s `/api/*`, so nothing is public
except `_PUBLIC` (health, the `/auth/*` routes, `/favicon.ico`, `/robots.txt`). Layered
on top (`server.py`): `/robots.txt` = `Disallow: /`; `X-Robots-Tag: noindex, nofollow,
noarchive` + `X-Frame-Options: SAMEORIGIN` (the launcher/sources/mdm shells iframe their
own apps) + `nosniff` + `no-referrer` on every response; and a light in-memory per-IP
rate limit on `/api/*` (`RATE_MAX`/`RATE_WINDOW`, default 600/60s, health exempt) so an
authenticated session can't be used to vacuum the whole book/catalog. Keep the gate
configured (`ALLOWED_EMAILS`) — it is the load-bearing control.
