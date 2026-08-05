# Master quality — served canon identity (head-to-head) — `build-master-quality-canon`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-master-quality-canon` |
| Runs | `import master_quality as m; m.score_canon()` |
| Module | `unifyd/master_quality.py` — 284 lines |
| Cadence | every 24h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/master_quality_test.py` |


**Registry note.** canon_item_id vs item_key on the same gold → the served-identity P/R lift, measured every cycle


## 2. Transport

_No literal endpoint constant in `master_quality.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `master_quality_canon`

3 rows · 14 columns


| column | type |
|---|---|
| `version` | `BIGINT` |
| `ts` | `BIGINT` |
| `identity` | `VARCHAR` |
| `gold_version` | `BIGINT` |
| `n_pairs` | `BIGINT` |
| `n_all` | `BIGINT` |
| `tp` | `BIGINT` |
| `fp` | `BIGINT` |
| `fn` | `BIGINT` |
| `tn` | `BIGINT` |
| `precision` | `DOUBLE` |
| `recall` | `DOUBLE` |
| `f1` | `DOUBLE` |
| `coverage` | `DOUBLE` |


**Written by** `master_quality.py:224` (write_accumulate)


## 4. `master_quality.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
master_quality.py — PROVE the master, don't assert it (MOAT-PLAN.md Workstream M / M1+M2).

"We have a matching engine" is good-enough; "P/R measured, every gap a number" is best. This builds a
DETERMINISTIC gold set (no human guessing) and scores the master's identity decision against it:

  GOLD (deterministic, authoritative — no LLM/human needed for v1):
    • POSITIVE pairs — two source records sharing an exact normalized UPC. Same UPC ⇒ same item.
      Tests UNDER-merge (recall): does the master put them under one item_key?
    • NEGATIVE pairs — two source records with different normalized brands. Different brand ⇒ different
      item. Tests OVER-merge (precision) — and it is NOT circular even if the matcher uses UPC, because
      the matcher should never merge across brands regardless.

  SCORE: the master's decision = "same item_key in xwalk_source_sku". Precision / Recall / F1 over the
  balanced gold sample, plus COVERAGE (share of gold pairs both sides could be resolved to an item_key).

  BASELINE + REGRESSION: each run lands master_quality and compares to the prior run — P or R dropping
  beyond a tolerance is flagged. The gate is anti-REGRESSION (ratchet up from the measured baseline),
  not an aspirational target the master doesn't meet yet — the honest posture when recall starts low.

Gold pairs land append-only + versioned in `gold_matches` (every future steward decision extends it).
The hard tail (same brand / adjacent size / no UPC) is the next gold expansion — needs adjudication.

    python master_quality.py            # build gold + score, land gold_matches + master_quality
    python master_quality.py --stats
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
