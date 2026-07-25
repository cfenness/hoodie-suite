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
         tables=["specs_products"], klass="headless", cadence="daily", enabled=True, note="Next.js sitemap"),
    dict(id="meijer", label="Meijer", code="import meijer as m; m.pull()",
         tables=["meijer_products"], klass="headless", cadence="daily", enabled=True,
         note="open storefront GraphQL (digital.meijer.com) — no auth/anti-bot; per-store alcohol sweep"),
    dict(id="trader-joes", label="Trader Joe's", code="import trader_joes as m; m.pull()",
         tables=["trader_joes_products"], klass="headless", cadence="weekly", enabled=True,
         note="open storefront GraphQL + Brandify locator — no auth/anti-bot; SKU (no UPC), national pricing"),
    dict(id="abc-facets", label="ABC FW&S (facets)", code="import abc_facets as m; m.pull(cap=None)",
         tables=["abc_products", "source_taxonomy"], klass="headless", cadence="daily", enabled=True, note="SearchSpring"),
    dict(id="abc-catalog", label="ABC FW&S (catalog)", code="import abc_catalog as m; m.run()",
         tables=["abc_catalog"], klass="headless", cadence="weekly", enabled=True, note="BigCommerce sitemap"),
    dict(id="abc-fws", label="ABC FW&S (inventory)", code="import abc_fws_scraper as m; m.pull(crawl_all=True)",
         tables=["abc_products"], klass="headless", cadence="daily", enabled=True, cost_class="proxy",
         note="per-store inventory",
         # COVERAGE (coverage.py): item/store columns + the KNOWN universe, so a run that lands far fewer
         # SKUs/stores than this reads `partial` instead of a silent stale merge. Omit expected_* to let
         # coverage self-calibrate from the touched high-water-mark; set them when the universe is known.
         item_col="sku", store_col="store", expected_items=13900, expected_stores=133),
    dict(id="haskells", label="Haskell's (MN)", code="import haskells as m; m.run(limit=None)",
         tables=["haskells_products"], klass="headless", cadence="daily", enabled=True, timeout=10800,
         note="first-party site; full-catalog crawl outgrew the 5400s default (timed out 07-18)"),
    dict(id="total-wine", label="Total Wine",
         code="import os, total_wine_full as m; m.run(os.environ.get('TW_STORE','920'), state=os.environ.get('TW_STATE','FL'))",
         tables=["total_wine_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac",
         note="PerimeterX — browser. run() needs a storeId (national catalog, that store's price/stock); "
              "920=Orlando Millenia is the documented default, override via TW_STORE/TW_STATE. (was run() -> TypeError)"),

    # ── Grocery / big-box ─────────────────────────────────────────────────────────────────────────────────────
    dict(id="walmart", label="Walmart", code="import walmart_direct as m; m.pull(detail_pages=True, detail_cap=600)",
         tables=["walmart_products"], klass="headless", cadence="daily", enabled=True, cost_class="proxy",
         note="walmart_direct: IPRoyal residential exit + curl_cffi Chrome-JA3, $0 (no BD, no API). "
              "A warmed WALMART_COOKIE is an OPTIONAL boost, NOT required — do not gate the run on it."),
    dict(id="target", label="Target", code="import target_scraper as m; m.run()",
         tables=["target_products", "target_stores"], klass="headless", cadence="daily", enabled=True, cost_class="bd", note="RedSky API"),
    dict(id="kroger", label="Kroger (atlas inventory)", code="import kroger_atlas as m; m.main([])",
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
         tables=["publix_products"], klass="headless", cadence="daily", enabled=True, cost_class="bd", note="weekly-ad API"),
    dict(id="stop-and-shop", label="Stop & Shop", code="import stop_and_shop as m; m.main([])",
         tables=["stop_and_shop_products"], klass="mac", cadence="daily", enabled=False, cost_class="mac", note="needs a warmed cookie — not headless"),

    # ── Aggregators / convenience (Mac headful — anti-bot) ────────────────────────────────────────────────────
    dict(id="ubereats", label="Uber Eats", code="import ubereats as m; m.main(['--site','ubereats','--max-stores','1000'])",
         tables=["ubereats_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=10, note="Uber BFF, all stores"),
    dict(id="postmates", label="Postmates", code="import ubereats as m; m.main(['--site','postmates','--max-stores','1000'])",
         tables=["postmates_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=11, note="Uber BFF, all stores"),
    dict(id="sevennow", label="7-Eleven (7NOW)", code="import sevennow_warm as m; m.main()",
         tables=["sevennow_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=60, note="Incapsula — patchright"),

    # ── Off-premise platforms ─────────────────────────────────────────────────────────────────────────────────
    dict(id="offprem-census", label="Off-premise census (Shopify/Woo/Wix/Sqsp)",
         code="import off_premise as m, warehouse, re;"
              "markets=sorted(set(re.sub(r'_offprem_census$','',d['name']) for d in warehouse.list_datasets() if d['name'].endswith('_offprem_census')));"
              "[m.run_census(market=x, platforms=('Shopify','WooCommerce','Wix','Squarespace')) for x in markets]",
         tables=["offprem_products"], klass="headless", cadence="daily", enabled=True, cost_class="proxy", note="22 markets, no-BD"),
    dict(id="shopify", label="Shopify (national sweep)", code="import off_premise as m; m.national_sweep('shopify')",
         tables=["national_shopify_products"], klass="headless", cadence="weekly", enabled=True,
         note="census sweep's Shopify pass — SHOPIFY_SEED via open /products.json ($0); OFFPREM_SERP=1 adds BD SERP discovery. Replaced standalone shopify_scraper (archived)"),
    dict(id="bottlecapps", label="Bottlecapps network", code="import bottlecapps as m; m.national()",
         tables=["bottlecapps_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=62, note="DataDome — patchright"),
    dict(id="cityhive", label="City Hive network", code="import cityhive as m; m.national(max_stores=12)",
         tables=["cityhive_products"], klass="mac", cadence="daily", enabled=True, cost_class="mac", priority=61, note="Cloudflare — patchright"),
    dict(id="bbg", label="BBG e-commerce", code="import bbg_salsify as m; m.pull()",
         tables=["bbg_products"], klass="headless", cadence="daily", enabled=True, note="Salsify API"),

    # ── Distributors / state / reference ──────────────────────────────────────────────────────────────────────
    dict(id="winebow", label="Winebow (distributor)", code="import winebow as m; m.pull()",
         tables=["winebow_brands"], klass="headless", cadence="weekly", enabled=True, note="portfolio"),
    dict(id="ab-inbev", label="AB InBev locator", code="import ab_fill as m; m.run()",
         tables=["ab_outlets"], klass="headless", cadence="weekly", enabled=True, note="beertech GraphQL"),
    dict(id="ca-abc", label="California ABC", code="import ca_abc as m; m.run()",
         tables=["ca_outlets"], klass="mac", cadence="weekly", enabled=True, cost_class="proxy", note="WAF — browser headers"),
    dict(id="control-states", label="Control states (OR/UT/NC/MT/ME/AL/BC/MontMD)", code="import control_state as m; m.build_all()",
         tables=["or_pricing", "ut_pricing", "mont_sales"], klass="headless", cadence="weekly", enabled=True, note="per-state fetchers"),
    dict(id="census", label="US Census ACS", code="import census_ref as m; m.build()",
         tables=["census_reference"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"], note="Census API (census_ref.build) — free key, re-derivable"),
    dict(id="tax-rates", label="Bev-alc tax RATES (TTB + state excise)", code="import tax_rates as m; m.build()",
         tables=["tax_rates"], klass="headless", cadence="weekly", enabled=True,
         note="federal CBMA schedule (encoded, TTB) + 51-jurisdiction state excise seed (Tax Foundation Jan 2026); "
              "effective-dated ref, landed_cost.py reads it — verify state cells vs DOR to promote seed->verified"),
    dict(id="tax-revenue", label="Bev-alc tax REVENUE (Census STC + TTB)", code="import tax_revenue as m; m.build()",
         tables=["tax_revenue"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"],
         note="Census govs STC (T10 alc sales tax, T20 alc license) per state — live; TTB federal commodity "
              "collections run live on the Mac (TTB TLS-blocked on Fly)"),
    dict(id="vtinfo", label="VTInfo locator", code="import vtinfo as m; m.pull()",
         tables=["vtinfo_titos"], klass="headless", cadence="weekly", enabled=True, note="where-to-buy GraphQL"),
    dict(id="doordash-sitemap", label="DoorDash store universe", code="import doordash_sitemap as m; m.run()",
         tables=["doordash_stores"], klass="headless", cadence="weekly", enabled=True, timeout=7200,
         note="$0 national store spine from DoorDash's own sitemaps (curl_cffi+ISP); feeds naop + retail"),
    dict(id="ubereats-sitemap", label="UberEats store universe", code="import ubereats_sitemap as m; m.run()",
         tables=["ubereats_outlets"], klass="headless", cadence="weekly", enabled=True, timeout=10800,
         note="$0 US UberEats store spine from its gzipped sitemaps (~1.3M); source layer for outlet_union"),
    dict(id="naop", label="NAOP on-premise", code="import doordash_naop as m; m.run()",
         tables=["naop_accounts", "naop_beverages"], klass="headless", cadence="daily", enabled=True, timeout=7200,
         note="DoorDash on-premise menus, $0 (ISP pool); consumes doordash_stores in NAOP_LIMIT batches"),
    dict(id="toast", label="Toast own-menus", code="import toast as m; m.run()",
         tables=["toast_outlets", "toast_beverages", "toast_menu_accounts"], klass="headless", cadence="daily",
         enabled=True, timeout=7200,
         note="$0 restaurant OWN menus from toasttab.com sitemaps (~100k); harvest + TOAST_LIMIT menu batches"),
    dict(id="outlet-union", label="Outlet pre-master", code="import outlet_union as m; m.run()",
         tables=["outlet_master"], klass="headless", cadence="daily", enabled=True,
         note="derived ($0): unions DoorDash/Toast outlet spines → mastered outlets + per-source menu freshness"),
    dict(id="ttb-cola", label="TTB COLA scrape", code="import ttb_pull as m; m.run()",
         tables=["ttb_cola"], klass="headless", cadence="weekly", enabled=True, timeout=5400,
         note="$0 off-Mac incremental COLA scrape (last TTB_DAYS) → accumulate ttb_cola; ttbonline.gov verify=False, direct (no BD/browser)"),
    dict(id="ttb", label="TTB COLA master build", code="import master_ttb as m; m.run()",
         tables=["ttb_master"], klass="headless", cadence="weekly", enabled=False,
         note="MASTER BUILD (reads ttb_cola → ttb_master); huge — refresh deliberately. Scrape is ttb-cola"),

    # ── Hemp ──────────────────────────────────────────────────────────────────────────────────────────────────
    dict(id="hemp-scan", label="Hemp products", code="import hemp_scan as m; m.main([])",
         tables=["hemp_products"], klass="headless", cadence="daily", enabled=True, note="hemp-bev feed"),
    dict(id="hemp-finder", label="Hemp retailers", code="import hemp_finder as m; m.run()",
         tables=["hemp_retailers"], klass="headless", cadence="weekly", enabled=True, note="retailer discovery"),
    dict(id="hemp-inventory", label="Hemp per-store inventory", code="import hemp_inventory as m; m.main([])",
         tables=["hemp_inventory"], klass="headless", cadence="daily", enabled=True,
         note="per-store COUNTS from Shopify hemp retailers (cart-add trick) — distinct from hemp-scan listings"),
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
         tables=["src_outlets", "dim_outlet"], klass="build", interval_h=6, enabled=True,
         note="src_outlets re-shred + cross-source geo-match consolidation (supersedes run_coverage_refresh.sh)"),
    dict(id="build-product-master", label="Product master (dim_sku chain)",
         code="import build_product_master as m; m.build()",
         tables=["dim_sku"], klass="build", interval_h=12, enabled=True,
         note="brand dict → stage → shred to dim_brand/product/item/sku + xwalk/coherence/identity clusters"),
    # MOAT-PLAN Workstream M — PROVE the master. Deterministic gold set (same-UPC positives / cross-
    # brand negatives) scored against the master's item_key decision → P/R/F1 + a regression baseline.
    # Reruns after the master rebuilds so quality is MEASURED every cycle, not asserted.
    dict(id="build-master-quality", label="Master quality (P/R vs gold)",
         code="import master_quality as m; m.build()",
         tables=["master_quality"], klass="build", interval_h=24, enabled=True,
         after=["build-product-master"],
         note="deterministic UPC/brand gold → precision/recall/F1 of item_key merges + regression flag"),
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
