# Hoodie Suite — Scraper Cutover Pack

**Audience:** the web-scraping engineer taking over the off-premise data pipeline.
**Goal of this pack:** everything needed to run, maintain, and extend the scrapers that feed
the product & inventory master — access mechanics, exact field schemas, real data samples, the
hard-won gotchas, and what each source can realistically deliver.

Start here, then read the per-source file you're working on. The companion HTML view
(`docs/scrape-field-catalog.html`) renders the same field/level/sample content in the browser.

## The eight sources

| Source | File | Access | Anti-bot | Inventory signal | Status |
|---|---|---|---|---|---|
| Uber Eats | [uber-eats.md](uber-eats.md) | First-party BFF (own Chrome) | Headful + residential IP | Bounded on-hand proxy (`max_qty`) | Live |
| Postmates | [postmates.md](postmates.md) | Same Uber BFF | Headful | Bounded on-hand proxy | Live |
| Instacart | [instacart.md](instacart.md) | Persisted GraphQL / BD dataset | BD Browser (Forter) | in/out | **Recon — not landing** |
| Kroger | [kroger.md](kroger.md) | Internal *atlas* API | Warmed cookie + `x-laf-object` | Exact on-hand (when populated) | Live |
| Total Wine | [total-wine.md](total-wine.md) | Own `getProduct` API | PerimeterX (warmed cookie) | Exact units + shelf | Live |
| ABC FW&S | [abc.md](abc.md) | BigCommerce page + storefront GraphQL | Polite stdlib, 10s delay | Exact per-store on-hand | Live |
| Binny's | [binnys.md](binnys.md) | Algolia public search API | None (public key) | Exact per-store units | Live |
| Spec's | [specs.md](specs.md) | Embedded variants + inventory API | None (serves bots) | Exact per-store units | Live |

## The mental model (how the whole thing fits together)

```
scrapers (unifyd/*.py)  ──►  warehouse (Parquet on Tigris/S3, queried in place by DuckDB)
        │                          │
        │  each run lands:         ├─ <source>_products      (rich catalog snapshot + raw_json)
        │                          ├─ retail_observations/…  (dated per-store price+inventory time-series)
        │                          └─ <source>_sitemap/_stores/_geo  (the account/outlet universe)
        ▼
  source_registry.py  ──►  run_sources.run_one  ──►  verify row-count moved  ──►  run log (Data Console)
```

- **`unifyd/source_registry.py` is the single source of truth** for what runs and how. One row per
  source: `id`, `label`, `code` (the exact import+call), `tables` (for row-count verification),
  `klass`, `cadence`, `enabled`, `requires=[env]`. **Adding a source = adding a row.**
- **`klass`** decides where it runs: `headless` (direct/API — safe in parallel), `mac`
  (anti-bot headful browser — one at a time), `creds` (needs API keys), `build` (a derived
  master rebuild, not a scrape).
- **Two grains land from most sources:** a **catalog snapshot** (`<source>_products`, the rich
  per-product record + `raw_json`) and a **dated observation** (`retail_observations`, the lean
  diff-able per-store price/inventory row via `observe.record`).

## Anti-bot infrastructure (shared, reusable)

