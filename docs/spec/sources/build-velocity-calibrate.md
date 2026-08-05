# Velocity calibration (conservation + MAPE) — `build-velocity-calibrate`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-velocity-calibrate` |
| Runs | `import velocity_calibrate as m; m.build()` |
| Module | `unifyd/velocity_calibrate.py` — 163 lines |
| Cadence | every 24h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/velocity_calibrate_test.py` |


**Registry note.** conservation ratio (sales≈restock) live; external MAPE pending an overlapping footprint


## 2. Transport

_No literal endpoint constant in `velocity_calibrate.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `velocity_calibration`

5 rows · 6 columns


| column | type |
|---|---|
| `kind` | `VARCHAR` |
| `anchor` | `VARCHAR` |
| `source` | `VARCHAR` |
| `coverage` | `BIGINT` |
| `metric` | `VARCHAR` |
| `value` | `DOUBLE` |


**Written by** `velocity_calibrate.py:142` (write_parquet)


## 4. `velocity_calibrate.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
velocity_calibrate.py — prove the velocity engine is RIGHT, not just plausible (MOAT-PLAN V4).

Two validators, honest about what each can do TODAY:

1. CONSERVATION (internal, runs now, needs no external data). In steady state a store re-stocks what
   it sells, so across a stable population implied SALES ≈ implied RESTOCK. The ratio is a calibration
   signal with a direction: ratio ≫ 1 ⇒ we're OVER-counting (recount noise leaking through as sales);
   ratio ≪ 1 ⇒ UNDER-counting (censoring/OOS eating real drawdowns). This is the accuracy number we
   can publish immediately, per source, and it directly critiques the estimator's own settings.

2. EXTERNAL MAPE (the scoreboard headline — needs an overlapping footprint). Joins velocity rolled to
   brand×market×period against a ground-truth actuals adapter, fits a single scale factor (least
   squares through the origin — velocity is a proportional proxy, not absolute), and reports MAPE plus
   COVERAGE (how many cells actually overlapped). It reports coverage HONESTLY: today the velocity
   footprint (binny's IL, sevennow TX) does not overlap the available anchors (Montgomery County MD
   sales; OR/UT/NC are price lists, not volume; Iowa BigQuery not yet landed), so coverage=0 and the
   harness says so rather than inventing a number. The moment a velocity source lands in an anchor
   market — or Iowa is pulled — this produces the real MAPE with no code change.

Ground-truth adapters (add one row to ANCHORS to wire a new anchor):
  mont_sales  Montgomery County MD DLC — real monthly retail unit sales (rtl_sales) by item×month.
  (iowa)      Iowa BigQuery — every Class-E spirits transaction; store-level, current. Land via iowa_bq.

    python velocity_calibrate.py                 # conservation + attempt every anchor
    python velocity_calibrate.py --anchor mont_sales --stats
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
