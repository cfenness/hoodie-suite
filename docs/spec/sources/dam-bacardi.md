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


| column | type |
|---|---|
| `source_id` | `VARCHAR` |
| `vendor` | `VARCHAR` |
| `drive_id` | `BIGINT` |
| `drive_name` | `VARCHAR` |
| `folder_id` | `BIGINT` |
| `folder_path` | `VARCHAR` |
| `asset_id` | `BIGINT` |
| `asset_token` | `VARCHAR` |
| `name` | `VARCHAR` |
| `title` | `VARCHAR` |
| `description` | `VARCHAR` |
| `asset_type` | `VARCHAR` |
| `extension` | `VARCHAR` |
| `mime_type` | `VARCHAR` |
| `size_bytes` | `BIGINT` |
| `asset_url` | `VARCHAR` |
| `thumb_url` | `VARCHAR` |
| `download_url` | `VARCHAR` |
| `created_on` | `VARCHAR` |
| `updated_on` | `VARCHAR` |
| `rights_ref` | `VARCHAR` |
| `image_use` | `VARCHAR` |
| `image_scope` | `VARCHAR` |
| `retention` | `VARCHAR` |
| `phash` | `INTEGER` |
| `embedding_ref` | `INTEGER` |
| `withheld_reason` | `VARCHAR` |
| `pulled_at` | `VARCHAR` |


**Written by** `dam.py:770` (write_accumulate)


### `brand_events`

329 rows · 23 columns


| column | type |
|---|---|
| `event_id` | `VARCHAR` |
| `hoodie_brand_id` | `VARCHAR` |
| `brand_key` | `INTEGER` |
| `canon_brand` | `INTEGER` |
| `brand_resolution` | `VARCHAR` |
| `brand` | `VARCHAR` |
| `sku_id` | `INTEGER` |
| `event_type` | `VARCHAR` |
| `event_date` | `VARCHAR` |
| `event_date_precision` | `VARCHAR` |
| `market` | `VARCHAR` |
| `price` | `DOUBLE` |
| `currency` | `VARCHAR` |
| `abv` | `DOUBLE` |
| `title` | `VARCHAR` |
| `asset_count` | `BIGINT` |
| `source` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `source_asset_ids` | `VARCHAR` |
| `source_url` | `VARCHAR` |
| `rights_ref` | `VARCHAR` |
| `field_provenance` | `VARCHAR` |
| `fetched_at` | `VARCHAR` |


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
