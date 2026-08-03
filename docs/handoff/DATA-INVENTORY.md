# Data inventory — what this system defines

Static map from the source tree (`ast`, no warehouse access). Reports what is DEFINED to be
written, not what is currently landed — row counts and sizes need Tigris and are a separate
question (`tools/warehouse_egress.py inventory`).

- registry entries: **80** (66 sources, 14 builds)
- distinct tables: **159**
- production write call sites: **208**

## The headline: this system is not statically inspectable

**62 of 208 write call sites (30%) do not name their table in the source.** The name is
computed at run time — `"%s_products" % site`, an f-string, or a variable — so no tool and
no amount of reading can produce a complete table map. You have to RUN it to find out.

The only thing binding a computed name to a real table is a hand-typed `tables=[...]` on a
registry entry. Add a site and the code works while the map goes silently wrong — which is
why `ubereats_products` below is declared by four entries and written by nothing traceable.

- write sites with a fully opaque table name: **41**
- write sites with a TEMPLATE name (`{}_products`): **21**
- tables only knowable from a registry declaration: **22**

## Where the map disagrees with itself

- concrete tables WRITTEN but declared by no registry entry: **56**
- concrete tables DECLARED but with no traceable writer: **22**
- concrete tables with writers in MORE THAN ONE module: **7**
- `write_partition` call sites that do NOT pin dtypes: **12 of 16**

### Template table names, and what they probably resolve to

Matched against registry-declared names by pattern. This join is a GUESS — the registry
is hand-typed, so a site present in code but absent from `tables=[...]` is invisible here.

- `national_{}_products` — `off_premise.py:881`, `off_premise.py:883` → `national_shopify_products`
- `{}_coverage` — `place_coverage.py:244` → **no declared match**
- `{}_geo` — `ue_geofill.py:171`, `ue_geofill.py:188` → **no declared match**
- `{}_menu_census` — `menu_site.py:306`, `menu_site.py:308` → **no declared match**
- `{}_merchants` — `doordash_geo.py:255`, `place_coverage.py:245` → **no declared match**
- `{}_offprem_census` — `platform_census.py:127`, `platform_census.py:130` → **no declared match**
- `{}_outlet_hours` — `place_coverage.py:142`, `place_coverage.py:144` → **no declared match**
- `{}_products` — `aggregator.py:95`, `ubereats.py:652` → `abc_products`, `binnys_products`, `bottlecapps_products`, `cityhive_products`, `haskells_products`, `hemp_products`, `instacart_products`, `kroger_atlas_products`, `kroger_products`, `meijer_products`, `national_shopify_products`, `offprem_products`, `postmates_products`, `publix_products`, `salsify_products`, `sevennow_products`, `specs_products`, `stop_and_shop_products`, `target_products`, `total_wine_products`, `trader_joes_products`, `ubereats_products`, `walmart_products`
- `{}_products_parts` — `ue_enrich.py:97` → **no declared match**
- `{}_sitemap` — `ue_sitemap.py:66` → `postmates_sitemap`, `ubereats_sitemap`
- `{}_store_misses` — `ue_catalog.py:869` → **no declared match**
- `{}_stores` — `reconcile_ue_ids.py:36`, `run_ue_coverage_geo.py:55`, `ue_crawl.py:522` → `doordash_stores`, `target_stores`

### Written, not declared

