# FRED macro pulse — `fred`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `fred` |
| Runs | `import fred_ref as m; m.build()` |
| Module | `unifyd/fred_ref.py` — 128 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `creds` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | `FRED_API_KEY` |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** FRED API (fred_ref.build) — monthly liquor-store retail sales (MRTSSM4453USN, the national off-prem pulse), food-service sales, real disposable income, consumer sentiment


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://api.stlouisfed.org/fred/series/observations` |


**Depends on** `warehouse`


## 3. What it lands


### `fred_reference`

550 rows · 9 columns


| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `series_id` | `VARCHAR` |
| `series_name` | `VARCHAR` |
| `date` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `period` | `VARCHAR` |
| `metric_value` | `DOUBLE` |
| `unit` | `VARCHAR` |
| `source_pulled_at` | `BIGINT` |


**Written by** `fred_ref.py:101` (write_accumulate)


## 4. `fred_ref.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
fred_ref.py — FRED macro context: the monthly PULSE series the annual layers can't provide.

The economic layer's fast axis ([[economic-data-layer]]): CEX/ACS/EC are annual-or-slower; FRED adds
the monthly read. Scope (each validated live 2026-07, FRED_API_KEY):
  • MRTSSM4453USN — retail sales, beer/wine/liquor stores, $M NSA monthly. The national off-premise
    sales pulse (~$6.3B/mo) — trend + seasonality benchmark for velocity and demand work. (The
    drinking-places twin MRTSSM7224 was discontinued in 2019 — deliberately NOT pulled.)
  • MRTSSM722USN — food services & drinking places total, $M monthly (the on-premise macro trend).
  • DSPIC96 — real disposable personal income (chained $B, SAAR): the demand-side driver.
  • UMCSENT — U. Michigan consumer sentiment: the discretionary-spend mood ring.

Free key required (FRED_API_KEY) — declared in the registry so a keyless host reports no-creds
honestly ([[reference-data-connectors]]). Long/tall fred_reference keyed (series_id, date); a
re-pull refreshes in place and late months simply appear.

    python fred_ref.py            # smoke: series list (no network)
    python fred_ref.py --build    # land fred_reference (needs FRED_API_KEY)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
