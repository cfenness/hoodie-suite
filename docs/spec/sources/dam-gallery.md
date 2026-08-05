# DAM CV reference gallery (scope-gated) — `dam-gallery`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `dam-gallery` |
| Runs | `import dam_gallery as m; m.build('dam-bacardi')` |
| Module | `unifyd/dam_gallery.py` — 294 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | free |
| Memory / timeout | 4096 MB / 3600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `pillow` |
| Unit test | `unifyd/dam_gallery_test.py` |


**Registry note.** pointer + licence + pHash + embedding per studio image, each derivation gated per asset. Embedding backend is pluggable and ABSENT by default (torch is not in the image) — rows land NULL vectors and name the backend rather than looking empty.


## 2. Transport

_No literal endpoint constant in `dam_gallery.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `dam`, `rights`, `warehouse`


## 3. What it lands


### `dam_gallery`

66 rows · 25 columns


| column | type |
|---|---|
| `gallery_id` | `VARCHAR` |
| `source_id` | `VARCHAR` |
| `asset_id` | `BIGINT` |
| `asset_url` | `VARCHAR` |
| `vendor` | `VARCHAR` |
| `hoodie_brand_id` | `INTEGER` |
| `brand` | `INTEGER` |
| `brand_key` | `INTEGER` |
| `sku_id` | `INTEGER` |
| `sku_match_method` | `INTEGER` |
| `image_kind` | `VARCHAR` |
| `width` | `INTEGER` |
| `height` | `INTEGER` |
| `size_bytes` | `BIGINT` |
| `phash` | `INTEGER` |
| `phash_algo` | `INTEGER` |
| `embedding` | `INTEGER` |
| `embedding_backend` | `VARCHAR` |
| `embedding_dim` | `BIGINT` |
| `retention` | `VARCHAR` |
| `rights_ref` | `VARCHAR` |
| `image_use` | `VARCHAR` |
| `image_scope` | `VARCHAR` |
| `withheld_reason` | `VARCHAR` |
| `built_at` | `VARCHAR` |


**Written by** `dam_gallery.py:272` (write_accumulate)


## 4. `dam_gallery.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
dam_gallery.py — the CV reference gallery (P3): official studio imagery per SKU, scope-gated.

WHAT THIS IS FOR
  A shelf photo is a hard recognition problem partly because there is no clean reference to match
  against. A supplier's own media centre has exactly that: the studio bottle shot, lit and centred,
  produced by the brand. This turns those into per-SKU reference rows the CV pipeline can match to.

WHAT IT STORES, AND WHY THE POINTER IS THE DEFAULT
  Per the design: **pointer + licence + perceptual hash + embedding** for every image, and the FULL
  asset only where the source's scope permits it. So a gallery row always exists — it is a fact about
  a file we can see — and the derived artefacts appear only when `rights.may` says so. A row with a
  NULL phash and a `withheld_reason` is the honest shape for a source we may not derive from; a
  missing row would just look like the supplier had no imagery.

THE GATE RUNS PER ASSET, NOT PER RUN
  Every image goes through `rights.require(rec, "derive_hash")` / `"derive_embedding"` individually
  and every emission is logged. `build()` cannot be talked into a bulk exception, and it re-checks
  rather than caching a verdict from the top of the run — a record that goes stale mid-run stops the
  rest of it.

WHAT A pHASH IS AND IS NOT GOOD FOR HERE
  The dHash implemented below is for **identity within the gallery** — the same studio file uploaded
  five times under five names collapses to one reference, and a re-pull can tell "already have this"
  from "new asset". It is explicitly NOT the studio→shelf matcher: perceptual hashes fail on bottles
  photographed in the wild ([[image-match-signal]]), where the embedding is the real signal. Stating
  that here because a pHash column invites exactly the wrong assumption.

THE EMBEDDING IS PLUGGABLE AND ABSENT BY DEFAULT
  CLIP-class embedding needs torch, which is a heavy dependency this image does not carry and which
  is not being added on my own authority. `embedder()` resolves one if the host has it and otherwise
  reports `embedding_backend="unavailable"` — landing the row with a NULL embedding and SAYING so,
  rather than silently shipping a gallery with no vectors in it ([[quiet-degrades]]).

STATUS: BUILT, AND CURRENTLY EMPTY ON PURPOSE
  No supplier we have surveyed grants image reuse. Bacardi is `prohibited`; the three census
  candidates (AB InBev, William Grant, Heaven Hill) all turned out to be INBOUND user-content
  licences, not grants to us — they classify `silent`, which holds. So this pipeline runs, produces
  pointer rows, and derives nothing. That is the gate working. The first real gallery needs a
  supplier whose terms grant AND a `counsel_cleared` sign-off on that record.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
