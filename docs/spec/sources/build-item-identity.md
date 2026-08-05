# Item identity (served, in-warehouse UPC) — `build-item-identity`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-item-identity` |
| Runs | `import build_item_identity as m; m.build()` |
| Module | `unifyd/build_item_identity.py` — 120 lines |
| Cadence | every 12h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/build_item_identity_test.py` |


**Registry note.** distinct-UPC → canon_item_id over _stage_product+retail; the served identity the overlay joins


## 2. Transport

_No literal endpoint constant in `build_item_identity.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `item_identity`

147,235 rows · 14 columns


| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `canon_item_id` | `BIGINT` | 100.0% |
| `brand` | `VARCHAR` | 58.9% |
| `product_name` | `VARCHAR` | 99.7% |
| `category` | `VARCHAR` | 26.1% |
| `size_ml` | `DOUBLE` | 9.9% |
| `size_raw` | `VARCHAR` | **0.7%** |
| `upcs` | `VARCHAR[]` | 100.0% |
| `identity_key` | `VARCHAR` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `merged_into` | `INTEGER` | **0%** ‹never populated› |
| `match_tier` | `BIGINT` | 100.0% |
| `method_version` | `VARCHAR` | 100.0% |

Fill measured over **full table** (147,235 rows).

> **1 column never populated:** `merged_into`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `build_item_identity.py:105` (write_parquet), `ingest_canon_identity.py:55` (write_parquet)


## 4. `build_item_identity.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
build_item_identity.py — produce the served `item_identity` IN-SYSTEM (warehouse), nothing local.

The matching-convergence cutover (MATCHING-CONVERGENCE.md) proved the served recall lift is
UPC-DETERMINISTIC: canon scored R=1.000 vs unifyd item_key's 0.269 on the UPC gold precisely because
canon anchors identity on the UPC. That collapse — "same UPC ⇒ same item" — needs no local Postgres and no
canon cascade; it is a group-by-UPC the warehouse does directly. So this build computes `item_identity`
(the table the serving overlay `canon_identity.py` joins) entirely in DuckDB, as a `source_registry` build
that runs in the system on cadence.

IDENTITY = the numeric UPC. `canon_item_id = CAST(<digits-only UPC> AS BIGINT)` — deterministic, stable
across runs, unique per UPC, and it collapses leading-zero variants ("012…"/"12…" → same bigint) for free
(the [[upc-resolution-engine]] zero-strip, built in). Every UPC-bearing source SKU across the FULL mapped
universe (`_stage_product`) unioned with `retail_observations` maps to that id; two sources on one UPC →
one identity (the lift). UPC-less SKUs are absent — they keep unifyd's `item_key` via the COALESCE overlay.

Cross-UPC merges (pack variants), no-UPC and fuzzy identity are the HARD TAIL the gold doesn't yet measure;
that is hoodie-canon's cascade, to run as a cloud engine later. This build owns `item_identity` for the
deterministic core today (single writer).

Schema = the frozen item_identity contract (same columns `ingest_canon_identity.py` landed), so the overlay
and `master_quality.score_canon` consume it unchanged.

    python build_item_identity.py         # (in-app) recompute item_identity from the warehouse
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
