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
| `abc_fws_scraper.py` | ABC FWS (abcfws.com) directional inventory tracker — polite, stdlib-only. See below. |
| `pull_sources.py` | Standalone batch pull (Florida + COLA) → emits `datasets.js` + `runs.json`. Use without the agent. |
| `schedule_pull.py` | Run any pull on a cadence locally (POSTs `/api/run` every N hours) — Layer-3-lite before the backend. |
| `tax_rates.py` | Bev-alc tax **rate** reference (`tax_rates`): federal CBMA excise (encoded from TTB) + 51-jurisdiction state excise seed (Tax Foundation, Jan 2026). Long/tall, effective-dated, append-only. `tax_rates_seed.csv` holds the state cells — promote seed→verified per-state vs DOR. |
| `tax_revenue.py` | Bev-alc tax **revenue** (`tax_revenue`): Census govs STC collections (T10 alc sales tax, T20 alc license) per state — live via `CENSUS_API_KEY`; TTB federal commodity series runs live on the Mac (TTB TLS-blocked on Fly). |
| `landed_cost.py` | The tax **translation layer**: `landed_cost(base, state, class, abv, size)` stacks federal + state tax into an itemized cost; `pretax_price(...)` strips excise to a comparable pre-tax basis for the price signal. Reads `tax_rates`; control states are flagged, not double-counted. Served at `/api/tax/{rates,revenue,landed}`. |
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

### ABC FWS — directional inventory (`abc_fws_scraper.py`)

abcfws.com runs on **BigCommerce**. The connId is **`abc-fws`**; it appears in **Hoodie
Pulls** alongside the others. Run it:

```
python abc_fws_scraper.py --sample 40 --out ./abc_out   # poll a 40-SKU spread
python abc_fws_scraper.py --all --limit 500             # wider crawl (slow; 10s/page)
```

**What it observes — and what it can't.** The storefront exposes, per SKU, a **price**
and a **binary in-stock / out-of-stock** status — but **no numeric quantity-on-hand**
(per-store stock is behind an AJAX endpoint robots.txt disallows). So you can't compute
literal "units sold" from a quantity delta. What you get, day over day, is **directional**:
price changes, out-of-stock ↔ restock transitions, and assortment churn (SKUs
appearing/disappearing). Imprecise, but a real read on what's moving.

**How it's polite.** robots.txt gives our crawler class a **10s crawl-delay** and
disallows cart/checkout/account/admin/search/facets + the store-stock AJAX. The scraper
touches **only the product sitemap and product pages**, sleeps `ABC_DELAY` (default 10s)
between requests, sends an honest identifying User-Agent (`ABC_UA`), and caps pages per
run. Read-only, stdlib-only.

**Cadence detection.** The sitemap has no `<lastmod>`, so each run snapshots a
**deterministic sample** (same SKUs every run, spread across the ~2,100-product catalog)
and diffs it against the previous snapshot (`abc_snapshot.json`). Run it daily for a few
days and *when* prices/stock flip tells you the refresh cadence — without crawling
everything each time. The "Δ" on the Hoodie Pulls row = SKUs that moved since the last run.
If price can't be read on most pages, the run self-reports **`degraded`**.

**STORE-LEVEL (validated live):** the store is a BigCommerce product **option** — each
product page lists ~133 store options (labels like `ABC #003 - OBT` / `Online`) and
`available_variant_values` names the **in-stock** store option-values. So we read per-store
in/out (+ the chain price) straight from the allowed product page — **no robots-disallowed
AJAX**. Snapshot is keyed `sku|storeValue`; the day-over-day diff catches per-store in/out
transitions + price moves. (~13.9k products via sitemap; price from `product:price:amount`.)
Self-reports `degraded` if the store-option / `available_variant_values` selectors drift.

**Run it on a cadence (before the backend exists):**

```
python unifyd/schedule_pull.py abc-fws --every 24h        # loop; runs now then daily
# or hands-off via cron:
0 6 * * *  curl -s -XPOST localhost:8765/api/run -H 'Content-Type: application/json' \
             -d '{"connId":"abc-fws","trigger":"scheduled"}' >/dev/null
```

### Data reader — `/api/analyze` (`analyze.py`)

