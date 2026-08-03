#!/usr/bin/env python3
"""source_registry.py — THE canonical list of every data source we run, and how.

One source of truth so nothing is missed or silently dropped. `run_sources.py` drives this: it runs each source,
checks its table row-count moved (verify-landing), and records a run-log the Data Console reads. Add a source =
add one row here.

Each entry:
  id      : stable key
  label   : human name
  code    : python to run it (executed in a subprocess: `import ...; ...` — must LAND to the warehouse itself)
  tables  : warehouse table(s) it writes (for before/after row-count verification)
  klass   : "headless" (direct/API — safe to run in parallel)
          | "mac"      (anti-bot headful browser — MUST run one-at-a-time on the Mac)
          | "creds"    (needs API creds; headless)
  cadence : "daily" | "weekly"  (weekly for huge/slow backfills like TTB)
  enabled : run it in the daily pass
  note    : mechanism / caveats

Optional scheduling metadata (the --due dispatcher, NRT-PLAN.md §3):
  interval_h : refresh interval in hours — overrides the cadence default (daily=24, weekly=168).
               This is the near-real-time knob: promote a source to the hot tier by setting e.g.
               interval_h=4 once its recipe supports cheap diffs. Due-ness is computed from the
               shared source_runs ledger, so any host (Mac tick, cloud runner) skips what another
               host just landed.
  priority   : Mac-queue order, lower first (default 50). Long aggregator sweeps run first; the
               contention-sensitive anti-bot trio last (was run_mac_queue.sh's hardcoded order).

TIME-BOUND sources (Hoodie Collect's run controls):
  window     : dict declaring that this source takes a LOOKBACK WINDOW, and the env knob that sets it:
                 {"env": "TTB_DAYS", "unit": "days", "default": 14, "all": 13000,
                  "note": "…what 'all' actually costs…"}
               Hoodie Collect renders "last 7 days / custom / all" from this and passes the value through
               run_ephemeral.py --days/--all → extra_env. A source WITHOUT a `window` REJECTS --days/--all
               rather than accepting and ignoring them: a window that is silently dropped produces a run
               labelled "all" that is really the default slice, which is the same class of lie as a silent
               cap in a "full" pull. Only declare `window` once you've confirmed the entrypoint honours it.
"""

