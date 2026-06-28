# Unifyd — the ingestion engine (Master Data Control Plane)

A Tableau-style MDM front end with a connections layer, data explorer, run
scheduler, and a target-sources prioritization board — plus the Python pipeline
that pulls real source data behind it.

> **Place in the suite.** This is the consolidated home for what used to live in a
> separate `unifyd-scraper/` project. It is the **owned layer** for the suite: the
> apps in `../apps/` are render targets; this is where ingestion actually happens.
> Nothing here ships to CloudFront — `unifyd/`, `*.py`, and runtime dirs are excluded
> from the deploy. Run it locally; it emits `datasets.js` the apps can embed, and it
> speaks the `/api/*` contract the backend on-ramp will eventually promote.

## Files

| File | What it is |
|------|------------|
| `hoodie_mdm.html` | The app. Open standalone for the preview, or serve it via the agent for live data. |
| `server.py` | Local agent. Serves the app and runs real pulls when you click **Run now**. |
| `ttb_cola_scraper.py` | TTB COLA public-registry scraper (date-chunked search, pagination, detail enrichment, OCR-UPC hook, resume). |
| `pull_sources.py` | Standalone batch pull (Florida + COLA) → emits `datasets.js` + `runs.json`. Use without the agent. |
| `requirements.txt` | Dependencies for the agent + scraper. |
| `fixtures/` | Captured TTB result pages (`cola_results.html`, `cola_debug.html`) — reference markup for confirming the parser's column map on a live run. |

## Quick start — live app

```bash
cd unifyd
pip install -r requirements.txt
python server.py
# open http://127.0.0.1:8765
```

With the agent running, the app detects it (the Connections banner turns green),
loads real data, and **Run now / Run all / the scheduler execute real pulls**:

- `Florida — Items` / `Florida — Outlets` → live DBPR/ABT CSV extracts
- `TTB — COLA Labels` → runs the scraper

State persists to `./agent_state/` (datasets, run history, COLA CSV) — git-ignored.

## Or — batch pull, no agent

```bash
python pull_sources.py all          # fl + cola
python pull_sources.py fl           # Florida only (no extra deps; tested/working)
```

Then drop `out/datasets.js` into the app in place of the embedded `const DATASETS = {...}`
and the Explore grids show your pulled data.

## Scraper — direct use

```bash
# last 7 days, fast (summary columns only)
python ttb_cola_scraper.py --from 06/18/2026 --to 06/25/2026

# a full month, enriched + UPC OCR, one day per search chunk
python ttb_cola_scraper.py --from 05/01/2026 --to 05/31/2026 --detail --ocr --chunk-days 1

# dump the registry's class/type + origin codes (handy for filtering)
python ttb_cola_scraper.py --list-codes
```

Key flags: `--detail` (open each COLA for applicant / fanciful / net contents /
status), `--ocr` (label-image UPC via `ttb_cola_labels`), `--resume` (skip TTB IDs
already captured), `--chunk-days` (smaller = safer against the result cap).

### Self-healing parse + drift detection

The parser is built to survive the TTB site shifting and to **never silently emit bad
data**:

- It finds the results table and rows by the stable **`ttbid=`** identifier (the innermost
  table holding one), not by a page name — so a URL/path change (e.g.
  `publicFormDisplay.do` → `viewColaDetails.do`) self-heals instead of breaking. Columns
  map by header name.
- When it *can't* map cleanly it marks the run **`degraded`** with `warnings[]` (surfaced
  via `/api/runs`): no results table, a populated table yielding 0 rows (row selector
  changed), rows with empty fields (column mapping off), or unrecognized headers (drift).
- Confirm against `fixtures/` after any change: `cola_debug.html` should parse to 20 rows
  with every field populated and no warnings.

**AI auto-fixer (`self_heal.py`, opt-in).** For structural changes the deterministic
matcher can't resolve, an LLM re-derives the missing column indices from the live header
+ a sample row and the parse retries — healed columns are logged and listed in each run's
diagnostics. **Off by default**; enable per run/deploy with:

```
AGENT_SELF_HEAL=1                  # turn it on
ANTHROPIC_API_KEY=...              # read by the SDK
AGENT_LLM_MODEL=claude-opus-4-8    # optional; this is the default
```

When off, the parser is fully deterministic and imports no Anthropic/AWS deps. In a
container, set these on the service (the API key via a vault/secret, not in the image).

## Data flow

```
  source                 puller                     app
  ──────                 ──────                     ───
  FL DBPR/ABT  ──CSV──>  fl_pull (urllib)   ─┐
  TTB COLA     ──HTML─>  ttb_cola_scraper   ─┼─> datasets.js / runs.json ─> hoodie_mdm.html
                          (profile + sample) ─┘        (served by server.py)
```

The app stays a thin presentation layer over a canonical item/outlet/party model;
the pullers own ingestion. Each puller computes a **full-file profile** (distinct
counts, fill %, top values across every record) plus a browsable row sample.

## Provenance & one caveat — read this

- **Florida is real and tested.** The pulls hit `www2.myfloridalicense.com` live;
  the Explore grids show real sampled records and the Fields panel is profiled over
  every record (112K brands, 52K outlets, etc.).