The brain behind the dashboard's **"Overlay your data"**. POST `{header, rows, filename,
registries, full}`; it profiles every column deterministically (type, null-rate,
cardinality, stats, top values — stdlib), then has **Claude (opus-4-8)** read it and return
a context-aware first pass: what the dataset is + which of the 5 verticals (bev-alc / hemp /
cannabis / CPG / supplemental) + trust/quality flags, headline KPIs, anomalies, and findings
in the house style — each with a **justification** for the measures/dimensions chosen. When
the data maps onto the bev-alc Report Builder model, it also returns ready-to-materialize
report specs (`rbOpenFromConfig`-shaped); otherwise the universal read stands alone.

**OFF unless `ANTHROPIC_API_KEY` is set** (`anthropic` lazily imported). No key → `503
llm-disabled`, model error → `502`; the front-end falls back to the deterministic overlay.
The front-end sends the Report Builder vocabulary (dims/measures/viz) since it lives in the
dashboard. Profiling + aggregation are deterministic; the LLM only interprets + lays out.

### Other chains via Bright Data (`brightdata.py`)

ABC FWS works with the polite stdlib scraper because it serves bots and renders prices
server-side. The other chains can't be reached that way — **Total Wine / Binny's / Kroger**
return 403 (CDN bot management) and **Spec's** renders prices client-side (Next.js). For
those, `brightdata.py` fetches each page through **Bright Data's Web Unlocker** (one
authenticated POST; JS executed + bot defenses cleared, returns HTML/markdown).

**One-time setup** (yours — it needs your Bright Data account/key):

```
curl -fsSL https://cli.brightdata.com/install.sh | bash   # installs the `bdata` CLI
bdata login                                               # OAuth; saves key + creates zones
export BRIGHTDATA_API_KEY=...                             # (bdata login does this; or set manually)
# smoke test:
bdata scrape "https://example.com" -f markdown
```

`brightdata.py` works two ways: the **REST API** when `BRIGHTDATA_API_KEY` is set (the
deployed container) **or** the logged-in **`bdata` CLI** (local dev after `bdata login` —
no key export needed). Inert/raises when neither is present. stdlib-only.

**Spec's (`specs_scraper.py`, connId `specs`) — STORE-LEVEL, validated live, NO Bright Data.**
Spec's serves bots and embeds a per-store **`variants`** object in each product page (~114
stores, each with `inStock` + `unitPrice` in cents, keyed by a store code in `code` =
"<storeCode>-<sku>"). So we fetch the product page directly and read per-store price +
availability. Snapshot keyed `sku|storeCode`; day-over-day per-store in/out + price moves are
the directional signal (Spec's gives binary in/out per store, not a unit count like Binny's).
Sitemap-harvested (~50k products), deterministic sample, `--all` to widen. Self-reports
`degraded` if the variants block can't be parsed on most pages.

**Binny's (`binnys_scraper.py`, connId `binnys`) — STORE-LEVEL, validated live, NO Bright Data.**
Binny's runs on Algolia and its search key is public (every Algolia storefront ships one), so
we query the index directly — the same call the site's search box makes. Each record carries
**`storesPriceAndInventory`**: a per-store array with a **numeric `purchaseAvailability`**
(units on hand) + per-store prices. So the snapshot is keyed by `sku|storeCode` and the
day-over-day delta of `purchaseAvailability` per store = **directional units sold** — the run's
headline `units_moved`. ~31k products (each expands to ~49 store cells); default samples N
products, `--all` paginates. App id / index / key env-overridable (`BINNYS_ALGOLIA_*`);
self-reports `degraded` if the per-store schema changes.

### Hemp + bev-alc DTC on Shopify (`shopify_scraper.py`, connId `shopify-dtc`)

Most hemp-THC-beverage / CBD brands (and many craft bev-alc DTC brands) run on **Shopify**,
which exposes a public **`/products.json`** feed (title, variants, price, `available`, sku) —
the same data the storefront renders. One connector covers many brands: pull each domain's
catalog, snapshot per variant (`brand|variantId`), diff → price moves / in-out / assortment.
Stdlib-only, no Bright Data (open endpoints). Brand domains via `SHOPIFY_DOMAINS` (comma) or
the seed (BRĒZ, Cann, Cornbread Hemp, HOP WTR, Olipop — verified). These are single online
stores, so the granularity is brand/online-level (the hemp vertical is mostly DTC). Validated
live: 5 brands → 270 products → 991 variant-cells.

### Instacart — store-level via Bright Data managed dataset (`instacart_scraper.py`, connId `instacart`)

Instacart forbids scraping + is DataDome-protected (the Web Unlocker **403s** it), so the
sanctioned route is Bright Data's **managed Instacart dataset** (Web Scraper API): give it
store/category URLs, it returns per-store product records. **Paid + ToS-gray — your informed
call** (the vendor carries the protection/compliance). Needs `BRIGHTDATA_API_KEY` +
`BRIGHTDATA_INSTACART_DATASET` (the dataset id from your BD dashboard) +
`BRIGHTDATA_INSTACART_URLS`. Field names vary by BD product, so the first live run dumps
`instacart_debug.json` to lock the mapping; snapshot is keyed `store|productId` (store-level).

**Clean alternative — official partner APIs** (Instacart Developer Platform / DoorDash
partner / Uber Eats Marketplace): durable + not ToS-gray, but gated behind merchant/partner
approval. Everything needed to pursue them is in **`DELIVERY_PARTNER_APIS.md`**.

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
`server.py`). It has also been **promoted into the suite** as `../apps/mdm-master.html` —
the same control plane re-served under suite wiring (`../suite.css`, `../spine/spine.js`,
`../suite-header.js`), reading `/api/*` with the embedded `DATASETS` as the offline
fallback. It is the **Master** tab of the suite's MDM console (`../apps/mdm.html`, which
also hosts Catalog · Pulls · Ingestion). That promotion replaced the old `apps/item-mdm.html`.

The two surfaces share one `/api/*` contract: `apps/mdm-master.html` is the deployed suite view
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
