# TTB COLA master build — `ttb`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ttb` |
| Runs | `import master_ttb as m; m.run()` |
| Module | `unifyd/master_ttb.py` — 148 lines |
| Cadence | weekly |
| Enabled | no — does not run on a cadence |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** MASTER BUILD (reads ttb_cola → ttb_master); huge — refresh deliberately. Scrape is ttb-cola


## 2. Transport

_No literal endpoint constant in `master_ttb.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `cola_tiering`, `upc`, `warehouse`


## 3. What it lands


### `ttb_master`

1,732 rows · 18 columns


| column | type | filled |
|---|---|---|
| `cluster_id` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `fanciful` | `VARCHAR` | **0%** ‹never populated› |
| `class_type` | `VARCHAR` | 100.0% |
| `size_ml` | `VARCHAR` | **0%** ‹never populated› |
| `supplier` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | 63.9% |
| `corroborated_by` | `VARCHAR` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |
| `match_kind` | `VARCHAR` | 100.0% |
| `size_matched` | `BOOLEAN` | 100.0% |
| `candidate_name` | `VARCHAR` | 100.0% |
| `matched_by` | `VARCHAR` | 100.0% |
| `member_count` | `BIGINT` | 100.0% |
| `members` | `VARCHAR` | 100.0% |
| `first_day` | `BIGINT` | 100.0% |
| `last_day` | `BIGINT` | 100.0% |
| `tier` | `BIGINT` | 100.0% |

Fill measured over **full table** (1,732 rows).

> **2 columns never populated:** `fanciful`, `size_ml`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `master_ttb.py:133` (write_parquet), `server.py:3574` (write_parquet)


## 4. `master_ttb.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
master_ttb.py — promote TTB COLA items into the canonical master ONLY when confirmed to exist elsewhere.

A TTB COLA filing is a label APPROVAL, not proof a product is on the market — millions are filed, many never
ship. So COLA seeds Tier 0 (quarantine); an item lands in the master only when an INDEPENDENT market source
(ABC, Total Wine, Binny's, on-premise menus, …) corroborates it. This assembles the market index from every
product source we hold, runs cola_tiering.tier() (cluster label-iterations -> corroborate -> promote, healing
the UPC on the way), and lands:
  · ttb_master   — Tier 1: confirmed-elsewhere items = the canonical master, each with PROVENANCE (the COLA
                   filings underneath + the corroborating source + confidence + healed UPC).
  · ttb_review   — low-confidence matches for the workbench review queue.
  · ttb_quarantine_summary — Tier 0 innovation radar (filed, unconfirmed) by supplier.

    python master_ttb.py --cola 25000        # scope the COLA slice (newest first); omit for a bigger run
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