SOURCES = [
    # ── Retail chains (headless: public APIs / feeds) ──────────────────────────────────────────────────────────
    dict(id="binnys", label="Binny's", code="import binnys_scraper as m; m.pull(crawl_all=True)",
         tables=["binnys_products"], klass="headless", cadence="daily", enabled=True, note="Algolia feed"),
    dict(id="specs", label="Spec's", code="import specs_scraper as m; m.pull(crawl_all=True)",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["specs_products"], klass="headless", cadence="daily", enabled=True, note="Next.js sitemap"),
    dict(id="meijer", label="Meijer", code="import meijer as m; m.pull()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["meijer_products"], klass="headless", cadence="daily", enabled=True,
         note="open storefront GraphQL (digital.meijer.com) — no auth/anti-bot; per-store alcohol sweep"),
    dict(id="trader-joes", label="Trader Joe's", code="import trader_joes as m; m.pull()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["trader_joes_products"], klass="headless", cadence="weekly", enabled=True,
         note="open storefront GraphQL + Brandify locator — no auth/anti-bot; SKU (no UPC), national pricing"),
    dict(id="abc-facets", label="ABC FW&S (facets)", code="import abc_facets as m; m.pull(cap=None)",
         tables=["abc_products", "source_taxonomy"], klass="headless", cadence="daily", enabled=True, note="SearchSpring"),
    # SUPERSEDED by abc-fws, which now parses the item master out of the SAME product-page fetch it
    # already makes for per-store availability. Running both meant crawling ~14k identical pages twice
    # (two ~4h sweeps, double the load on a live retailer) for data present in one response — and left
    # the item row and the store row describing states hours apart. Disabled rather than deleted; the
    # module stays until abc-fws has proven the merged output in the bench.
    dict(id="abc-catalog", label="ABC FW&S (catalog) — superseded by abc-fws",
         code="import abc_catalog as m; m.run()", enabled_note="merged into abc-fws",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["abc_catalog"], klass="headless", cadence="weekly", enabled=False,
         note="SUPERSEDED — abc-fws lands abc_catalog from the same crawl (one fetch, both layers)"),
    dict(id="abc-fws", label="ABC FW&S (inventory)", code="import abc_fws_scraper as m; m.pull(crawl_all=True)",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         # TIMEOUT: the full sweep is ~13.9k product pages paced by `polite` at ~1 req/s (ABC_MIN_INTERVAL
         # 0.6 + jitter, per-host serialized — the 12 workers do NOT multiply throughput). That is ~4h, so
         # the old 5400s default killed every run mid-crawl; the ledger shows an unbroken TIMEOUT streak.
         # 6h leaves headroom. The crawl now lands per batch and checkpoints, so even a kill keeps its work
         # and the next run resumes — the timeout is a backstop, no longer a data-loss event.
         timeout=21600, mem=8192,
         tables=["retail_observations", "abc_catalog"], klass="headless", cadence="daily", enabled=True,
         cost_class="proxy",
         note="per-store inventory → lands retail_observations (NOT abc_products, which abc-facets owns/overwrites)",
         # COVERAGE (coverage.py): item/store columns + the KNOWN universe, so a run that lands far fewer
         # SKUs/stores than this reads `partial` instead of a silent stale merge. Omit expected_* to let
         # coverage self-calibrate from the touched high-water-mark; set them when the universe is known.
         item_col="sku", store_col="store", expected_items=13900, expected_stores=133),
    dict(id="haskells", label="Haskell's (MN)", code="import haskells as m; m.run(limit=None)",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["haskells_products"], klass="headless", cadence="daily", enabled=True, timeout=10800,
         note="first-party site; full-catalog crawl outgrew the 5400s default (timed out 07-18)"),
    dict(id="total-wine", label="Total Wine",
         code="import os, total_wine_full as m; m.run(os.environ.get('TW_STORE','920'), state=os.environ.get('TW_STATE','FL'))",
         tables=["total_wine_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac",
         note="PerimeterX — browser. run() needs a storeId (national catalog, that store's price/stock); "
              "920=Orlando Millenia is the documented default, override via TW_STORE/TW_STATE. (was run() -> TypeError)"),

    # ── Grocery / big-box ─────────────────────────────────────────────────────────────────────────────────────
    dict(id="walmart", label="Walmart", code="import walmart_direct as m; m.pull(detail_pages=True, detail_cap=600)",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["walmart_products"], klass="headless", cadence="daily", enabled=True, cost_class="proxy",
         note="walmart_direct: IPRoyal residential exit + curl_cffi Chrome-JA3, $0 (no BD, no API). "
              "A warmed WALMART_COOKIE is an OPTIONAL boost, NOT required — do not gate the run on it."),
    dict(id="target", label="Target", code="import target_scraper as m; m.run()",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["target_products", "target_stores"], klass="headless", cadence="daily", enabled=True, cost_class="bd", note="RedSky API"),
    dict(id="kroger", label="Kroger (atlas inventory)", code="import kroger_atlas as m; m.main([])",
         caps=['patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["kroger_atlas_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac",
         # PREP: warm the Akamai cookie in headful Chrome before the pull (cookie_warm), so the atlas API
         # accepts the replay. requires=[] because KROGER_COOKIE is minted by the prep, not pre-set; the
         # store/facility default to a real store via `static` (extend to enumeration later).
         cookie={"host": "www.kroger.com", "env": "KROGER_COOKIE",
                 "static": {"KROGER_STORE": "01100439", "KROGER_FACILITY": "14732"}},
         note="INTERNAL atlas endpoint = exact per-store on-hand + dims + ABV; Akamai cookie AUTO-WARMED per "
              "run (cookie_warm headful Chrome — no manual paste); store 01100439/fac 14732 default"),
    # DELIBERATELY OFF — do not "fix" this by adding the creds. The public Kroger API carries NO
    # INVENTORY, which is the only reason we scrape Kroger at all (the atlas endpoint gives exact
    # per-store on-hand). Left enabled with requires=[], it sat in every triage as a permanent
    # "no-creds, just set the secrets!" prompt and got proposed as a free win more than once. It is
    # not a free win; it is a source we chose not to run. Re-enable ONLY if the UPC seed is wanted
    # for the atlas GTIN universe, and never as a substitute for inventory.
    dict(id="kroger-api", label="Kroger (API UPC seed) — OFF: no inventory",
         code="import kroger_api as m; m.main()",
         tables=["kroger_products"], klass="creds", cadence="weekly", enabled=False,
         requires=["KROGER_CLIENT_ID", "KROGER_CLIENT_SECRET"],
         note="OFF BY CHOICE — public OAuth API has NO inventory. Inventory comes from kroger (atlas). "
              "Do not enable to clear a no-creds warning."),
    dict(id="publix", label="Publix", code="import publix as m; m.run()",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["publix_products"], klass="headless", cadence="daily", enabled=True, cost_class="bd", note="weekly-ad API"),
    dict(id="stop-and-shop", label="Stop & Shop", code="import stop_and_shop as m; m.main([])",
         tables=["stop_and_shop_products"], klass="mac", cadence="daily", enabled=False, cost_class="mac", note="needs a warmed cookie — not headless"),

    # ── Aggregators / convenience (Mac headful — anti-bot) ────────────────────────────────────────────────────
    # THE UBEREATS SOURCE. Headless, list-driven, sharded — replaces the headful zone crawler that ran
    # max_stores=1000 against a 502,212-store universe (0.2%, with the cap hidden in the registry).
    # Both the catalog (getStoreV1) and the per-item UPC/detail (getMenuItemV1) answer COLD to plain
    # curl_cffi — proven live from a Fly datacenter IP — so no browser, no proxy, no Bright Data, $0.
    # One pass does BOTH layers because the item's section context is only in hand while we hold the
    # catalog; a second sweep for UPC would repeat the abc-catalog mistake.
    # Sharding is the day budget: --shard i/N splits the universe by stable hash, one ephemeral machine
    # per shard. Start at 8; the run logs the observed rate and the shard-hours the universe needs, so
    # the count is set by measurement rather than guesswork.
    # SWEEP AND ENRICH ARE DIFFERENT JOBS ON DIFFERENT CLOCKS.
    # The sweep is ONE request per store: 502,212 requests, ~30 minutes across the fleet. Enrichment is
    # one request per NEW item — measured at ~82 items/store, so inline it turns a 502k-request job into
    # a ~41.7M-request job, and it ran SERIALLY inside each store's thread (~18.5s/store, matching the
    # observed rate exactly). That is a 30-minute pull wearing a 46-hour coat.
    #
    # They are separable because they answer different questions: UPC/brand/size/ABV are STATIC per item
    # (fetch once, ever), while price and stock are volatile and come from the catalog call we already
    # make. So the sweep runs fast and complete on a daily clock, and enrichment drains the backlog of
    # genuinely-new items continuously — converging, then costing almost nothing in steady state.
    # `shards` makes the SCHEDULER dispatch the fleet too; without it an unattended run was one machine.
    # The other half of the split: drains the STATIC-attribute backlog (UPC/GTIN/brand/size/ABV) that the
    # sweep no longer carries. Sharded and append-only like the sweep. Day one is a real backfill; after
    # that only genuinely-new items cost anything, because a resolved item is never re-fetched.
    # LADDER_MAX_RUNG=impersonate: forbids ladder.py from auto-escalating this recipe to the `browser`
    # rung. Grounded in the 2026-07-29 incident ladder.py's own docstring documents: UberEats escalated
    # to `browser` on an isolated (datacenter) Fly machine — a rung only proven on a residential exit —
    # and 6+ concurrent Chromium instances also exhausted the machine's memory, causing an SSH-unresponsive
    # stall. Setting the env var here (not just on a hand-run machine) matters because ladder.current()
    # PERSISTS its rung choice in the warehouse across processes: a fresh ephemeral dispatch that never
    # sets this cap would read back a previously-persisted `browser` choice and boot straight into it,
    # silently undoing the fix the moment a normal dispatcher tick spawns a headless (non-browser-capable)
    # machine for this source. All three entries share the same cold getstore.py fetch path (getStoreV1 /
    # getMenuItemV1), so all three need the cap.
    dict(id="ubereats-enrich", label="Uber Eats item UPC/GTIN backfill (sharded)", shards=8,
         code="import os; os.environ['LADDER_MAX_RUNG']='impersonate'; import ue_enrich as m; "
              "m.main(['--site','ubereats','--shard',os.environ.get('UE_SHARD','0/8')])",
         caps=['curl_cffi'],
         tables=["ubereats_products"], klass="headless", cadence="daily",
         enabled=True, cost_class="free", timeout=21600, mem=4096, priority=11,
         note="separate clock from the sweep: static per-item attributes, fetched once ever"),
    # session_budget: requests one primed cookie may serve before re-priming. Measured ~50 on this
    # source (collapse tracked request COUNT, not time, across three runs); 40 leaves margin, and
    # sessions.py corrects it from observed burns. Session lifecycle is a per-DOMAIN property like the
    # parser and the rate policy, so it belongs in the playbook, not hard-coded in a fetcher.
    # impersonate: measured 2026-07-29 — this target blocks the desktop-Chrome TLS family specifically
    # (chrome/chrome124/chrome131 all challenged) while safari/firefox/edge/android all returned real
    # catalogs on the same IPs at the same moment. The costume is a per-domain property, like the parser.
    dict(id="ubereats", label="Uber Eats store catalog (sharded)", shards=8, session_budget=40,
         impersonate="safari17_0",
         code="import os; os.environ['LADDER_MAX_RUNG']='impersonate'; import ue_catalog as m; "
              "m.main(['--site','ubereats','--shard',os.environ.get('UE_SHARD','0/8'),'--no-enrich'])",
         caps=['curl_cffi'],
         tables=["ubereats_products", "retail_observations"], klass="headless", cadence="daily",
         enabled=True, cost_class="free", timeout=21600, mem=4096, priority=10,
         item_col="item_uuid", store_col="store_uuid",
         note="COLD getStoreV1 + getMenuItemV1 over the 502k-store sitemap universe; shardable "
              "(UE_SHARD=i/N), resumable, no caps. Headful ubereats.py archived as the zone crawler."),
    # Postmates is the SAME Uber BFF on a different domain, so it is the identical recipe — one code
    # path, not a parallel copy that can drift.
    dict(id="postmates", label="Postmates (catalog + UPC, sharded)",
         code="import os; os.environ['LADDER_MAX_RUNG']='impersonate'; import ue_catalog as m; "
              "m.main(['--site','postmates','--shard',os.environ.get('UE_SHARD','0/8')])",
         caps=['curl_cffi'],
         tables=["postmates_products", "retail_observations"], klass="headless", cadence="daily",
         enabled=True, cost_class="free", timeout=21600, mem=4096, priority=11,
         item_col="item_uuid", store_col="store_uuid",
         note="same cold Uber BFF recipe as ubereats, postmates.com domain"),
    # Deep full-detail crawl (ue_crawl.py: getStoreV1+getMenuItemV1, full per-item UPC/price/recipe) —
    # bounded to 5 major metros + capped stores/items so ONE run finishes in hours, not the multi-day
    # national sweep the crawler is capable of. NO proxy: ue_crawl.py was proven from the operator's
    # HOME residential IP; its own UE_PROXY=1 option routes through resi._session_url — the METERED
    # per-GB tier — so we never set it. RESI_ISP_ONLY=1 is belt-and-suspenders (hard-forbids per-GB
    # globally even if something downstream reached for it) — worst case this runs on the bare Fly IP
    # with zero proxy, which is exactly the open question this run is meant to answer. enabled=False:
    # manual trigger only, never joins the automatic hourly scan, until a real run proves it's not
    # degraded (near-zero merchants is the known failure signature of a flagged/foreign exit IP).
    dict(id="ubereats-full", label="Uber Eats — bounded full-detail crawl",
         code="import os; os.environ['RESI_ISP_ONLY']='1'; import ue_crawl as m; "
              "m.main(['--zones','New York, NY;Los Angeles, CA;Chicago, IL;Miami, FL;Houston, TX',"
              "'--site','ubereats','--max-stores','60','--max-items-enrich','40'])",
         tables=["ubereats_products"], klass="mac", cadence="daily", enabled=False,
         timeout=10800, mem=8192, cost_class="free",
         note="ONE bounded run (5 metros, capped stores/items), NO proxy (RESI_ISP_ONLY=1 forbids "
              "metered spend) — validates the bare Fly IP before any wider run. Manual trigger only."),
    dict(id="postmates-full", label="Postmates — bounded full-detail crawl",
         code="import os; os.environ['RESI_ISP_ONLY']='1'; import ue_crawl as m; "
              "m.main(['--zones','New York, NY;Los Angeles, CA;Chicago, IL;Miami, FL;Houston, TX',"
              "'--site','postmates','--max-stores','60','--max-items-enrich','40'])",
         tables=["postmates_products"], klass="mac", cadence="daily", enabled=False,
         timeout=10800, mem=8192, cost_class="free",
         note="Postmates twin of ubereats-full — same bounds, same $0/no-proxy posture, manual trigger only."),
    # Instacart bev-alc is gated behind a logged-in, age-verified account session — anonymous gets
    # "alcohol products aren't available." requires= keeps every tick an honest no-creds skip until
    # INSTACART_SESSION_COOKIES is set (Chris's own account, injected via instacart.py's _launch()).
    # ONE zone, a handful of alcohol terms — proving the gate lifts at all is the whole point of this
    # first run, not a sweep. enabled=False: manual trigger only.
    dict(id="instacart-bevalc", label="Instacart — bev-alc (session-gated)",
         code="import instacart as m; r = m.Instacart().pull(address='10001', retailers=['grocery'], "
              "queries=['vodka','wine','whiskey','beer','tequila'], per_query_pages=2); print(len(r))",
         tables=["instacart_products"], klass="mac", cadence="daily", enabled=False,
         requires=["INSTACART_SESSION_COOKIES"], cost_class="free",
         note="ONE zone / a few alcohol terms — proves whether a plain logged-in session lifts the "
              "anonymous alcohol gate. No proxy (free self-hosted browser, per instacart.py; the driver is "
              "patchright — the image has no playwright). Manual trigger only."),
    dict(id="sevennow", label="7-Eleven (7NOW)", code="import sevennow_warm as m; m.main()",
         caps=['patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["sevennow_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=60, note="Incapsula — patchright"),

    # ── Off-premise platforms ─────────────────────────────────────────────────────────────────────────────────
    dict(id="offprem-census", label="Off-premise census (Shopify/Woo/Wix/Sqsp)",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         code="import off_premise as m, warehouse, re;"
              "markets=sorted(set(re.sub(r'_offprem_census$','',d['name']) for d in warehouse.list_datasets() if d['name'].endswith('_offprem_census')));"
              "[m.run_census(market=x, platforms=('Shopify','WooCommerce','Wix','Squarespace')) for x in markets]",
         tables=["offprem_products"], klass="headless", cadence="daily", enabled=True, cost_class="proxy", note="22 markets, no-BD"),
    dict(id="shopify", label="Shopify (national sweep)", code="import off_premise as m; m.national_sweep('shopify')",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["national_shopify_products"], klass="headless", cadence="weekly", enabled=True,
         note="census sweep's Shopify pass — SHOPIFY_SEED via open /products.json ($0); OFFPREM_SERP=1 adds BD SERP discovery. Replaced standalone shopify_scraper (archived)"),
    dict(id="bottlecapps", label="Bottlecapps network", code="import bottlecapps as m; m.national()",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["bottlecapps_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=62, note="DataDome — patchright"),
    dict(id="cityhive", label="City Hive network", code="import cityhive as m; m.national(max_stores=100)",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["cityhive_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=61, note="Cloudflare — patchright"),
    dict(id="bbg", label="BBG e-commerce", code="import bbg_salsify as m; m.pull()",
         tables=["bbg_products"], klass="headless", cadence="daily", enabled=True, note="Salsify API"),

    # ── Distributors / state / reference ──────────────────────────────────────────────────────────────────────
    dict(id="winebow", label="Winebow (distributor)", code="import winebow as m; m.pull()",
         tables=["winebow_brands"], klass="headless", cadence="weekly", enabled=True, note="portfolio"),
    dict(id="ab-inbev", label="AB InBev locator", code="import ab_fill as m; m.run()",
         tables=["ab_outlets"], klass="headless", cadence="weekly", enabled=True, note="beertech GraphQL"),
    dict(id="ca-abc", label="California ABC", code="import ca_abc as m; m.run()",
         tables=["ca_outlets"], klass="headless", cadence="weekly", enabled=True, cost_class="proxy",
         note="WAF — spoofed browser HEADERS on stdlib urllib (NOT a headful browser); klass was wrongly 'mac' → Mac queue"),
    dict(id="control-states", label="Control states (OR/UT/NC/MT/ME/AL/BC/MontMD)", code="import control_state as m; m.build_all()",
         tables=["or_pricing", "ut_pricing", "mont_sales"], klass="headless", cadence="weekly", enabled=True, note="per-state fetchers"),
    # THREE distinct Census sources (NOT duplicates): `census` = census_ref.py's supply-side business
    # patterns (CBP/NES/PEP) + ACS/Economic-Census demand-side additions -> census_reference (tall,
    # metric-keyed); `census-acs` = census.py's separate demand-side ACS demographics by county ->
    # census_demographic/economic/housing (wide, geoid-keyed, enrich.merge_census-joinable); `census-acs5`
    # = census_ref.py's full ~1,193-table ACS5 detailed-table sweep -> census_acs (breadth-first, distinct
    # from both of the above). `census-migration` = census_ref.py's county-to-county migration flows ->
    # census_migration. Different Census API calls, different tables, different consumers — keep them
    # registered separately. (NOTE: `census-acs` was already claimed by census.py before this Census-ref
    # expansion landed, so the new full-ACS5-sweep source is `census-acs5`, not `census-acs`.)
    dict(id="census", label="US Census (CBP · Nonemp · PEP · ACS)", code="import census_ref as m; m.build()",
         tables=["census_reference"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"], note="Census API (census_ref.build) — CBP/Nonemp/PEP supply-side + ACS demand-side demographics at state/county/ZCTA grain (~33k ZIPs) + Economic Census OBSERVED receipts (dataset ecn, $1000s); free key, re-derivable"),
    dict(id="census-acs", label="US Census — ACS demographics", code="import census as m; m.build()",
         tables=["census_demographic", "census_economic", "census_housing"], klass="creds", cadence="weekly",
         enabled=True, requires=["CENSUS_API_KEY"],
         note="demand-side ACS5 by county (census.build) — population/income/housing packs, wide + geoid-keyed for enrich.merge_census outlet joins; free key, re-derivable"),
    dict(id="census-acs5", label="US Census ACS5 (all detailed tables + featured)", code="import census_ref as m; m.build_acs()",
         tables=["census_acs"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"],
         note="ALL ~1,193 ACS5 detailed tables @ state + featured bev-alc metrics (21+, income, households) @ "
              "county; ~1,193 group() calls. Full all-tables×county + tract/block-group is a partitioned/bulk follow-up"),
    dict(id="census-migration", label="US Census migration flows", code="import census_ref as m; m.build_flows()",
         tables=["census_migration"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"],
         note="ACS county-to-county flows (MOVEDIN/OUT/NET + FROMABROAD) — market-momentum signal for trade areas"),
    dict(id="cex", label="BLS Consumer Expenditure (alcohol × income)", code="import cex_ref as m; m.build(); m.build_demand()",
         tables=["cex_reference"], klass="headless", cadence="weekly", enabled=True,
         note="BLS CEX API (cex_ref.build) — mean annual alcohol $ per CU (total / at-home / away) by "
              "income-before-taxes bracket; keyless OK (BLS_API_KEY raises limits); build_demand derives "
              "trade_area_demand = CEX × ACS B19001 (needs the census source's brackets landed)"),
    dict(id="cpi", label="BLS CPI (alcoholic beverages)", code="import cpi_ref as m; m.build()",
         tables=["cpi_reference"], klass="headless", cadence="weekly", enabled=True,
         note="BLS CPI-U API (cpi_ref.build) — alcohol total/at-home/away + beer/spirits/wine sub-items, "
              "US + 4 regions, monthly + M13 annual; keyless OK; real_series() = alcohol rebased vs "
              "all-items (the deflator / price-index benchmark)"),
    dict(id="fred", label="FRED macro pulse", code="import fred_ref as m; m.build()",
         tables=["fred_reference"], klass="creds", cadence="weekly", enabled=True,
         requires=["FRED_API_KEY"],
         note="FRED API (fred_ref.build) — monthly liquor-store retail sales (MRTSSM4453USN, the national "
              "off-prem pulse), food-service sales, real disposable income, consumer sentiment"),
    dict(id="bea", label="BEA regional income", code="import bea_ref as m; m.build()",
         tables=["bea_reference"], klass="creds", cadence="weekly", enabled=True,
         requires=["BEA_API_KEY"],
         note="BEA Regional API (bea_ref.build) — state disposable income (SAINC51) + county personal "
              "income (CAINC1), annual; a fresh BEA key must be ACTIVATED via BEA's email link or the "
              "API returns in-band Error 4 (reported degraded, never silent)"),
    dict(id="tax-rates", label="Bev-alc tax RATES (TTB + state excise)", code="import tax_rates as m; m.build()",
         tables=["tax_rates"], klass="headless", cadence="weekly", enabled=True,
         note="federal CBMA schedule (encoded, TTB) + 51-jurisdiction state excise seed (Tax Foundation Jan 2026); "
              "effective-dated ref, landed_cost.py reads it — verify state cells vs DOR to promote seed->verified"),
    dict(id="tax-revenue", label="Bev-alc tax REVENUE (Census STC + TTB)", code="import tax_revenue as m; m.build()",
         tables=["tax_revenue"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"],
         note="Census govs STC (T10 alc sales tax, T20 alc license) per state — live; TTB federal commodity "
              "collections run live on the Mac (TTB TLS-blocked on Fly)"),
    dict(id="vtinfo", label="VTInfo locator", code="import vtinfo as m; m.run()",
         tables=["vtinfo_titos"], klass="headless", cadence="weekly", enabled=True,
         note="brand→retailer 'where to buy' (HTML-fragment POST, not GraphQL). m.run() LANDS it; m.pull() alone "
              "returned rows but never wrote (the never-persisted bug)"),
    # ── Distributor catalogs (open JSON APIs — one recipe per PLATFORM, keyed by distributor id/slug) ──
    dict(id="vip-brandbuilder", label="VIP Brand Builder (distributor catalogs)",
         code="import vtinfo_bbs as m; m.pull()", tables=["vip_brandbuilder_items"],
         klass="headless", cadence="weekly", enabled=True,
         note="products.vtinfo.com/bbs — distributor product+package catalog w/ retail UPCs, no auth; "
              "parameterized by VIP sourceCode (Columbia 01191 seed). Add distributors to DISTRIBUTORS."),
    dict(id="sevenfifty", label="SevenFifty storefronts (distributor catalogs)",
         code="import sevenfifty as m; m.pull()", tables=["sevenfifty_items"],
         klass="headless", cadence="weekly", enabled=True,
         note="<slug>.storefronts.site/search.json — distributor item master (SKUs), no auth (prices need "
              "partner login); parameterized by storefront slug (johnsonbrothers seed). Add slugs to STOREFRONTS."),
    # The custID keyspace is 3 alnum chars (case-insensitive) = 46,656 — enumerable, so the VIP tenant
    # directory is a census, not a hand-harvested dict. Resumable + checkpointed: each run takes a
    # deadline-bounded bite and the next resumes. cost_class=proxy — one IP 429s in seconds, so it
    # needs the ISP pool (it REFUSES to run direct without --allow-direct).
    dict(id="vip-finder-census", label="VIP finder tenant census",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         code="import vip_finder_census as m; m.pull(argv=['--deadline', '3000'])",
         tables=["vip_finder_tenants", "vip_finder_brands"], klass="headless", cadence="weekly",
         enabled=True, cost_class="proxy", timeout=3600,
         note="enumerates custID 36^3 through the ISP pool; each run takes a 50min resumable bite "
              "(checkpoint in the warehouse) until the keyspace is walked. Pacing is adaptive — 1s/IP, "
              "doubling on 429 — so it self-throttles; --calibrate only makes it faster"),
    dict(id="doordash-sitemap", label="DoorDash store universe", code="import doordash_sitemap as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["doordash_stores"], klass="headless", cadence="weekly", enabled=True, timeout=7200,
         note="$0 national store spine from DoorDash's own sitemaps (curl_cffi+ISP); feeds naop + retail"),
    dict(id="ubereats-sitemap", label="UberEats store universe",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         code="import ue_sitemap as m; m.pull('ubereats'); m.sitemap_to_src_outlets('ubereats')",
         tables=["ubereats_sitemap", "src_outlets"], klass="headless", cadence="weekly", enabled=True,
         timeout=10800, mem=8192,
         note="$0 US UberEats universe from its gzipped sitemaps (~285k) → src_outlets (the coverage book). "
              "Canonical UberEats harvester (ubereats_sitemap.py archived). accumulate into 995k src_outlets → 8gb"),
    dict(id="postmates-sitemap", label="Postmates store universe",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         code="import ue_sitemap as m; m.pull('postmates'); m.sitemap_to_src_outlets('postmates')",
         tables=["postmates_sitemap", "src_outlets"], klass="headless", cadence="weekly", enabled=True,
         timeout=10800, mem=8192, note="$0 US Postmates universe from its sitemaps → src_outlets (coverage book)"),
    dict(id="geocode", label="Geocode (Census, $0)", code="import geocode as m; m.run()",
         tables=["src_outlets"], klass="headless", cadence="daily", enabled=False, timeout=5400, mem=16384,
         note="automate lat/lng: free US Census batch-geocodes addressed-but-ungeocoded src_outlets → maps on "
              "the Coverage page; unmatched marked county_fips=00000 so they aren't retried. GEOCODE_LIMIT/run"),
    # TEXAS BY GEOGRAPHY — the only route to a state DoorDash's own feed will not serve.
    # sitemap-doordash-tx-stores.xml returns a 270-byte stub with ONE store (California's carries
    # 103,811) and there is no alternate URL: tx-1 / texas / .gz / hou / dal all 403. So Texas cannot
    # be harvested from the sitemap at all, and the grid sweep is what replaces it. Ported off Bright
    # Data (#687): local Chromium + standard CDP geolocation + the flat ISP pool, $0.
    # klass="mac" for the 8GB headful guest — run_ephemeral.sh provides Xvfb and system Chrome.
    # enabled=False: hand-kicked until a full Texas run is proven, so it cannot burn a nightly slot
    # on an unvalidated sweep.
    dict(id="doordash-geo-tx", label="DoorDash geo sweep — Texas",
         code="import doordash_geo as m; m.run_texas()",
         caps=['patchright'],
         tables=["doordash_stores"], klass="mac", cadence="weekly", enabled=False,
         timeout=21600, mem=8192, cost_class="free",
         note="240-point lattice across Houston/Dallas/Fort Worth/Austin/San Antonio at 0.07 deg "
              "(~4-5 mi, inside a delivery radius so the grid has no holes). Replaces the broken TX "
              "sitemap; a pin where every search term fails now RAISES rather than reporting an "
              "empty market."),
    dict(id="aggregator-geo", label="Aggregator geo (page-fetch)", code="import aggregator_geo as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         # SHARDED AND LIVE. The write is now shard-safe: each shard write_partitions its own parquet
         # part (disjoint paths, no read-modify-write), and geo_all folds the whole stage into
         # src_outlets in ONE serialized write_accumulate. Shards never merge — that would recreate
         # the concurrent rewrite this staging exists to prevent.
         # Measured ~450 rows/min at 64 workers => a ~770k backlog is ~28h of fetching, which does not
         # fit one window, so the read is sharded 6 ways by stable hash (UE_SHARD=i/N, verified a total
         # partition in agg_shard_test.py). Merge is last-write-wins by staged_at, so a re-run cannot
         # re-apply an old staged row over a newer exact geo.
         shards=6,
         # ENABLED as its own dispatched FLEET (6 shards). geo_all no longer runs the aggregator
         # in-process — it would duplicate this work on a 7th machine — it only merges the stage.
         tables=["src_outlets"], klass="headless", cadence="daily", enabled=True, timeout=7200, mem=16384,
         note="$0 page-fetch PRECISE geo for the ~790k no-address ubereats/postmates outlets (schema.org "
              "lat/lng → geo_precision=exact; empty pages marked agg_miss). Big crawl — chips away, "
              "AGG_GEO_LIMIT/run. (doordash is mapped by the city-centroid fast layer, not here.)"),
    dict(id="city-centroid-build", label="City centroids (Census Gazetteer)",
         code="import city_centroid as m; m.build_reference()",
         tables=["city_centroids"], klass="headless", cadence="monthly", enabled=True, timeout=1800, mem=2048,
         note="build the $0 Census Gazetteer place/township centroid reference (state|city → lat/lng) the fast "
              "geo layer joins against. Refresh yearly; static otherwise"),
    dict(id="fast-geo", label="Fast geo (city centroid, $0)", code="import city_centroid as m; m.run()",
         tables=["src_outlets"], klass="headless", cadence="daily", enabled=False, timeout=5400, mem=16384,
         note="THE FAST LAYER: instantly city-centroid every un-geocoded src_outlet that ships a city+state "
              "(DoorDash: all 587k) → geo_precision=city, maps on Coverage immediately; the exact crawl upgrades "
              "city→exact. No fetch. FAST_GEO_LIMIT/run"),
    dict(id="geo", label="Geo pipeline (all layers)", code="import geo_all as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["src_outlets"], klass="headless", cadence="daily", enabled=True, timeout=21600, mem=16384,
         # 3h was not enough to walk the ~770k aggregator backlog, and aggregator_geo wrote only at the
         # very end — so the kill discarded the entire run's fetches, every day, and the backlog never
         # moved. The write is now chunked (durable per 40k), and this gives one pass room to finish.
         note="THE daily geo run: fast-geo → geocode → aggregator-geo IN SEQUENCE on one machine. They each "
              "rewrite the whole src_outlets table, so running them concurrently would clobber each other — this "
              "serializes them. The three stay registered (enabled=False) for targeted manual backfills."),
    dict(id="naop", label="NAOP on-premise", code="import doordash_naop as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["naop_accounts", "naop_beverages"], klass="headless", cadence="daily", enabled=True, timeout=7200,
         note="DoorDash on-premise menus, $0 (ISP pool); consumes doordash_stores in NAOP_LIMIT batches"),
    dict(id="doordash-full", label="DoorDash retail — full catalog (national, all beverage alcohol)",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         code="import doordash_chains as m; m.run()",
         tables=["doordash_full_runs", "doordash_products_full", "doordash_outlets_full"],
         klass="headless", cadence="daily", enabled=True, timeout=14400,
         cost_class="free",
         note="RESUMABLE national sweep of the FULL doordash_stores sitemap universe (767k+) via "
              "doordash_full.py's category-tree walk — NO curated chain list (a prior version matched "
              "only ~15 hand-picked banners against ~25k of the 767k stores; removed as a self-imposed "
              "scope limit, not a real constraint — the sitemap carries no chain/vertical field, so a "
              "non-retail store just costs one wasted fetch before the tree walk empties out). Lands one "
              "unified doordash_products_full/doordash_outlets_full table with per-store real-name "
              "attribution, not a per-chain table. shard/nshard partitions the remaining stores for "
              "running multiple machines concurrently at this scale. DDFULL_BATCH caps stores per run "
              "(accumulate-merged, never overwrites a prior batch, covered check unions the new table "
              "with every legacy per-chain table so nothing already scraped gets redone) — no permanent "
              "cap, no silent coverage gap. $0 flat ISP pool (Bright Data retired for DoorDash 2026-07-24)"),
    dict(id="toast", label="Toast own-menus", code="import toast as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["toast_outlets", "toast_beverages", "toast_menu_accounts"], klass="headless", cadence="daily",
         enabled=True, timeout=7200,
         note="$0 restaurant OWN menus from toasttab.com sitemaps (~100k); harvest + TOAST_LIMIT menu batches"),
    dict(id="outlet-union", label="Outlet pre-master", code="import outlet_union as m; m.run()",
         tables=["outlet_master"], klass="headless", cadence="daily", enabled=True, mem=8192,
         note="derived ($0): unions DoorDash/Toast outlet spines → mastered outlets + per-source menu freshness. "
              "mem was unset (defaults to 4096) — undersized for a national union-find that materializes "
              "doordash_stores + toast_outlets + toast_menu_accounts + naop_accounts fully in Python before "
              "resolving; OOM-killed 2026-07-29. Root cause is the join shape (no fat column to trim — the "
              "account tables are already lean), so this is a right-size, not a workaround; escalate to 16384 "
              "(geo's ceiling for a comparable-scale join) if this still OOMs."),
    # WINDOW verified against ttb_pull.pull(): `days` defaults to int(os.environ["TTB_DAYS"] or 14) and is
    # applied as (today - days) → today, chunked a day at a time with --resume. `all` = ~36y back, which
    # covers the public COLA registry to its start; it is a multi-day resumable crawl, not a click-and-wait.
    dict(id="ttb-cola", label="TTB COLA scrape", code="import ttb_pull as m; m.run()",
         window={"env": "TTB_DAYS", "unit": "days", "default": 14, "all": 13000,
                 "note": "'all' walks the registry back to ~1990 one day-chunk at a time (resumable). "
                         "Expect days of wall-clock, not minutes."},
         caps=['bs4', 'pillow', 'pylibdmtx', 'pytesseract', 'pyzbar'],   # optional libs this source silently degrades without (capability.py)
         tables=["ttb_cola"], klass="headless", cadence="weekly", enabled=True, timeout=5400,
         note="$0 off-Mac incremental COLA scrape (last TTB_DAYS) → accumulate ttb_cola; ttbonline.gov verify=False, direct (no BD/browser)"),
    dict(id="ttb-enrich", label="TTB COLA enrich (detail+UPC)", code="import ttb_pull as m; m.run_enrich()",
         caps=['bs4', 'pillow', 'pylibdmtx', 'pytesseract', 'pyzbar'],   # optional libs this source silently degrades without (capability.py)
         tables=["ttb_cola_detail", "ttb_cola_labels"], klass="headless", cadence="daily", enabled=True,
         timeout=7200, mem=8192,   # accumulate merges the 1.86M-row ttb_cola_detail → needs >4GB headroom
         note="$0 off-Mac producer that EXTENDS the existing ttb_cola_detail + ttb_cola_labels (accumulate by "
              "ttb_id, snake_case schemas via ttb_enrich's validated parsers) for COLAs not yet detailed — new "
              "COLAs from ttb-cola get detail + label-barcode UPC off-Mac. Gentle concurrency on the .gov site "
              "(TTB_ENRICH_WORKERS=4); needs libzbar0+pyzbar+pillow (in the image)"),
    dict(id="ttb", label="TTB COLA master build", code="import master_ttb as m; m.run()",
         tables=["ttb_master"], klass="headless", cadence="weekly", enabled=False,
         note="MASTER BUILD (reads ttb_cola → ttb_master); huge — refresh deliberately. Scrape is ttb-cola"),

    # ── Hemp ──────────────────────────────────────────────────────────────────────────────────────────────────
    dict(id="hemp-scan", label="Hemp products", code="import hemp_scan as m; m.main([])",
         tables=["hemp_products"], klass="headless", cadence="daily", enabled=True, note="hemp-bev feed"),
    dict(id="hemp-finder", label="Hemp retailers", code="import hemp_finder as m, vtinfo; m.run(brands=vtinfo.HEMP_BRANDS)",
         tables=["hemp_retailers"], klass="headless", cadence="weekly", enabled=True,
         note="retailer discovery — ALL 5 hemp brands (cann/wynk/trail-magic/uncle-arnies/crescent-9); run() alone was cann-only"),
    dict(id="hemp-inventory", label="Hemp per-store inventory", code="import hemp_inventory as m; m.main([])",
         tables=["hemp_inventory"], klass="headless", cadence="daily", enabled=False,
         note="PARKED (2026-07): its base universe was starved — it read a phantom orlando_hemp_products table "
              "(now removed) + only the incidental Shopify subset of offprem_products; most rows had no count "
              "(oversell). Hemp is covered by hemp-finder (retailers) + hemp-scan (listings). Re-enable once "
              "pointed at a real Shopify hemp-store universe with a platform filter"),
]


# ── Derived master builds (NRT-PLAN.md Phase 3) ───────────────────────────────────────────────────────────────
# Not scrapes: these consolidate LANDED source tables into the master, so they belong to the dispatcher, not a
# human. The --due pass runs a build when any upstream source has landed NEW rows (status "ok") since the
# build's last attempt, throttled by interval_h (the min gap between rebuilds). Same subprocess + verify-landing
# + source_runs ledger treatment as sources. `after=[source ids]` narrows the trigger; omitted = any source.
# Builds run ONLY on the plain `--due` host (the Mac tick today) — the --headless-only cloud runner skips them —
# so the single-writer rule holds for dim_* tables.
BUILDS = [
    # THE SHARDS CANNOT MERGE. write_accumulate is read-modify-write with no lock, so eight concurrent
    # shards silently drop each other's rows (seen live: ubereats_products fluctuating wildly while the
    # fleet ran). Shards therefore append parts, and this is the SINGLE writer that folds them into the
    # canonical catalog — latest observation per (store_uuid, item_uuid) wins. Without it the parts
    # accumulate and the catalog never updates, so it is a registered build, not a manual step.
    dict(id="build-ue-catalog", label="UberEats catalog consolidate (shard parts → catalog)",
         # NOTHING-TO-DO IS NOT A FAILURE. Folding ubereats' 451,821 part rows succeeded while postmates
         # simply had no parts yet, and the build reported `incomplete` — a green job reading as broken
         # teaches you to ignore the colour, which is the same trust defect as a broken job reading green.
         # Report the total folded so the run is graded on what it actually did.
         code=("import ue_catalog as m; n = m.consolidate('ubereats') + m.consolidate('postmates'); "
               "print('HOODIE_RESULT {\"status\": \"ok\", \"items_done\": %d, \"items_total\": %d}' % (n, n))"),
         tables=["ubereats_products", "postmates_products"], klass="build", interval_h=6, enabled=True,
         mem=8192, after=["ubereats"],
         note="single-writer fold of append-only shard parts; shards must never merge (lost updates)"),
    dict(id="build-outlets", label="Outlet shred → dim_outlet",
         code="import normalize as m; m.build(catalog=False, outlets=True, facts=False)",
         tables=["src_outlets", "dim_outlet"], klass="build", interval_h=6, enabled=True, mem=16384,
         note="src_outlets re-shred + cross-source geo-match consolidation (1.76M-row whole-table merge peaks >8GB)"),
    dict(id="build-product-master", label="Product master (dim_sku chain)",
         code="import build_product_master as m; m.build()",
         tables=["dim_sku"], klass="build", interval_h=12, enabled=True, mem=8192,
         note="brand dict → stage → shred to dim_brand/product/item/sku + xwalk/coherence/identity clusters"),
    # MOAT-PLAN Workstream M — PROVE the master. Deterministic gold set (same-UPC positives / cross-
    # brand negatives) scored against the master's item_key decision → P/R/F1 + a regression baseline.
    # Reruns after the master rebuilds so quality is MEASURED every cycle, not asserted.
    dict(id="build-master-quality", label="Master quality (P/R vs gold)",
         code="import master_quality as m; m.build()",
         tables=["master_quality"], klass="build", interval_h=24, enabled=True,
         after=["build-product-master"],
         note="deterministic UPC/brand gold → precision/recall/F1 of item_key merges + regression flag"),
    # S4 convergence (MATCHING-CONVERGENCE.md) — the SERVED canon identity, computed IN-WAREHOUSE. The prod
    # head-to-head proved the recall lift is UPC-deterministic (canon R=1.000 vs item_key 0.269 on the UPC
    # gold), so item_identity = a group-by-UPC over the full mapped universe — no local Postgres, no canon
    # cascade. canon_item_id = the UPC as bigint; two sources on one UPC → one identity (the lift), leading-zero
    # variants collapse. The serving overlay (canon_identity.py) COALESCEs it onto item_key; UPC-less SKUs
    # (absent here) keep item_key. Cross-UPC / no-UPC / fuzzy identity is hoodie-canon's cascade — a cloud
    # engine for later; this owns item_identity for the deterministic core (single writer).
    dict(id="build-item-identity", label="Item identity (served, in-warehouse UPC)",
         code="import build_item_identity as m; m.build()",
         tables=["item_identity"], klass="build", interval_h=12, enabled=True,
         after=["build-product-master"],
         note="distinct-UPC → canon_item_id over _stage_product+retail; the served identity the overlay joins"),
    # …then score that SERVED canon identity against item_key on the SAME gold the item_key run built, so the
    # head-to-head that justifies the cutover is MEASURED in-app every cycle. Additive: lands master_quality_canon,
    # never touches the item_key ratchet. after build-master-quality (gold) + build-item-identity (fresh identity).
    # The Overlay's Tier-3 match spine (OVERLAY-DESIGN §6). Uploaded files are DISTRIBUTOR-shaped —
    # their own item numbers, not always a retail UPC — so `dist_item_code → canon_item_id` is the
    # tier that lands hardest with a distributor buyer ("we matched on your own item numbers").
    # Reads the landed distributor catalogs (VIP Brand Builder, Salsify tenants); adding a
    # distributor is a scrape upstream, not a change here. after build-item-identity so the canon
    # ids it resolves against are the fresh ones.
    dict(id="build-dist-xwalk", label="Distributor item crosswalk (Overlay Tier-3 spine)",
         code="import dist_xwalk as m; m.build()",
         tables=["dist_item_xwalk"], klass="build", interval_h=24, enabled=True,
         after=["build-item-identity"],
         note="dist_item_code|retail_upc → canon_item_id from vip_brandbuilder_items + bbg_products"),
    dict(id="build-master-quality-canon", label="Master quality — served canon identity (head-to-head)",
         code="import master_quality as m; m.score_canon()",
         tables=["master_quality_canon"], klass="build", interval_h=24, enabled=True,
         after=["build-master-quality", "build-item-identity"],
         note="canon_item_id vs item_key on the same gold → the served-identity P/R lift, measured every cycle"),
    # MOAT-PLAN Workstream R — representativeness. Coverage per state (observed outlets ÷ outlet_master
    # universe) + market metrics in OBSERVED (deterministic) vs PROJECTED (survey estimator + CI,
    # suppressed below a coverage/obs floor). Turns the observation engine into a market-truth engine —
    # honestly (today IL coverage 0.18% → all cells OBSERVED-only; projection self-activates as coverage grows).
    dict(id="build-representativeness", label="Representativeness (coverage + projection)",
         code="import representativeness as m; m.build()",
         tables=["coverage_cells", "market_projection"], klass="build", interval_h=24, enabled=True,
         after=["build-velocity"],
         note="state coverage + OBSERVED vs PROJECTED brand velocity w/ CIs; suppress below the floor"),
    # MOAT-PLAN Workstream V1 — the observation-quality error model over retail_observations. The
    # instrument card every velocity number will cite: per-source qty semantics (real count vs
    # status-bucket-in-disguise), cadence, diffability; per-cell jitter fingerprint. Cheap (~2min),
    # rebuilt as observations accumulate. Substrate for V2 (delta decomposition) + fact_velocity.
    dict(id="build-obs-quality", label="Observation quality (velocity substrate)",
         code="import obs_quality as m; m.build()",
         tables=["obs_quality_source", "obs_quality_cell"], klass="build", interval_h=12, enabled=True,
         note="per-source instrument card + per-(store,sku) cell quality/jitter over retail_observations"),
    # MOAT-PLAN Workstream V2/V3 — delta decomposition → implied sell-through. Reads obs_quality (the
    # jitter fingerprint + tier) and the retail_observations time-series; classifies each observation
    # pair SALE/RESTOCK/OOS/CENSORED/NOISE and lands fact_velocity (source×store×sku×week, +confidence)
    # plus the dimension-bounded brand×week serving mart. The signal SipSource/Nielsen can't produce.
    dict(id="build-velocity", label="Velocity (implied sell-through)",
         code="import velocity as m; m.build()",
         tables=["fact_velocity", "mart_velocity_brand_week"], klass="build", interval_h=12, enabled=True,
         after=["build-obs-quality"],
         note="inventory deltas → SALE units w/ noise-damp + confidence; count-tier sources only; brand×week mart"),
    # MOAT-PLAN Workstream V4 — prove velocity is RIGHT. Internal CONSERVATION (implied sales vs restock,
    # runs now) + external MAPE vs ground-truth actuals (Montgomery MD sales; Iowa when landed) — the
    # latter reports coverage honestly (0 today: velocity is IL, anchors are MD; pending an overlap).
    dict(id="build-velocity-calibrate", label="Velocity calibration (conservation + MAPE)",
         code="import velocity_calibrate as m; m.build()",
         tables=["velocity_calibration"], klass="build", interval_h=24, enabled=True,
         after=["build-velocity"],
         note="conservation ratio (sales≈restock) live; external MAPE pending an overlapping footprint"),
    # MOAT-PLAN Workstream V5 — the salable output. signal_movers (brand accel/decel, MATCHED-CELL
    # week-over-week — coverage-proof; a partial start/current week is flagged, never shown as a fake
    # trend) + signal_voids (OOS distribution opportunities → estimated recoverable units, positive
    # framing). Voids ship now; movers self-activate once two full observation weeks exist.
    dict(id="build-velocity-signals", label="Velocity signals (movers + voids)",
         code="import velocity_signals as m; m.build()",
         tables=["signal_movers", "signal_voids"], klass="build", interval_h=12, enabled=True,
         after=["build-velocity"],
         note="matched-cell WoW movers (partial-week flagged) + OOS void opportunities w/ recoverable units"),
    # Phase 4 — SipSource depletion feed (NRT-PLAN §1d): the ~500M-row raw grain (sip_raw, hive-
    # partitioned period=YYYYMM/market=XX) is NEVER served; this rolls it into small dimension-BOUNDED
    # marts the site reads. Wired but DISABLED until the real feed lands (no `sipsource-feed` source yet
    # — the feed is a delivered file, landed by sipsource_ingest.land(); sipsource_sim proves the shape).
    # Flip enabled=True the day the feed arrives; `after` makes it rebuild only when a new drop lands.
    dict(id="build-sipsource-marts", label="SipSource depletion marts",
         code="import sipsource_ingest as m; m.build_marts('sip_raw')",
         tables=["mart_sip_brand_market_month"], klass="build", interval_h=24, enabled=False,
         after=["sipsource-feed"],
         note="raw 500M sip_raw → brand×market×month + supplier×cat + category marts (bounded, +YoY)"),
    # The Snowflake morning drop (snowflake/README.md) — change-aware COPY of the warehouse into the
    # UNIFYD database: RAW = one table per source, MASTER = the src_<grain> feeds + the dim_* star.
    # First run against an empty account = the full seed; every run after touches only tables whose
    # Parquet actually moved (load ledger at _snowflake/load_state.json). Deliberately NO `after`:
    # due_builds' default upstream set (any enabled source landing new rows) triggers it, because
    # pinning it to the dim builds could starve it on a delta=0 ("current") rebuild — and an "early"
    # run is cheap, unmoved tables are skipped in SQL. requires= keeps ticks an honest no-creds skip
    # until the SNOWFLAKE_* Fly secrets are set. timeout covers the seed (INFER_SCHEMA over ~200 RAW
    # tables + an ~80M-row COPY on an XSMALL warehouse); mem is small — Snowflake does the compute,
    # the machine only regenerates SQL (Parquet footer reads) and drives the connector.
    dict(id="snowflake-load", label="Snowflake morning drop (RAW + MASTER mirror)",
         code="import snowflake_load as m; m.run()",
         tables=["snowflake_load_runs"], klass="build", interval_h=24, enabled=True,
         mem=2048, timeout=14400,
         requires=["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER"],
         note="change-aware COPY into UNIFYD (RAW per-source + src_ grains + star); verify-lands "
              "snowflake_load_runs; needs SNOWFLAKE_ACCOUNT/USER + key or password as Fly secrets"),
]


def by_id(sid):
    return next((s for s in SOURCES if s["id"] == sid), None)


def enabled(klass=None, cadence=None):
    out = [s for s in SOURCES if s.get("enabled")]
    if klass:
        out = [s for s in out if s["klass"] == klass]
    if cadence:
        out = [s for s in out if s.get("cadence") == cadence]
    return out


if __name__ == "__main__":
    print("%d sources (%d enabled)" % (len(SOURCES), len(enabled())))
    for k in ("headless", "creds", "mac"):
        ss = [s for s in SOURCES if s["klass"] == k]
        print("\n%s (%d):" % (k.upper(), len(ss)))
        for s in ss:
            print("  %-16s %-32s -> %s%s" % (
                s["id"], s["label"], ",".join(s["tables"]), "" if s.get("enabled") else "  [disabled]"))
