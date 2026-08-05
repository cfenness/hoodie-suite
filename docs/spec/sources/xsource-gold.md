# Cross-source gold set (human labelling) — `xsource-gold`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `xsource-gold` |
| Runs | `import xsource_gold as m; m.main(['export'])` |
| Module | `unifyd/xsource_gold.py` — 441 lines |
| Cadence | every 8760h |
| Enabled | no — does not run on a cadence |
| Executor class | `build` |
| Cost class | free |
| Memory / timeout | 8192 MB / 3600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/xsource_gold_test.py` |


**Registry note.** export -> a human labels y/n/? -> ingest -> score. Stratified merged/near_miss/control; the control rows are same-product-different-size and audit the labeller.


## 2. Transport

_No literal endpoint constant in `xsource_gold.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`, `xlsx_write`, `xsource_match`


## 3. What it lands


### `xsource_gold`

6 rows · 31 columns


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


**Written by** `xsource_gold.py:383` (write_accumulate), `xsource_gold.py:430` (write_accumulate)


## 4. `xsource_gold.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
xsource_gold.py — build the HUMAN-labelled gold set that `xsource_match` needs.

WHY THIS IS NEEDED AT ALL
  The cross-source merge scored precision 0.233 against UPC-derived gold, and that number cannot be
  trusted in either direction: 59,455 of 67,099 rows were unscoreable because the sources that most
  need merging (binnys, abc, total-wine) carry no UPC, and a `resolved_id` can legitimately span
  several UPCs, which makes UPC-disagreement a harsher test than "different item". So the matcher is
  currently unmeasured, not proven bad — and an unmeasured matcher cannot ship
  ([[matching-at-scale]]: humans handle exceptions, machines handle scale).

THE SHAPE, WHICH IS THE PLATFORM'S EXISTING PATTERN
  Precompute candidates → a model pre-adjudicates → a human confirms a prioritized queue. Three
  rules make the resulting gold trustworthy rather than circular:

  1. **STRATIFIED, AND SCORED PER STRATUM.** Sampling only the pairs the rule merges measures
     precision and tells you nothing about recall. So the sheet mixes `merged` (what the rule
     claims), `near_miss` (same brand+size, names differ — what it declined), and `control` (same
     brand, DIFFERENT size — which must always be NO). Blending the strata into one accuracy number
     would hide which half is broken, so `score()` reports them separately.

  2. **THE MODEL'S OPINION NEVER OCCUPIES THE ANSWER COLUMN.** `suggested` sits in its own column
     with its reason; `label` ships EMPTY. A pre-filled answer column produces rubber-stamping, and
     a gold set that agrees with the model by construction measures nothing. The model is there to
     order the queue and explain itself, not to answer.

  3. **THE CONTROL STRATUM IS THE LABELLER'S OWN CHECK.** Controls have a known answer (different
     size = different item). A labelled sheet whose controls come back wrong is a sheet filled in
     carelessly, and `ingest()` reports that rather than folding it into the score.

USAGE
    python xsource_gold.py export --n 300 --out /tmp/gold.xlsx    # build the sheet
    #   ... a human fills the `label` column with y / n / ? ...
    python xsource_gold.py ingest --path /tmp/gold.xlsx           # land it
    python xsource_gold.py score                                  # measure the matcher on it
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
