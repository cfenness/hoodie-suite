# AB InBev locator — `ab-inbev`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ab-inbev` |
| Runs | `import ab_fill as m; m.run()` |
| Module | `unifyd/ab_fill.py` — 131 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** beertech GraphQL


## 2. Transport

_No literal endpoint constant in `ab_fill.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `ab_locator`, `warehouse`


## 3. What it lands


### `ab_outlets`

278,510 rows · 10 columns


| column | type |
|---|---|
| `VPID` | `VARCHAR` |
| `Name` | `VARCHAR` |
| `Address` | `VARCHAR` |
| `City` | `VARCHAR` |
| `State` | `VARCHAR` |
| `Zip` | `VARCHAR` |
| `Lat` | `DOUBLE` |
| `Lng` | `DOUBLE` |
| `AB_Brands` | `VARCHAR` |
| `Zips_Hit` | `VARCHAR` |


**Written by** `ab_fill.py:68` (write_parquet)


## 4. `ab_fill.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
ab_fill.py — complete the AB InBev locator sweep for UNDER-SWEPT regions (the west), ACCUMULATING into the
existing ab_outlets (never clobbering the east). The national sweep was seeded with too few western zips, so
WA/OR/AZ/NV/ID/… came out thin (7 WA metros alone yield ~600 outlets the table was missing).

No zip database is needed: BFS. Seed each target state's metros, sweep all AB brands (radius 25mi), harvest the
returned outlets' OWN zips, sweep the new ones, repeat — tiling the populated area organically. Merge by vpid
(union brand carriage + zips-hit), checkpoint to the warehouse periodically so a long run survives interruption.

    python ab_fill.py --cap 900        # BFS up to 900 western zips, then stop (re-run to go deeper)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
