#!/usr/bin/env python3
"""build_snowflake_sql.py — generate the Snowflake load build from the SAME registry the engine runs.

WHY a generator (not hand-kept SQL): `unifyd/source_registry.py` is already the single source of
truth for every source we pull, and the canonical star (dim_brand/product/item/sku, dim_outlet, the
src_<grain> feeds, the signal tables) is defined once in `build_product_master.py` / `normalize.py`
/ `dim_outlet.py`. Hand-maintaining a parallel list of Snowflake DDL would drift the moment a source
is added or a scraper widens its schema. So this script DERIVES the build:

  • RAW schema  — one landing table per warehouse Parquet, loaded schema-agnostically via Snowflake's
    INFER_SCHEMA + COPY … MATCH_BY_COLUMN_NAME. Drift-proof: a scraper can add a column and the next
    load just picks it up, exactly like the DuckDB `read_parquet` path does today. The table LIST comes
    from source_registry (SOURCES + BUILDS) so "add a source = add one registry row" still holds.
  • MASTER schema — the canonical star, with EXPLICIT typed DDL (below) because those are stable
    contracts, not scraper output. This is the seed to Unifyd — real columns, real types, clustering
    keys that mirror the Parquet partitioning (NRT-PLAN.md §2: "the Parquet layout is deliberately
    Snowflake-loadable — migration is a load, not a rewrite").
  • MART schema — a few convenience views that join the star the way the apps ask for it.

Two run modes:
  python build_snowflake_sql.py                 # OFFLINE (default): emit the whole build from the
                                                #   registry + the typed catalog below. No warehouse
                                                #   needed — this is what's committed under sql/.
  python build_snowflake_sql.py --live          # LIVE: additionally read the warehouse (Tigris or
                                                #   local) to (a) include EVERY table actually present,
                                                #   (b) resolve bucketed (v2) tables to their manifest's
                                                #   active part files (so a COPY never double-loads a
                                                #   superseded part), and (c) print row counts.

The offline build assumes the v1 single-file layout (`<name>.parquet`) — correct for every named
"full" source and the entire master layer. Big accumulating catalogs migrated to the bucketed layout
(e.g. offprem_products, ttb_master) MUST be loaded via `--live`, which emits their exact live file
list from the manifest. See sql/03_raw_tables.sql's header and README.md.

Emitted files (idempotent — CREATE … IF NOT EXISTS / CREATE OR REPLACE where safe):
  sql/00_config.template.sql   account-level names + Tigris creds (placeholders — copy to a local file)
  sql/01_database.sql          database UNIFYD + schemas RAW / MASTER / MART
  sql/02_stage.sql             Parquet file format + external stage → Tigris (S3-compatible)
  sql/03_raw_tables.sql        per-source INFER_SCHEMA landing + COPY (all sources, full)
  sql/04_master.sql            typed canonical star DDL + COPY  ← the seed
  sql/05_marts.sql             convenience views over the star
  sql/06_validate.sql          post-load row-count / freshness assertions (named sources must be > 0)
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "unifyd"))
import source_registry as reg  # noqa: E402  (the one shared source of truth)

# ── account-level identifiers (overridable via 00_config; these are the defaults the SQL assumes) ──
DB = "UNIFYD"
SCHEMA_RAW, SCHEMA_MASTER, SCHEMA_MART = "RAW", "MASTER", "MART"
STAGE = "WH"                      # external stage → the warehouse/ prefix in the Tigris bucket
FMT = "PARQUET_FMT"

# ── the named "full" priority sources the seed must land completely (Chris's list) ────────────────
# Total Wine · Kroger (atlas inventory + API UPC seed) · ABC (facets + catalog + taxonomy) · AB InBev
# outlets · Tito's outlets. These lead the raw load and are asserted non-empty in 06_validate.sql.
PRIORITY = {
    "total_wine_products": "Total Wine — full catalog (PerimeterX browser crawl)",
    "kroger_atlas_products": "Kroger — per-store on-hand + dims + ABV (internal atlas)",
    "kroger_products": "Kroger — public API product/UPC seed (atlas GTIN universe)",
    "abc_products": "ABC FW&S — inventory + SearchSpring facets (varietal/region/type)",
    "abc_catalog": "ABC FW&S — BigCommerce catalog (UPC)",
    "source_taxonomy": "ABC FW&S — retailer drill-path taxonomy",
    "ab_outlets": "AB InBev — national retailer locator (where their beer is sold)",
    "vtinfo_titos": "Tito's — where-to-buy outlet locator",
}

# Underscore-prefixed staging Parquet is intermediate (the wide pre-shred union) — never landed in
# Snowflake. Everything else the engine writes IS a real table.
SKIP_TABLES = {"_stage_product", "_stage_outlet"}

# ══════════════════════════════════════════════════════════════════════════════════════════════════
# RAW catalog — every source table, derived from the registry. Schema-agnostic (INFER_SCHEMA), so this
# needs only the NAME + provenance metadata, never a column list.
# ══════════════════════════════════════════════════════════════════════════════════════════════════

# Tables the engine writes that the registry's `tables` lists don't enumerate directly (they're written
# by normalize.py / the aggregator crawlers / the geocoder), but are real warehouse Parquet we want to
# land. Reference + fact feeds, not scrapes.
EXTRA_RAW = [
    ("ubereats_stores", "Uber Eats — geocoded delivery merchants (from ue_crawl)"),
    ("postmates_stores", "Postmates — geocoded delivery merchants (from ue_crawl)"),
    ("geocode_cache", "Address → lat/lng/county_fips cache (persistent)"),
]

# retail_observations is a TIME-SERIES partition set (warehouse/retail_observations/<date>_<src>.parquet),
# not a single file — handled specially (load the whole prefix, cluster by date).
TIMESERIES = {
    "retail_observations": "Dated per-store price + inventory observations (the fact spine; union_by_name)",
}


def raw_catalog():
    """[(table, note, source_ids, priority_bool)] for every source Parquet, from the registry + extras.
    De-duped and sorted with the priority "full" sources first, then alphabetical."""
    by_table = {}
    for s in reg.SOURCES:
        for t in s.get("tables", []):
            if t in SKIP_TABLES or t in TIMESERIES:
                continue
            e = by_table.setdefault(t, {"note": s.get("note", ""), "srcs": []})
            e["srcs"].append(s["id"])
    for b in reg.BUILDS:                          # dim_outlet / src_outlets / dim_sku are covered by MASTER,
        for t in b.get("tables", []):            # so BUILDS tables are intentionally NOT re-landed as RAW.
            by_table.pop(t, None)
    for t, note in EXTRA_RAW:
        by_table.setdefault(t, {"note": note, "srcs": []})
    rows = []
    for t, e in by_table.items():
        note = PRIORITY.get(t) or e["note"] or ""
        rows.append((t, note, sorted(set(e["srcs"])), t in PRIORITY))
    rows.sort(key=lambda r: (not r[3], r[0]))     # priority first, then alphabetical
    return rows


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# MASTER catalog — the canonical star, TYPED. Columns mirror the exact output of the engine's builders:
#   dim_brand/product/item/sku/supplier/vintage   ← master_apply.resolve_hierarchy (build_product_master)
#   src_brands/products/items/skus/outlets, src_summary, dim_product_type, src_pricing/inventory ← normalize
#   dim_outlet                                     ← dim_outlet.build
#   xwalk_source_sku, price_coherence, category_cluster, identity_cluster ← build_product_master
# Types: S=STRING, N=NUMBER(38,0) integer counts/epochs, F=FLOAT (abv/price/geo/size), B=BOOLEAN,
#        A=ARRAY (Parquet LIST from DuckDB list_distinct). `cluster` mirrors the query/partition grain.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
S, N, F, B, A = "STRING", "NUMBER(38,0)", "FLOAT", "BOOLEAN", "ARRAY"

# provenance/audit tail every resolve_hierarchy dim carries
_PROV = [("source_rows", N), ("sources", N), ("source_list", A),
         ("master_created_at", N), ("master_updated_at", N), ("updated_by", S)]

MASTER = [
    dict(name="dim_brand", load="dim_brand", cluster=["brand_key"],
         comment="Brand grain — the top of the product hierarchy. Quality gate: a source row needs a "
                 "brand to be promoted into the master.",
         # brand_group lands at the brand grain (GRAIN_DEFAULTS) — currently always NULL but part of
         # the emitted schema, so it's typed here to future-proof the contract (MATCH_BY_COLUMN_NAME
         # would otherwise silently drop it once the engine starts populating it).
         cols=[("brand_key", S), ("brand", S), ("brand_group", S)] + _PROV),
    dict(name="dim_product", load="dim_product", cluster=["brand_key"],
         comment="Product grain — brand + class/type + distinguisher core. Key aligns TTB and retail "
                 "spellings of the same product.",
         cols=[("product_key", S), ("brand_key", S), ("product_name", S), ("core_name", S),
               ("class_type", S), ("flavor", S), ("abv", F), ("style", S), ("category", S),
               ("origin", S), ("bottled_in", S), ("region", S), ("sub_region", S), ("appellation", S),
               ("varietal", S), ("country", S), ("state", S), ("image", S), ("taste", S), ("body", S),
               ("food_pairing", S), ("expert_rating", S), ("finish", S)] + _PROV),
    dict(name="dim_item", load="dim_item", cluster=["product_key"],
         comment="Item grain — product + size + container.",
         cols=[("item_key", S), ("product_key", S), ("size_ml", F), ("packsize", S),
               ("container", S)] + _PROV),
    dict(name="dim_sku", load="dim_sku", cluster=["item_key"],
         comment="SKU grain — the fully-specified sellable unit (item + pack + UPC). A UPC is its "
                 "global identity; vintage/edition are attributes, not splits.",
         cols=[("sku_key", S), ("item_key", S), ("pack", F), ("upc", S), ("gtin", S),
               ("vintage", S), ("edition", S)] + _PROV),
    dict(name="dim_supplier", load="dim_supplier", cluster=["brand_key"],
         comment="Brand↔supplier association (SCD) with active/inactive dates — persists items across "
                 "acquisition. Not a hierarchy level.",
         cols=[("brand_key", S), ("supplier_name", S), ("active_date", N), ("inactive_date", N),
               ("source_rows", N), ("source_list", A)]),
    dict(name="dim_vintage", load="dim_vintage", cluster=["sku_key"],
         comment="SKU↔vintage association — vintages do NOT multiply SKUs; tracked here so 2020 vs 2021 "
                 "is queryable.",
         cols=[("sku_key", S), ("vintage", S), ("active_date", N), ("source_rows", N),
               ("source_list", A)]),

    # ── outlet master ──
    dict(name="dim_outlet", load="dim_outlet", cluster=["state", "chain"],
         comment="Distinct real outlets — the raw src_outlets book consolidated by address/phone/geo. "
                 "hoodie_outlet_id is our own spine id; vpid (AB InBev/VTInfo) is a cross-ref attribute, "
                 "NEVER the anchor.",
         cols=[("hoodie_outlet_id", S), ("outlet_name", S), ("address", S), ("city", S), ("state", S),
               ("zip", S), ("lat", F), ("lng", F), ("county_fips", S), ("phone", S), ("chain", S),
               ("is_chain", B), ("f_beer", B), ("f_wine", B), ("f_spirits", B), ("f_hemp", B),
               ("f_cannabis", B), ("f_rtd_spirits", B), ("sources", A), ("source_count", N),
               ("record_count", N), ("vpid", S)]),

    # ── source-grain feeds (normalize.py) — one deduped record per (source, grain mnemonic) ──
    dict(name="src_brands", load="src_brands", cluster=["source"],
         comment="Per-source brand records, tagged with the Hoodie brand mnemonic (cross-source "
                 "corroboration without a SKU match).",
         cols=[("source", S), ("source_id", S), ("hoodie_brand", S), ("brand", S), ("name_key", S)]),
    dict(name="src_products", load="src_products", cluster=["source"],
         comment="Per-source product records + derived rich attributes (proof/age/origin_class/volume "
                 "tier/organic/non_alc) and the product-type dimension id.",
         cols=[("source", S), ("source_id", S), ("hoodie_product", S), ("brand", S), ("product_name", S),
               ("name_key", S), ("flavor", S), ("category", S), ("product_type_id", N),
               ("product_type", S), ("class_type", S), ("abv", F), ("proof", F), ("varietal", S),
               ("origin", S), ("origin_class", S), ("region", S), ("age_years", N), ("volume_tier", S),
               ("organic", B), ("non_alc", B), ("image", S)]),
    dict(name="src_items", load="src_items", cluster=["source"],
         comment="Per-source item records (product + size + container).",
         cols=[("source", S), ("source_id", S), ("hoodie_item", S), ("brand", S), ("product_name", S),
               ("name_key", S), ("product_type_id", N), ("product_type", S), ("size_ml", F),
               ("container", S), ("volume_tier", S)]),
    dict(name="src_skus", load="src_skus", cluster=["source"],
         comment="Per-source SKU records with the raw + GTIN-14-normalized UPC and pack shape.",
         cols=[("source", S), ("source_id", S), ("upc", S), ("upc_norm", S), ("hoodie_sku", S),
               ("brand", S), ("product_name", S), ("name_key", S), ("product_type_id", N),
               ("product_type", S), ("size_ml", F), ("container", S), ("pack", F), ("pack_size", N),
               ("pack_type", S)]),
    dict(name="src_outlets", load="src_outlets", cluster=["source", "state"],
         comment="The full book of accounts — an outlet pulled through from EVERY store-bearing source, "
                 "with sell-flags (license + product-carried), chain grouping, and the match keys "
                 "dim_outlet consolidates on.",
         cols=[("source", S), ("store_id", S), ("store_name", S), ("chain", S), ("is_chain", B),
               ("f_beer", B), ("f_wine", B), ("f_spirits", B), ("f_hemp", B), ("f_cannabis", B),
               ("f_rtd_spirits", B), ("flag_basis", S), ("license_conflict", B), ("address", S),
               ("city", S), ("state", S), ("zip", S), ("lat", F), ("lng", F), ("phone", S),
               ("addr_valid", B), ("hoodie_outlet", S), ("name_key", S), ("phone_norm", S),
               ("addr_key", S), ("geo_cell", S), ("county_fips", S)]),
    dict(name="dim_product_type", load="dim_product_type", cluster=[],
         comment="Product-type dimension (Wine/Beer/Spirits/Hemp/Cannabis).",
         cols=[("product_type_id", N), ("product_type", S)]),
    dict(name="src_summary", load="src_summary", cluster=[],
         comment="Per-grain corroboration rollup: records, distinct entities, and how many are seen by "
                 "2+ sources.",
         cols=[("grain", S), ("table", S), ("records", N), ("entities", N), ("corroborated", N)]),

    # ── crosswalk + signal tables (build_product_master) ──
    dict(name="xwalk_source_sku", load="xwalk_source_sku", cluster=["source"],
         comment="(source, source_product_id) → product_key / item_key / sku_key. Resolves UPC-less "
                 "retailer observations (ABC/Binny's/Target/Total Wine) to a master product.",
         cols=[("source", S), ("product_id", S), ("product_key", S), ("item_key", S), ("sku_key", S)]),
    dict(name="price_coherence", load="price_coherence", cluster=[],
         comment="Soft cross-source unit-price agreement per product_key: price-corroborated matches + "
                 "over-merge/size DQ (divergent >2x). Never a key.",
         cols=[("product_key", S), ("n_priced", N), ("n_sources", N), ("median_unit_price", F),
               ("min_unit_price", F), ("max_unit_price", F), ("spread_ratio", F),
               ("agree_within_band", N), ("price_corroborated", B), ("divergent", B)]),
    dict(name="category_cluster", load="category_cluster", cluster=[],
         comment="UPC-free identity via the category discriminator tree — the match path for the ~88% "
                 "of rows with no barcode.",
         cols=[("category_key", S), ("canon", S), ("members", N), ("n_sources", N), ("sources", S),
               ("sample_name", S), ("corroborated", B)]),
    dict(name="identity_cluster", load="identity_cluster", cluster=[],
         comment="Resolved spine identity — union-find over category clusters (price/UPC-confirmed "
                 "merges). Tier 1 = commercial-corroborated master; Tier 0 = TTB-only (TTB is a "
                 "contributor, not the spine).",
         cols=[("cluster_id", S), ("members", N), ("n_sources", N), ("commercial_sources", N),
               ("has_ttb", B), ("tier", N), ("sources", S), ("sample_name", S), ("corroborated", B)]),

    # ── dated fact feeds (normalize_facts) ──
    dict(name="src_pricing", load="src_pricing", cluster=["date"],
         comment="Dated price observations keyed (source, store, product, upc, date).",
         cols=[("date", S), ("source", S), ("store_id", S), ("product_id", S), ("upc", S),
               ("price", F), ("on_promo", S), ("promo", S)]),
    dict(name="src_inventory", load="src_inventory", cluster=["date"],
         comment="Dated inventory observations keyed (source, store, product, upc, date).",
         cols=[("date", S), ("source", S), ("store_id", S), ("product_id", S), ("upc", S),
               ("qty", F), ("in_stock", S), ("stock_level", S)]),
]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Live-mode helpers — read the real warehouse to refine the offline build.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
def live_layout():
    """{table: {rows, files}} from the actual warehouse. `files` is the stage-relative list a COPY
    should read: [<name>.parquet] for v1, or the manifest's ACTIVE part files for bucketed (v2), so a
    bucketed table never double-loads a superseded part. Empty dict if the warehouse isn't reachable."""
    try:
        import warehouse
    except Exception as e:                        # pragma: no cover
        print("!! --live: cannot import warehouse (%s); emitting the offline build" % e, file=sys.stderr)
        return {}
    out = {}
    for d in warehouse.list_datasets():
        name = d["name"]
        man = warehouse.read_manifest(name)
        if man and man.get("layout") == "bucketed":
            files = [f for p in (man.get("parts") or {}).values() for f in (p.get("files") or [])]
        else:
            files = ["%s.parquet" % name]
        out[name] = {"rows": d.get("rows", 0), "files": files, "bucketed": bool(man)}
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# SQL emitters
# ══════════════════════════════════════════════════════════════════════════════════════════════════
_BANNER = ("-- ===================================================================================\n"
           "-- GENERATED by snowflake/build_snowflake_sql.py — do not edit by hand.\n"
           "-- Regenerate:  python snowflake/build_snowflake_sql.py [--live]\n"
           "-- Source of truth: unifyd/source_registry.py (raw) + the typed MASTER catalog in the\n"
           "-- generator (canonical star). See snowflake/README.md.\n"
           "-- ===================================================================================\n")