| Module | What it gives you |
|---|---|
| `unifyd/polite.py` | Per-host rate-limit + jitter, exponential backoff on 429/503 (honors `Retry-After`), **circuit breaker** (stop hammering a blocking host — the key control), UA rotation, gzip decode, optional BD-Unlocker proxy on `use_proxy=True`. |
| `unifyd/resi.py` | Provider-agnostic **residential proxy** shim (one `RESI_PROXY` env; IPRoyal→Webshare; BD fallback). `sticky(tag)` / `_session_url(tag)` pin one exit IP per warmed cookie; `geo_session_url(state=…)` targets a state. `isp_pool()` = flat-rate ISP IPs. `python resi.py` prints exit-IP vs home-IP. |
| `unifyd/browser_warm.py` | `Warmer(domain, channel="chrome", headful=True)` — persistent per-domain Chrome profile, `.human()` (mouse/scroll), `.click_through(uuid)` (trusted click that clears reCAPTCHA), `.post_json()` / in-page `fetch` for TLS-bound tokens. Generic `BROWSER_PROXY` hook (any provider). |
| `curl_cffi` | `requests` with `impersonate="chrome"` → a **real Chrome TLS/JA3 fingerprint** (stdlib urllib's TLS is an instant PerimeterX tell). The headless workhorse for API/JSON surfaces. |
| `unifyd/brightdata.py` | Bright Data Unlocker + Browser API + managed datasets. **Fallback only** — see the cost rule below. |

## The escalation ladder (pick the cheapest tier that works)

1. **Direct + `curl_cffi`** (Chrome TLS) — free. Works for open/public APIs (Binny's Algolia, Spec's, sitemaps).
2. **Polite + circuit breaker** — free, ban-safe. Default for HTML crawls (ABC, Spec's).
3. **Residential proxy** (`resi.py`) — cheap, flat-rate (Webshare). For IP-reputation walls.
4. **Warm-cookie-then-direct** — warm an anti-bot cookie once in a browser, then `requests.Session` on the *same IP* (PerimeterX/Akamai bind cookie↔IP). Total Wine, Kroger.
5. **Headful browser** (`browser_warm`, real Chrome, Xvfb in cloud) — for reCAPTCHA/botdefense (Uber Eats, Postmates).
6. **Bright Data** (Unlocker / Browser API / managed dataset) — DataDome/Forter or last resort.

**Cost rule (load-bearing):** Bright Data is ~95% of the scraping bill. **Direct-first, BD-fallback**
(`off_premise._fetch` escalates to BD only on a real bot wall). This took the bill from
~$2,700-3,400/mo to a ~$500-900/mo floor. `OFFPREM_NO_BD=1` forbids the BD fallback entirely.

## Standing rules (read these — they encode past outages)

- **`warehouse.write_parquet` fully OVERWRITES** the table — there is no append. Persistent,
  cross-run-growing catalogs **must** use `warehouse.write_accumulate(name, rows, key=…)`
  (query existing → drop re-written keys → append), or a partial/`--limit` run silently wipes a
  bigger prior one. An **empty-write clobber guard** refuses to overwrite a populated table with 0
  rows (an empty write is a failed scrape, not a rebuild).
- **One active scraper per source.** The registry names the live module. Supersede one → repoint
  the registry **and** `git mv unifyd/<old>.py unifyd/_archive/`. Never leave two iterations of a
  source side-by-side (that's how the thin `kroger_api` ran instead of the real `kroger_atlas`).
- **Landed data is NEVER rewritten.** Raw source tables stay raw (audit trail + model fuel). Every
  heal/normalization is a *translation-layer* rule applied downstream (`normalization_scout.py`
  discovers un-normalized classes; code fixes them). No `UPDATE` against landed Parquet.
- **Capture EVERYTHING + `raw_json`.** Promote structured columns on top, but keep the full source
  payload so nothing is lost — even fields we don't map yet. Same for outlets: capture the account
  (name/address/geo/phone/banner) from **every** source, even on a zero-product run.
- **Self-report `degraded`.** When selectors/keys drift, land a run with `status="degraded"` +
  `warnings[]` rather than silently emitting bad data. This is the maintainability keystone — the
  health digest and the run log surface it.
- **⚠ Dual-dispatch drift — the #1 cause of "fixed scrapers regressing."** There are TWO run paths:
  the registry (`source_registry.py` → `run_sources.run_one`, correct) and the app
  (`server.py` `/api/run` → hand-maintained `*_pull` functions, which drift). A fix lands in the
  registry and the app silently runs the old version. **The fix is to make `/api/run` delegate to
  `run_sources.run_one`.** Until that lands, always confirm which path actually ran.

## Where things run

- **`unifyd/server.py`** — local Flask agent (port 8765) serving the MDM console + `/api/run`.
- **Fly.io** — `hoodie-suite.fly.dev`, two process groups from one image: `app` (serves the suite +
  `/api`) and **`runner`** (8gb, no public route, runs the pulls off the serving box). The runner
  **must be in `main`'s `fly.toml`** — any merge to main auto-deploys and reconciles machines, so a
  runner missing from main gets destroyed mid-crawl. Every deploy restarts it → **prefer resumable
  scrapers.** 8gb won't run Binny's-full + ABC-full concurrently (merge memory) → run heavy crawls
  sequentially.
- **Mac (launchd)** — the near-real-time dispatcher (`run_due`). **launchd gotcha:** a job whose
  script lives under `~/Desktop` fails at spawn with `Operation not permitted` (exit 126) and never
  runs — use the `bash -c 'exec bash "<script>"'` form.

## Health, provenance & maintenance

- **`unifyd/health_digest.py`** — daily deterministic verdict on every registry source (failed/
  degraded runs, staleness vs cadence, row-count collapse, honest no-creds skips), each finding with
  evidence + `first_seen`. Exit 2 = critical. Mondays add **`deep_audit.py`**: field-drift (a column
  went null vs baseline = silent selector rot), fixture regression vs frozen `unifyd/fixtures/`, and
  docs-drift.
- **`tools/smoke_check.py` + `/smoke`** — proves every registered app serves and references resolve.
- **`unifyd/monitor.py` + `apps/data-console.html`** — the one trustworthy data-inspection surface
  (footer-only counts, run-log health, warehouse-identity stamping; never mock).
- **`gen_provenance.py` + `source_spec.py`** — keep the provenance summary and the **RAW per-source
  field inventory** current with every scraper change.

## Environment gotchas

- The repo lives under an **iCloud path with a smart apostrophe** (`Chris's MacBook Pro`), which
  breaks OpenSSL `cafile` loading → `polite.py` loads certifi via `cadata`, not `cafile=`. Always
  invoke `python -m playwright` (the venv console-script shebangs point at the old pre-iCloud path).
- **One branch per session** — the checkout is shared across concurrent agent sessions; do git work
  in an isolated `git worktree` off `origin/main`, never by switching the shared HEAD.

## The recipe pattern (how this scales)

Prove a *platform* config once and every store on that platform becomes a deterministic pull:
**Algolia** (Binny's), **SearchSpring** (ABC facets), **BigCommerce** (ABC), **Shopify /
WooCommerce / Wix / Squarespace** (off-premise independents), **Bottlecapps**, **City Hive** (SEO
surface). `source_analyzer.py` detects the platform + resolves config for unknown sites; proven
configs become reusable recipes. This is the leverage: one recipe unlocks a whole platform nationally.
