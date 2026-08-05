# TTB COLA scrape — `ttb-cola`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ttb-cola` |
| Runs | `import ttb_pull as m; m.run()` |
| Module | `unifyd/ttb_pull.py` — 258 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / 5400 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `bs4`, `pillow`, `pylibdmtx`, `pytesseract`, `pyzbar` |
| Unit test | **none** |


**Registry note.** $0 off-Mac incremental COLA scrape (last TTB_DAYS) → accumulate ttb_cola; ttbonline.gov verify=False, direct (no BD/browser)


## 2. Transport

_No literal endpoint constant in `ttb_pull.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `ttb_cola_labels`, `ttb_cola_scraper`, `ttb_enrich`, `upc`, `warehouse`


## 3. What it lands


### `ttb_cola`

1,071,850 rows · 13 columns


| column | type | filled |
|---|---|---|
| `TTB ID` | `VARCHAR` | 100.0% |
| `Permit Number` | `VARCHAR` | 100.0% |
| `Serial Number` | `VARCHAR` | 100.0% |
| `Brand Name` | `VARCHAR` | 100.0% |
| `Fanciful Name` | `VARCHAR` | **0%** ‹never populated› |
| `Class/Type` | `VARCHAR` | 100.0% |
| `Origin` | `VARCHAR` | 100.0% |
| `Applicant` | `VARCHAR` | **0%** ‹never populated› |
| `Status` | `VARCHAR` | **0%** ‹never populated› |
| `Completed Date` | `VARCHAR` | 100.0% |
| `Approval Date` | `VARCHAR` | **0%** ‹never populated› |
| `Net Contents` | `VARCHAR` | **0%** ‹never populated› |
| `UPC` | `VARCHAR` | **0%** ‹never populated› |

Fill measured over **first 400,000 rows** (400,000 rows).

> **6 columns never populated:** `Fanciful Name`, `Applicant`, `Status`, `Approval Date`, `Net Contents`, `UPC`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `ttb_pull.py:45` (write_accumulate)


## 4. `ttb_pull.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
ttb_pull.py — incremental TTB COLA scrape that runs OFF-MAC (Fly ephemeral), landing into ttb_cola.

The COLA scraper (ttb_cola_scraper) already works from a server container: ttbonline.gov serves an
incomplete TLS chain, so it uses verify=False and talks DIRECT (Bright Data DC zones KYC-gate .gov) — no
browser warm, no F5 dance, no Mac. Verified live from Fly: a 2-day window returned 160 COLAs, status success.
The only thing that kept TTB on the Mac was that no registry source ran the scrape + LANDED it; the app path
(cola_pull) only scraped to an in-memory preview. This is that source.

Each run scrapes the last TTB_DAYS (default 14) of the public COLA registry → a CSV on the ephemeral machine
→ ACCUMULATE into ttb_cola by TTB ID (never overwrites the ~1M-row backfill; the overlap just dedups). The
CSV columns are exactly ttb_cola_scraper.COLA_HEADER, which matches the ttb_cola schema, so no drift.

    python ttb_pull.py                 # last 14 days
    python ttb_pull.py --days 30       # wider catch-up window
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
