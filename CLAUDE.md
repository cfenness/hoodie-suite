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
- **NEVER assert a paid path is REQUIRED without grounding it in the code (load-bearing — unsupported
  "you have to pay for this" claims have wasted real money).** Before saying a paid proxy / API / service
  is necessary, cite the specific evidence: the fetch code that has no direct/mobile-UA/local-browser
  path, or an actual tested block *from a residential IP* — not a datacenter one. "It's blocked" is NOT
  proof a paid path is required; a datacenter-IP block is an execution-PLACEMENT problem (run it on a
  residential executor), and the default assumption is that a free path exists until the code proves it
  cannot. If you can't ground the claim in code, don't make it — investigate first.
- **FREE-FIRST — do NOT default to paid proxies (load-bearing, keeps getting reverted).**
  Two proxy tiers: the flat-rate **ISP pool** (fixed per-IP, unlimited bandwidth) and the
  **per-GB** rotating-residential / BD-Unlocker tier (the one that runs up a thousands-a-month
  tab). `FETCH_POLICY` (default `flat`) is the dial: `free` = no proxies at all; `flat` = ISP
  pool only; `paid` = per-GB **opt-in**. `resi.paygo_allowed()` is `False` unless `FETCH_POLICY=paid`,
  and every per-GB seam (`resi.parts()` → url/browser/opener/…, plus `polite`'s BD path) honors it.
  **~20 of ~29 sources need NO proxy** (direct HTTP / public API / sitemap); only the 9 `anti-bot`
  sources even tempt one — see `cost_class` in `/api/registry/sources`. When you touch a scraper,
  NEVER add a per-GB proxy as the default path or "to make it work" — try direct → mobile-UA →
  a real **local browser** (patchright/playwright, no proxy) → the flat ISP pool, in that order.
- **The anti-bot free method runs from a RESIDENTIAL IP, not the cloud.** The hard-won recipe
  (e.g. `ubereats.py`: a real Chromium on a residential IP hitting the first-party BFF with the
  app's own `x-uber-*` headers — NO Bright Data, NO cookie; `ue_geofill.py`: universe fetched
  DIRECT from the home IP) only clears PerimeterX/Forter **because the exit IP is residential**.
  On a datacenter host (Fly/CI) the datacenter IP is blocked, and the wrong reflex is "buy a
  residential proxy." The RIGHT answer: run the `anti-bot` sources on the **residential executor**
  (the Mac — `$0`) and keep the cloud for the ~20 free API sources. `ue_crawl.py`'s "proxy for
  everything" path is opt-in scale (`FETCH_POLICY=paid`), never the default. Don't re-litigate this.
  - **Not every `anti-bot` source needs the residential executor — test, don't assume.** `unifyd/instacart.py`
    is the proven counter-example: a self-hosted **headless** Chromium (Playwright, NO Bright Data, NO proxy)
    drives Instacart's own `SearchResultsPlacements` GraphQL and lands per-store product+price **from a bare
    datacenter IP** — a CI matrix confirmed headless-no-Xvfb lands data, so the Fly image ships just the
    bundled Chromium and the Instacart pull runs in-app for `$0`. The old BD managed-dataset scraper is
    archived (`_archive/instacart_scraper.py`). Never re-add Bright Data to Instacart "to make it work";
    if the datacenter path ever regresses, prove the block from a residential IP first (see the paid-path rule).

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
- `unifyd/label_reader.py` — read ONE product-page/label URL into clean MDM fields,
  interactively (the human-in-the-loop twin of the catalog scrapers). Dispatch by host: Total
  Wine reuses `total_wine.parse_product`; ABC + everything else use a generic PDP parser
  (schema.org JSON-LD + OpenGraph + on-page `<dt>/<dd>` & `<th>/<td>` spec tables) mapped onto a
  canonical field set (brand/varietal/appellation-hierarchy/ABV/ratings/closure/… + full
  attribute set as raw_json). Fetch is mobile-UA where PerimeterX-walled, BD-Unlocker fallback;
  a light SSRF guard blocks internal hosts. Optional Claude-vision pass (`label_vision.extract`)
  fills what the HTML doesn't structure, per-field provenance = structured vs vision. Reads land
  in `label_reads`. Endpoints: `POST /api/label/read {url, vision?}`, `GET /api/label/reads`.
  Surface: `apps/mdm-label-reader.html` (the **Label Reader** section in `apps/mdm.html`).
- `unifyd/menu_ingest.py` — parse a DISTRIBUTOR WHOLESALE MENU file (xlsx/csv; cannabis
  Curaleaf NY is the reference shape) into normalized order lines. stdlib-only (xlsx = zipped
  XML), heuristic header-row detection + column synonyms, Excel serial dates, THC normalization.
  **Inline sub-headers are parsed, not dropped:** a section/sub-header row (no price/batch/units/
  thc/msrp) is classified brand-vs-product-family, its size/form/category are read off the header
  and cascaded to the child rows, and a terse child (`Indica : Wedding Cake`) is composed into the
  full product using its family header — which is often the ONLY place the product/size lives.
  Lands `distributor_menu_items`; behind `apps/ordering.html`
  (Wholesale Ordering: all menus → one catalog → one order → per-distributor PO sheets via
  `/api/menus/*` + `/api/orders*`).
- `unifyd/hoodie_mdm.html` — the MDM control plane the agent serves. Reads `/api/*` when
  the agent is up, falls back to an embedded `const DATASETS` preview otherwise.
- **Runtime is git-ignored:** `agent_state/`, `cola_out/`, `out/`, `__pycache__/`.
- **Promoted to the suite:** `apps/mdm.html` is the canonical MDM surface — a
  **composite console** (same pattern as `sources.html`): an inline Overview plus a
  sidebar of 14 sections, each an existing app iframed and lazy-mounted (most with
  `?embed=1` so they hide their own chrome). **Manage:** Master
  (`master-match.html`, the matching workbench), Steward (`steward.html`), Review
  (`cluster-review.html`), Catalog (`mdm-catalog.html`), Outlets (`mdm-outlets.html`),
  Coverage (`coverage-map.html`), Registrations (`product-registrations.html`),
  Mapping (`field-mapping.html`), Dictionary (`mdm-dictionary.html`). **Sources:**
  Sources (`mdm-sources.html`), Provenance (`mdm-provenance.html`), Source Spec
  (`mdm-sourcespec.html`), Chains (`mdm-chains.html`). **Operate:** Active Runs
  (`runs.html`). Those apps are surfaced only through `mdm.html`, not as their own
  `APPS` tiles. `unifyd/hoodie_mdm.html` remains the engine's local, agent-backed
  console (served by `server.py`); its old suite twin (`mdm-master.html`) was
  superseded by the matching workbench and lives in `apps/_archive/`.

### Backend on-ramp (the engine is the first slice)
The contract is designed so the message protocol does **not** change when a backend
arrives — only the data source. `host()` will fetch `/api/hierarchy` instead of the
sample file; apps keep calling `applyContext(ctx)` but fetch `/api/<entity>?scope=...`
inside. The intended shape is API Gateway + Lambda behind a `/api/*` CloudFront
behavior on the same domain. See the "backend on-ramp" sections of `README.md` and
`SPINE.md` before adding any server code.

### Snowflake load — the seed to Unifyd (`snowflake/`)
A staged SQL build that lands the whole warehouse in Snowflake — the Parquet layout was designed to be
Snowflake-loadable (NRT-PLAN.md §2: "migration is a load, not a rewrite"), and this is that load. It's
**generated, not hand-kept**: `snowflake/build_snowflake_sql.py` derives everything from the same
sources of truth the engine uses — `source_registry.py` for the raw source list, the typed catalog in
the generator (mirroring `build_product_master.py`/`normalize.py`/`dim_outlet.py`) for the canonical
star. Three schemas: `RAW` (one landing table per source Parquet, **schema-agnostic** via Snowflake
`INFER_SCHEMA` + `MATCH_BY_COLUMN_NAME` — scraper drift just flows through, same as the DuckDB
`read_parquet` path), `MASTER` (the **typed** star `dim_brand/product/item/sku` + `dim_outlet` +
`src_<grain>` + signal tables — the seed), `MART` (views). It stages SQL only — nothing connects to
Snowflake or touches prod. `python snowflake/build_snowflake_sql.py [--live]` regenerates; `--live`
reads the warehouse to include every present table and resolve bucketed (v2) tables to their manifest's
active parts. `snowflake/` is engine/infra — never web-served (not in `_SUITE_OK_TOP`), like `unifyd/`.
See `snowflake/README.md`.

## Health & smoke (keeping the served data trustworthy)

Two standing tools exist so failures are loud, not quiet. Keep them passing and keep them honest.

- **Data health — `unifyd/health_digest.py`**: the daily deterministic verdict on every
  registry-enabled source (failed/degraded runs, staleness vs cadence, row-count collapse,
  honest no-creds skips). Every finding cites evidence and carries `first_seen` so new breaks
  stand out. Runs via `unifyd/run_health_digest.sh` (launchd `com.hoodie.health`, 07:30 daily);
  writes `unifyd/agent_state/health/latest.{json,txt}` + an optional Claude triage in
  `latest_triage.md` (judgment layer only — it NEVER changes the verdict). Exit 2 = critical.
  Mondays (or `--weekly` / `HEALTH_WEEKLY=1`) add the **deep audit** (`unifyd/deep_audit.py`):
  field-drift (a source lands rows but a column went null — footer null-stats vs baseline),
  parser regression vs `unifyd/fixtures/` (add a scraper = one `FIXTURE_CHECKS` row), and
  docs-drift (CLAUDE/README/SPINE referencing paths that no longer exist).
- **Suite smoke — `python3 tools/smoke_check.py`**: deterministic; proves every `APPS` entry
  serves, no dangling ids/groups, every local src/href/iframe reference resolves, orphan app
  files are surfaced. The `/smoke` skill layers a browser runtime pass on top (console errors,
  blank renders, composite tabs). Run before "ship it" and after any shell/spine change.
- **launchd gotcha (load-bearing)**: a LaunchAgent whose script argument lives under `~/Desktop`
  fails at spawn with `Operation not permitted` (exit 126) — the job silently never runs. Use the
  `bash -c 'exec bash "<script>"'` form (see `unifyd/launchd/*.plist`). This killed the entire
  scheduled-scrape pipeline once; the health digest is what catches it if it regresses.

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
