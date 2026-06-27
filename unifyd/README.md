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
