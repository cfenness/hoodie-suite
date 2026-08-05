# DoorDash geo sweep — Texas — `doordash-geo-tx`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `doordash-geo-tx` |
| Runs | `import doordash_geo as m; m.run_texas()` |
| Module | `unifyd/doordash_geo.py` — 308 lines |
| Cadence | weekly |
| Enabled | no — does not run on a cadence |
| Executor class | `mac` |
| Cost class | free |
| Memory / timeout | 8192 MB / 21600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `patchright` |
| Unit test | `unifyd/doordash_geo_test.py` |


**Registry note.** 240-point lattice across Houston/Dallas/Fort Worth/Austin/San Antonio at 0.07 deg (~4-5 mi, inside a delivery radius so the grid has no holes). Replaces the broken TX sitemap; a pin where every search term fails now RAISES rather than reporting an empty market.


## 2. Transport

_No literal endpoint constant in `doordash_geo.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `browser_warm`, `cuisine`, `resi`, `warehouse`


## 3. What it lands


### `doordash_stores`

773,357 rows · 7 columns


| column | type |
|---|---|
| `store_id` | `VARCHAR` |
| `name` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `url` | `VARCHAR` |
| `type` | `VARCHAR` |
| `source` | `VARCHAR` |


**Written by** `doordash_discover.py:133` (write_accumulate), `doordash_sitemap.py:142` (write_accumulate)


## 4. `doordash_geo.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
doordash_geo.py — GEOGRAPHIC harvest: every alcohol-delivering merchant in a market (chains + INDEPENDENTS).

Chain-targeting misses the long tail; a market's independents (bottle shops, neighborhood bars, single-
location restaurants) are the scan-dark differentiator. We sweep a GRID of setLocation points across the
metro — one pin only sees merchants whose delivery zone reaches it (~few-mi radius), so a lattice with
spacing < that radius, unioned, covers the whole city. At each pin we search alcohol terms, dedup by store
id, and classify each merchant (retail|restaurant · chain|independent). Run to saturation; ground-truth the
found set against the FL license universe (DBPR/ABT) to measure coverage and find gaps.

    python doordash_geo.py --market orlando            # full grid (background job)
    python doordash_geo.py --market orlando --points 3 # smoke test
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