- **TTB COLA needs TTB reachable.** The scraper is written against the registry's
  documented structure but was **not executable from the build sandbox** (TTB is
  TLS-blocked there). On your first live run, do a small window (one day, no
  `--detail`) and sanity-check the row count and columns. If columns land oddly,
  the fix is the index map in `parse_results()` (the `col(...)` calls) — that's the
  one spot that depends on the live HTML, and it's commented. `fixtures/` has a
  captured results page to check against.
- **Preview vs live.** Opened standalone (no agent), the app's run status is
  simulated and labeled as such. With `server.py` running, runs are real.

## The MDM console and the suite app (decided)

`hoodie_mdm.html` lives here as the engine's **local, agent-backed console** (served by
`server.py`). It has also been **promoted into the suite** as `../apps/mdm.html`, the
canonical MDM surface — the same control plane re-served under suite wiring
(`../suite.css`, `../spine/spine.js`, `../suite-header.js`), registered in the shell's
`APPS` array, and reading `/api/*` with the embedded `DATASETS` as the offline fallback.
That promotion replaced the old `apps/item-mdm.html`.

The two surfaces share one `/api/*` contract: `apps/mdm.html` is the deployed suite view
(offline fallback when no backend is up); `hoodie_mdm.html` here is the always-live local
view the agent serves. Keep them in sync if you change the control-plane UI.

## Deploying the agent as the `/api/*` backend

The agent runs as a container behind a CloudFront `/api/*` behavior on the suite's own
domain, on **Amazon ECS Express Mode** (App Runner's successor — App Runner stopped
accepting new accounts in 2026). Chosen over Lambda so `server.py` runs as-is.

- **`Dockerfile`** — the agent image (gunicorn, `$PORT`). Also runnable anywhere:
  `docker build -t hoodie-unifyd . && docker run -p 8080:8080 hoodie-unifyd`.
- **`../scripts/provision-ecs-express.sh`** — one-time: ECR repo + the two IAM roles
  Express Mode needs; prints the GitHub Variables to set.
- **`../.github/workflows/deploy-api.yml`** — builds the image → ECR → deploys via the
  official `amazon-ecs-deploy-express-service` action (creates the service, then updates
  it on every push to `main`). Gated on the `ECS_EXEC_ROLE_ARN` repo variable.
- Then `../scripts/add-api-cloudfront-behavior.sh` routes `/api/*` to the service URL
  (`hoodie-unifyd.ecs.<region>.on.aws`). The MDM console goes live with no front-end change.

Full runbook: suite `README.md` → "Stand it up (the runbook)".

### State persistence

`server.py` has a pluggable state store:

- **Local disk (default).** Datasets + run history live in `./agent_state/`. Perfect
  for local dev; on a container this disk is ephemeral, so pulled data resets on redeploy.
- **S3 (durable).** Set `STATE_BUCKET` (and optionally `STATE_PREFIX`, default
  `unifyd-state`) and `load()`/`save()` read/write `datasets.json` + `runs.json` to S3
  instead. State then survives redeploys — on boot the agent loads it back from the bucket.

To turn it on:

```bash
STATE_BUCKET=hoodie-suite-state ./scripts/provision-state.sh
```

That creates a private state bucket (separate from the website bucket — state is never
web-served) and prints the env vars + the least-privilege IAM policy
(`s3:GetObject`/`s3:PutObject` on the prefix) to attach to the service's instance role.

Two notes: the container runs **one** gunicorn worker because state is held in-process;
and keep the service at **min = max = 1 instance** (state is per-instance — fine for a
single-user control plane). `boto3` is only imported when `STATE_BUCKET` is set, so local
disk mode pulls in no AWS deps. The raw COLA CSV the scraper writes stays on local disk;
the parsed datasets it produces are what persist to S3.

### API hardening

- **Errors are JSON.** 404 / 405 / unhandled exceptions return `{"ok":false,"error":...}`
  with the right status, so callers never get an HTML error page.
- **`/api/health`** reports readiness: `ok`, `sources`, dataset/run counts, and the active
  `state` backend (`disk` or `s3:<bucket>`). The suite shell probes it and shows an **api**
  status dot in the top bar (lit when the backend answers).
- **Optional token gate.** Set `AGENT_TOKEN` to require `Authorization: Bearer <token>`
  (or `X-Agent-Token`) on every `/api/*` call except `/api/health`. Off by default — local
  dev and browser apps behind the CloudFront password function are unaffected. Use it for
  non-browser callers, or as defense-in-depth in front of the CloudFront gate.

### Scoped data & a hierarchy from the real data

- **`/api/hierarchy` is derived from the pulled datasets** when any are present:
  `source → entity` (top-N registrants/applicants by row count), e.g. *Florida — Items →
  MHW LTD*. With no data it falls back to the bundled seed. `derive_hierarchy()` is a
  first-cut grouping (by an `Owner Name`/`Applicant`/`Brand Name` column) — refine the
  level model as the master-data layer matures.
- **`/api/datasets?q=<term>&dataset=<id>`** filters rows server-side (any cell contains
  the term); no params returns everything. Because the derived scope nodes are real entity
  names, selecting a scope in the shell now matches real rows — the MDM Explore view
  filters to them, and `apps/spine-adapter.html` shows the live match count as the
  copy-paste reference for the pattern.