- `_stage_product` — build_product_master.py:813
- `account_logos` — menu_site.py:385
- `agg_geo_stage` — aggregator_geo.py:66
- `bevalc_chains` — chains.py:201
- `category_cluster` — build_product_master.py:569
- `cityhive_chain_products` — off_premise.py:731
- `cola_cluster` — cola_cluster.py:272
- `cola_cluster_membership` — cola_cluster.py:273
- `coverage_log` — coverage.py:111
- `cv_reads` — label_vision.py:153
- `dim_product_type` — normalize.py:237
- `dim_store` — facts.py:159
- `distributor_menu_items` — menu_ingest.py:467
- `fact_inventory` — facts.py:152
- `fact_price` — facts.py:154
- `field_stats` — extract_qa.py:128
- `geo_cbsa_ref` — geo_resolve.py:118
- `hoodie_ids` — hoodie_ids.py:184
- `identity_cluster` — build_product_master.py:666
- `img_matches` — img_embed.py:210
- `img_vec` — img_embed.py:120
- `kroger_atlas_debug` — kroger_atlas.py:199
- `kroger_runs` — kroger_api.py:233
- `label_extract` — label_vision.py:207
- `label_reads` — label_reader.py:449
- `ladder_state` — ladder.py:234
- `master_decisions` — server.py:3371, server.py:3394
- `master_overrides` — server.py:3485
- `match_dict` — dict_apply.py:129, dict_apply.py:165
- `menu_beverages` — menu_site.py:381
- `menu_files` — menu_site.py:383
- `normalization_findings` — normalization_scout.py:272
- `outlet_geography` — geo_resolve.py:257
- `planogram_placements` — planogram_build.py:82
- `price_coherence` — build_product_master.py:525
- `raw_payloads` — raw_capture.py:68
- `scrape_runs` — runlog.py:63
- `source_runs_log` — run_sources.py:555
- `src_brands` — normalize.py:224
- `src_items` — normalize.py:230
- `src_products` — normalize.py:226
- `src_skus` — normalize.py:233
- `src_summary` — normalize.py:743
- `trade_area_demand` — cex_ref.py:407
- `ttb_quarantine_summary` — master_ttb.py:135
- `ttb_review` — master_ttb.py:134
- `vip_brandbuilder_sellsheet_packages` — vip_brandbuilder_sellsheets.py:212
- `walmart_runs` — walmart_api.py:163
- `wb_master` — wb_views.py:312
- `wb_matches` — wb_views.py:196
- `wb_merges` — wb_views.py:104
- `wb_queue` — wb_views.py:313
- `wb_summary` — wb_views.py:328
- `xwalk_item_identity` — build_product_master.py:690
- `xwalk_source_sku` — build_product_master.py:481
- `zcta_centroids` — zcta.py:75

### Declared, never written

- `census_demographic` — declared by census-acs
- `census_economic` — declared by census-acs
- `census_housing` — declared by census-acs
- `dim_sku` — declared by build-product-master
- `doordash_outlets_full` — declared by doordash-full
- `doordash_products_full` — declared by doordash-full
- `fact_velocity` — declared by build-velocity
- `instacart_products` — declared by instacart-bevalc
- `mart_sip_brand_market_month` — declared by build-sipsource-marts
- `mart_velocity_brand_week` — declared by build-velocity
- `mont_sales` — declared by control-states
- `national_shopify_products` — declared by shopify
- `obs_quality_cell` — declared by build-obs-quality
- `obs_quality_source` — declared by build-obs-quality
- `or_pricing` — declared by control-states
- `postmates_products` — declared by postmates, postmates-full, build-ue-catalog
- `postmates_sitemap` — declared by postmates-sitemap
- `signal_movers` — declared by build-velocity-signals
- `signal_voids` — declared by build-velocity-signals
- `ubereats_products` — declared by ubereats-enrich, ubereats, ubereats-full, build-ue-catalog
- `ubereats_sitemap` — declared by ubereats-sitemap
- `ut_pricing` — declared by control-states

### Multiple writing modules (shared tables)

- `abc_catalog` — abc_catalog.py, abc_fws_scraper.py
- `doordash_stores` — doordash_discover.py, doordash_sitemap.py
- `item_identity` — build_item_identity.py, ingest_canon_identity.py
- `src_outlets` — aggregator_geo.py, city_centroid.py, geocode.py, mappability.py, normalize.py, reconcile_ue_ids.py, refresh_fast.py, ue_sitemap.py
- `total_wine_products` — total_wine.py, total_wine_full.py, total_wine_inventory.py
- `ttb_master` — master_ttb.py, server.py
- `walmart_products` — walmart_api.py, walmart_direct.py