# Snowflake reserved keywords that can't be a bare column identifier (subset that could plausibly show
# up as an engine column). Quoted UPPERCASE so the column behaves like a normal uppercase identifier —
# CASE_INSENSITIVE COPY still matches the Parquet's lowercase column, and references just add quotes.
_RESERVED = {"table", "date", "order", "values", "group", "select", "from", "rows", "row", "start",
             "column", "check", "constraint", "view", "user", "current", "all", "is", "in", "to"}


def q(col):
    """Quote a column identifier iff it's a reserved word (else leave it bare)."""
    return '"%s"' % col.upper() if col.lower() in _RESERVED else col


def _fqtn(schema, table):
    return "%s.%s.%s" % (DB, schema, table.upper())


def emit_config():
    return _BANNER + """--
-- 00_config.template.sql — one-time account setup + how to fill in the Tigris credentials.
--
-- Snowflake DDL (CREATE STAGE) does NOT accept session variables or string concatenation in its
-- URL / ENDPOINT / CREDENTIALS clauses — those must be string literals. So 02_stage.sql carries
-- ${...} placeholders (envsubst-style) instead. Fill them one of two ways:
--
--   A) envsubst (keeps secrets out of the committed file):
--        export TIGRIS_BUCKET=hoodie-warehouse TIGRIS_PREFIX=warehouse \\
--               TIGRIS_ENDPOINT=fly.storage.tigris.dev \\
--               TIGRIS_KEY_ID=... TIGRIS_SECRET=...
--        envsubst < sql/02_stage.sql | snowsql -a <acct> -u <user>          # then run 01,03,04,05,06
--      (the Tigris values are the AWS_* / BUCKET_NAME / WAREHOUSE_PREFIX the engine already uses —
--       see unifyd/warehouse.py; on Fly: `fly storage list` / the app secrets.)
--
--   B) edit sql/02_stage.sql inline and replace each ${...} with the literal value. Do NOT commit
--      real credentials back — 02_stage.sql is committed with placeholders on purpose.
--
-- Recommended secure alternative to inline keys: create the stage once by hand from a Snowflake
-- SECRET, or run the whole build under a role whose stage already exists. Never paste keys into a
-- shared worksheet history.

-- ── one-time account setup ───────────────────────────────────────────────────────────────────────
-- Compute to run the load with (XS is plenty for the seed; it auto-suspends):
CREATE WAREHOUSE IF NOT EXISTS UNIFYD_LOAD
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE
  COMMENT = 'Ephemeral compute for the Hoodie → Unifyd Snowflake load.';
USE WAREHOUSE UNIFYD_LOAD;

-- Then run, in order:  01_database → 02_stage (substituted) → 03_raw → 04_master → 05_marts → 06_validate
"""


