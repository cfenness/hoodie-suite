# DAM vendor census (supplier -> media centre -> vendor) — `dam-census`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `dam-census` |
| Runs | `import dam_census as m; m.run()` |
| Module | `unifyd/dam_census.py` — 703 lines |
| Cadence | monthly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | free |
| Memory / timeout | 2048 MB / 3600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/dam_census_test.py` |


**Registry note.** link discovery from each supplier's OWN published nav (no hostname guessing; DAM_CENSUS_PROBE=1 opts into conventional media.*/press.* hosts). Names its failures — age gate / JS shell / no link — rather than reporting them as 'no media centre'.


## 2. Transport

_No literal endpoint constant in `dam_census.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `dam_dna`, `rights`, `warehouse`


## 3. What it lands


### `dam_census`

67 rows · 26 columns


| column | type | filled |
|---|---|---|
| `supplier` | `VARCHAR` | 100.0% |
| `corporate_domain` | `VARCHAR` | 100.0% |
| `media_url` | `VARCHAR` | 82.1% |
| `media_host` | `VARCHAR` | 82.1% |
| `dam_vendor` | `VARCHAR` | 7.5% |
| `vendor_signals` | `VARCHAR` | 9.0% |
| `vendor_confidence` | `VARCHAR` | 9.0% |
| `kind` | `VARCHAR` | 47.8% |
| `public` | `BOOLEAN` | 32.8% |
| `drive_id` | `INTEGER` | **0%** ‹never populated› |
| `company_id` | `INTEGER` | **0%** ‹never populated› |
| `reachable` | `BOOLEAN` | 100.0% |
| `http_status` | `BIGINT` | 95.5% |
| `robots_allows` | `BOOLEAN` | 88.1% |
| `tos_url` | `VARCHAR` | 53.7% |
| `tos_chars` | `BIGINT` | 70.1% |
| `tos_capture` | `VARCHAR` | 70.1% |
| `image_use` | `VARCHAR` | 44.8% |
| `scope` | `VARCHAR` | 44.8% |
| `confidence` | `VARCHAR` | 44.8% |
| `needs_counsel` | `BOOLEAN` | 70.1% |
| `provisional` | `BOOLEAN` | 100.0% |
| `discovery_method` | `VARCHAR` | 100.0% |
| `connector` | `VARCHAR` | **1.5%** |
| `notes` | `VARCHAR` | 95.5% |
| `checked_at` | `VARCHAR` | 100.0% |

Fill measured over **full table** (67 rows).

> **2 columns never populated:** `drive_id`, `company_id`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `dam_census.py:654` (write_accumulate)


## 4. `dam_census.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
dam_census.py — map SUPPLIER → media centre → DAM VENDOR → public? → provisional permission class.

This is the multiplier's input. `dam_dna.py` proved that one connector covers every supplier on one
platform; the census is what tells you WHICH platform each supplier is on, so connector work is
spent on the vendors that cover the most suppliers instead of on whoever came up first.

HOW A MEDIA CENTRE IS FOUND — AND THE GUARDRAIL THAT SHAPED IT
  The capability's method rule is: public share drives + documented public APIs + robots-permitted
  paths only, and explicitly **no subdomain enumeration**. The design's own discovery sketch lists
  `media.<co>.com` / `press.<co>.com` / `assets.<co>.com` as patterns to try, which is in tension
  with that rule — guessing hostnames is a mild form of the thing the rule forbids.

  Resolved in favour of the rule, and the result is better discovery anyway:

    PRIMARY (default, always on) — LINK DISCOVERY. Fetch the supplier's own corporate site and read
    the media-centre link THEY PUBLISH. Nothing is guessed; we follow what the company chose to make
    public, which is also how a human would find it. One request per supplier, robots-checked.

    SECONDARY (opt-in, `DAM_CENSUS_PROBE=1`, off by default) — the conventional hostnames. Every row
    records `discovery_method`, so a link-discovered centre and a probed one are never confused in
    the output, and the census can be run entirely within the strict reading of the rule.

  Nothing here touches cert-transparency logs, wordlists, or non-public drives.

WHAT A CENSUS ROW CARRIES
  supplier, corporate domain, media-centre URL, DAM VENDOR (fingerprinted), public-vs-gated, robots
  posture, and a PROVISIONAL permission class from `rights.classify` over the terms we could reach.
  Provisional is the operative word: it is a machine read of a ToS page, it is never a rights record,
  and `dam_census` is a research table — no connector may run off it. Promoting a supplier means
  authoring a reviewed `rights_records/<id>.json` and a TENANTS row, deliberately.

THE TWO PLANS (design §4)
  `plan(url)` emits the extraction plan (vendor, folder API, asset types, public?) AND the rights
  plan (robots, ToS snapshot + classification, needs_counsel) for one media-centre URL. That is what
  `source_analyzer.analyze()` attaches when a page fingerprints as a DAM, so pointing the analyzer at
  a media centre answers "how would we pull it" and "may we" in the same pass.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