### write_partition without pinned dtypes

Each of these can land a batch-inferred schema; a union read across partitions then
reconciles incompatible types and corrupts rather than fails. Two live incidents so far.

- `agg_geo_stage` — aggregator_geo.py:66
- `coverage_log` — coverage.py:111
- `field_stats` — extract_qa.py:128
- `fact_inventory` — facts.py:152
- `fact_price` — facts.py:154
- `ladder_state` — ladder.py:234
- `raw_payloads` — raw_capture.py:68
- `source_runs_log` — run_sources.py:555
- `scrape_runs` — runlog.py:63
- `salsify_properties` — salsify.py:867
- `{}_store_misses` — ue_catalog.py:869
- `{}_products_parts` — ue_enrich.py:97

## Landing verification blind spots

A run is graded by the row-count delta across its DECLARED tables. If a source's declared
tables are not the tables it writes, the grade is measuring something else.

### Declares only tables with no traceable writer

These can never post a positive delta from their own work, so they report `current`
(or `empty`) however much they land — and `due_builds` only advances on `ok`.

**CANDIDATES, NOT CONFIRMED.** A writer using a computed table name is indistinguishable
here from no writer at all, so this list mixes real defects with the limits of static
analysis — which is itself the finding. Each needs the writing module read to settle.
Confirmed so far: `ubereats-enrich` (writes `ubereats_products_parts` via
`ue_enrich.py:97`, declares `ubereats_products`, so its delta is always 0).

- `build-obs-quality` → declares `obs_quality_source`, `obs_quality_cell`
- `build-product-master` → declares `dim_sku`
- `build-sipsource-marts` → declares `mart_sip_brand_market_month`
- `build-ue-catalog` → declares `ubereats_products`, `postmates_products`
- `build-velocity` → declares `fact_velocity`, `mart_velocity_brand_week`
- `build-velocity-signals` → declares `signal_movers`, `signal_voids`
- `census-acs` → declares `census_demographic`, `census_economic`, `census_housing`
- `control-states` → declares `or_pricing`, `ut_pricing`, `mont_sales`
- `instacart-bevalc` → declares `instacart_products`
- `postmates-full` → declares `postmates_products`
- `shopify` → declares `national_shopify_products`
- `ubereats-enrich` → declares `ubereats_products`
- `ubereats-full` → declares `ubereats_products`

### Declares only SHARED tables

The delta includes every other source writing the same table, so it is not a per-source
signal at all.

- `abc-catalog` → declares `abc_catalog`
- `aggregator-geo` → declares `src_outlets`
- `build-item-identity` → declares `item_identity`
- `doordash-geo-tx` → declares `doordash_stores`
- `doordash-sitemap` → declares `doordash_stores`
- `fast-geo` → declares `src_outlets`
- `geo` → declares `src_outlets`
- `geocode` → declares `src_outlets`
- `total-wine` → declares `total_wine_products`
- `ttb` → declares `ttb_master`
- `walmart` → declares `walmart_products`

## Registry families

Grouping is by leading id token because the registry has no family field — which is why
`build-ue-catalog` does not group with `ubereats`.

