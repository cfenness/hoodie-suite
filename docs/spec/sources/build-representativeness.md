# Representativeness (coverage + projection) — `build-representativeness`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-representativeness` |
| Runs | `import representativeness as m; m.build()` |
| Module | `unifyd/representativeness.py` — 175 lines |
| Cadence | every 24h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/representativeness_test.py` |


**Registry note.** state coverage + OBSERVED vs PROJECTED brand velocity w/ CIs; suppress below the floor


## 2. Transport

_No literal endpoint constant in `representativeness.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `coverage_cells`

1 rows · 5 columns


| column | type |
|---|---|
| `state` | `VARCHAR` |
| `universe_outlets` | `BIGINT` |
| `observed_outlets` | `BIGINT` |
| `coverage` | `DOUBLE` |
| `brands_observed` | `BIGINT` |


**Written by** `representativeness.py:135` (write_parquet)


### `market_projection`

5,921 rows · 11 columns


| column | type |
|---|---|
| `state` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `universe_outlets` | `BIGINT` |
| `obs_stores` | `BIGINT` |
| `coverage` | `DOUBLE` |
| `observed_units` | `BIGINT` |
| `projected_units` | `INTEGER` |
| `ci_low` | `INTEGER` |
| `ci_high` | `INTEGER` |
| `ci_pct` | `INTEGER` |
| `projected_status` | `VARCHAR` |


**Written by** `representativeness.py:112` (write_parquet)


## 4. `representativeness.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
representativeness.py — from "what we OBSERVED" to "what the MARKET did" (MOAT-PLAN.md Workstream R).

The bridge that turns an observation engine into a market-truth engine — honestly. We never pretend a
scrape is a census; we MEASURE how far it is from one and project with stated uncertainty.

  R1 COVERAGE    per market cell (state × channel): observed outlets ÷ the known universe
                 (outlet_master). The ugly cells included — especially the ugly cells.
  R2 PROJECTION  every market metric ships in TWO labelled flavours:
                   OBSERVED  — deterministic. The sum of what we actually saw. Always shown.
                   PROJECTED — inference. Observed per-store mean × the universe, with a CI from the
                              finite-population survey estimator (Var = N²·(s²/n)·FPC). LABELLED, never
                              blended with OBSERVED (the DETERMINISTIC-vs-INFERENCE doctrine, extended
                              to statistics).
  R3 SUPPRESS    below MIN_OBS stores or MIN_COVERAGE, PROJECTED is withheld with the reason shown —
                 a blank that says why beats a number that lies.

Reuses the velocity brand×store data (fact_velocity) as the metric to project and `outlet_master` as
the universe; source→state from the same map the calibrator uses. R4 (anchor validation of the
projection) reuses velocity_calibrate's spine — one validation loop, two consumers.

    python representativeness.py            # build coverage_cells + market_projection
    python representativeness.py --stats
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