def emit_database():
    return _BANNER + """--
-- 01_database.sql — the database + the three schemas the build lands into.
CREATE DATABASE IF NOT EXISTS {db}
  COMMENT = 'Hoodie → Unifyd master data. Seeded from the DuckDB-on-Tigris warehouse.';

CREATE SCHEMA IF NOT EXISTS {db}.{raw}
  COMMENT = 'Landing zone — one table per source Parquet, loaded schema-agnostically (INFER_SCHEMA).';
CREATE SCHEMA IF NOT EXISTS {db}.{master}
  COMMENT = 'The canonical star — dim_brand/product/item/sku, dim_outlet, src_<grain> feeds, signals. The seed to Unifyd.';
CREATE SCHEMA IF NOT EXISTS {db}.{mart}
  COMMENT = 'Convenience views over the star (product/outlet full-width, coverage).';
""".format(db=DB, raw=SCHEMA_RAW, master=SCHEMA_MASTER, mart=SCHEMA_MART)


def emit_stage():
    return _BANNER + """--
-- 02_stage.sql — the Parquet file format + the external stage → Tigris (S3-compatible).
--
-- The ${{...}} tokens are placeholders — substitute them before running (see 00_config: `envsubst`
-- or edit inline). Snowflake DDL can't take session vars here, so these must become real literals.
USE SCHEMA {db}.{raw};

CREATE FILE FORMAT IF NOT EXISTS {fmt}
  TYPE = PARQUET
  COMPRESSION = AUTO
  BINARY_AS_TEXT = FALSE          -- keep parquet BYTE_ARRAY strings as text, don't hex them
  TRIM_SPACE = FALSE
  COMMENT = 'Parquet as written by unifyd/warehouse.py (zstd/snappy, DuckDB/pyarrow).';

-- S3-compatible external stage. s3compat:// + ENDPOINT is Snowflake's supported path for non-AWS S3
-- stores like Tigris. Credentials are the Tigris (AWS_*) key pair the engine uses (unifyd/warehouse.py).
-- Defaults: TIGRIS_PREFIX=warehouse, TIGRIS_ENDPOINT=fly.storage.tigris.dev.
CREATE OR REPLACE STAGE {stage}
  URL = 's3compat://${{TIGRIS_BUCKET}}/${{TIGRIS_PREFIX}}/'
  ENDPOINT = '${{TIGRIS_ENDPOINT}}'
  CREDENTIALS = ( AWS_KEY_ID = '${{TIGRIS_KEY_ID}}'  AWS_SECRET_KEY = '${{TIGRIS_SECRET}}' )
  FILE_FORMAT = {fmt}
  COMMENT = 'Hoodie warehouse Parquet (warehouse/ prefix in the Tigris bucket).';

-- Smoke: list a couple of the named "full" sources so a bad cred/endpoint fails HERE, loudly.
LIST @{stage}/total_wine_products.parquet;
LIST @{stage}/ab_outlets.parquet;
""".format(db=DB, raw=SCHEMA_RAW, fmt=FMT, stage=STAGE)


