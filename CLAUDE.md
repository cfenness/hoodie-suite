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
    is the proven counter-example: a self-hosted Chromium (NO Bright Data, NO proxy) drives Instacart's own
    `SearchResultsPlacements` GraphQL and lands per-store product+price **from a bare datacenter IP** — a cloud
    probe on a bare datacenter runner cleared the anti-bot with no paid layer. That datacenter-IP finding is the
    load-bearing lesson and it stands. The old BD managed-dataset scraper is archived
    (`_archive/instacart_scraper.py`). Never re-add Bright Data to Instacart "to make it work"; if the
    datacenter path ever regresses, prove the block from a residential IP first (see the paid-path rule).
    **Operational status is narrower than the capability** (don't read the paragraph above as "it's running"):
    the only registry entry is `instacart-bevalc`, `enabled=False`, `requires=["INSTACART_SESSION_COOKIES"]`,
    **manual trigger only** — because bev-alc specifically needs a logged-in, age-verified session (anonymous
    gets "alcohol products aren't available"). The dispatcher therefore never runs Instacart; nothing pulls
    on a cadence today. The free anonymous path covers NON-alcohol only. Also: the driver is **patchright**,
    not playwright (the image ships only patchright — see the browser-driver rule below), and the connector
    defaults **headful** under Xvfb (`BROWSER_HEADFUL` unset → `headless=False`, registry `klass="mac"`), not
    headless.
- **NEVER import playwright directly — resolve the driver (load-bearing, shipped broken 7×).** The Fly image
  installs **`patchright` and NOT `playwright`** (`unifyd/requirements.txt` has `playwright>=1.40` commented
  out; the Dockerfile pip line adds only patchright). Because every such import is *function-local*, a module
  that reaches for playwright imports fine, compiles, passes its own tests, and deploys green — then
  `ModuleNotFoundError`s the first time that path actually runs in production, where no check we own can see
  it. Use the one shared resolver:
  `import browser_warm; sync_playwright = browser_warm.sync_playwright_api()`. This was fixed once for
  DoorDash (PR #688) and later found in **six more** modules, two of them `enabled=True` daily (`publix`
  hard-failed every tick; `total-wine` failed inside a per-batch `try/except` and reported runs COMPLETE
  having landed 0 rows — a quiet degrade that looks like success). `unifyd/browser_driver_test.py` is the
  mechanical ratchet; it names the offending file:line. Don't add playwright to the image to "fix" this.

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
- `unifyd/vip_brandbuilder_census.py` — the **complete** Brand Builder distributor directory.
  The sourceCode is a 5-digit numeric id, so the keyspace (`00000`-`99999`) is ENUMERABLE — no
  auth, no proxy, ~20 req/s server-side ceiling. Every `/info` hit is confirmed against
  `/products` before it counts, so a VIP record with no catalog lands as `info_only`, not as a
  find. Swept 2026-08-02: **99,997/100,000 codes probed → 338 confirmed catalogs + 14 info_only**
  → `vip_brandbuilder_directory`. That 338 IS the Brand Builder universe; there is no
  hand-harvesting left to do, and `vtinfo_bbs.targets()` reads this table as the pull list.
- `unifyd/vtinfo_bbs.py` — VIP **Brand Builder** distributor catalog (connId `vip-brandbuilder`).
  Distinct from `vtinfo.py` (that's `finder.vtinfo.com` where-to-buy carriage); this is
  `products.vtinfo.com/bbs/v1/distributor/<sourceCode>/{info,brands,products}` — an **open JSON
  API** (no auth/cookie/token, CORS `*`) behind the Angular UI at `/brandbuilder/<sourceCode>/`.
  One recipe per **platform**: point it at any VIP sourceCode → that distributor's whole book,
  at **package grain** (product × pack) with `dist_item_code` + **retail UPC** (zero-padded to 12
  the way the app does) + ABV/style/supplier + category (from the brand-group join). Snapshot per
  distributor (keyed `dist_item_code`) → new/dropped; self-reports `degraded` if `product_packages`
  yields 0 rows or UPC fill collapses. Lands `vip_brandbuilder_items` (`write_accumulate`, key
  `distributor_id|dist_item_code`). Seed = Columbia Distributing WA `01191` (6,756 items); grow the
  `DISTRIBUTORS` map (`--discover <distributor-url>` harvests a sourceCode). stdlib-only, headless.
- `unifyd/sevenfifty.py` — SevenFifty/Provi distributor **storefront** catalog (connId `sevenfifty`).
  `<slug>.storefronts.site/search.json?page&per_page=100` — an **open JSON search API** (no auth;
  only partner PRICING is login-gated, so this is an **item-master** pull, not a price pull).
  One recipe per **platform**: any storefront slug → that distributor's full item book at **SKU
  grain** (the distributor's real item numbers) with producer/supplier/type/style/appellation/
  country/size/case/image/token. Paginates on `meta.total_pages`, dedupes by SKU, snapshot per
  storefront → new/dropped; self-reports `degraded` if the API reports items but 0 parse. Lands
  `sevenfifty_items` (`write_accumulate`, key `storefront|sku`). Seed = Johnson Brothers
  `johnsonbrothers` (25,590 items); grow the `STOREFRONTS` map. stdlib-only, headless. Together
  with `vip-brandbuilder` these two platform recipes are the fast path to the major distributors'
  catalogs (Reyes, Breakthru, RNDC, …) — one sourceCode / slug at a time.

### DAM harvesting — `rights.py` + `dam.py` + `dam_<vendor>.py` (ToS is the contract)
Harvesting supplier/brand **digital-asset libraries** (media centres) is the first capability where
the payload is somebody else's **copyrighted work**, not a fact. Every other source here lands prices
and counts, which are uncopyrightable; a DAM lands studio imagery, and a 200 OK is not a licence.

- **`unifyd/rights.py` — the gate, enforced in code.** Every DAM source carries a
  `rights_records/<id>.json`: a **verbatim ToS snapshot + sha256 + capture date**, the **verbatim
  robots.txt** plus a per-URL decision for each path the connector fetches, and a parsed permission
  classification (`image_use` = permitted/prohibited/silent, `scope` =
  none/internal_only/editorial_press/commercial_redistribution, attribution/alteration/trade-only/
  expiry/confidence/needs_counsel, plus `facts_use` written out explicitly so a `prohibited`
  record cannot be misread as "this source is off"). The design's three scope values gain a fourth,
  `none`, because a hold needs a scope to *be*.
  **A grant must run the right way.** Every corporate ToS contains a lavish perpetual ROYALTY-FREE
  licence — and it is the one *you* grant *them* over anything you upload. Measured live on three
  suppliers at once (AB InBev, William Grant, Heaven Hill), all three classified
  `permitted/editorial_press` at HIGH confidence off their user-content clause, which would have
  authorised a CV gallery on assets nobody licensed to us. Grant rules are therefore direction-
  guarded; an inbound match is kept as ZERO-weight evidence (`grant-inbound`) so a reviewer can see
  the clause was found and rejected. `rights.load()` **raises if the record is missing** — there is no
  harvest-now-sort-the-rights-out-later path. `may()/require()/emit()` are the only interpretation of
  the model, every emission (allowed *and denied*) is logged to `dam_emissions`, and `dam_rights_test.py`
  is the ratchet that fails a registry row lacking its record.
  Three rules are load-bearing and tested: (1) **facts always flow** — `catalog_metadata`/
  `catalog_pointer` are ungated; (2) **silence is not permission** — `silent` holds exactly like
  `prohibited`, and an unknown action, an unrecognized scope, an expired grant or a **stale** record
  (terms moved since review) all deny; (3) **`needs_counsel` guards the affirmative act** — a grant is
  inert until `counsel_cleared`, while an enforced hold needs no lawyer. A grant phrase inside a
  negation is not a grant ("does **not** grant you any … license" scored as a grant once — never again).
- **`unifyd/dam.py` — the shared spine + THE CHOKEPOINT.** `asset_bytes()` is the only function that
  may fetch an asset's bytes and it calls `require(rec, "fetch_asset")` first; a perceptual hash or
  embedding is a derivative work and is gated at the same level. Connectors never open an asset URL —
  `dam_dna_test.py` scans the package's source and fails if one does. Lands `dam_assets`
  (pointer rows carrying `retention` / `phash` / `embedding_ref` / `withheld_reason` / `rights_ref`,
  so the row shows what was withheld and why) and `brand_events` (the dated product-event feed).
  **Reading press releases for facts** is a SECOND, separate chokepoint — `dam.document_text()`,
  gated on `fetch_document_facts`, which is ungated by scope because facts are uncopyrightable and a
  press release exists to be read. Four things keep it from being a hole in `fetch_asset`, all
  enforced: **text document types only** (an image is refused *before* the request), **bytes are
  transient** (never written or cached), **the prose never lands** (`land()` refuses any event field
  over 500 chars, so expression cannot ride into the warehouse inside a "fact" column), and it is
  **logged separately**. It is staleness-sensitive — it touches their server — while cataloguing what
  we already hold is not. Dates/markets/prices come from the **dateline and marked retail prices**,
  read verbatim; an unmarked currency amount is not a price point (a $1M donation is not an SRP).
  PDF text needs the optional `pypdf` cap: without it PDF releases contribute no facts and the run
  SAYS so, because 0-of-91-PDFs-read must never look like a source with no PDFs.
  **The LLM narrative pass** (design §3) is `DAM_LLM=1`, off by default, and structurally additive:
  it is only asked about fields that neither the DOCUMENT stated nor the EVENT already carries, and
  everything it returns is written `INFERENCE`. Checking the document alone was not enough — it let
  the model replace a folder-derived (deterministic) date with a guess simply because the release
  didn't repeat it. The exact read always wins; the model can add a fact, never replace one.

  Honesty contract: every derived field is labelled **DETERMINISTIC or INFERENCE** in
  `field_provenance` — brand match is deterministic *unless* the alias is also a common word
  (MARTINI, BOMBAY, PATRON → INFERENCE), event_type/market are always inference. **A DAM's
  `created_on` is the UPLOAD stamp, not the event date** (Bacardi's whole 2018 folder reads
  2018-04-11, the bulk-migration day), so dates come from **year folders** at `precision=year` and are
  otherwise NULL — never back-filled.
- **`unifyd/dam_canon.py` — the canon key (P2).** Resolves a DAM brand literal to `dim_brand` via
  `overlay_match.brand_key()` applied to BOTH sides, so `brand_events.hoodie_brand_id` is the master's
  `hoodie_id` rather than a vendor slug. **One tier, exact key match, no fuzzy fallback** — a wrong
  `hoodie_brand_id` silently attributes a competitor's launch to your brand in every roll-up and
  nothing about the row looks wrong; fuzzy identity is hoodie-canon's cascade, not a regex here.
  Unmatched → the vendor slug stays, `brand_resolution="unresolved"`, and the provenance claim for
  `hoodie_brand_id` is REMOVED. There is deliberately **no local re-implementation of the key**: the
  obvious lookalike misses `precleanse.nbrand`'s generic-token drop (real key for "Grey Goose Vodka"
  is `grey goose`), so a fallback wouldn't degrade the match, it would silently produce a DIFFERENT
  match set — the module refuses to resolve instead. An unreadable master is `master-unavailable`,
  distinct from `unresolved`, and never costs the facts: the events land in full either way.
- **`unifyd/dam_dna.py`** — the **DNA** platform connector (`dna.online`). ONE CONNECTOR PER DAM
  VENDOR, one rights record per SUPPLIER: transport is a property of the platform, permission is a
  property of the supplier, so `TENANTS` holds host+drive+brands per supplier and each is its own
  registry source with its own record. Bacardi's media centre is not bespoke — its footer is
  "Powered by DNA" and the surface is DNA's stock shape (`company_id` tenant, numbered
  `company_drive_id` drives, `/drives/view-new/`, `/drives/get-tree/`, `/company-files/`, an Algolia
  index prefixed `DNA_`), so adding a supplier on DNA is a `TENANTS` row + a rights record, not new
  code. `fingerprint(url)` is the discovery half (feeds the P4 vendor census): it reports whether a
  candidate media centre is a DNA tenant and which drive. Source `dam-bacardi` (weekly) is the first
  tenant: `media.bacardilimited.com` drive 42 ("Bacardi Public"): the page bootstraps
  `window.DriveViewState` (brace-matched, not regex-terminated — descriptions contain `}`), and
  `/drives/get-tree/<drive>?folder_id=<n>` is the SPA's own JSON API. Both live **outside** every
  robots `Disallow` (`/api/` **is** disallowed — if the tree ever moves under it, this connector
  stops). No auth, no cookie, no browser: **3 requests enumerate all 2,490 assets across 17/17
  folders**. Coverage is *not* gated on the platform's `file_amount` counter, which is stale in both
  directions (Videos claims 2 serves 4; Media Files claims 0 serves 75) — it is gated on visiting
  every folder, and a shortfall is a warning, never a silent partial.
- **`unifyd/dam_gallery.py`** (source `dam-gallery`, weekly) — the **CV reference gallery** (P3):
  pointer + licence + perceptual hash + embedding per official studio image, each derivation gated
  **per asset** (never once at the top of a run, so a record going stale mid-run stops the rest).
  A row always lands — a NULL `phash` with a `withheld_reason` is the honest shape; a missing row
  would read as "the supplier had no imagery". The dHash is for **identity within the gallery**
  (the same studio file uploaded five times collapses to one reference) and explicitly NOT the
  studio→shelf matcher — perceptual hashes fail on bottles in the wild ([[image-match-signal]]),
  where the embedding is the signal. The embedding backend is **pluggable and absent by default**:
  CLIP needs torch, which this image does not ship, so `embedder()` resolves one if present and
  otherwise reports `embedding_backend="unavailable"` rather than quietly shipping a vector-less
  gallery.
  **It is currently empty on purpose.** No surveyed supplier grants image reuse, so the pipeline runs,
  lands pointers, and derives nothing. That is the gate working.
- **`unifyd/dam_census.py`** (source `dam-census`, monthly) — the **vendor census** (P4): supplier →
  media centre → DAM vendor → public? → a **provisional** permission class. It is what tells you which
  platform to build the next connector for. **Discovery is link-following, not hostname guessing**:
  the capability's method rule forbids subdomain enumeration while the design sketched
  `media.<co>.com` patterns, and that tension is resolved in favour of the rule — we fetch the
  supplier's own site and follow the media link THEY publish. Conventional hostnames are opt-in
  (`DAM_CENSUS_PROBE=1`, off), and every row records `discovery_method`. Hints are two-tier: the tidy
  phrasing alone found ONE centre across 24 suppliers, because what companies actually publish is
  `/news`, `/media`, `news-and-media`. **Failures are named, never counted as absence** — age gate /
  client-rendered shell / SSL mismatch / no link are four different findings, and only some mean stop
  looking. `dam_census` is a RESEARCH table: nothing runs off it, and promoting a supplier means
  authoring a reviewed rights record + a `TENANTS` row by hand.
  `source_analyzer.analyze()` attaches `out["dam"] = {extraction, rights}` when a page fingerprints as
  a media centre — design §4's two plans, marked provisional.
- **What Bacardi's terms actually say, and why the CV gallery is empty for them.** Their ToS (which by
  its own §1 covers "any and all other online or digital platforms … which we maintain") grants **no
  reuse licence**: §3 "does not grant you any rights, title, interest or license to any Materials",
  downloads are for "your lawful, personal, non-commercial use", "You must not use any part of the
  Materials … for commercial purposes"; §4 "You are not permitted to use the Materials outside of the
  Site". A scan of all 23.7k characters finds **no press/editorial carve-out** — no `press`,
  `editorial`, `journalis`, `broadcast`, `royalty`, `attribution` or `credit` clause exists to rely on.
  So the source runs at `prohibited`/`none`: 2,490 asset **pointers** + 343 brand events land, and
  **zero bytes, hashes or embeddings** are produced. **That is the connector working, not failing** —
  do NOT "fix" the empty gallery by widening scope in the registry or the record. Scope widens only
  via a new record revision with `counsel_cleared`, backed by the written permission the record's
  `escalation` field describes (ToS §13 → Bacardi's Digital Director).
- `unifyd/salsify.py` — **Salsify Sites**: every public catalog on `sites.salsify.com`. **Registry id
  `salsify` (daily) is the ONE writer** — it refreshes the directory then pulls every seeded catalog
  (bbg, sazerac, heaven-hill) sequentially in one process. `bbg` exists as a disabled registry entry for
  history + manual BBG-only runs; do NOT re-enable it as its own source. `salsify_products` is merged
  with `write_accumulate` (read-modify-write, single-writer), so two sources writing it get their own
  dispatcher machines and silently lose each other's rows — observed 2026-08-03: a run journalled 8,200
  landed and the table held 1,574 afterwards. The third platform recipe, and **the URL loops it**: `sites.salsify.com/robots.txt` publishes `sitemap_index.xml`, a live
  directory of every PUBLIC catalog on the platform (519 sites / 118 orgs at 2026-08-03) — `discover()`
  walks it, probes each root, lands `salsify_catalogs`, and any row there is pullable by org/site uuid.
  Route shape (same for every site): root `__NEXT_DATA__` → live `buildId` + name/size/facets;
  `_next/data/<buildId>/index.json` = **list page 1** (`products/1.json` 403s — that bug silently dropped
  the first 16 products of every catalog); `products/<N>.json` for N>1; `product/<id>/<slug>.json` for
  detail; `sitemap_1.xml` for the whole id/slug universe in one fetch when published (Sazerac/Heaven Hill
  yes, BBG no → paging fallback). **Capture is two-grained because the catalogs share no property
  namespace** — BBG publishes SAP wholesaler attributes, Sazerac a full GS1/GDSN item master (GTIN, ABV,
  proof, net content, weights, ingredients, nutrients, closure, TTB COLA id), Heaven Hill a third set:
  `salsify_products` (accumulating, thin canonical columns incl. **`dist_item_code`** = Breakthru's SAP
  Material ID and **`item_description`** = their own description) + `salsify_properties` (append-only,
  date-partitioned — EVERY property of every property set, every facet, every asset, **every value of a
  multi-value field**, whatever the catalog calls it; only products whose fingerprint MOVED are re-emitted,
  so a daily cadence costs the diff, not the catalog). Canonical mapping is exact-alias first, then a
  guarded pattern pass; unmapped properties are never dropped, only left out of a first-class column.
  stdlib-only, headless, resumable. Tests: `salsify_test.py` (fixtures from two namespaces, no network).
  Supersedes `bbg_salsify.py` (archived).
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
- **Overlay Your Data** (`unifyd/overlay*.py`) — upload → match → cleanse → derive → diagnose →
  report, in one pass, on an arbitrary customer file. `apps/overlay.html` is the surface;
  `docs/OVERLAY-DESIGN.md` is the full design. `overlay.py` orchestrates and writes the artifact;
  the stages are separate modules so each is testable alone, and each is INJECTED with its data
  source (`MemoryMaster`/`MemoryObs` in tests, `WarehouseMaster`/`WarehouseObs` in production) —
  which is why the whole pipeline tests with no DuckDB and no network (`overlay_test.py`, 45 checks).
  - `overlay_map.py` — their columns → master fields, **values before headers**: a column whose
    values pass GS1 classification IS an identifier regardless of its header (this is how "your UPC
    field is actually GTIN" gets caught). Header-only mappings are INFERENCE, never fact. Every
    column lands in `mapped | derivable | proprietary | pii` — the three-way count ("of your 140
    fields: 100 cleansed, 20 derived, 20 yours") is a headline stat, and the PII bucket is excluded
    from every downstream stage **by construction** (it never enters the mapped view) and publishes
    no statistics about itself.
  - `overlay_match.py` — the genuinely new pass: five tiers (UPC exact → zero-strip heal →
    `dist_item_code` → signature cluster → unmatched-with-a-why-histogram), reported **by tier,
    never blended**, because the deterministic share is the number that matters. Bulk lookups over
    distinct keys, so 25k rows resolve in <1s. A tier whose table is unreadable disables only
    itself and says so — a missing table must never read as a low match rate.
  - `overlay_detect.py` + `overlay_bands.json` — the **detector registry** (14 today). Three gates:
    `requires` (fields absent ⇒ silent skip), the **precision gate** (structural rules are proofs and
    pass by construction; a heuristic with no measured precision in `overlay_precision.json` RUNS
    SILENTLY rather than optimistically), and dq.js's misfire suppression (>5% of eligible rows at
    flat confidence ⇒ withheld + one meta-card, unless each hit is individually proven). Findings
    carry a ROOT CAUSE and the arithmetic, never a symptom. `backtest()` is what moves a heuristic
    from silent to visible. ABV bands + state quirks are versioned DATA, not code.
  - `overlay_market.py` — the join to our own observations, bar-gated: every claim carries n,
    geography, freshness and method, and **a block below its bar is absent, not padded**.
    `mode="brand"` strips negative claims server-side (a brand-embedded widget must not badge that
    brand's own accounts). Price is a PERCENTILE against the observed distribution, never a % off.
  - `dist_xwalk.py` → `dist_item_xwalk` — the Tier-3 spine (registry build `build-dist-xwalk`),
    from `vip_brandbuilder_items` + `bbg_products`. Adding a distributor is a scrape upstream.
  - `xlsx_write.py` — stdlib .xlsx writer. The returned workbook is THE deliverable (it gets
    forwarded internally), so its writer has no dependency risk. Five sheets: their data annotated
    (originals **never modified**, `clean_*` alongside), the field map, findings, the join report,
    provenance (run stamp, registry version, measured P/R, what we withheld and why).
  - Endpoints: `POST /api/overlay/run`, `POST /api/overlay/workbook` (re-runs rather than caching —
    that is what keeps the ephemerality statement on the page true), `GET /api/overlay/registry`
    (the catalog, published on purpose), `GET /api/overlay/provenance`.
- `unifyd/locator_signal.py` — the **"why go here"** layer under the Product Locator. `/api/locator`
  answers WHO CARRIES IT from the distributor feed (vtinfo); `/api/locator/offers` answers why go
  *here*, from data we already land: verified stock + on-hand (`retail_observations`), everyday vs.
  promo price, sell-through (`fact_velocity`), instrument tier (`obs_quality_source`) and geo
  (`src_outlets`, joined on `(source, store_id)`). The price primitive is **Google-Flights-shaped** —
  not "cheapest" but "is this a GOOD price", scored against the trailing local distribution — plus a
  wait-or-buy read off the store's own promo cadence. **Three rules are load-bearing and tested:**
  (1) rank by price PERCENTILE, never by % off — a deep cut on an inflated everyday can still sit
  above the area median; (2) compare only per-750ml equivalents (`price_signal.unit_price`) so a
  1.75L can't pollute a 750ml pool; (3) below `MIN_REF` priced stores, or in a FLAT market, emit no
  band and say why — which is also why there's no hardcoded control-state list (uniform state pricing
  *is* a flat distribution, so it falls out of the data). Two render modes, filtered **server-side**:
  `consumer` gets the full verdict, `mode=brand` strips every negative claim (a brand-embedded widget
  pointing at the brand's own accounts must not badge them "high price"). Surface:
  `apps/product-locator.html`. Tests: `locator_signal_test.py` (pure, stdlib-only) +
  `locator_offers_test.py` (seeded local warehouse — needs duckdb, skips cleanly without it).
- `unifyd/menu_ingest.py` — parse a DISTRIBUTOR WHOLESALE MENU file (xlsx/csv; cannabis
  Curaleaf NY is the reference shape) into normalized order lines. stdlib-only (xlsx = zipped
  XML), heuristic header-row detection + column synonyms, Excel serial dates, THC normalization.
  **Inline sub-headers are parsed, not dropped:** a section/sub-header row (no price/batch/units/
  thc/msrp) is classified brand-vs-product-family, its size/form/category are read off the header
  and cascaded to the child rows, and a terse child (`Indica : Wedding Cake`) is composed into the
  full product using its family header — which is often the ONLY place the product/size lives.
  **`parse_smart()` adds a Claude fallback** that fires ONLY when the deterministic pass fails or is
  low-confidence (few lines / most lines unpriced): it hands the raw grid to Claude (forced tool call,
  needs `ANTHROPIC_API_KEY`) to recover the same normalized lines — so an alien layout still lands, with
  no LLM cost on menus that already parse cleanly (the `/api/menus/upload` endpoint uses it).
  Lands `distributor_menu_items`; behind `apps/ordering.html`
  (Wholesale Ordering: all menus → one catalog → one order → per-distributor PO sheets via
  `/api/menus/*` + `/api/orders*`). **Auto-send:** a distributor contact book
  (`/api/distributors`, `distributor_contacts.json`) + `POST /api/orders/<id>/send` emails each
  distributor its PO-sheet CSV via SMTP (`SMTP_HOST/PORT/USER/PASS/FROM`, STARTTLS default on);
  unconfigured → returns `email-not-configured` and the UI falls back to download + copy-email.
  **Order status:** `POST /api/orders/<id>/status` advances a forward-only lifecycle
  (submitted → sent → confirmed → delivered, or cancelled) with a timestamped `status_history`;
  the ordering page shows a status pill per order + advance/cancel controls in the order modal.
- `unifyd/menu_mailbox.py` — **auto-ingest emailed menus (IMAP)**: `POST /api/menus/poll` pulls the
  spreadsheet attachments off unseen messages, runs each through `menu_ingest.parse_smart`, lands
  them, and marks the mail seen — so the catalog stays current with no manual upload. Env-gated
  (`MENU_IMAP_HOST/PORT/USER/PASS/FOLDER`, optional `MENU_IMAP_SENDERS` allowlist); unconfigured →
  `mailbox-not-configured` and the ordering page hides its "Check email" button (`/api/menus/mailbox`
  reports the state). Runnable on a cadence via `schedule_pull` / cron.
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
`src_<grain>` + signal tables — the seed), `MART` (views). The generator stages SQL only;
**the load itself is the registry BUILD `snowflake-load`** (`unifyd/snowflake_load.py` →
`snowflake/run_load.py`, `snowflake-connector-python`): the hourly dispatcher runs it daily on its
own ephemeral machine, change-aware (only tables whose Parquet moved reload; ledger at
`_snowflake/load_state.json`), verify-landing one row per run in `snowflake_load_runs`. It reports
`no-creds` until the `SNOWFLAKE_*` Fly secrets are set (go-live runbook in `snowflake/README.md`).
`python snowflake/build_snowflake_sql.py [--live]` regenerates; `--live`
reads the warehouse to include every present table and resolve bucketed (v2) tables to their manifest's
active parts. `snowflake/` is engine/infra — never web-served (not in `_SUITE_OK_TOP`), like `unifyd/`.
See `snowflake/README.md`.

## Health & smoke (keeping the served data trustworthy)

Two standing tools exist so failures are loud, not quiet. Keep them passing and keep them honest.

- **Data health — `unifyd/health_digest.py`**: the daily deterministic verdict on every
  registry-enabled source (failed/degraded runs, staleness vs cadence, row-count collapse,
  honest no-creds skips). Every finding cites evidence and carries `first_seen` so new breaks
  stand out. Runs **on Fly** — the hourly dispatcher (`dispatch_ephemeral._refresh_health`) recomputes it
  each tick; there is **no Mac launchd** (see the "nothing runs locally" rule below). It
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
- **NOTHING RUNS LOCALLY (hard rule)**: no scrape, pull, geo pass, backfill, health digest, or scheduled
  tick runs on anyone's Mac — **all execution is on Fly**. Scheduling is the Fly hourly dispatcher
  (`unifyd/dispatch_ephemeral.py`, a Fly scheduled machine): it reads the shared ledger, spawns an ephemeral
  Fly machine per due source (headless on 4GB; **headful** — the `klass="mac"` sources, a legacy name for
  "real browser": `kroger`/`ubereats`/`postmates`/`sevennow`/`bottlecapps`/`cityhive` — on 8GB with Xvfb +
  system Chrome + patchright, see `run_ephemeral.sh`), and folds in the health digest. The old Mac launchd
  agents (`com.hoodie.due`, `com.hoodie.health`) and their scripts (`run_due.sh`, `run_health_digest.sh`) are
  **retired/removed** — the Fly dispatcher already runs the exact same set. Never tight-loop `flyctl`
  (it rate-blocks the home IP).
  - **Scheduling is NOT on GitHub Actions.** `cloud-sources.yml` / `scrape-runner.yml` /
    `warm-sources.yml` are `workflow_dispatch`-only escape hatches — their crons were removed
    (2026-07-27). The repo has no Actions minutes, so every scheduled run was a standing failure,
    and the Fly dispatcher already covers the same registry. Don't re-add a `schedule:` to them.
  - **RE-PIN THE DISPATCHER AFTER A DEPLOY THAT TOUCHES `source_registry.py`** —
    `tools/repin_dispatcher.sh`. The dispatcher machine (metadata `role=dispatcher`) deliberately has
    no process group, which also means `flyctl deploy` never updates it: it keeps running the image it
    was pinned to. Because due-ness is computed from *its* copy of the registry, a newly added source
    stays invisible to the scheduler until it's re-pinned. The dispatcher now logs a loud
    `WARNING — dispatcher image is STALE` when this has happened. (Failure mode seen live: the machine
    sat on `init.cmd=["bash"]`, so every hourly tick started, exited 0 in ~1s, and dispatched nothing.)

## Deploy

**Production is Fly.io** — `hoodie-suite.fly.dev`, one all-in-one machine serving the
static suite **and** `/api` (see `DEPLOY-FLY.md`, `fly.toml`, `Dockerfile`). `main` is
production; there is no staging branch.

- **THERE IS NO AUTO-DEPLOY. Merging does NOT ship.** Every deploy is run deliberately.
  GitHub Actions is **not** used here (metered/variable cost — same reason scheduling
  already moved to the Fly dispatcher). The deploy/scrape workflows were deleted; do not
  re-add them, and do not treat Actions billing as a deploy blocker.
- **The deploy command** — always from a *clean worktree of `origin/main`*, never the
  local tree (a dirty/behind tree silently ships someone else's WIP or reverts a fix):

  ```bash
  git worktree add /tmp/deploy-main origin/main --detach
  cd /tmp/deploy-main && ~/.fly/bin/flyctl deploy --ha=false --remote-only
  ```

  `--remote-only` builds on **Fly's** builder, so nothing compiles on the Mac.
- **VERIFY THE DEPLOY LANDED — the output is not proof.** Concurrent sessions deploy this
  same app; a later deploy of a *stale* tree silently reverts a fix that was already live
  (this happened 2026-07-27 and cost hours). Confirm the running container actually has
  your change: `flyctl ssh console -a hoodie-suite --machine <id> -C "grep -n <marker> /app/..."`,
  plus `flyctl releases -a hoodie-suite` and a `curl` of `/api/health`.
- **What ships:** the Dockerfile copies the repo; the engine (`unifyd/`, `*.py`, secrets,
  dotfiles) is present in the image but **never web-served** — the static file route
  enforces a `_SUITE_OK_TOP` allowlist on the resolved path.
- **NO GITHUB ACTIONS, EVER — variable cost (hard rule).** The repo now has **zero** workflows.
  The deploy/scrape ones were removed earlier; the last survivor, `tests.yml` (warehouse-compat +
  dispatch-guard), went on 2026-07-28 — it was failing 6/6 on environment issues while both suites
  passed locally, i.e. billing minutes to produce noise, and it never ran the guard that actually
  caught a real break. **Nothing auto-deploys — merging a PR ships nothing.**
  The replacement is free, local, and strictly broader:
  `python3 tools/release_train.py integrate` runs `smoke_check` plus **every** `unifyd/*_test.py`
  (not a path-triggered subset), labels each failure introduced-vs-pre-existing, and attributes it
  to the PR that broke it. Ship with `python3 tools/release_train.py deploy` (or a manual
  `flyctl deploy --remote-only` from a clean `origin/main`). **Never re-add a workflow.**
- **DEPLOY GUARD (installed, mechanical).** `flyctl deploy` ships the WORKING TREE, not a branch,
  and with several sessions in separate worktrees that is a live clobber — on 2026-07-28 a merged
  feature was deployed and then silently wiped from the running container by a later deploy from a
  stale worktree, while every release read `complete`. `tools/deploy_guard.py` installs a shim at
  `~/.fly/bin/flyctl` that refuses `deploy` unless the tree is a **clean origin/main**; every other
  subcommand passes through untouched, and it **fails open** on any error it can't resolve.
  `HOODIE_DEPLOY_OK=1` bypasses it deliberately; `python3 tools/deploy_guard.py uninstall` removes it.
  **The supported way to ship is `python3 tools/release_train.py deploy`** — it builds its own clean
  checkout at origin/main, verifies it, deploys, confirms the release landed, and re-pins the
  dispatcher when `source_registry.py` moved.
- **Legacy S3/CloudFront** (`deploy.sh`, `cloudfront/`) is **DORMANT** — kept for
  reference only. Ignore it unless deliberately resuming S3 serving.

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
