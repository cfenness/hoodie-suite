# Bev-alc tax RATES (TTB + state excise) — `tax-rates`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `tax-rates` |
| Runs | `import tax_rates as m; m.build()` |
| Module | `unifyd/tax_rates.py` — 231 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** federal CBMA schedule (encoded, TTB) + 51-jurisdiction state excise seed (Tax Foundation Jan 2026); effective-dated ref, landed_cost.py reads it — verify state cells vs DOR to promote seed->verified


## 2. Transport

| constant | value |
|---|---|
| `url` | `https://www.ttb.gov/tax-audit/tax-and-fee-rates` |


**Depends on** `warehouse`


## 3. What it lands


### `tax_rates`

**Has never landed.** `HTTP Error: HTTP GET error reading 's3://hoodie-suite-warehouse/warehouse/tax_rates.parquet' in region 'auto' (HTTP 404 Not Found)`

This is a registered source whose table does not exist in the warehouse — it has never completed a successful run, or it writes under a different name than the registry declares.


## 4. `tax_rates.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
tax_rates.py — beverage-alcohol TAX RATE reference layer (federal excise + state excise/sales).

The per-UNIT tax schedule the cost model stacks onto base cost — NOT collections (that's tax_revenue.py).
Long/tall, effective-dated, append-only: a re-pull REPLACES a
(level, jurisdiction, class, abv band, rate_type, effective_date) cell but never deletes prior
effective_dates, so rate HISTORY is preserved ([[append-only-versioned-master]]). landed_cost.py reads
it via current().

Two tiers, mirroring how the rest of the engine treats authority ([[cola-tiering]]):
  • FEDERAL (TTB) — the CBMA-permanent excise schedule: small, stable, high-confidence. Encoded here
    (FED_RATES) as the default, and optionally re-confirmed live from the TTB rate page. TTB is
    TLS-blocked from Fly, so live-refresh is a Mac-run step ([[ttb-fast-scrape]]); the encoded schedule
    is what Fly lands.
  • STATE (seed → DOR) — state excise + any special alcohol sales rate in tax_rates_seed.csv. Base layer
    is Tax Foundation (Jan 2026, provenance=seed); TRANCHE-1 DOR VERIFICATION (2026-07) promoted 12 pipeline
    states (CA/TX/NY/FL/IL/MN/NJ/MA/CO/WA/VA/PA) to provenance=verified against state DOR/statute, correcting
    where TF's *effective* rate diverged from the *statutory* excise (MN spirits $8.74→$5.03 etc.) and
    decomposing bundled taxes into a clean excise row + a separate percentage row (MN 2.5% gross-receipts,
    WA 20.5% spirits sales, WA spirits as a $3.7708/L liter tax). LOCAL rows were added where material
    (Cook County & Chicago IL, NYC). Remaining states stay TF seed — extend the tranche the same way.
    CONTROL states carry an IMPLIED spirits excise — the state markup IS the tax — flagged is_control_state
    and read from the control_state.py price book, not taken as a clean statutory rate ([[control-states-and-ca]]).

Honest failure: a class/state we can't map is emitted with provenance='unverified' + a warning, never a
silent 0 — a missing rate must look missing, not free. build() returns {rows, warnings, degraded}.

    python tax_rates.py            # smoke test: assemble + print sample rows + a landed-cost demo (no land)
    python tax_rates.py --build    # land to the warehouse (needs pyarrow / Tigris creds, e.g. on Fly)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
