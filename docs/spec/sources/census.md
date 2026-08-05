# US Census (CBP · Nonemp · PEP · ACS) — `census`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `census` |
| Runs | `import census_ref as m; m.build()` |
| Module | `unifyd/census_ref.py` — 429 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `creds` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | `CENSUS_API_KEY` |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** Census API (census_ref.build) — CBP/Nonemp/PEP supply-side + ACS demand-side demographics at state/county/ZCTA grain (~33k ZIPs) + Economic Census OBSERVED receipts (dataset ecn, $1000s); free key, re-derivable


## 2. Transport

_No literal endpoint constant in `census_ref.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `census_reference`

876,266 rows · 9 columns


| column | type | filled |
|---|---|---|
| `dataset` | `VARCHAR` | 100.0% |
| `vintage_year` | `BIGINT` | 100.0% |
| `naics_code` | `VARCHAR` | 5.3% |
| `geo_level` | `VARCHAR` | 100.0% |
| `geo_fips` | `VARCHAR` | 100.0% |
| `metric_name` | `VARCHAR` | 100.0% |
| `metric_value` | `DOUBLE` | 99.4% |
| `suppressed` | `BOOLEAN` | 100.0% |
| `source_pulled_at` | `BIGINT` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

**Written by** `census_ref.py:371` (write_accumulate)


## 4. `census_ref.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
census_ref.py — U.S. Census reference layer: CBP / Nonemployer / PEP (supply-side) + ACS (demand-side).

A REFERENCE/dimension layer for the MDM: aggregate counts by geography + NAICS, joined to entity
tables (permits, products, retailers, territories) at QUERY TIME by geo/NAICS — never a baked FK.
Long/tall `census_reference` so any dataset/metric fits without a schema change. Alcohol scope:
NAICS 4248 (bev-alc merchant wholesalers), 44531 (beer/wine/liquor stores), 722 (food service &
drinking places, for on-premise). Free Census API — no scrape — but now needs CENSUS_API_KEY for
ALL requests (keyless -> 302 missing_key). Stores to the warehouse (Parquet/DuckDB).

Parses by HEADER NAME (array-of-arrays, header row first) so it survives Census's column reshuffles.
build() runs CBP/Nonemployer/PEP/ACS(featured)/Economic-Census → long/tall census_reference rows;
query() reads them back by dataset/geo/naics/metric. build_acs() separately sweeps ALL ~1,193 ACS5
detailed tables into census_acs; build_flows() lands county-to-county migration into census_migration.
Suppression (cells Census withholds for confidentiality) is a flagged state, never treated as 0.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
