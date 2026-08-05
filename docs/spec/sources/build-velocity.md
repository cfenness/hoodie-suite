# Velocity (implied sell-through) — `build-velocity`

> BUILD (derives from tables we already hold)

## 1. The contract

|  |  |
|---|---|
| Registry id | `build-velocity` |
| Runs | `import velocity as m; m.build()` |
| Module | `unifyd/velocity.py` — 202 lines |
| Cadence | every 12h |
| Enabled | **yes** |
| Executor class | `build` |
| Cost class | — |
| Memory / timeout | 4096 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/velocity_test.py` |


**Registry note.** inventory deltas → SALE units w/ noise-damp + confidence; count-tier sources only; brand×week mart


## 2. Transport

_No literal endpoint constant in `velocity.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `warehouse`


## 3. What it lands


### `fact_velocity`

3,319,500 rows · 17 columns


| column | type | filled |
|---|---|---|
| `source` | `VARCHAR` | 100.0% |
| `store_id` | `VARCHAR` | 100.0% |
| `product_id` | `VARCHAR` | 100.0% |
| `upc` | `VARCHAR` | **0.5%** |
| `brand` | `VARCHAR` | 92.3% |
| `week` | `TIMESTAMP` | 100.0% |
| `implied_units` | `DOUBLE` | 100.0% |
| `sale_events` | `DECIMAL(38,0)` | 100.0% |
| `restock_events` | `DECIMAL(38,0)` | 100.0% |
| `restock_units` | `DOUBLE` | 100.0% |
| `noise_damped_units` | `DOUBLE` | 100.0% |
| `censored_pairs` | `DECIMAL(38,0)` | 100.0% |
| `oos_events` | `DECIMAL(38,0)` | 100.0% |
| `pairs` | `DECIMAL(38,0)` | 100.0% |
| `jitter_frac` | `DOUBLE` | 100.0% |
| `cadence` | `DOUBLE` | 100.0% |
| `confidence` | `DOUBLE` | 100.0% |

Fill measured over **first 400,000 rows** (400,000 rows).

### `mart_velocity_brand_week`

13,282 rows · 9 columns


| column | type | filled |
|---|---|---|
| `brand` | `VARCHAR` | 100.0% |
| `week` | `TIMESTAMP` | 100.0% |
| `implied_units` | `DOUBLE` | 100.0% |
| `conf_wt_units_per_store` | `DOUBLE` | 100.0% |
| `cells` | `BIGINT` | 100.0% |
| `stores` | `BIGINT` | 100.0% |
| `restock_events` | `DECIMAL(38,0)` | 100.0% |
| `oos_events` | `DECIMAL(38,0)` | 100.0% |
| `avg_confidence` | `DOUBLE` | 100.0% |

Fill measured over **full table** (13,282 rows).

## 4. `velocity.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
velocity.py — delta decomposition → implied sell-through (MOAT-PLAN.md Workstream V2/V3).

The crown jewel: per-store inventory deltas ARE demand data. A count going 40→33→21→48 is two
sales then a restock, per store, per day — a signal SipSource (monthly, distributor grain) and
Nielsen (panel, lagged) structurally can't produce. This turns `retail_observations` into
`fact_velocity` with a per-cell CONFIDENCE, then a dimension-bounded brand×week serving mart.

Estimator (per source×store×sku, ordered by date; pure DuckDB window functions — binny's is 7.3M obs):
Each consecutive observation PAIR is classified by its qty delta `dq`, the gap in days, and — crucially,
because V1 measured binny's at 58% jitter — whether the move is NOISE:
  • NOISE     dq≠0, |dq|≤JITTER_ABS, price unchanged, promo unchanged  → shelf-recount wobble, DAMPED
              (the load-bearing guard: without it, binny's "sales" would be mostly noise)
  • SALE      dq<0 and not noise and not censored                      → implied units = -dq
  • RESTOCK   dq>0 and not noise                                       → delivery event (resets baseline)
  • OOS       qty=0 (enter) / recovered from 0 (exit)                  → censored demand + a void signal
  • CENSORED  gap too long vs the source's cadence (a restock could hide a drawdown) → excluded from the
              unit estimate, counted against confidence — never silently summed
Only COUNT-tier sources (real unit counts, per obs_quality_source) get a unit estimate; STATE-tier
sources (in/out only) still yield OOS/void signals but implied_units stays null (honest — we can't
invent counts we never observed).

Confidence 0–1 = cadence tightness × (1 − censored fraction) × (1 − ½·source jitter fraction),
clamped. Every downstream number displays it; below CONF_FLOOR the cell is suppressed, not shown.

    python velocity.py            # rebuild fact_velocity + mart_velocity_brand_week
    python velocity.py --stats
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
