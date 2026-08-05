# Snowflake morning drop (RAW + MASTER mirror) — `snowflake-load`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `snowflake-load` |
| Runs | `import snowflake_load as m; m.run()` |
| Module | `unifyd/snowflake_load.py` — 60 lines |
| Cadence | every 24h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 2048 MB / 14400 s |
| Shards | 1 |
| Credentials required | `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` |
| Capabilities | none |
| Unit test | `unifyd/snowflake_load_test.py` |


**Registry note.** change-aware COPY into UNIFYD (RAW per-source + src_ grains + star); verify-lands snowflake_load_runs; needs SNOWFLAKE_ACCOUNT/USER + key or password as Fly secrets


## 2. Transport

_No literal endpoint constant in `snowflake_load.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `snowflake_load_runs`

4 rows · 10 columns


| column | type | filled |
|---|---|---|
| `ts` | `BIGINT` | 100.0% |
| `duration_s` | `DOUBLE` | 100.0% |
| `rows_total` | `BIGINT` | 100.0% |
| `tables` | `BIGINT` | 100.0% |
| `raw_tables` | `BIGINT` | 100.0% |
| `raw_rows` | `BIGINT` | 100.0% |
| `master_tables` | `BIGINT` | 100.0% |
| `master_rows` | `BIGINT` | 100.0% |
| `scope` | `VARCHAR` | 100.0% |
| `host` | `VARCHAR` | 100.0% |

Fill measured over **full table** (4 rows).

**Written by** `snowflake_load.py:53` (write_accumulate)


## 4. `snowflake_load.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
snowflake_load.py — the registry-schedulable wrapper around snowflake/run_load.py.

The Snowflake morning drop runs as a normal registry BUILD (source_registry.BUILDS id
"snowflake-load"): the hourly Fly dispatcher gives it its own ephemeral machine, run_one
verify-lands it, and the Data Console / health digest see it like every other job.

WHY a shim (and not `code="import run_load..."` directly): run_one's verify-landing checks a
WAREHOUSE table's row delta, and a job that only writes to Snowflake would read "current"/"empty"
forever. So after a successful drop this lands one row per run in `snowflake_load_runs` — a
small single-file accumulate table keyed on ts (NOT write_partition: row_count_strict only reads
single-file footers / bucket manifests, so a partitioned run table would be invisible to the
verifier). One writer at a time is guaranteed by the dispatcher (a live snowflake-load machine
suppresses respawn), so the accumulate read-modify-write is race-free here. That row gives
run_one a real +1 delta → status "ok", and doubles as the queryable run history for the load.

A failed load raises — the run ledger records "failed", never a silent ok.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
