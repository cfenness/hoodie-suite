# Bev-alc tax REVENUE (Census STC + TTB) — `tax-revenue`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `tax-revenue` |
| Runs | `import tax_revenue as m; m.build()` |
| Module | `unifyd/tax_revenue.py` — 202 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `creds` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | `CENSUS_API_KEY` |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** Census govs STC (T10 alc sales tax, T20 alc license) per state — live; TTB federal commodity collections run live on the Mac (TTB TLS-blocked on Fly)


## 2. Transport

| constant | value |
|---|---|
| `url` | `https://www.ttb.gov/resources/statistics` |


**Depends on** `warehouse`


## 3. What it lands


### `tax_revenue`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/tax_revenue.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `tax_revenue.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
tax_revenue.py — beverage-alcohol TAX REVENUE (collections) reference layer.

The dollars actually COLLECTED — the market-size / demand axis, distinct from the per-unit schedule in
tax_rates.py. Long/tall time series, keyed (source, jurisdiction, fiscal_year, tax_kind, beverage_class),
append-only so each year's figure is its own row and a re-pull refreshes in place ([[reference-data-connectors]]).

Two authoritative, free sources — no scrape:
  • CENSUS STC (state) — the Annual Survey of State Government Tax Collections via the governments
    timeseries API (SVY_COMP=STC). Two alcohol-specific item codes: T10 = Alcoholic Beverages Sales Tax,
    T20 = Alcoholic Beverages License. Per state + US, by year. Reuses CENSUS_API_KEY and the same
    header-name parsing discipline as census_ref.py — validate LIVE on Fly (keyless -> "Missing Key" HTML).
    The govs `AMOUNT` field is reported in THOUSANDS of dollars (Census government-finance convention;
    confirmed against FRED's Census-sourced T10 series — e.g. CA FY2021 = $411,969 thousand). Stored raw
    with unit="USD_thousands", _AMOUNT_SCALE=1. Every live pull SELF-CHECKS this via _verify_scale() against
    a known anchor cell and warns if the magnitude instead looks like whole dollars (a 1000x margin error is
    loud, not silent) — so no manual scale confirmation is needed.
  • TTB (federal) — TTB's tax-collections statistical reports, by commodity (distilled spirits / wine /
    beer). TTB is TLS-blocked from Fly, so refresh_ttb() only succeeds on a network-capable host (the Mac);
    on Fly it self-reports degraded and lands nothing federal rather than emitting fabricated numbers.

Honest failure: no key -> Census yields nothing and build() is degraded (not a silent empty catalog);
TTB unreachable -> degraded federal, Census still lands.

    python tax_revenue.py             # smoke: print the exact Census call it will make (no key needed)
    python tax_revenue.py --build     # land (needs CENSUS_API_KEY; TTB needs live network)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