def _raw_block(table, note, srcs, priority, files=None, rows=None):
    """One RAW table's INFER_SCHEMA create + COPY. `files` (live mode) overrides the default single
    file — used for bucketed tables (the manifest's active parts) so no superseded part is re-loaded."""
    fqtn = _fqtn(SCHEMA_RAW, table)
    loc = "@%s/%s.parquet" % (STAGE, table)                      # v1 default
    header = "-- %s%s\n" % (table, "   [FULL — priority seed]" if priority else "")
    if note:
        header += "--   %s\n" % note
    if srcs:
        header += "--   registry source(s): %s\n" % ", ".join(srcs)
    if rows is not None:
        header += "--   warehouse rows at generation: %s\n" % f"{rows:,}"
    if files and files != ["%s.parquet" % table]:               # bucketed / multi-file (live mode)
        header += "--   bucketed layout — loading %d active manifest part(s)\n" % len(files)
        copy_from = "(\n" + ",\n".join("    '@%s/%s'" % (STAGE, f) for f in files) + "\n  )"
        infer_loc = "'@%s/%s'" % (STAGE, files[0])
    else:
        copy_from = "'%s'" % loc
        infer_loc = "'%s'" % loc
    return header + (
        "CREATE TABLE IF NOT EXISTS {fqtn}\n"
        "  USING TEMPLATE (\n"
        "    SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*))\n"
        "    FROM TABLE(INFER_SCHEMA(LOCATION => {infer}, FILE_FORMAT => '{fmt}')));\n"
        "COPY INTO {fqtn}\n"
        "  FROM {cfrom}\n"
        "  FILE_FORMAT = (FORMAT_NAME = '{fmt}')\n"
        "  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE\n"
        "  ON_ERROR = ABORT_STATEMENT;\n"
    ).format(fqtn=fqtn, infer=infer_loc, cfrom=copy_from, fmt=FMT)


