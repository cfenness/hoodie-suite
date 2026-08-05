# US Census — ACS demographics — `census-acs`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `census-acs` |
| Runs | `import census as m; m.build()` |
| Module | `unifyd/census.py` — 139 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `creds` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | `CENSUS_API_KEY` |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** demand-side ACS5 by county (census.build) — population/income/housing packs, wide + geoid-keyed for enrich.merge_census outlet joins; free key, re-derivable


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://api.census.gov/data` |


**Depends on** `warehouse`


## 3. What it lands


### `census_demographic`

423 rows · 11 columns


| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `population` | `VARCHAR` | 100.0% |
| `median_age` | `VARCHAR` | 100.0% |
| `households` | `VARCHAR` | 100.0% |
| `hispanic_pop` | `VARCHAR` | 100.0% |
| `white_pop` | `VARCHAR` | 100.0% |
| `black_pop` | `VARCHAR` | 100.0% |
| `asian_pop` | `VARCHAR` | 100.0% |
| `state_fips` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `geoid` | `VARCHAR` | 100.0% |

Fill measured over **full table** (423 rows).

### `census_economic`

423 rows · 9 columns


| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `median_household_income` | `VARCHAR` | 99.8% |
| `per_capita_income` | `VARCHAR` | 100.0% |
| `poverty_pop` | `VARCHAR` | 100.0% |
| `labor_force` | `VARCHAR` | 100.0% |
| `unemployed` | `VARCHAR` | 100.0% |
| `state_fips` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `geoid` | `VARCHAR` | 100.0% |

Fill measured over **full table** (423 rows).

### `census_housing`

423 rows · 9 columns


| column | type | filled |
|---|---|---|
| `name` | `VARCHAR` | 100.0% |
| `median_home_value` | `VARCHAR` | 99.1% |
| `median_gross_rent` | `VARCHAR` | 98.1% |
| `housing_units` | `VARCHAR` | 100.0% |
| `owner_occupied` | `VARCHAR` | 100.0% |
| `renter_occupied` | `VARCHAR` | 100.0% |
| `state_fips` | `VARCHAR` | 100.0% |
| `county_fips` | `VARCHAR` | 100.0% |
| `geoid` | `VARCHAR` | 100.0% |

Fill measured over **full table** (423 rows).

## 4. `census.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
census.py — US Census ACS5 reference data by county (connId 'census-acs').

Reference data is a STANDALONE constellation, not something baked into outlets: this pulls the
ACS in THREE thematic packs, each landing as its own dataset (its own node in the estate model),
all county-keyed (name + geoid) so any of them JOINS to the outlet master on demand:

  census_demographic — population, median age, households, race/ethnicity
  census_economic    — household + per-capita income, poverty, labor force, unemployment
  census_housing     — median home value + gross rent, units, owner/renter occupancy

One Socrata-free Census API call per state (ACS allows many variables at once), split into the
three datasets. Free key REQUIRED (CENSUS_API_KEY). Stdlib-only, same (datasets,[run],movement)
contract; self-reports degraded/failed with clear warnings.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