| family | entries | ids |
|---|---:|---|
| `build` | 13 | `build-dist-xwalk`, `build-item-identity`, `build-master-quality`, `build-master-quality-canon`, `build-obs-quality`, `build-outlets`, `build-product-master`, `build-representativeness`, `build-sipsource-marts`, `build-ue-catalog`, `build-velocity`, `build-velocity-calibrate`, `build-velocity-signals` |
| `ubereats` | 4 | `ubereats`, `ubereats-enrich`, `ubereats-full`, `ubereats-sitemap` |
| `census` | 4 | `census`, `census-acs`, `census-acs5`, `census-migration` |
| `abc` | 3 | `abc-catalog`, `abc-facets`, `abc-fws` |
| `postmates` | 3 | `postmates`, `postmates-full`, `postmates-sitemap` |
| `vip` | 3 | `vip-brandbuilder`, `vip-brandbuilder-census`, `vip-finder-census` |
| `doordash` | 3 | `doordash-full`, `doordash-geo-tx`, `doordash-sitemap` |
| `ttb` | 3 | `ttb`, `ttb-cola`, `ttb-enrich` |
| `hemp` | 3 | `hemp-finder`, `hemp-inventory`, `hemp-scan` |
| `kroger` | 2 | `kroger`, `kroger-api` |
| `tax` | 2 | `tax-rates`, `tax-revenue` |

## Every table