def emit_raw(layout=None):
    cat = raw_catalog()
    parts = [_BANNER, """--
-- 03_raw_tables.sql — land every source Parquet into {db}.{raw}, schema-agnostically.
--
-- Each table is CREATEd from the Parquet's own schema (INFER_SCHEMA) and loaded with
-- MATCH_BY_COLUMN_NAME, so a scraper widening its columns needs no change here — exactly the
-- drift-tolerance the DuckDB read_parquet path has today. Re-running COPY is safe: Snowflake skips
-- files already loaded (load metadata) unless FORCE=TRUE.
--
-- LAYOUT: this offline build assumes the v1 single-file layout (<name>.parquet), correct for every
-- named "full" source and the whole master layer. Tables migrated to the bucketed (v2) layout (big
-- accumulating catalogs) MUST be regenerated with `--live`, which emits their exact active part list
-- from the manifest — blindly loading a bucketed directory would double-load superseded parts.
{live_note}
USE SCHEMA {db}.{raw};
""".format(db=DB, raw=SCHEMA_RAW,
           live_note="-- (this file WAS generated with --live: bucketed tables carry their resolved part list.)\n"
                     if layout else "")]

    # time-series prefix loads first (special: whole partition set → one table, clustered by date)
    parts.append("\n-- ── time-series fact partitions (load the whole prefix; union_by_name) ──")
    for t, note in TIMESERIES.items():
        fqtn = _fqtn(SCHEMA_RAW, t)
        rows = (layout.get(t, {}) or {}).get("rows") if layout else None
        hdr = "\n-- %s\n--   %s\n" % (t, note)
        if rows is not None:
            hdr += "--   warehouse rows at generation: %s\n" % f"{rows:,}"
        parts.append(hdr + (
            "CREATE TABLE IF NOT EXISTS {fqtn}\n"
            "  USING TEMPLATE (\n"
            "    SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*))\n"
            "    FROM TABLE(INFER_SCHEMA(LOCATION => '@{stage}/{t}/', FILE_FORMAT => '{fmt}',\n"
            "                            IGNORE_CASE => TRUE)));\n"
            "COPY INTO {fqtn}\n"
            "  FROM '@{stage}/{t}/'\n"
            "  FILE_FORMAT = (FORMAT_NAME = '{fmt}')\n"
            "  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE\n"
            "  PATTERN = '.*[.]parquet'\n"
            "  ON_ERROR = CONTINUE;\n"                       # per-source schema drift across partitions
        ).format(fqtn=fqtn, stage=STAGE, t=t, fmt=FMT))

    parts.append("\n-- ── source catalogs ──")
    for table, note, srcs, priority in cat:
        info = (layout or {}).get(table) or {}
        parts.append("\n" + _raw_block(table, note, srcs, priority,
                                       files=info.get("files"), rows=info.get("rows")))
    return "\n".join(parts)


