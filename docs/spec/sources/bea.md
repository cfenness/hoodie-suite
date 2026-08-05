# BEA regional income — `bea`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `bea` |
| Runs | `import bea_ref as m; m.build()` |
| Module | `unifyd/bea_ref.py` — 153 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `creds` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | `BEA_API_KEY` |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** BEA Regional API (bea_ref.build) — state disposable income (SAINC51) + county personal income (CAINC1), annual; a fresh BEA key must be ACTIVATED via BEA's email link or the API returns in-band Error 4 (reported degraded, never silent)


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://apps.bea.gov/api/data` |


**Depends on** `warehouse`


## 3. What it lands


### `bea_reference`

96,450 rows · 10 columns


| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `table_name` | `VARCHAR` | 100.0% |
| `line_code` | `VARCHAR` | 100.0% |
| `metric_name` | `VARCHAR` | 100.0% |
| `geo_level` | `VARCHAR` | 100.0% |
| `geo_fips` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `metric_value` | `DOUBLE` | 100.0% |
| `unit` | `VARCHAR` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (96,450 rows).

**Written by** `bea_ref.py:124` (write_accumulate)


## 4. `bea_ref.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
bea_ref.py — BEA regional income: the ANNUAL demand-side driver at county grain.

Why alongside ACS ([[economic-data-layer]]): ACS median income is a 5-year rolling estimate; BEA
personal income is an annual administrative-data figure, fresher and revised on a known schedule —
the cleaner year-over-year demand driver for trade-area work.

Scope (BEA Regional API, free UserID key = BEA_API_KEY):
  • SAINC51 (state) — disposable personal income: line 51 total, 52 population, 53 per-capita ($).
  • CAINC1 (county) — personal income summary: line 1 total ($K), 2 population, 3 per-capita ($).
    (County-grain DISPOSABLE income is not published; per-capita personal income is the county driver.)

KEY-ACTIVATION GOTCHA (hit live 2026-07): a fresh BEA UserID returns HTTP 200 with
Results.Error APIErrorCode 4 "This UserId is not active" until the emailed activation link is
clicked. build() detects the in-band Error object and reports DEGRADED with the exact message —
never a silent empty land. The data-path parse follows BEA's documented Data[] shape and is
confirmed on the first active-key run.

    python bea_ref.py            # smoke: the exact calls (no network)
    python bea_ref.py --build    # land bea_reference (needs an ACTIVATED BEA_API_KEY)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