| table | layout | writers | declared by |
|---|---|---|---|
| `<dynamic>` | accumulating (merge; bucketed if migrated), flat (from csv), flat (full overwrite), partitioned (append-only parts) | `bottle_dims.py:387`, `bottle_dims.py:389`, `census.py:123`, `cola_to_warehouse.py:40`, `control_state.py:113`, `control_state.py:301`, `control_state.py:90`, `coverage.py:216`, `coverage.py:227`, `coverage.py:238`, `doordash.py:325`, `doordash.py:330`, `doordash_full.py:266`, `doordash_full.py:273`, `doordash_full.py:297`, `doordash_full.py:310`, `doordash_full.py:325`, `doordash_full.py:333`, `hoodie_ids.py:203`, `iowa_bq.py:104`, `menu_site.py:400`, `normalize.py:718`, `off_premise.py:1022`, `off_premise.py:966`, `places.py:168`, `places.py:203`, `platform_census.py:184`, `platform_census.py:186`, `seed.py:137`, `server.py:3514`, `server.py:677`, `snapshot_land.py:51`, `snapshot_land.py:70`, `ue_catalog.py:478`, `ue_catalog.py:588`, `ue_catalog.py:590`, `ue_crawl.py:264`, `ue_feed_sweep.py:143`, `warehouse.py:287`, `warehouse.py:290`, `warehouse.py:373` | — |
| `_stage_product` | flat (full overwrite) | `build_product_master.py:813` | — |
| `ab_outlets` | flat (full overwrite) | `ab_fill.py:68` | `ab-inbev` |
| `abc_catalog` | accumulating (merge; bucketed if migrated) | `abc_catalog.py:68`, `abc_catalog.py:77`, `abc_fws_scraper.py:435` | `abc-catalog`, `abc-fws` |
| `abc_products` | flat (full overwrite) | `abc_facets.py:117` | `abc-facets` |
| `account_logos` | accumulating (merge; bucketed if migrated) | `menu_site.py:385` | — |
| `agg_geo_stage` | partitioned (append-only parts) | `aggregator_geo.py:66` | — |
| `bea_reference` | accumulating (merge; bucketed if migrated) | `bea_ref.py:124` | `bea` |
| `bevalc_chains` | flat (full overwrite) | `chains.py:201` | — |
| `binnys_products` | accumulating (merge; bucketed if migrated), flat (full rebuild, layout-preserving) | `binnys_scraper.py:281`, `binnys_scraper.py:283` | `binnys` |
| `bottlecapps_products` | accumulating (merge; bucketed if migrated) | `bottlecapps.py:197` | `bottlecapps` |
| `ca_outlets` | flat (full overwrite) | `ca_abc.py:46` | `ca-abc` |
| `category_cluster` | flat (full overwrite) | `build_product_master.py:569` | — |
| `census_acs` | flat (full overwrite) | `census_ref.py:384` | `census-acs5` |
| `census_demographic` | — | — | `census-acs` |
| `census_economic` | — | — | `census-acs` |
| `census_housing` | — | — | `census-acs` |
| `census_migration` | flat (full overwrite) | `census_ref.py:395` | `census-migration` |
| `census_reference` | accumulating (merge; bucketed if migrated) | `census_ref.py:371` | `census` |
| `cex_reference` | accumulating (merge; bucketed if migrated) | `cex_ref.py:195` | `cex` |
| `city_centroids` | flat (full overwrite) | `city_centroid.py:93` | `city-centroid-build` |
| `cityhive_chain_products` | accumulating (merge; bucketed if migrated) | `off_premise.py:731` | — |
| `cityhive_products` | accumulating (merge; bucketed if migrated) | `cityhive.py:147` | `cityhive` |
| `cola_cluster` | flat (full overwrite) | `cola_cluster.py:272` | — |
| `cola_cluster_membership` | flat (full overwrite) | `cola_cluster.py:273` | — |
| `coverage_cells` | flat (full overwrite) | `representativeness.py:135` | `build-representativeness` |
| `coverage_log` | partitioned (append-only parts) | `coverage.py:111` | — |
| `cpi_reference` | accumulating (merge; bucketed if migrated) | `cpi_ref.py:142` | `cpi` |
| `cv_reads` | accumulating (merge; bucketed if migrated) | `label_vision.py:153` | — |
| `dim_outlet` | flat (full overwrite) | `dim_outlet.py:124` | `build-outlets` |
| `dim_product_type` | flat (full overwrite) | `normalize.py:237` | — |
| `dim_sku` | — | — | `build-product-master` |
| `dim_store` | accumulating (merge; bucketed if migrated) | `facts.py:159` | — |
| `dist_item_xwalk` | flat (full overwrite) | `dist_xwalk.py:119` | `build-dist-xwalk` |
| `distributor_menu_items` | accumulating (merge; bucketed if migrated) | `menu_ingest.py:467` | — |
| `doordash_full_runs` | accumulating (merge; bucketed if migrated) | `doordash_chains.py:185` | `doordash-full` |
| `doordash_outlets_full` | — | — | `doordash-full` |
| `doordash_products_full` | — | — | `doordash-full` |
| `doordash_stores` | accumulating (merge; bucketed if migrated) | `doordash_discover.py:133`, `doordash_sitemap.py:142` | `doordash-geo-tx`, `doordash-sitemap` |
| `fact_inventory` | partitioned (append-only parts) | `facts.py:152` | — |
| `fact_price` | partitioned (append-only parts) | `facts.py:154` | — |
| `fact_velocity` | — | — | `build-velocity` |
| `field_stats` | partitioned (append-only parts) | `extract_qa.py:128` | — |
| `fred_reference` | accumulating (merge; bucketed if migrated) | `fred_ref.py:101` | `fred` |
| `geo_cbsa_ref` | flat (full overwrite) | `geo_resolve.py:118` | — |
| `haskells_products` | accumulating (merge; bucketed if migrated) | `haskells.py:167` | `haskells` |
| `hemp_inventory` | accumulating (merge; bucketed if migrated) | `hemp_inventory.py:127` | `hemp-inventory` |
| `hemp_products` | flat (full overwrite) | `hemp_scan.py:120` | `hemp-scan` |
| `hemp_retailers` | accumulating (merge; bucketed if migrated) | `hemp_finder.py:85` | `hemp-finder` |
| `hoodie_ids` | flat (full overwrite) | `hoodie_ids.py:184` | — |
| `identity_cluster` | flat (full overwrite) | `build_product_master.py:666` | — |
| `img_matches` | flat (full overwrite) | `img_embed.py:210` | — |
| `img_vec` | accumulating (merge; bucketed if migrated) | `img_embed.py:120` | — |
| `instacart_products` | — | — | `instacart-bevalc` |
| `item_identity` | flat (full overwrite) | `build_item_identity.py:105`, `ingest_canon_identity.py:55` | `build-item-identity` |
| `kroger_atlas_debug` | flat (full overwrite) | `kroger_atlas.py:199` | — |
| `kroger_atlas_products` | accumulating (merge; bucketed if migrated) | `kroger_atlas.py:226` | `kroger` |
| `kroger_products` | flat (full overwrite) | `kroger_api.py:218` | `kroger-api` |
| `kroger_runs` | flat (full overwrite) | `kroger_api.py:233` | — |
| `label_extract` | accumulating (merge; bucketed if migrated) | `label_vision.py:207` | — |
| `label_reads` | accumulating (merge; bucketed if migrated) | `label_reader.py:449` | — |
| `ladder_state` | partitioned (append-only parts) | `ladder.py:234` | — |
| `market_projection` | flat (full overwrite) | `representativeness.py:112` | `build-representativeness` |
| `mart_sip_brand_market_month` | — | — | `build-sipsource-marts` |
| `mart_velocity_brand_week` | — | — | `build-velocity` |
| `master_decisions` | flat (full overwrite) | `server.py:3371`, `server.py:3394` | — |
| `master_overrides` | flat (full overwrite) | `server.py:3485` | — |
| `master_quality` | accumulating (merge; bucketed if migrated) | `master_quality.py:150` | `build-master-quality` |
| `master_quality_canon` | accumulating (merge; bucketed if migrated) | `master_quality.py:224` | `build-master-quality-canon` |
| `match_dict` | accumulating (merge; bucketed if migrated), flat (full overwrite) | `dict_apply.py:129`, `dict_apply.py:165` | — |
| `meijer_products` | accumulating (merge; bucketed if migrated) | `meijer.py:151` | `meijer` |
| `menu_beverages` | accumulating (merge; bucketed if migrated) | `menu_site.py:381` | — |
| `menu_files` | accumulating (merge; bucketed if migrated) | `menu_site.py:383` | — |
| `mont_sales` | — | — | `control-states` |
| `naop_accounts` | flat (full overwrite) | `doordash_naop.py:195` | `naop` |
| `naop_beverages` | flat (full overwrite) | `doordash_naop.py:193` | `naop` |
| `national_shopify_products` | — | — | `shopify` |
| `national_{}_products` | accumulating (merge; bucketed if migrated) | `off_premise.py:881`, `off_premise.py:883` | — |
| `normalization_findings` | flat (full overwrite) | `normalization_scout.py:272` | — |
| `obs_quality_cell` | — | — | `build-obs-quality` |
| `obs_quality_source` | — | — | `build-obs-quality` |
| `offprem_products` | accumulating (merge; bucketed if migrated) | `off_premise.py:976` | `offprem-census` |
| `or_pricing` | — | — | `control-states` |
| `outlet_geography` | flat (full rebuild, layout-preserving) | `geo_resolve.py:257` | — |
| `outlet_master` | flat (full overwrite) | `outlet_union.py:137` | `outlet-union` |
| `planogram_placements` | accumulating (merge; bucketed if migrated) | `planogram_build.py:82` | — |
| `postmates_products` | — | — | `build-ue-catalog`, `postmates`, `postmates-full` |
| `postmates_sitemap` | — | — | `postmates-sitemap` |
| `price_coherence` | flat (full overwrite) | `build_product_master.py:525` | — |
| `publix_products` | accumulating (merge; bucketed if migrated) | `publix.py:140` | `publix` |
| `raw_payloads` | partitioned (append-only parts) | `raw_capture.py:68` | — |
| `retail_observations` | partitioned (append-only parts) | `observe.py:156` | `abc-fws`, `postmates`, `ubereats` |
| `salsify_catalogs` | accumulating (merge; bucketed if migrated) | `salsify.py:344`, `salsify.py:761` | `salsify` |
| `salsify_products` | accumulating (merge; bucketed if migrated) | `salsify.py:852` | `bbg`, `salsify` |
| `salsify_properties` | partitioned (append-only parts) | `salsify.py:867` | `bbg`, `salsify` |
| `scrape_runs` | partitioned (append-only parts) | `runlog.py:63` | — |
| `sevenfifty_items` | accumulating (merge; bucketed if migrated) | `sevenfifty.py:179` | `sevenfifty` |
| `sevennow_products` | accumulating (merge; bucketed if migrated) | `sevennow.py:224` | `sevennow` |
| `signal_movers` | — | — | `build-velocity-signals` |
| `signal_voids` | — | — | `build-velocity-signals` |
| `snowflake_load_runs` | accumulating (merge; bucketed if migrated) | `snowflake_load.py:53` | `snowflake-load` |
| `source_runs_log` | partitioned (append-only parts) | `run_sources.py:555` | — |
| `source_taxonomy` | accumulating (merge; bucketed if migrated) | `abc_facets.py:120` | `abc-facets` |
| `specs_products` | accumulating (merge; bucketed if migrated), flat (full overwrite) | `specs_scraper.py:410`, `specs_scraper.py:415`, `specs_scraper.py:419` | `specs` |
| `src_brands` | flat (full overwrite) | `normalize.py:224` | — |
| `src_items` | flat (full overwrite) | `normalize.py:230` | — |
| `src_outlets` | accumulating (merge; bucketed if migrated), flat (full rebuild, layout-preserving) | `aggregator_geo.py:93`, `city_centroid.py:268`, `geocode.py:134`, `mappability.py:163`, `normalize.py:651`, `reconcile_ue_ids.py:45`, `refresh_fast.py:60`, `ue_sitemap.py:97` | `aggregator-geo`, `build-outlets`, `fast-geo`, `geo`, `geocode`, `postmates-sitemap`, `ubereats-sitemap` |
| `src_products` | flat (full overwrite) | `normalize.py:226` | — |
| `src_skus` | flat (full overwrite) | `normalize.py:233` | — |
| `src_summary` | flat (full overwrite) | `normalize.py:743` | — |
| `stop_and_shop_products` | accumulating (merge; bucketed if migrated) | `stop_and_shop.py:125` | `stop-and-shop` |
| `target_products` | accumulating (merge; bucketed if migrated) | `target_scraper.py:274`, `target_scraper.py:319` | `target` |
| `target_stores` | accumulating (merge; bucketed if migrated) | `target_scraper.py:188` | `target` |
| `tax_rates` | accumulating (merge; bucketed if migrated) | `tax_rates.py:165` | `tax-rates` |
| `tax_revenue` | accumulating (merge; bucketed if migrated) | `tax_revenue.py:175` | `tax-revenue` |
| `toast_beverages` | accumulating (merge; bucketed if migrated) | `toast.py:205` | `toast` |
| `toast_menu_accounts` | accumulating (merge; bucketed if migrated) | `toast.py:207` | `toast` |
| `toast_outlets` | accumulating (merge; bucketed if migrated) | `toast.py:101` | `toast` |
| `total_wine_products` | accumulating (merge; bucketed if migrated) | `total_wine.py:202`, `total_wine_full.py:44`, `total_wine_inventory.py:269` | `total-wine` |
| `trade_area_demand` | flat (full overwrite) | `cex_ref.py:407` | — |
| `trader_joes_products` | accumulating (merge; bucketed if migrated) | `trader_joes.py:145` | `trader-joes` |
| `ttb_cola` | accumulating (merge; bucketed if migrated) | `ttb_pull.py:45` | `ttb-cola` |
| `ttb_cola_detail` | accumulating (merge; bucketed if migrated) | `ttb_pull.py:220` | `ttb-enrich` |
| `ttb_cola_labels` | accumulating (merge; bucketed if migrated) | `ttb_pull.py:222` | `ttb-enrich` |
| `ttb_master` | flat (full overwrite) | `master_ttb.py:133`, `server.py:3402` | `ttb` |
| `ttb_quarantine_summary` | flat (full overwrite) | `master_ttb.py:135` | — |
| `ttb_review` | flat (full overwrite) | `master_ttb.py:134` | — |
| `ubereats_products` | — | — | `build-ue-catalog`, `ubereats`, `ubereats-enrich`, `ubereats-full` |
| `ubereats_sitemap` | — | — | `ubereats-sitemap` |
| `ut_pricing` | — | — | `control-states` |
| `velocity_calibration` | flat (full overwrite) | `velocity_calibrate.py:142` | `build-velocity-calibrate` |
| `vip_brandbuilder_directory` | accumulating (merge; bucketed if migrated) | `vip_brandbuilder_census.py:347` | `vip-brandbuilder-census` |
| `vip_brandbuilder_items` | accumulating (merge; bucketed if migrated) | `vtinfo_bbs.py:234` | `vip-brandbuilder` |
| `vip_brandbuilder_sellsheet_packages` | accumulating (merge; bucketed if migrated) | `vip_brandbuilder_sellsheets.py:212` | — |
| `vip_finder_brands` | accumulating (merge; bucketed if migrated) | `vip_finder_census.py:466` | `vip-finder-census` |
| `vip_finder_tenants` | accumulating (merge; bucketed if migrated) | `vip_finder_census.py:463` | `vip-finder-census` |
| `vtinfo_titos` | accumulating (merge; bucketed if migrated) | `vtinfo.py:277` | `vtinfo` |
| `walmart_products` | accumulating (merge; bucketed if migrated) | `walmart_api.py:150`, `walmart_direct.py:300` | `walmart` |
| `walmart_runs` | flat (full overwrite) | `walmart_api.py:163` | — |
| `wb_master` | flat (full overwrite) | `wb_views.py:312` | — |
| `wb_matches` | flat (full overwrite) | `wb_views.py:196` | — |
| `wb_merges` | flat (full overwrite) | `wb_views.py:104` | — |
| `wb_queue` | flat (full overwrite) | `wb_views.py:313` | — |
| `wb_summary` | flat (full overwrite) | `wb_views.py:328` | — |
| `winebow_brands` | flat (full overwrite) | `winebow.py:86` | `winebow` |
| `xwalk_item_identity` | flat (full overwrite) | `build_product_master.py:690` | — |
| `xwalk_source_sku` | flat (full overwrite) | `build_product_master.py:481` | — |
| `zcta_centroids` | flat (full overwrite) | `zcta.py:75` | — |
| `{}_coverage` | flat (full overwrite) | `place_coverage.py:244` | — |
| `{}_geo` | accumulating (merge; bucketed if migrated) | `ue_geofill.py:171`, `ue_geofill.py:188` | — |
| `{}_menu_census` | flat (full overwrite) | `menu_site.py:306`, `menu_site.py:308` | — |
| `{}_merchants` | flat (full overwrite) | `doordash_geo.py:255`, `place_coverage.py:245` | — |
| `{}_offprem_census` | flat (full overwrite) | `platform_census.py:127`, `platform_census.py:130` | — |
| `{}_outlet_hours` | flat (full overwrite) | `place_coverage.py:142`, `place_coverage.py:144` | — |
| `{}_products` | accumulating (merge; bucketed if migrated) | `aggregator.py:95`, `ubereats.py:652` | — |
| `{}_products_parts` | partitioned (append-only parts) | `ue_enrich.py:97` | — |
| `{}_sitemap` | accumulating (merge; bucketed if migrated) | `ue_sitemap.py:66` | — |
| `{}_store_misses` | partitioned (append-only parts) | `ue_catalog.py:869` | — |
| `{}_stores` | accumulating (merge; bucketed if migrated), flat (full overwrite) | `reconcile_ue_ids.py:36`, `run_ue_coverage_geo.py:55`, `ue_crawl.py:522` | — |

