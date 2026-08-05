# Hemp retailers — `hemp-finder`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `hemp-finder` |
| Runs | `import hemp_finder as m, vtinfo; m.run(brands=vtinfo.HEMP_BRANDS)` |
| Module | `unifyd/hemp_finder.py` — 106 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** retailer discovery — ALL 5 hemp brands (cann/wynk/trail-magic/uncle-arnies/crescent-9); run() alone was cann-only


## 2. Transport

_No literal endpoint constant in `hemp_finder.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `vtinfo`, `warehouse`


## 3. What it lands


### `hemp_retailers`

2,144 rows · 13 columns


| column | type |
|---|---|
| `brand` | `VARCHAR` |
| `account` | `VARCHAR` |
| `chain` | `VARCHAR` |
| `street` | `VARCHAR` |
| `city` | `VARCHAR` |
| `state` | `VARCHAR` |
| `zip` | `VARCHAR` |
| `phone` | `VARCHAR` |
| `lat` | `VARCHAR` |
| `lng` | `VARCHAR` |
| `store_type` | `VARCHAR` |
| `source` | `VARCHAR` |
| `zip_searched` | `VARCHAR` |


**Written by** `hemp_finder.py:85` (write_accumulate)


## 4. `hemp_finder.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
hemp_finder.py — hemp/THC beverage retailers in EVERY state, via the VTInfo brand finder (vtinfo.py).

A hemp brand's VIP "where to buy" returns every account carrying it near a ZIP. Run a hemp brand across a
national ZIP grid (a metro per state) → all its retailers nationwide: which CHAINS carry hemp (Total Wine /
Kwik Trip / Cub / Coborn's / Target …) AND the independents, address-level, tagged with state + a chain guess.
Cann alone spans the hemp-legal states (it returned 200 accounts in one MN zip); add more brands to widen.
Lands `hemp_retailers`. This is the "all states I can find" engine.

    python hemp_finder.py --brands cann
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