def _create_table(schema, spec):
    cols = [(q(c), t) for c, t in spec["cols"]]        # quote reserved-word column names
    width = max(len(c) for c, _ in cols)
    lines = ",\n".join("  %-*s %s" % (width, c, t) for c, t in cols)
    fqtn = _fqtn(schema, spec["name"])
    out = "-- %s\n--   %s\n" % (spec["name"], spec["comment"])
    out += "CREATE OR REPLACE TABLE %s (\n%s\n)" % (fqtn, lines)
    if spec.get("cluster"):
        out += "\n  CLUSTER BY (%s)" % ", ".join(q(c) for c in spec["cluster"])
    out += "\n  COMMENT = '%s';\n" % spec["comment"].replace("'", "''")
    return out


def _copy_master(schema, spec, layout=None):
    fqtn = _fqtn(schema, spec["name"])
    load = spec["load"]
    info = (layout or {}).get(load) or {}
    files = info.get("files")
    if files and files != ["%s.parquet" % load]:               # bucketed (live)
        cfrom = "(\n" + ",\n".join("    '@%s.%s.%s/%s'" % (DB, SCHEMA_RAW, STAGE, f) for f in files) + "\n  )"
    else:
        cfrom = "'@%s.%s.%s/%s.parquet'" % (DB, SCHEMA_RAW, STAGE, load)
    return (
        "COPY INTO {fqtn}\n"
        "  FROM {cfrom}\n"
        "  FILE_FORMAT = (FORMAT_NAME = '{db}.{raw}.{fmt}')\n"
        "  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE\n"
        "  ON_ERROR = ABORT_STATEMENT;\n"
    ).format(fqtn=fqtn, cfrom=cfrom, db=DB, raw=SCHEMA_RAW, fmt=FMT)


