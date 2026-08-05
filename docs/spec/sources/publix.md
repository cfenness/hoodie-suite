# Publix — `publix`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `publix` |
| Runs | `import publix as m; m.run()` |
| Module | `unifyd/publix.py` — 151 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | bd |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi`, `patchright` |
| Unit test | **none** |


**Registry note.** weekly-ad API


## 2. Transport

| constant | value |
|---|---|
| `VIEW_ALL` | `https://www.publix.com/savings/weekly-ad/view-all` |


**Depends on** `brightdata`, `browser_warm`, `observe`, `resi`, `warehouse`


## 3. What it lands


### `publix_products`

5,477 rows · 9 columns


| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `promo_type` | `VARCHAR` | 100.0% |
| `is_bogo` | `BOOLEAN` | 100.0% |
| `savings` | `DOUBLE` | 85.4% |
| `deal_text` | `VARCHAR` | 100.0% |
| `is_hemp` | `BOOLEAN` | 100.0% |
| `store` | `VARCHAR` | 100.0% |
| `market` | `VARCHAR` | 100.0% |
| `source` | `VARCHAR` | 100.0% |

Fill measured over **full table** (5,477 rows).

**Written by** `publix.py:140` (write_accumulate)


## 4. `publix.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
publix.py — Publix WEEKLY AD (BOGO) capture. Publix's own ad, NOT the Instacart-gated side, NOT age-gated.

WORKING (2026-07-10): the weekly-ad API is a flyer/GraphQL maze whose XHRs carry headers/persisted-queries
that fight replay, so instead we read the RENDERED deals off publix.com/savings/weekly-ad/view-all — which
lists every deal with its BOGO/savings text. Access via the BD Browser API over CDP (defeats Akamai), with
CDP Proxy.setLocation pinned to a Publix-footprint city so a real store auto-selects (Publix operates in
FL/GA/AL/SC/NC/TN/VA/KY — a non-footprint exit IP yields no store and an empty ad). Lands as publix_products
+ retail_observations (BOGO drives huge volume). Store #331 = Kirkman Oaks, Orlando.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
