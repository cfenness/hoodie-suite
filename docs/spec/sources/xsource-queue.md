# Match Trainer queue (candidate pool) — `xsource-queue`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `xsource-queue` |
| Runs | `import xsource_queue as m; m.build()` |
| Module | `unifyd/xsource_queue.py` — 334 lines |
| Cadence | every 168h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | free |
| Memory / timeout | 8192 MB / 7200 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/xsource_queue_test.py` |


**Registry note.** ranked pool of matching candidates — unseen difference-causes first, then rule/stratum disagreements, then widely-carried items. Resolutions land in xsource_gold + xsource_dictionary via /api/xsource/resolve.


## 2. Transport

_No literal endpoint constant in `xsource_queue.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `product_taxonomy`, `warehouse`, `xsource_gold`, `xsource_match`


## 3. What it lands


### `xsource_queue`

4,617 rows · 35 columns


| column | type |
|---|---|
| `pair_id` | `VARCHAR` |
| `stratum` | `VARCHAR` |
| `a_id` | `VARCHAR` |
| `a_source` | `VARCHAR` |
| `a_brand` | `VARCHAR` |
| `a_name` | `VARCHAR` |
| `a_size` | `VARCHAR` |
| `a_upc` | `VARCHAR` |
| `b_id` | `VARCHAR` |
| `b_source` | `VARCHAR` |
| `b_brand` | `VARCHAR` |
| `b_name` | `VARCHAR` |
| `b_size` | `VARCHAR` |
| `b_upc` | `VARCHAR` |
| `rule_merges` | `BOOLEAN` |
| `suggested` | `VARCHAR` |
| `suggest_reason` | `VARCHAR` |
| `label` | `VARCHAR` |
| `labelled_by` | `VARCHAR` |
| `labelled_at` | `VARCHAR` |
| `canon_brand` | `VARCHAR` |
| `canon_product` | `VARCHAR` |
| `canon_size` | `VARCHAR` |
| `canon_category` | `INTEGER` |
| `canon_type` | `VARCHAR` |
| `canon_class` | `VARCHAR` |
| `canon_subclass` | `VARCHAR` |
| `canon_varietal` | `VARCHAR` |
| `annotations` | `INTEGER` |
| `sample_seed` | `BIGINT` |
| `built_at` | `VARCHAR` |
| `priority` | `BIGINT` |
| `resolved` | `VARCHAR` |
| `queued_at` | `VARCHAR` |
| `difference` | `VARCHAR` |


**Written by** `xsource_queue.py:278` (write_accumulate), `xsource_queue.py:198` (write_accumulate)


## 4. `xsource_queue.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
xsource_queue.py — the endless matching queue behind the Match Trainer.

WHY A POOL AND NOT A LIVE QUERY
  Generating candidates means joining every image-bearing retail catalog to `xwalk_source_sku` and
  `dim_sku` — measured at tens of seconds on a warm machine and enough to OOM the serving box when
  done carelessly. That is fine as a nightly build and completely unacceptable per keystroke. So the
  pool is BUILT once into `xsource_queue` and the API just reads the next unresolved slice.

  There is no shortage of work: 67k master rows across the retail catalogs produced pools of ~851
  merged / ~40k near-miss / ~1.8k control pairs from five sources alone, so the queue is effectively
  endless and the interesting question is ORDER, not supply.

ORDER IS THE PRODUCT
  A random pair teaches almost nothing; the same difference answered ten times teaches nothing after
  the second. So the pool is ranked by how much a human answer would move the model:

    1. pairs whose difference class is UNSEEN or still inconsistent — a new cause, or one where the
       answers so far disagree, is where a judgement changes a rule
    2. pairs the rule and the signature DISAGREE about — the boundary
    3. multi-source items — an answer that resolves an item seen by four retailers is worth more
       than one seen by two, because divergence and coverage both key on the collapse
    4. everything else

  `priority` is stored on the row, so the ordering is inspectable and reproducible rather than being
  a query the UI happens to run.

RESOLUTIONS ARE DICTIONARY ENTRIES
  A resolution lands twice: as a labelled pair in `xsource_gold`, and as value mappings in
  `xsource_dictionary` (canonical value + the source spelling that maps to it). The dictionary is
  what makes the queue get faster — the next pair carrying an already-resolved spelling arrives
  pre-filled, so the human is teaching a vocabulary rather than re-answering.
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
