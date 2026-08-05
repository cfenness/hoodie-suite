# TTB COLA enrich (detail+UPC) — `ttb-enrich`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `ttb-enrich` |
| Runs | `import ttb_pull as m; m.run_enrich()` |
| Module | `unifyd/ttb_pull.py` — 258 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 8192 MB / 7200 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `bs4`, `pillow`, `pylibdmtx`, `pytesseract`, `pyzbar` |
| Unit test | **none** |


**Registry note.** $0 off-Mac producer that EXTENDS the existing ttb_cola_detail + ttb_cola_labels (accumulate by ttb_id, snake_case schemas via ttb_enrich's validated parsers) for COLAs not yet detailed — new COLAs from ttb-cola get detail + label-barcode UPC off-Mac. Gentle concurrency on the .gov site (TTB_ENRICH_WORKERS=4); needs libzbar0+pyzbar+pillow (in the image)


## 2. Transport

_No literal endpoint constant in `ttb_pull.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `ttb_cola_labels`, `ttb_cola_scraper`, `ttb_enrich`, `upc`, `warehouse`


## 3. What it lands


### `ttb_cola_detail`

1,858,375 rows · 21 columns


| column | type | filled |
|---|---|---|
| `ttb_id` | `VARCHAR` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `vendor_code` | `VARCHAR` | 87.1% |
| `serial_number` | `VARCHAR` | 100.0% |
| `class_type_code` | `VARCHAR` | **0%** ‹never populated› |
| `class_type_desc` | `VARCHAR` | 100.0% |
| `origin_code` | `VARCHAR` | 100.0% |
| `brand_name` | `VARCHAR` | 100.0% |
| `fanciful_name` | `VARCHAR` | 33.6% |
| `application_type` | `VARCHAR` | 95.4% |
| `for_sale_in` | `VARCHAR` | **0.2%** |
| `net_contents` | `VARCHAR` | 28.4% |
| `wine_vintage` | `VARCHAR` | 69.2% |
| `grape_varietal` | `VARCHAR` | 85.3% |
| `alcohol_content` | `VARCHAR` | **0%** ‹never populated› |
| `formula` | `VARCHAR` | **2.7%** |
| `approval_date` | `VARCHAR` | 98.9% |
| `qualifications` | `VARCHAR` | **0%** ‹never populated› |
| `plant_permit` | `VARCHAR` | **0%** ‹never populated› |
| `label_image_url` | `VARCHAR` | 98.4% |
| `other_json` | `VARCHAR` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

> **4 columns never populated:** `class_type_code`, `alcohol_content`, `qualifications`, `plant_permit`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `ttb_pull.py:220` (write_accumulate)


### `ttb_cola_labels`

23,874 rows · 11 columns


| column | type | filled |
|---|---|---|
| `ttb_id` | `VARCHAR` | 100.0% |
| `image_file` | `VARCHAR` | 99.7% |
| `upc` | `VARCHAR` | 34.9% |
| `abv` | `VARCHAR` | 24.2% |
| `net_contents` | `VARCHAR` | 55.1% |
| `claims` | `VARCHAR` | 19.6% |
| `gov_warning` | `VARCHAR` | 98.7% |
| `ocr_chars` | `VARCHAR` | 99.0% |
| `front_label_url` | `VARCHAR` | 99.7% |
| `back_label_url` | `VARCHAR` | 40.9% |
| `label_urls` | `VARCHAR` | 99.7% |

Fill measured over **full table** (23,874 rows).

**Written by** `ttb_pull.py:222` (write_accumulate)


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