def emit_master(layout=None):
    parts = [_BANNER, """--
-- 04_master.sql — the canonical star, typed. THE SEED TO UNIFYD.
--
-- These columns mirror the exact output of the engine's builders (build_product_master.py /
-- normalize.py / dim_outlet.py). Unlike RAW, these are stable contracts, so the DDL is explicit —
-- real types, clustering keys that mirror the query grain. Loaded from the SAME Tigris stage.
--
-- Order matters only for readability; there are no enforced FKs (Snowflake doesn't enforce them),
-- but the *_key columns join the star: dim_sku.item_key → dim_item.item_key → dim_product.product_key
-- → dim_brand.brand_key; xwalk_source_sku bridges raw source rows to those keys.
USE SCHEMA {db}.{master};
""".format(db=DB, master=SCHEMA_MASTER)]
    for spec in MASTER:
        parts.append("\n" + _create_table(SCHEMA_MASTER, spec) + _copy_master(SCHEMA_MASTER, spec, layout))
    return "\n".join(parts)


def emit_marts():
    return _BANNER + """--
-- 05_marts.sql — convenience views over the star (the shapes the apps ask for).
USE SCHEMA {db}.{mart};

-- Full-width product: SKU joined up through item → product → brand. One row per sellable unit.
CREATE OR REPLACE VIEW v_product_full COMMENT = 'SKU × item × product × brand — the whole product line.' AS
SELECT s.sku_key, s.upc, s.gtin, s.pack, s.vintage, s.edition,
       i.item_key, i.size_ml, i.container,
       p.product_key, p.product_name, p.core_name, p.class_type, p.category, p.abv, p.varietal,
       p.origin, p.region, p.appellation, p.image,
       b.brand_key, b.brand,
       s.sources AS sku_sources, s.source_rows AS sku_source_rows
FROM {db}.{master}.dim_sku    s
LEFT JOIN {db}.{master}.dim_item    i ON i.item_key    = s.item_key
LEFT JOIN {db}.{master}.dim_product p ON p.product_key = i.product_key
LEFT JOIN {db}.{master}.dim_brand   b ON b.brand_key   = p.brand_key;

-- Outlet book with sell-flags and chain grouping — the account map.
CREATE OR REPLACE VIEW v_outlet_full COMMENT = 'Distinct outlets with sell-flags, chain, geo, corroboration.' AS
SELECT hoodie_outlet_id, outlet_name, chain, is_chain, address, city, state, zip, lat, lng,
       county_fips, phone, f_beer, f_wine, f_spirits, f_hemp, f_cannabis, f_rtd_spirits,
       source_count, sources, vpid
FROM {db}.{master}.dim_outlet;

-- Source coverage: how many distinct entities each source contributes at each grain, and how many
-- are corroborated by 2+ sources. Reads the corroboration rollup normalize.py already computes.
CREATE OR REPLACE VIEW v_source_coverage COMMENT = 'Per-grain entity + corroboration counts by source.' AS
SELECT grain, "TABLE" AS src_table, records, entities, corroborated,
       ROUND(100.0 * corroborated / NULLIF(entities, 0), 1) AS corroborated_pct
FROM {db}.{master}.src_summary;

-- Chain rollup: outlet count + sell-flag reach per chain banner (Total Wine, Kroger, ABC, …).
CREATE OR REPLACE VIEW v_chain_rollup COMMENT = 'Outlets per chain banner with sell-flag reach.' AS
SELECT COALESCE(NULLIF(chain, ''), '(independent)') AS chain,
       COUNT(*) AS outlets,
       COUNT_IF(f_beer) AS sells_beer, COUNT_IF(f_wine) AS sells_wine,
       COUNT_IF(f_spirits) AS sells_spirits, COUNT_IF(f_rtd_spirits) AS rtd_spirits_candidates
FROM {db}.{master}.dim_outlet
GROUP BY 1
ORDER BY outlets DESC;
""".format(db=DB, master=SCHEMA_MASTER, mart=SCHEMA_MART)


