# Velocity signals (movers + voids) — `build-velocity-signals`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-velocity-signals` |
| Runs | `import velocity_signals as m; m.build()` |
| Module | `unifyd/velocity_signals.py` — 200 lines |
| Cadence | every 12h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/velocity_signals_test.py` |


**Registry note.** matched-cell WoW movers (partial-week flagged) + OOS void opportunities w/ recoverable units


## 2. Transport

_No literal endpoint constant in `velocity_signals.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `signal_movers`

1,015 rows · 12 columns


| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `week` | `TIMESTAMP` | 100.0% |
| `prev_week` | `TIMESTAMP` | 100.0% |
| `matched_cells` | `BIGINT` | 100.0% |
| `units` | `DOUBLE` | 100.0% |
| `prev_units` | `DOUBLE` | 100.0% |
| `delta_units` | `DOUBLE` | 100.0% |
| `pct_change` | `DOUBLE` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |
| `partial` | `BOOLEAN` | **0%** ‹never populated› |
| `cur_obs_days` | `INTEGER` | 100.0% |
| `prev_obs_days` | `INTEGER` | 100.0% |

Fill measured over **full table** (1,015 rows).

> **1 column never populated:** `partial`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


### `signal_voids`

2,235 rows · 8 columns


| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `oos_stores` | `BIGINT` | 100.0% |
| `stores_seen` | `BIGINT` | 100.0% |
| `oos_events` | `DECIMAL(38,0)` | 100.0% |
| `typ_units_per_store_wk` | `DOUBLE` | 100.0% |
| `est_recoverable_units_wk` | `DOUBLE` | 100.0% |
| `pct_stores_out` | `DOUBLE` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |

Fill measured over **full table** (2,235 rows).

## 4. `velocity_signals.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
velocity_signals.py — the salable output on top of velocity (MOAT-PLAN.md Workstream V5).

What a sales team actually buys, derived from fact_velocity + its confidence:

  signal_movers  brand acceleration/deceleration, week-over-week. MATCHED-CELL (same-store-sales)
                 method: only cells (store×sku) observed in BOTH weeks count — so a partial
                 observation week can't manufacture a fake crash (weeks 1 & 3 in the current data are
                 partial; raw-unit WoW would lie, matched-cell doesn't). Confidence-gated.
  signal_voids   distribution OPPORTUNITIES, framed as the win + the next move (positive-framing
                 doctrine): a brand went out of stock at N stores → estimated lost units = the
                 brand's own typical per-store velocity × the out cells. Every void cites its evidence.

Both are dimension-small (brand grain), land to the warehouse, and carry confidence + as_of so the
surface can prove what it shows. Restock-cadence (order-timing) needs the finer restock-timing grain
than the weekly fact carries — deferred to a follow-up that reads the pair stream directly.

    python velocity_signals.py            # rebuild signal_movers + signal_voids
    python velocity_signals.py --stats
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
