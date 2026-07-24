# `_archive/` — superseded scraper iterations (kept, not deleted)

## The standard rule

**One active scraper per source. The active one is whatever `source_registry.py`
points at — that is the single source of truth.** When a scraper is replaced by a
better iteration, the old file **moves here** with a one-line note below. We never
delete the work (it's expensive to re-derive and sometimes worth reviving), but it
must not sit alongside the active module where it gets picked up by mistake — that
is exactly how `kroger_api` got run instead of `kroger_atlas`, landing thin data
while the real per-store inventory bypass sat idle.

Nothing here is imported by an active source. `_archive/` is excluded from the run
path; moving a file here is a deliberate "this is retired" signal.

### When you replace a scraper
1. Point the `source_registry.py` entry at the new module.
2. `git mv unifyd/<old>.py unifyd/_archive/<old>.py`.
3. Add a line to the table below: what it was, what supersedes it, why.
4. Confirm nothing still imports it (`grep -rl "import <old>" unifyd/`), and that
   `server.py` + `run_sources.py` still import cleanly.

## Superseded vs parked — only *superseded* belongs here
- **Superseded** = a better iteration of the SAME job now runs instead → archive.
- **Parked** = unfinished/blocked work with no active replacement (e.g. Instacart,
  the DoorDash per-store catalog) → **stays in `unifyd/`**. It's not confusing the
  active source because there is no active source for that job yet.

## Archived

| File | Was | Superseded by | Note |
|---|---|---|---|
| `full_pull.py` | "run every no-BD/no-cookie source at full scale" one-shot runner | `run_sources.py` + `source_registry.py` | The registry-driven runner does this with verify-landing + per-source status; the old flat runner had neither. |
| `instacart_scraper.py` | store-level Instacart via **Bright Data** managed dataset (paid, per-record) | `instacart.py` (free self-hosted Playwright driver on the aggregator base) | The data was always Instacart's own `SearchResultsPlacements` GraphQL; BD was only the browser. The free driver captures a live zone + replays the persisted query — proven to land products with NO proxy/NO bd (instacart-free-verify CI). BD spend removed. |

## Superseded but NOT yet moved (blocked on a code change — do not lose track)

These are old iterations still wired into the **legacy `server.py /api/run`** path
(`_CONN_PULL`), so they can't move until that route is migrated to the registry runner.
Tracked here so they're not forgotten:

| File | Was | Superseded by | Blocker |
|---|---|---|---|
| `walmart_scraper.py` | Walmart via Bright Data (`walmart_product`, ~24 sampled) | `walmart_direct.py` (free `__NEXT_DATA__`, full catalog) | `server.py:walmart_pull` + `_CONN_PULL["walmart"]` still import it |
| `walmart_api.py` | Walmart I/O Affiliate API catalog | `walmart_direct.py` | `server.py:walmart_api_pull` + `_CONN_PULL["walmart-api"]` still import it |

## Active scraper per source (the map — read this before touching a scraper)

| Source | ACTIVE module (registry) | Do NOT run these (older/other) |
|---|---|---|
| Kroger inventory | `kroger_atlas.py` (exact per-store counts) | `kroger_api.py` = UPC seed only (kept, labeled `kroger-api`) |
| Walmart | `walmart_direct.py` | `walmart_scraper.py`, `walmart_api.py` (legacy, see above) |
| Total Wine | `total_wine_full.py` → drives `total_wine_inventory.py` (getProduct API) → uses `total_wine.py` for URLs | none — all three are one live chain |
| Hemp listings | `hemp_scan.py` | — |
| Hemp per-store counts | `hemp_inventory.py` | — |
| 7-Eleven | `sevennow_warm.py` | `sevennow.py` = library it wraps (keep) |
| Instacart | `instacart.py` (free Playwright driver, aggregator base) | `instacart_scraper.py` = archived BD dataset (paid) |
| Shopify | `off_premise.py` → `national_sweep("shopify")` / `shopify_catalog` (census-sweep recipe, registry id `shopify`) | `shopify_scraper.py` = standalone DTC scraper, folded into the census sweep 2026-07-24 (its brand seed → `off_premise.SHOPIFY_SEED`) |
