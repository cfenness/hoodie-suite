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
    dict(id="abc-facets", label="ABC FW&S (facets)", code="import abc_facets as m; m.pull(cap=None)",
         tables=["abc_products", "source_taxonomy"], klass="headless", cadence="daily", enabled=True, note="SearchSpring"),
    dict(id="abc-catalog", label="ABC FW&S (catalog)", code="import abc_catalog as m; m.run()",
         tables=["abc_catalog"], klass="headless", cadence="weekly", enabled=True, note="BigCommerce sitemap"),
    dict(id="abc-fws", label="ABC FW&S (inventory)", code="import abc_fws_scraper as m; m.pull(crawl_all=True)",
         tables=["abc_products"], klass="headless", cadence="daily", enabled=True, note="per-store inventory"),
    dict(id="haskells", label="Haskell's (MN)", code="import haskells as m; m.run(limit=None)",
         tables=["haskells_products"], klass="headless", cadence="daily", enabled=True, timeout=10800,
         note="first-party site; full-catalog crawl outgrew the 5400s default (timed out 07-18)"),
    dict(id="total-wine", label="Total Wine", code="import total_wine_full as m; m.run()",
         tables=["total_wine_products"], klass="mac", cadence="daily", enabled=True, note="PerimeterX — browser"),

    # ── Grocery / big-box ─────────────────────────────────────────────────────────────────────────────────────
    dict(id="walmart", label="Walmart", code="import walmart_direct as m; m.pull(detail_pages=True, detail_cap=600)",
         tables=["walmart_products"], klass="mac", cadence="daily", enabled=True,
         requires=["WALMART_COOKIE"], note="__NEXT_DATA__ over stdlib HTTP, no BD — needs a warmed PX cookie"),
    dict(id="target", label="Target", code="import target_scraper as m; m.run()",
         tables=["target_products", "target_stores"], klass="headless", cadence="daily", enabled=True, note="RedSky API"),
    dict(id="kroger", label="Kroger (atlas inventory)", code="import kroger_atlas as m; m.main([])",
         tables=["kroger_atlas_products"], klass="mac", cadence="daily", enabled=True,
         requires=["KROGER_COOKIE", "KROGER_STORE", "KROGER_FACILITY"],
         note="INTERNAL atlas endpoint = exact per-store on-hand + dims + ABV; warmed cookie (anti-bot), Tier B"),
    dict(id="kroger-api", label="Kroger (API UPC seed)", code="import kroger_api as m; m.main()",
         tables=["kroger_products"], klass="creds", cadence="weekly", enabled=True,
         requires=["KROGER_CLIENT_ID", "KROGER_CLIENT_SECRET"],
         note="thin public OAuth API — product/UPC seed that feeds the atlas GTIN universe (NOT real inventory)"),
    dict(id="publix", label="Publix", code="import publix as m; m.run()",
         tables=["publix_products"], klass="headless", cadence="daily", enabled=True, note="weekly-ad API"),
    dict(id="stop-and-shop", label="Stop & Shop", code="import stop_and_shop as m; m.main([])",
         tables=["stop_and_shop_products"], klass="mac", cadence="daily", enabled=False, note="needs a warmed cookie — not headless"),

    # ── Aggregators / convenience (Mac headful — anti-bot) ────────────────────────────────────────────────────
    dict(id="ubereats", label="Uber Eats", code="import ubereats as m; m.main(['--site','ubereats','--max-stores','1000'])",
         tables=["ubereats_products"], klass="mac", cadence="daily", enabled=True, priority=10, note="Uber BFF, all stores"),
    dict(id="postmates", label="Postmates", code="import ubereats as m; m.main(['--site','postmates','--max-stores','1000'])",
         tables=["postmates_products"], klass="mac", cadence="daily", enabled=True, priority=11, note="Uber BFF, all stores"),
    dict(id="sevennow", label="7-Eleven (7NOW)", code="import sevennow_warm as m; m.main()",
         tables=["sevennow_products"], klass="mac", cadence="daily", enabled=True, priority=60, note="Incapsula — patchright"),

    # ── Off-premise platforms ─────────────────────────────────────────────────────────────────────────────────
    dict(id="offprem-census", label="Off-premise census (Shopify/Woo/Wix/Sqsp)",
         code="import off_premise as m, warehouse, re;"
              "markets=sorted(set(re.sub(r'_offprem_census$','',d['name']) for d in warehouse.list_datasets() if d['name'].endswith('_offprem_census')));"
              "[m.run_census(market=x, platforms=('Shopify','WooCommerce','Wix','Squarespace')) for x in markets]",
         tables=["offprem_products"], klass="headless", cadence="daily", enabled=True, note="22 markets, no-BD"),
    dict(id="bottlecapps", label="Bottlecapps network", code="import bottlecapps as m; m.national()",
         tables=["bottlecapps_products"], klass="mac", cadence="daily", enabled=True, priority=62, note="DataDome — patchright"),
    dict(id="cityhive", label="City Hive network", code="import cityhive as m; m.national(max_stores=12)",
         tables=["cityhive_products"], klass="mac", cadence="daily", enabled=True, priority=61, note="Cloudflare — patchright"),
    dict(id="bbg", label="BBG e-commerce", code="import bbg_salsify as m; m.pull()",
         tables=["bbg_products"], klass="headless", cadence="daily", enabled=True, note="Salsify API"),

    # ── Distributors / state / reference ──────────────────────────────────────────────────────────────────────
    dict(id="winebow", label="Winebow (distributor)", code="import winebow as m; m.pull()",
         tables=["winebow_brands"], klass="headless", cadence="weekly", enabled=True, note="portfolio"),
    dict(id="ab-inbev", label="AB InBev locator", code="import ab_fill as m; m.run()",
         tables=["ab_outlets"], klass="headless", cadence="weekly", enabled=True, note="beertech GraphQL"),
    dict(id="ca-abc", label="California ABC", code="import ca_abc as m; m.run()",
         tables=["ca_outlets"], klass="mac", cadence="weekly", enabled=True, note="WAF — browser headers"),
    dict(id="control-states", label="Control states (OR/UT/NC/MT/ME/AL/BC/MontMD)", code="import control_state as m; m.build_all()",
         tables=["or_pricing", "ut_pricing", "mont_sales"], klass="headless", cadence="weekly", enabled=True, note="per-state fetchers"),
    dict(id="census", label="US Census ACS", code="import census_ref as m; m.build()",
         tables=["census_reference"], klass="creds", cadence="weekly", enabled=True,
         requires=["CENSUS_API_KEY"], note="Census API (census_ref.build) — free key, re-derivable"),
    dict(id="vtinfo", label="VTInfo locator", code="import vtinfo as m; m.pull()",
         tables=["vtinfo_titos"], klass="headless", cadence="weekly", enabled=True, note="where-to-buy GraphQL"),
    dict(id="naop", label="NAOP on-premise", code="import doordash_naop as m; m.run()",
         tables=["naop_accounts", "naop_beverages"], klass="headless", cadence="weekly", enabled=True, note="DoorDash menus"),
    dict(id="ttb", label="TTB COLA registry", code="import master_ttb as m; m.run()",
         tables=["ttb_master"], klass="headless", cadence="weekly", enabled=False, note="huge backfill — refresh deliberately"),

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
