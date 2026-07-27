# Snowflake load — the seed to Unifyd

A staged SQL build that loads everything the Hoodie warehouse holds into Snowflake: every source
catalog, every outlet book, and the canonical star (`dim_brand/product/item/sku`, `dim_outlet`, the
`src_<grain>` feeds, the identity/price signals). This is the **seed to Unifyd** — the first slice of
master data landed in a real warehouse, with the named "full" sources (Total Wine, Kroger, ABC, AB
InBev outlets, Tito's outlets) guaranteed complete.

It exists because the Parquet layout was **designed to be Snowflake-loadable** — see NRT-PLAN.md §2
("the Parquet layout above is deliberately Snowflake-loadable (same partitioning maps to clustering
keys), so migration is a load, not a rewrite"). This build is that load. It does **not** replace
DuckDB-on-Tigris (still the engine); it lands a copy in Snowflake for cross-grain joins, BI, and
whatever Unifyd is built on next.

## What it is (and isn't)

- **Generated, not hand-kept.** `build_snowflake_sql.py` derives the whole build from the same single
  sources of truth the engine uses: `unifyd/source_registry.py` for the raw source list, and the
  typed catalog in the generator (mirroring `build_product_master.py` / `normalize.py` /
  `dim_outlet.py`) for the canonical star. Add a source in the registry → regenerate → it's in the
  load. No parallel list to drift.
- **SQL only.** This stages the DDL + `COPY` statements. It doesn't connect to Snowflake or move
  bytes itself — you run the emitted `sql/*.sql` with your own client (`snowsql`, the Snowflake CLI,
  a worksheet, dbt, whatever). Nothing here touches production.

## The three schemas

```
UNIFYD.RAW      one landing table per source Parquet — loaded schema-agnostically (INFER_SCHEMA)
UNIFYD.MASTER   the canonical star — typed, explicit DDL, clustering keys   ← the seed
UNIFYD.MART     convenience views over the star (product/outlet full-width, coverage, chain rollup)
```

**Why RAW is schema-agnostic.** Scraper output drifts on purpose — `total_wine_products` alone is
written by three code paths whose columns are unioned at the Parquet level, and `ca_outlets` takes its
columns straight from a CSV header at runtime. Hand-typed DDL would be wrong the day it's written. So
each RAW table is created from the Parquet's own footer via `INFER_SCHEMA` and loaded with
`MATCH_BY_COLUMN_NAME` — a scraper can widen its columns and the next load just picks them up, exactly
like the DuckDB `read_parquet` path does today.

**Why MASTER is typed.** The star is a stable contract, not scraper output. Its columns come from the
engine's builders and change deliberately, so the DDL is explicit — real types, clustering keys that
mirror the query grain. That's what makes it a seed and not just a dump. `MATCH_BY_COLUMN_NAME` still
tolerates the engine adding a column (it's ignored until you regenerate), so a benign engine change
never breaks the load.

## The named "full" sources

These lead the raw load and are asserted non-empty in `06_validate.sql`:

| Source | Table(s) | What lands |
|---|---|---|
| **Total Wine** | `total_wine_products` | Full catalog (varietal/origin/region/appellation, price, ABV) |
| **Kroger** | `kroger_atlas_products`, `kroger_products` | Per-store on-hand + dims + ABV (internal atlas) · public-API UPC seed |
| **ABC FW&S** | `abc_products`, `abc_catalog`, `source_taxonomy` | Facet catalog + BigCommerce catalog (UPC) + drill-path taxonomy |
| **AB InBev outlets** | `ab_outlets` | National retailer locator (where their beer is sold) |
| **Tito's outlets** | `vtinfo_titos` | Where-to-buy outlet locator |

Everything else the registry knows about lands too (Binny's, Spec's, Walmart, Target, Publix, the
aggregator/off-premise feeds, the control-state price books, TTB, hemp, the dated
`retail_observations` fact partitions, …) — "all data we have."

## Run it

Prereqs: a Snowflake account + a role that can `CREATE DATABASE/SCHEMA/STAGE/TABLE`, and the Tigris
credentials the engine uses (`AWS_ENDPOINT_URL_S3`, `BUCKET_NAME`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `WAREHOUSE_PREFIX` — see `unifyd/warehouse.py`; on Fly: `fly storage list`
and the app secrets).

```bash
# 1) one-time account setup (compute warehouse); read the header for the credential options
snowsql -a <account> -u <user> -f sql/00_config.template.sql

# 2) the stage carries ${...} placeholders (Snowflake DDL can't take session vars) — fill them.
#    envsubst keeps the secrets out of the committed file:
export TIGRIS_BUCKET=<bucket> TIGRIS_PREFIX=warehouse \
       TIGRIS_ENDPOINT=fly.storage.tigris.dev \
       TIGRIS_KEY_ID=<key> TIGRIS_SECRET=<secret>

snowsql -a <account> -u <user> -f sql/01_database.sql
envsubst < sql/02_stage.sql | snowsql -a <account> -u <user>     # LISTs two files — fails loud on bad creds
snowsql -a <account> -u <user> -f sql/03_raw_tables.sql          # all sources → UNIFYD.RAW
snowsql -a <account> -u <user> -f sql/04_master.sql              # the canonical star → UNIFYD.MASTER
snowsql -a <account> -u <user> -f sql/05_marts.sql               # views → UNIFYD.MART
snowsql -a <account> -u <user> -f sql/06_validate.sql            # eyeball the STATUS column
```

## Daily refresh — the morning drop

Built to run every morning after the overnight scrape pass, idempotently, at scale (tens of millions
of rows is a routine bulk `COPY` for Snowflake — the volume is trivial; correctness is the thing that
matters). One command:

```bash
export SNOW_CONN=<snowsql connection name>          # ~/.snowsql/config (account/user/role/warehouse)
export TIGRIS_BUCKET=<bucket> TIGRIS_KEY_ID=<key> TIGRIS_SECRET=<secret>
export AWS_ENDPOINT_URL_S3=https://fly.storage.tigris.dev BUCKET_NAME=$TIGRIS_BUCKET \
       AWS_ACCESS_KEY_ID=$TIGRIS_KEY_ID AWS_SECRET_ACCESS_KEY=$TIGRIS_SECRET   # for the --live regen
./load.sh                # regenerates from the live warehouse, then loads; prints the record count
./load.sh --dry-run      # regenerate + show the plan, run nothing
```

**Why it's safe to re-run every day (no duplicates).** Two refresh disciplines, matched to how the
engine writes each table:

- **Source catalogs** (Total Wine, Kroger, ABC, the off-premise feeds, …) are **rewritten in place**
  by the engine (`write_accumulate` / overwrite reuse the same filename). So RAW loads them with
  `CREATE OR REPLACE TABLE … + COPY … FORCE = TRUE` — a **full refresh** that mirrors the current
  file. Re-inferring the schema each run also absorbs scraper drift. A naive `IF NOT EXISTS` + append
  would double-load the rewritten file every morning; this can't.
- **Time-series** (`retail_observations`) are **write-once dated partitions**, so RAW loads them with
  `CREATE IF NOT EXISTS` + append `COPY` — load metadata skips what's already in, only the new day
  lands, history accumulates. This is the incremental path.
- **The master star** is `CREATE OR REPLACE` + `COPY` — a clean rebuild from the engine's freshly
  built dims each morning.

**Change-aware — only reload what actually moved.** A store doesn't swap its whole catalog nightly;
the file object is rewritten but the data barely changes. So under `--live` the generator diffs each
table's signature (object mtime for single-file, manifest version for bucketed, row count) against a
**load ledger** persisted in the warehouse (`_snowflake/load_state.json`) and emits a refresh **only
for the tables that changed** — everything else becomes a `-- … UNCHANGED, skipped` comment, and
tables not yet scraped are skipped too. `load.sh` commits the ledger **only after a successful load**
(`--commit-state`), so a failed load never marks a table clean. First run (empty ledger) = full seed;
every morning after touches just the deltas. The offline committed build under `sql/` is always the
full set (no ledger) — the change-aware skipping is a `--live`-only optimization.

`06_validate.sql` prints the headline at the end of every run — total records loaded and the
per-table breakdown, straight from `INFORMATION_SCHEMA.ROW_COUNT` (no scans) — so "how many records
did we drop this morning" is answered by the load itself.

**Run `load.sh` where the warehouse is reachable** (a box with `snowsql` + the Tigris env) — it does
the `--live` regen so only present, changed tables load, bucketed catalogs resolve to their active
parts, and the counts are real.

### On the Fly machine (no snowsql) — `run_load.py`

The Fly image is `python:3.12-slim` with no `snowsql` binary, so the load runs there via
**`run_load.py`** — the Python-connector twin of `load.sh` (same change-aware regen → load →
commit-ledger flow, executed through `snowflake-connector-python`). It **reuses the Tigris creds the
app already has** (`BUCKET_NAME`, `AWS_*`), so the only new secrets are the Snowflake ones:

```bash
fly secrets set -a hoodie-suite \
  SNOWFLAKE_ACCOUNT=<acct> SNOWFLAKE_USER=<user> SNOWFLAKE_PASSWORD=<pw>   # or SNOWFLAKE_PRIVATE_KEY=<PEM>
# optional: SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE (default UNIFYD_LOAD)
```

Schedule it as its **own daily Fly Machine** (separate from the serving machine — the prod site is
never touched), built from the same image:

```bash
fly machine run -a hoodie-suite --schedule daily --vm-memory 2048 \
  sh -lc "pip install -q snowflake-connector-python && python /app/snowflake/run_load.py"
```

Run once by hand first to confirm creds/schema: `fly ssh console -a hoodie-suite -C \
"sh -lc 'pip install -q snowflake-connector-python && python /app/snowflake/run_load.py --dry-run'"`,
then drop `--dry-run`. It prints the total records loaded at the end.

## Regenerate

```bash
python build_snowflake_sql.py            # OFFLINE (default) — the committed build under sql/
python build_snowflake_sql.py --live     # read the warehouse to refine (see below)
```

**Offline** (what's committed) derives the build from the registry + the typed catalog. It assumes the
v1 single-file Parquet layout (`<name>.parquet`) — correct for every named source and the whole master
layer.

**`--live`** additionally reads the actual warehouse (Tigris or the local-disk fallback) to:
- include **every table actually present**, not just what the registry enumerates;
- resolve tables migrated to the **bucketed (v2) layout** (big accumulating catalogs like
  `offprem_products` / `ttb_master`) to their manifest's **active part files**, so a `COPY` never
  double-loads a superseded part — blindly loading a bucketed directory would;
- annotate each table with its **row count** at generation time.

Run `--live` from a host with warehouse access (the Fly machine, or locally with the `AWS_*` env set)
before loading if any priority/large table is bucketed. `python -c "import warehouse; ..."` deps
(`pyarrow`, `duckdb`) are only needed for `--live`.

## Credential hygiene

`sql/02_stage.sql` is committed with `${...}` placeholders **on purpose** — never commit real keys
back. Fill them via `envsubst` (above) or edit a local copy; `*.local.sql` is git-ignored. The
`s3compat://` + `ENDPOINT` stage form is Snowflake's supported path for non-AWS S3 stores like Tigris;
credentials are inline on the stage (S3-compatible stages don't support storage integrations), so
prefer creating the stage under a controlled role and keep keys out of shared worksheet history.

## Files

```
build_snowflake_sql.py   the generator (single source of truth for the build)
load.sh                  the morning drop via snowsql — regenerate --live, load, commit ledger
run_load.py              the morning drop via snowflake-connector-python (the Fly machine; no snowsql)
requirements-load.txt    the one extra dep for run_load.py (snowflake-connector-python)
sql/00_config.template.sql   account setup + how to fill the Tigris credentials
sql/01_database.sql          database UNIFYD + schemas RAW / MASTER / MART
sql/02_stage.sql             Parquet file format + external stage → Tigris (${...} placeholders)
sql/03_raw_tables.sql        per-source INFER_SCHEMA landing + COPY (all sources, full)
sql/04_master.sql            typed canonical star DDL + COPY  ← the seed
sql/05_marts.sql             convenience views over the star
sql/06_validate.sql          post-load row-count / freshness assertions
```
