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
    dict(id="abc-catalog", label="ABC FW&S (catalog)", code="import abc_catalog as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["abc_catalog"], klass="headless", cadence="weekly", enabled=True, note="BigCommerce sitemap"),
    dict(id="abc-fws", label="ABC FW&S (inventory)", code="import abc_fws_scraper as m; m.pull(crawl_all=True)",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["retail_observations"], klass="headless", cadence="daily", enabled=True, cost_class="proxy",
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
    dict(id="kroger-api", label="Kroger (API UPC seed)", code="import kroger_api as m; m.main()",
         tables=["kroger_products"], klass="creds", cadence="weekly", enabled=True,
         requires=["KROGER_CLIENT_ID", "KROGER_CLIENT_SECRET"],
         note="thin public OAuth API — product/UPC seed that feeds the atlas GTIN universe (NOT real inventory)"),
    dict(id="publix", label="Publix", code="import publix as m; m.run()",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["publix_products"], klass="headless", cadence="daily", enabled=True, cost_class="bd", note="weekly-ad API"),
    dict(id="stop-and-shop", label="Stop & Shop", code="import stop_and_shop as m; m.main([])",
         tables=["stop_and_shop_products"], klass="mac", cadence="daily", enabled=False, cost_class="mac", note="needs a warmed cookie — not headless"),

    # ── Aggregators / convenience (Mac headful — anti-bot) ────────────────────────────────────────────────────
    dict(id="ubereats", label="Uber Eats", code="import ubereats as m; m.main(['--site','ubereats','--max-stores','1000'])",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["ubereats_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=10, note="Uber BFF, all stores"),
    dict(id="postmates", label="Postmates", code="import ubereats as m; m.main(['--site','postmates','--max-stores','1000'])",
         caps=['curl_cffi', 'patchright'],   # optional libs this source silently degrades without (capability.py)
         tables=["postmates_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=11, note="Uber BFF, all stores"),
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
              "anonymous alcohol gate. No proxy (free Playwright, per instacart.py). Manual trigger only."),
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
    # TWO distinct Census sources (NOT duplicates): `census` = census_ref.py's supply-side business
    # patterns (CBP/NES/PEP) + ACS/Economic-Census demand-side additions -> census_reference (tall,
    # metric-keyed); `census-acs` = census.py's separate demand-side ACS demographics by county ->
    # census_demographic/economic/housing (wide, geoid-keyed, enrich.merge_census-joinable). Different
    # Census API calls, different tables, different consumers — keep them registered separately.
    dict(id="census", label="US Census (CBP · Nonemp · PEP · ACS)", code="import census_ref as m; m.build()",
         tables=["census_reference"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"], note="Census API (census_ref.build) — CBP/Nonemp/PEP supply-side + ACS demand-side demographics at state/county/ZCTA grain (~33k ZIPs) + Economic Census OBSERVED receipts (dataset ecn, $1000s); free key, re-derivable"),
    dict(id="census-acs", label="US Census — ACS demographics", code="import census as m; m.build()",
         tables=["census_demographic", "census_economic", "census_housing"], klass="creds", cadence="weekly",
         enabled=True, requires=["CENSUS_API_KEY"],
         note="demand-side ACS5 by county (census.build) — population/income/housing packs, wide + geoid-keyed for enrich.merge_census outlet joins; free key, re-derivable"),
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
    dict(id="aggregator-geo", label="Aggregator geo (page-fetch)", code="import aggregator_geo as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["src_outlets"], klass="headless", cadence="daily", enabled=False, timeout=7200, mem=16384,
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
         tables=["src_outlets"], klass="headless", cadence="daily", enabled=True, timeout=10800, mem=16384,
         note="THE daily geo run: fast-geo → geocode → aggregator-geo IN SEQUENCE on one machine. They each "
              "rewrite the whole src_outlets table, so running them concurrently would clobber each other — this "
              "serializes them. The three stay registered (enabled=False) for targeted manual backfills."),
    dict(id="naop", label="NAOP on-premise", code="import doordash_naop as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["naop_accounts", "naop_beverages"], klass="headless", cadence="daily", enabled=True, timeout=7200,
         note="DoorDash on-premise menus, $0 (ISP pool); consumes doordash_stores in NAOP_LIMIT batches"),
    dict(id="doordash-full", label="DoorDash retail — full catalog (chain-attributed, national)",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         code="import doordash_chains as m; m.run()",
         tables=["doordash_full_runs"], klass="headless", cadence="daily", enabled=True, timeout=14400,
         cost_class="free",
         note="RESUMABLE national sweep of a curated major-chain list via doordash_full.py's category-tree "
              "walk (doordash_chains.py buckets doordash_stores by chain-name heuristic, same pattern as "
              "naop's _RETAIL_CHAINS, inverted). Each run advances every chain toward full coverage in "
              "DDFULL_BATCH_PER_CHAIN batches (accumulate-merged, never overwrites a prior batch) and lands "
              "matched/covered/remaining every time — no permanent cap, no silent coverage gap. $0 flat ISP "
              "pool (Bright Data retired for DoorDash 2026-07-24)"),
    dict(id="toast", label="Toast own-menus", code="import toast as m; m.run()",
         caps=['curl_cffi'],   # optional libs this source silently degrades without (capability.py)
         tables=["toast_outlets", "toast_beverages", "toast_menu_accounts"], klass="headless", cadence="daily",
         enabled=True, timeout=7200,
         note="$0 restaurant OWN menus from toasttab.com sitemaps (~100k); harvest + TOAST_LIMIT menu batches"),
    dict(id="outlet-union", label="Outlet pre-master", code="import outlet_union as m; m.run()",
         tables=["outlet_master"], klass="headless", cadence="daily", enabled=True,
         note="derived ($0): unions DoorDash/Toast outlet spines → mastered outlets + per-source menu freshness"),
    dict(id="ttb-cola", label="TTB COLA scrape", code="import ttb_pull as m; m.run()",
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
