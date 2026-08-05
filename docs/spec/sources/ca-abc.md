# California ABC — `ca-abc`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ca-abc` |
| Runs | `import ca_abc as m; m.run()` |
| Module | `unifyd/ca_abc.py` — 54 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | proxy |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** WAF — spoofed browser HEADERS on stdlib urllib (NOT a headful browser); klass was wrongly 'mac' → Mac queue


## 2. Transport

| constant | value |
|---|---|
| `URL` | `https://www.abc.ca.gov/wp-content/uploads/DailyExport-CSV.zip` |


**Depends on** `warehouse`


## 3. What it lands


### `ca_outlets`

128,950 rows · 26 columns


| column | type | filled |
|---|---|---|
| `License Type` | `VARCHAR` | 100.0% |
| `File Number` | `VARCHAR` | 100.0% |
| `Lic or App` | `VARCHAR` | 100.0% |
| `Type Status` | `VARCHAR` | 100.0% |
| `Type Orig Iss Date` | `VARCHAR` | 84.4% |
| `Expir Date` | `VARCHAR` | 84.4% |
| `Fee Codes` | `VARCHAR` | 99.9% |
| `Dup Counts` | `VARCHAR` | 14.7% |
| `Master Ind` | `VARCHAR` | 100.0% |
| `Term in # of Months` | `VARCHAR` | 100.0% |
| `Geo Code` | `VARCHAR` | 99.9% |
| `District` | `VARCHAR` | 100.0% |
| `Primary Name` | `VARCHAR` | 99.7% |
| `Prem Addr 1` | `VARCHAR` | 99.9% |
| `Prem Addr 2` | `VARCHAR` | 20.3% |
| `Prem City` | `VARCHAR` | 99.8% |
| `Prem State` | `VARCHAR` | 99.8% |
| `Prem Zip` | `VARCHAR` | 99.8% |
| `DBA Name` | `VARCHAR` | 94.0% |
| `Mail Addr 1` | `VARCHAR` | 59.7% |
| `Mail Addr 2` | `VARCHAR` | 22.3% |
| `Mail City` | `VARCHAR` | 59.7% |
| `Mail State` | `VARCHAR` | 59.7% |
| `Mail Zip` | `VARCHAR` | 59.7% |
| `Prem County` | `VARCHAR` | 98.2% |
| `Prem Census Tract #` | `VARCHAR` | 97.4% |

Fill measured over **full table** (128,950 rows).

**Written by** `ca_abc.py:46` (write_parquet)


## 4. `ca_abc.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
ca_abc.py — California ABC statewide license export -> ca_outlets (the CA outlet spine).

California is a LICENSE state (no state stores / no control-state price book), so the value is the
outlet map: the ABC's daily export lists every licensed alcohol premise statewide (~129k), refreshed
daily, with license type + premises address + county + census tract. The download is WAF-gated, so it
needs a realistic browser header set + referer (the Unlocker returns 0 bytes for this binary).

Off-sale package RETAIL = License Type 20 (beer & wine) + 21 (general); on-sale = 41/47/48.
`Lic or App` = LIC (active license) | APP (pending application).

    python ca_abc.py            # download + land ca_outlets
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
