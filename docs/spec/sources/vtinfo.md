# VTInfo locator — `vtinfo`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `vtinfo` |
| Runs | `import vtinfo as m; m.run()` |
| Module | `unifyd/vtinfo.py` — 311 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/vtinfo_test.py` |


**Registry note.** brand→retailer 'where to buy' (HTML-fragment POST, not GraphQL). m.run() LANDS it; m.pull() alone returned rows but never wrote (the never-persisted bug)


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://finder.vtinfo.com/finder/web/v2/` |


**Depends on** `warehouse`


## 3. What it lands


### `vtinfo_titos`

399 rows · 13 columns


| column | type | filled |
|---|---|---|
| `Brand` | `VARCHAR` | 100.0% |
| `Account` | `VARCHAR` | 100.0% |
| `Street` | `VARCHAR` | 100.0% |
| `City` | `VARCHAR` | 99.7% |
| `State` | `VARCHAR` | 99.7% |
| `Zip` | `VARCHAR` | **0%** ‹never populated› |
| `Phone` | `VARCHAR` | 100.0% |
| `Miles` | `VARCHAR` | 100.0% |
| `Lat` | `VARCHAR` | 100.0% |
| `Lng` | `VARCHAR` | 100.0% |
| `StoreType` | `VARCHAR` | **0%** ‹never populated› |
| `Source` | `VARCHAR` | 100.0% |
| `Zip_Searched` | `VARCHAR` | 100.0% |

Fill measured over **full table** (399 rows).

> **2 columns never populated:** `Zip`, `StoreType`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `vtinfo.py:277` (write_accumulate)


## 4. `vtinfo.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
vtinfo.py — brand → retailer distribution from the VTInfo / VIP "where to buy" finder.

WHAT THIS IS (and why it's the highest-leverage inventory path we have):
  Vermont Information Processing (VIP) runs the beverage-alcohol industry's depletion
  backbone. A huge share of brands' "where to buy" widgets are `finder.vtinfo.com` iframes
  driven off VIP SALES data — i.e. the accounts that ACTUALLY MOVED the brand, not a generic
  store list. Point it at a brand + a set of ZIPs and it returns every retailer/on-premise
  account carrying that brand, with street/city/state/zip/phone/distance. That's a real
  distribution + velocity signal (which accounts carry which brands), and it corroborates
  outlets_master (which accounts exist) from an independent source.

HOW IT WORKS (fully reverse-engineered, stdlib only):
  1. GET  /finder/web/v2/iframe?custID=<CID>&uuid=<UUID>&m=5
     → the page carries a STATELESS global `CSRFToken = "..."` (a signed token, no cookie
       needed) + the hidden `form#finder` fields (custID, uuid, pagesize, action=results…).
  2. POST /finder/web/v2/iframe/search  with those fields + `z=<zip>` + `page=<n>`
     → an HTML fragment of result rows. Pages are 1-indexed; a probe with page=0 returns
       "You must specify a page between 1 and N" — that N is the page count for the ZIP.
     → each row: .finder_dba_text (account) · address <a> (street) · .finder_address_city ·
       .finder_address_state · zip · .finder_phone · .finder_miles · row class `source-sales`
       (VIP sales/depletion) vs other source tags.
  Snapshot {brand|dba|street: {...}} and diff (new/dropped accounts) — repeatable.

CAVEATS: one `custID`+`uuid` per brand (Tito's = TTO, uuid below). Other brands' uuids are
harvested from each brand's own "where to buy" page (`discover` below reads the iframe src).
An `h-captcha` CAN be armed server-side (`showCaptcha`); this run bails to `degraded` if so
rather than trying to solve it. Be polite (delay); the token is per-iframe-load, refetched
per ZIP. No UPC/price — this is account-level CARRIAGE, matched to the outlet spine by
name+address, feeding [[chain-inventory-acquisition]] and the outlets master.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
