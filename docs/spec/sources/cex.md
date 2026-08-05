# BLS Consumer Expenditure (alcohol × income) — `cex`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `cex` |
| Runs | `import cex_ref as m; m.build(); m.build_demand()` |
| Module | `unifyd/cex_ref.py` — 425 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** BLS CEX API (cex_ref.build) — mean annual alcohol $ per CU (total / at-home / away) by income-before-taxes bracket; keyless OK (BLS_API_KEY raises limits); build_demand derives trade_area_demand = CEX × ACS B19001 (needs the census source's brackets landed)


## 2. Transport

| constant | value |
|---|---|
| `API` | `https://api.bls.gov/publicAPI/v2/timeseries/data/` |


**Depends on** `census_ref`, `warehouse`


## 3. What it lands


### `cex_reference`

450 rows · 13 columns


| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `item_code` | `VARCHAR` | 100.0% |
| `item_name` | `VARCHAR` | 100.0% |
| `demographic` | `VARCHAR` | 100.0% |
| `bracket_code` | `VARCHAR` | 100.0% |
| `bracket_label` | `VARCHAR` | 100.0% |
| `bracket_lo` | `DOUBLE` | 100.0% |
| `bracket_hi` | `DOUBLE` | 80.0% |
| `metric_name` | `VARCHAR` | 100.0% |
| `metric_value` | `DOUBLE` | 100.0% |
| `suppressed` | `BOOLEAN` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **full table** (450 rows).

**Written by** `cex_ref.py:195` (write_accumulate)


## 4. `cex_ref.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
cex_ref.py — BLS Consumer Expenditure Survey (CEX): alcohol spend by income bracket + trade-area demand $.

The DEMAND-DOLLAR layer ([[economic-data-layer]]): what households actually SPEND on alcohol per year,
cut by income — the multiplier that turns the ACS household-income distribution (census_ref.py) into a
quantitative trade-area demand estimate, with no sales feed required.

Source: the official BLS timeseries API (api.bls.gov/publicAPI/v2) — an API, not a scrape
([[reference-data-connectors]]). Keyless works (25 series/query, 10 years, 25 queries/day);
BLS_API_KEY raises the limits. CX series id = CXU + item + demographic·characteristic + M(ean), e.g.
CXUALCBEVGLB0221M = mean alcohol spend, income-before-taxes $100–149,999. All layout facts below were
validated LIVE (2026-07): the active income-before-taxes DOLLAR brackets live under LB02 with
NON-CONTIGUOUS characteristic codes (18,19,07,08,09,20,21,22,23 — the gaps are discontinued historical
brackets), and LB01 is QUINTILES, not dollars. Items: ALCBEVG (total alcohol) = ALCHOME (at home,
off-premise) + ALCAWAY (away from home, on-premise) — the split maps 1:1 onto Hoodie's account types.
2023 anchor: all-CU alcohol = $637 = 294 + 343, matching the published CEX tables.

Self-checks every build (degraded + warnings, never silent):
  • bracket-placement — the pulled INCBEFTX (mean income of CUs in the bracket) must fall INSIDE the
    bracket's dollar range; a BLS renumbering would put means outside their ranges, and that bracket's
    rows are DROPPED (mislabeled spend is worse than missing spend).
  • home+away identity — ALCHOME + ALCAWAY ≈ ALCBEVG per cell.
  • anchor — all-CU 2023 alcohol = $637 ±8% (a revision drifts, a unit change screams).

Demand $ (the CEX × ACS join, [[signal-stack-cross-bucket]]): demand_for(geo_fips) maps the 16 ACS
B19001 household-income brackets onto the 9 CEX brackets (one straddle: ACS 60–75k splits 2/3:1/3
across the 70k boundary, uniform assumption) and sums households × mean spend. Consumer unit ≈
household is an approximation; means are national (no regional cut yet) — both stated in caveats,
never hidden. build_demand() lands the full county+state layer as trade_area_demand (derived rebuild,
plain write). ACS brackets are key-gated (CENSUS_API_KEY) so demand self-reports not-landed until a
census build has run with the full B19001 set.

    python cex_ref.py                 # smoke: print the exact series ids + limits (no network)
    python cex_ref.py --build         # land cex_reference (keyless OK)
    python cex_ref.py --demand 12095  # trade-area demand $ for one geo (needs ACS brackets landed)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
