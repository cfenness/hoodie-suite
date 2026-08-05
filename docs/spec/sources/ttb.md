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


| column | type |
|---|---|
| `cluster_id` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `fanciful` | `VARCHAR` |
| `class_type` | `VARCHAR` |
| `size_ml` | `VARCHAR` |
| `supplier` | `VARCHAR` |
| `upc` | `VARCHAR` |
| `corroborated_by` | `VARCHAR` |
| `confidence` | `DOUBLE` |
| `match_kind` | `VARCHAR` |
| `size_matched` | `BOOLEAN` |
| `candidate_name` | `VARCHAR` |
| `matched_by` | `VARCHAR` |
| `member_count` | `BIGINT` |
| `members` | `VARCHAR` |
| `first_day` | `BIGINT` |
| `last_day` | `BIGINT` |
| `tier` | `BIGINT` |


**Written by** `master_ttb.py:133` (write_parquet), `server.py:3477` (write_parquet)


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
