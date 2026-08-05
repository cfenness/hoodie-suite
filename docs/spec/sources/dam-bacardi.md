# Bacardi Media Centre (public drive) — `dam-bacardi`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `dam-bacardi` |
| Runs | `import dam_dna as m; m.pull('bacardi')` |
| Module | `unifyd/dam_dna.py` — 426 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | free |
| Memory / timeout | 2048 MB / 1800 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `pypdf` |
| Unit test | `unifyd/dam_dna_test.py` |


**Registry note.** media.bacardilimited.com drive 42 — robots-permitted /drives/ JSON tree, no auth, 3 requests for the whole drive. POINTERS + facts only: the rights record classifies image reuse `prohibited`, so no asset bytes, hashes or embeddings are ever produced.


## 2. Transport

_No literal endpoint constant in `dam_dna.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `dam`, `dam_canon`, `rights`


## 3. What it lands


### `dam_assets`

2,490 rows · 28 columns


| column | type | filled |
|---|---|---|
| `source_id` | `VARCHAR` | 100.0% |
| `vendor` | `VARCHAR` | 100.0% |
| `drive_id` | `BIGINT` | 100.0% |
| `drive_name` | `VARCHAR` | 100.0% |
| `folder_id` | `BIGINT` | 100.0% |
| `folder_path` | `VARCHAR` | 100.0% |
| `asset_id` | `BIGINT` | 100.0% |
| `asset_token` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `title` | `VARCHAR` | 100.0% |
| `description` | `VARCHAR` | 100.0% |
| `asset_type` | `VARCHAR` | 100.0% |
| `extension` | `VARCHAR` | 99.5% |
| `mime_type` | `VARCHAR` | **1.0%** |
| `size_bytes` | `BIGINT` | 100.0% |
| `asset_url` | `VARCHAR` | 100.0% |
| `thumb_url` | `VARCHAR` | 99.0% |
| `download_url` | `VARCHAR` | 100.0% |
| `created_on` | `VARCHAR` | 100.0% |
| `updated_on` | `VARCHAR` | 100.0% |
| `rights_ref` | `VARCHAR` | 100.0% |
| `image_use` | `VARCHAR` | 100.0% |
| `image_scope` | `VARCHAR` | 100.0% |
| `retention` | `VARCHAR` | 100.0% |
| `phash` | `INTEGER` | **0%** ‹never populated› |
| `embedding_ref` | `INTEGER` | **0%** ‹never populated› |
| `withheld_reason` | `VARCHAR` | 100.0% |
| `pulled_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (2,490 rows).

> **2 columns never populated:** `phash`, `embedding_ref`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `dam.py:770` (write_accumulate)


### `brand_events`

329 rows · 23 columns


| column | type | filled |
|---|---|---|
| `event_id` | `VARCHAR` | 100.0% |
| `hoodie_brand_id` | `VARCHAR` | 100.0% |
| `brand_key` | `INTEGER` | **0%** ‹never populated› |
| `canon_brand` | `INTEGER` | **0%** ‹never populated› |
| `brand_resolution` | `VARCHAR` | 100.0% |
| `brand` | `VARCHAR` | 100.0% |
| `sku_id` | `INTEGER` | **0%** ‹never populated› |
| `event_type` | `VARCHAR` | 100.0% |
| `event_date` | `VARCHAR` | 87.8% |
| `event_date_precision` | `VARCHAR` | 100.0% |
| `market` | `VARCHAR` | 27.7% |
| `price` | `DOUBLE` | **3.3%** |
| `currency` | `VARCHAR` | **3.3%** |
| `abv` | `DOUBLE` | **3.3%** |
| `title` | `VARCHAR` | 100.0% |
| `asset_count` | `BIGINT` | 100.0% |
| `source` | `VARCHAR` | 100.0% |
| `source_id` | `VARCHAR` | 100.0% |
| `source_asset_ids` | `VARCHAR` | 100.0% |
| `source_url` | `VARCHAR` | 100.0% |
| `rights_ref` | `VARCHAR` | 100.0% |
| `field_provenance` | `VARCHAR` | 100.0% |
| `fetched_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (329 rows).

> **3 columns never populated:** `brand_key`, `canon_brand`, `sku_id`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `dam.py:776` (write_accumulate)


## 4. `dam_dna.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
dam_dna.py — the **DNA** DAM platform connector (dna.online). One connector per DAM VENDOR.

WHY THIS IS A VENDOR RECIPE, NOT A BACARDI SCRAPER
  Bacardi's media centre is not bespoke: its footer is "Powered by DNA" (`dna.online`,
  `/a/global/dna-footer-logo.v2.svg`), and the whole surface is DNA's stock shape — a `company_id`
  tenant, numbered `company_drive_id` drives, `/drives/view-new/<drive>`, `/drives/get-tree/<drive>`,
  `/company-files/<company>/original/<token>.<ext>`, an Algolia index prefixed `DNA_`. So proving the
  recipe once proves every supplier on DNA: point `TENANTS` at another host + drive and that
  supplier's whole public drive is a deterministic pull. Same payoff as the VIP Brand Builder and
  SevenFifty platform recipes ([[system-scrape-recipes]]), one tenant at a time.

  `fingerprint()` is the discovery half: hand it any candidate media-centre URL and it says whether
  the host is a DNA tenant (and which drive), which is what turns the P4 vendor census into new
  sources without new code.

ONE CONNECTOR PER VENDOR, ONE RIGHTS RECORD PER SUPPLIER
  These are different axes and both matter. The TRANSPORT is a property of the vendor (DNA), so it
  lives here once. The PERMISSION is a property of the supplier — Bacardi's terms bind Bacardi's
  assets and say nothing about anyone else's — so every tenant is its own registry source with its
  own `rights_records/<id>.json`, and `pull()` loads that record before it fetches anything.

THE TWO READS (both robots-PERMITTED; `/api/` is disallowed and we never touch it)
    GET /drives/view-new/<drive>               → HTML bootstrapping `window.DriveViewState = {...}`
                                                 (drive, root/current folder, **all_folders**, files)
    GET /drives/get-tree/<drive>?folder_id=<n> → {"status":"ok","body":{"tree":{…}}} — that folder

  DNA's robots.txt (captured verbatim in each tenant's rights record) disallows `/api/`, `/users/`,
  `/administrators/`, `/dashboard`, `/settings`, `/seo/`, `/login`, `/shared/`, `/account/`. Both
  reads sit outside every one of those, and `_check_robots` enforces it per request against the
  snapshot. If DNA ever moves the tree endpoint under `/api/`, this connector STOPS — it does not
  look for another way in. Nothing here enumerates ids, tampers with parameters, or probes for
  non-public drives: the drive id is the one published in the tenant's own navigation.

DEGRADED, NEVER SILENT
  `file_amount` is a stale denormalized counter on this platform — measured live on Bacardi, "Videos"
  reports 2 and serves 4, "Media Files" reports 0 and serves 75 — so coverage is NOT gated on it.
  Coverage is gated on visiting every folder in `all_folders`; a folder that serves fewer files than
  its own counter claims is re-fetched and reported. A run that can't parse the bootstrap, sees 0
  files from a populated drive, or leaves a folder unvisited is `degraded`, never a quiet partial.

CLI:  python dam_dna.py --tenant bacardi              # full drive, land
      python dam_dna.py --tenant bacardi --no-land    # parse only
      python dam_dna.py --rights bacardi              # show the gate decisions and exit
      python dam_dna.py --fingerprint https://media.example.com/   # is this host a DNA tenant?
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
