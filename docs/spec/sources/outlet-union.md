# Outlet pre-master — `outlet-union`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `outlet-union` |
| Runs | `import outlet_union as m; m.run()` |
| Module | `unifyd/outlet_union.py` — 150 lines |
| Cadence | daily |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 8192 MB / — s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** derived ($0): unions DoorDash/Toast outlet spines → mastered outlets + per-source menu freshness. mem was unset (defaults to 4096) — undersized for a national union-find that materializes doordash_stores + toast_outlets + toast_menu_accounts + naop_accounts fully in Python before resolving; OOM-killed 2026-07-29. Root cause is the join shape (no fat column to trim — the account tables are already lean), so this is a right-size, not a workaround; escalate to 16384 (geo's ceiling for a comparable-scale join) if this still OOMs.


## 2. Transport

_No literal endpoint constant in `outlet_union.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `outlet_ident`, `warehouse`


## 3. What it lands


### `outlet_master`

1,818,275 rows · 18 columns


| column | type | filled |
|---|---|---|
| `outlet_id` | `VARCHAR` | 100.0% |
| `name` | `VARCHAR` | 100.0% |
| `street` | `VARCHAR` | **0.2%** |
| `city` | `VARCHAR` | 100.0% |
| `state` | `VARCHAR` | 99.9% |
| `phone` | `VARCHAR` | **0%** ‹never populated› |
| `lat` | `DOUBLE` | **0%** ‹never populated› |
| `lng` | `DOUBLE` | **0%** ‹never populated› |
| `sources` | `VARCHAR` | 100.0% |
| `source_count` | `BIGINT` | 100.0% |
| `doordash_id` | `VARCHAR` | 100.0% |
| `toast_guid` | `VARCHAR` | **0%** ‹never populated› |
| `ubereats_id` | `VARCHAR` | **0%** ‹never populated› |
| `doordash_menu_date` | `VARCHAR` | **0.3%** |
| `toast_menu_date` | `VARCHAR` | **0%** ‹never populated› |
| `ubereats_menu_date` | `VARCHAR` | **0%** ‹never populated› |
| `freshest_source` | `VARCHAR` | **0.3%** |
| `freshest_date` | `VARCHAR` | **0.3%** |

Fill measured over **first 400,000 rows** (400,000 rows).

> **7 columns never populated:** `phone`, `lat`, `lng`, `toast_guid`, `ubereats_id`, `toast_menu_date`, `ubereats_menu_date`.
>
> Declared by a writer and always NULL or empty. That is a capture GAP when the source returns the field and the parse drops it, and it is CORRECT when the column is awaiting input (a label nobody has answered, a derived field a later build fills). The measurement cannot tell those apart — it tells you where to look.


**Written by** `outlet_union.py:137` (write_parquet)


## 4. `outlet_union.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
outlet_union.py — PRE-MASTER the on-premise outlets across every source, then judge menu freshness per source.

Each source (DoorDash, Toast, soon UberEats/Postmates) publishes its own store universe; we union them and
resolve the SAME physical outlet across sources via outlet_ident (phone / address / geo — the identity signals
we capture on the page during the menu pull). The mastered outlet then carries, per source, WHICH has it and
HOW FRESH its menu is — so the pipeline can pick the freshest source and target stale re-pulls.

CONSERVATIVE by design: only STRONG keys (phone/address/geo) collapse two records into one outlet — never
name alone (a chain is a hundred bars). Unenriched sitemap outlets stay their own outlet until a page fetch
gives them identity, so cross-source links grow as menu-pull coverage grows. Better to under-merge than to
falsely fuse two different bars.

DERIVED build ($0, no network). Reads the source outlet + menu-account tables, writes outlet_master.
    python outlet_union.py
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