def emit_validate():
    priority_checks = "\nUNION ALL\n".join(
        "SELECT '%s' AS table_name, COUNT(*) AS n_rows, IFF(COUNT(*) > 0, 'OK', 'EMPTY — investigate') AS status "
        "FROM %s" % (t, _fqtn(SCHEMA_RAW, t)) for t in PRIORITY)
    return _BANNER + """--
-- 06_validate.sql — post-load assertions. The named "full" sources MUST be non-empty; the star must
-- have landed; the joins must resolve. Run after 03/04. Eyeball the STATUS column.

-- 1) Named priority sources — every one must be > 0 rows (an EMPTY here = a failed load, not a rebuild).
{priority}
ORDER BY table_name;

-- 2) The canonical star landed.
SELECT 'dim_brand'   AS tbl, COUNT(*) FROM {db}.{master}.dim_brand
UNION ALL SELECT 'dim_product', COUNT(*) FROM {db}.{master}.dim_product
UNION ALL SELECT 'dim_item',    COUNT(*) FROM {db}.{master}.dim_item
UNION ALL SELECT 'dim_sku',     COUNT(*) FROM {db}.{master}.dim_sku
UNION ALL SELECT 'dim_outlet',  COUNT(*) FROM {db}.{master}.dim_outlet
UNION ALL SELECT 'xwalk_source_sku', COUNT(*) FROM {db}.{master}.xwalk_source_sku
ORDER BY tbl;

-- 3) Referential sanity — every SKU's item resolves up the hierarchy (orphans = a broken load).
SELECT COUNT(*) AS skus,
       COUNT_IF(i.item_key IS NULL) AS skus_missing_item,
       COUNT_IF(p.product_key IS NULL) AS items_missing_product
FROM {db}.{master}.dim_sku s
LEFT JOIN {db}.{master}.dim_item    i ON i.item_key    = s.item_key
LEFT JOIN {db}.{master}.dim_product p ON p.product_key = i.product_key;

-- 4) Outlet corroboration — how many of the named outlet sources made it into dim_outlet.
SELECT COUNT(*) AS distinct_outlets,
       COUNT_IF(source_count >= 2) AS corroborated_2plus,
       COUNT_IF(ARRAY_CONTAINS('ab-inbev'::VARIANT, sources)) AS from_ab_inbev,
       COUNT_IF(vpid <> '') AS with_vpid
FROM {db}.{master}.dim_outlet;
""".format(priority=priority_checks, db=DB, master=SCHEMA_MASTER)


FILES = [
    ("00_config.template.sql", lambda layout: emit_config()),
    ("01_database.sql", lambda layout: emit_database()),
    ("02_stage.sql", lambda layout: emit_stage()),
    ("03_raw_tables.sql", lambda layout: emit_raw(layout)),
    ("04_master.sql", lambda layout: emit_master(layout)),
    ("05_marts.sql", lambda layout: emit_marts()),
    ("06_validate.sql", lambda layout: emit_validate()),
]


def main():
    ap = argparse.ArgumentParser(description="Generate the Snowflake load build from the registry.")
    ap.add_argument("--live", action="store_true",
                    help="read the warehouse to include every present table + resolve bucketed layouts")
    ap.add_argument("--out", default=os.path.join(HERE, "sql"), help="output dir (default snowflake/sql)")
    a = ap.parse_args()
    layout = live_layout() if a.live else None
    os.makedirs(a.out, exist_ok=True)
    for fname, fn in FILES:
        path = os.path.join(a.out, fname)
        with open(path, "w") as f:
            f.write(fn(layout))
        print("wrote %s" % os.path.relpath(path, os.path.dirname(HERE)))
    n_raw = len(raw_catalog()) + len(TIMESERIES)
    print("\n%d raw tables · %d master tables · %d priority 'full' sources%s"
          % (n_raw, len(MASTER), len(PRIORITY), "  (LIVE: refined from warehouse)" if layout else ""))


if __name__ == "__main__":
    main()
