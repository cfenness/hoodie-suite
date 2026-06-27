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

- `unifyd/server.py` — a local Flask agent (`python unifyd/server.py`, port 8765) that
  serves `hoodie_mdm.html` and runs real pulls on `/api/run`. Endpoints: `/api/health`,
  `/api/datasets`, `/api/runs`, `/api/run`. State persists to `unifyd/agent_state/`.
- `unifyd/ttb_cola_scraper.py` — the TTB COLA registry scraper (date-chunked search,
  pagination, optional `--detail`/`--ocr`/`--resume`). The one selector-fragile spot is
  `parse_results()`'s `col(...)` index map; `unifyd/fixtures/` holds captured pages to
  confirm it against. **TTB is TLS-blocked from sandboxes — first run live, small window.**
- `unifyd/pull_sources.py` — agent-less batch pull (Florida is live/tested; COLA needs
  `requests`+`bs4`). Emits `out/datasets.js` + `out/runs.json`.
- `unifyd/hoodie_mdm.html` — the MDM control plane the agent serves. Reads `/api/*` when
  the agent is up, falls back to an embedded `const DATASETS` preview otherwise.
- **Runtime is git-ignored:** `agent_state/`, `cola_out/`, `out/`, `__pycache__/`.
- **Open decision** (see `unifyd/README.md`): whether `hoodie_mdm.html` stays the engine's
  local console or is promoted into `apps/` as the canonical MDM surface (replacing
  `apps/item-mdm.html`), wired to `/api/*` with the embedded data as offline fallback.

### Backend on-ramp (the engine is the first slice)
The contract is designed so the message protocol does **not** change when a backend
arrives — only the data source. `host()` will fetch `/api/hierarchy` instead of the
sample file; apps keep calling `applyContext(ctx)` but fetch `/api/<entity>?scope=...`
inside. The intended shape is API Gateway + Lambda behind a `/api/*` CloudFront
behavior on the same domain. See the "backend on-ramp" sections of `README.md` and
`SPINE.md` before adding any server code.

## Deploy

- **Auto-deploy:** any push to `main` triggers `.github/workflows/deploy.yml`, which
  `aws s3 sync`s the repo to S3 (with `--delete`) and invalidates CloudFront. There is
  no staging branch — `main` is production.
- **Manual deploy:** `./deploy.sh` (needs `aws configure` done; reads `S3_BUCKET` and
  optional `CLOUDFRONT_DISTRIBUTION_ID` from the environment).
- Both the workflow and `deploy.sh` exclude `.git/`, `.github/`, `cloudfront/`,
  `README.md`, `deploy.sh`, and `.gitignore` from the upload. **If you add files that
  should not ship (docs, scripts, configs), add them to the exclude lists in both
  places**, or they will be served publicly via CloudFront.

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
