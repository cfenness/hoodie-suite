# BLS CPI (alcoholic beverages) — `cpi`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `cpi` |
| Runs | `import cpi_ref as m; m.build()` |
| Module | `unifyd/cpi_ref.py` — 193 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** BLS CPI-U API (cpi_ref.build) — alcohol total/at-home/away + beer/spirits/wine sub-items, US + 4 regions, monthly + M13 annual; keyless OK; real_series() = alcohol rebased vs all-items (the deflator / price-index benchmark)


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://api.bls.gov/publicAPI/v2/timeseries/data/` |


**Depends on** `warehouse`


## 3. What it lands


### `cpi_reference`

1,830 rows · 10 columns


| column | type |
|---|---|
| `dataset` | `VARCHAR` |
| `series_id` | `VARCHAR` |
| `item_code` | `VARCHAR` |
| `item_name` | `VARCHAR` |
| `area_code` | `VARCHAR` |
| `area_name` | `VARCHAR` |
| `vintage_year` | `BIGINT` |
| `period` | `VARCHAR` |
| `metric_value` | `DOUBLE` |
| `source_pulled_at` | `BIGINT` |


**Written by** `cpi_ref.py:142` (write_accumulate)


## 4. `cpi_ref.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
cpi_ref.py — BLS CPI-U alcoholic beverages: the DEFLATOR / benchmark layer.

Two jobs ([[economic-data-layer]]):
  • Real terms — deflate shelf prices (retail_observations, the price-index app) and demand $ so a
    price move can be split into "inflation" vs "actually more expensive".
  • Benchmark — our own bev-alc shelf-price index has an official yardstick: when Hoodie's index and
    BLS CPI diverge, that divergence is the story (assortment shift / premiumization / promo depth),
    not an error bar.

Source: the official BLS timeseries API, keyless OK — same client discipline as cex_ref.py
([[reference-data-connectors]]). CPI-U NSA series id = CUUR + area + item. Scope (validated live
2026-07): items SA0 (all items — the deflation base), SAF116 (alcoholic beverages), SEFW (at home)
with sub-items SEFW01 beer / SEFW02 distilled spirits / SEFW03 wine, SEFX (away from home — no
published sub-items); areas US + the four census regions (SA0 + SAF116 only at region grain).
Monthly + M13 annual averages. Anchor: US all-items 2023 annual = 304.702.

The premiumization tell baked into the data: away-from-home alcohol (SEFX 429 in 2024) has inflated
FAR faster than at-home (SEFW 229) on the same 1982-84=100 base, and wine at home (182.6) has barely
moved — relative real prices, not nominal, are the signal. query(real=True) serves any alcohol series
REBASED against all-items for exactly that read.

    python cpi_ref.py            # smoke: print the series set (no network)
    python cpi_ref.py --build    # land cpi_reference (keyless OK)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
