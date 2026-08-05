# SipSource depletion marts — `build-sipsource-marts`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-sipsource-marts` |
| Runs | `import sipsource_ingest as m; m.build_marts('sip_raw')` |
| Module | `unifyd/sipsource_ingest.py` — 151 lines |
| Cadence | every 24h |
| Enabled | no — does not run on a cadence |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** raw 500M sip_raw → brand×market×month + supplier×cat + category marts (bounded, +YoY)


## 2. Transport

_No literal endpoint constant in `sipsource_ingest.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `mart_sip_brand_market_month`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/mart_sip_brand_market_month.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `sipsource_ingest.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
sipsource_ingest.py — land the SipSource feed and build the SERVING MARTS (NRT-PLAN.md Phase 4 / §1d).

The raw grain (~500M rows) is NEVER queried by the site. Ingest reads the hive-partitioned raw feed
(period=YYYYMM/market=XX/…) ONCE in DuckDB and rolls it up into a handful of small marts (a few
million rows each) that the console/API read. DuckDB streams the aggregation over Parquet with a
memory_limit (spills to disk), so the roll-up runs on the ephemeral worker, not the serving VM.

Marts (each with YoY via a 12-month self-join so % change is real, not invented):
  mart_sip_brand_market_month     brand × market × premise × month   — the workhorse drill grain
  mart_sip_supplier_cat_month     supplier × category × month        — portfolio view
  mart_sip_category_month         category × month (national)        — headline trends

Real-feed note: when the actual file arrives it lands to the SAME raw layout (a CSV/xlsx streamed to
period=…/market=… parquet); only the *reader* changes, never these marts or the API. This module is
the reader-agnostic half.

    python sipsource_ingest.py --raw sip_raw          # build all marts from the landed raw feed
    python sipsource_ingest.py --raw sip_raw --stats  # + print mart sizes / a scoped-query timing
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
