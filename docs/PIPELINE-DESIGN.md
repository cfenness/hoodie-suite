# Pipeline design — one path per source, inspectable at every stage

**Status: design, not implementation.** Nothing here is built. Every claim about current
behaviour cites file:line and was verified by reading the code; where something is inferred
rather than confirmed it says so.

## The problem, stated once

The data is not trusted, and the reason is not that it is wrong — it is that **nothing can be
checked**. Concretely, today:

- 62 of 208 warehouse write sites (30%) do not name their table in the source; the name is
  computed at run time. No complete table map can be derived by reading the code.
- A source is graded on the row-count delta of its *declared* tables. `ubereats-enrich`
  declares `ubereats_products` and writes `ubereats_products_parts` (`ue_enrich.py:97`), so its
  delta is always 0 and `run_sources.py:456` reports `current` — the same status whether it
  lands 500,000 rows or none.
- `ubereats_products` has two independent `write_accumulate` writers — the fold, and any zone
  crawl via `ubereats.land()` (`ubereats.py:652`, called at `ue_crawl.py:412/414/520/525`).
  `warehouse.py` states the consequence: concurrent callers silently drop each other's rows.
- The fold reads the entire parts history into memory every run and prunes nothing
  (`ue_catalog.consolidate`), so its cost grows with total history rather than new data.
- The default fold is an additive merge, so `ubereats_products` carries ~98k rows whose
  provenance, in the module's own words, "cannot be stated".

Trust is not restored by fixing these one at a time. It is restored when every stage can be
**seen**, and when there is only one way for data to get from one stage to the next.

## The stage model

Six stages. Data moves in one direction. Each arrow is the *only* way across.

```
  0 discover   →  outlets / universe        who exists
  1 capture    →  <source>_parts            append-only, one part per run×scope×shard
  2 consolidate→  <source>_<grain>          per-source aggregate, ONE writer (the fold)
  3 normalize  →  src_brands/products/…     cross-source, still source-attributed
  4 master     →  dim_supplier/brand/…      resolved identity
  5 facts      →  fact_price/inventory/…    volatile observations, riding alongside
```

Stages 0–2 are per source. Stages 3–5 are cross-source. `retail_observations` is a stage-5
table fed from stage 1, not a per-source table — which is why declaring it in a source's
`tables=[...]` makes that source's landing delta meaningless (it moves when *any* source runs).

## The four contracts

Every stage obeys all four. Most of what we found is a violation of one of them.

**C1 — One writer per table.** Only the fold writes a stage-2 aggregate. Everything upstream
appends a part. This is already the documented rule for shards; it is not enforced against
non-sharded writers, which is how `ue_crawl` writes the aggregate directly. The existing guard
(`warehouse.py`, raises when `HOODIE_SHARD`/`UE_SHARD` is set) does not fire for unsharded runs.

**C2 — Schema belongs to the table, not the call site.** `dtypes` is currently an argument at
each `write_partition` call, which is why 12 of 16 sites omit it and why two tables have been
made unreadable by inferred-schema drift. A table declares its fields and types once; writers
inherit them.

**C3 — Every promotion has a watermark.** A stage knows what it has already consumed. This is
what makes the fold incremental (cost proportional to new parts, not history), makes parts
prunable, and makes "how much is waiting" a number you can display rather than a guess.

**C4 — Advancing a stage is triggered by its own backlog.** Today a fold runs when an upstream
reports `ok` (`run_sources.due_builds`), which fails four ways: a failed fold does not retry
(it compares against `last_attempt`), a source landing under a non-`ok` status never triggers,
`after=[...]` is hand-typed and omits `ubereats-enrich`, and builds share `MAX_SPAWN` with
sources so a busy tick spawns none. A stage should advance because it has unconsumed input.

## One parameterized program per source

A source is **one program**. Variants are parameters — never separate modules, never separate
registry entries.

```
  <source>(scope=…, refresh=…, shard=…)
```

- `scope` — universe | zone | state | store-set. **"A Florida run" is this parameter.** It
  produces a part tagged with the scope; it is not a separate dataset and not a separate source.
- `refresh` — which fields to re-fetch. This is the one distinction that must survive
  collapsing catalog and enrich: UPC/GTIN are static per item while price and stock are
  volatile, and `ue_catalog.py:182` records that re-enriching resolved items daily would cost
  ~30M requests. The split exists for refresh economics, not capability.
- `shard` — parallelism. Shards append parts; they never merge.

For UberEats this collapses eight registry entries into one program with parameters.
`ue_catalog` already proves the shape: `--no-enrich` (`ue_catalog.py:997`) toggles enrichment
inline (`:799`), which is why Postmates runs catalog+enrich in one pass while UberEats runs them
as two jobs. Same capability, two topologies, decided by a flag in a hand-typed string.

`ue_crawl` should be archived rather than deleted (the work is expensive to re-derive). Its
parameter surface is a superset of catalog's; the one capability to preserve is zone-based
discovery (`--zones`/`--coverage`), which becomes stage 0's `scope` parameter.

**The registry declares; it does not script.** Today each entry carries an inline Python string.
A platform declaration expanded at import — sites × phases — makes the dependency graph derived
rather than typed, which is what stops `after=[...]` from silently omitting a phase. Any real
per-site difference must then be an explicit override instead of a diff between two strings.

## Inspection: the part that actually fixes trust

Each stage must answer the same five questions, for any source, from real landed data:

| question | why it matters |
|---|---|
| how many rows arrived, and when | the landing signal, measured on the table actually written |
| how many are waiting to be promoted | the watermark gap — where data is stuck, as a number |
| what does a row look like *here* | inspect real data at this stage, not a summary of it |
| what was dropped between stages, and why | a fold that discards must say what and why |
| which run/part produced this row | provenance answerable per row, not per table |

Two design rules for these surfaces, both learned from failures already in this repo:

- **Nothing-to-do is not a failure, and neither is it success.** A stage with an empty backlog
  should read distinctly from one that is stalled. The current `current`/`ok`/`empty` collapse
  is what let a source report benignly while landing nothing.
- **A withheld number is better than a padded one.** Where a count cannot be computed (an
  unreadable table, a stage with no watermark yet), the surface says so rather than showing 0.
  A missing table must never read as a low row count.

The suite already hosts the right shell: `apps/mdm.html` is a composite console and
`apps/runs.html`, `mdm-sources.html`, `mdm-provenance.html` already exist. What none of them
shows is **data moving between stages** — they are run-oriented and source-oriented. The new
surface is stage-oriented: one row per (source × stage), with backlog, last promotion, and a
drill-through to real rows.

## Current state → target

| stage | exists today | gap |
|---|---|---|
| 0 discover | `ue_sitemap`, `ue_crawl --coverage`, `src_outlets` | two discovery paths, no scope parameter; `src_outlets` has **8 writing modules** (unlocked merge) |
| 1 capture | `<t>_parts` via `write_partition` | schema per call site (C2); parts never pruned (C3); part key format differs per writer |
| 2 consolidate | `build-ue-catalog` → `ue_catalog.consolidate` | in-memory fold over full history; second writer via `ue_crawl` (C1); additive merge, so not a function of its inputs |
| 3 normalize | `normalize.py` → `src_*` | cross-source product layer is thin next to `src_outlets` |
| 4 master | `build_product_master.py` → `dim_*` | classified structurally **sound** — reproducible; blocked by its inputs, not itself |
| 5 facts | `facts.py`, `retail_observations` | `fact_price`/`fact_inventory` written without pinned dtypes (C2) |

The master chain is the good news: it is a single-writer full rebuild, so it is reproducible by
construction. It is blocked by what feeds it, not by itself.

## What each contract closes

| finding | closed by |
|---|---|
| `ue_crawl` writes the aggregate directly; lost updates | C1 |
| 12/16 `write_partition` without dtypes; 2 tables made unreadable | C2 |
| fold cost grows with history; eventual stall | C3 |
| ~98k rows of unstateable provenance | C3 + rebuild-from-parts |
| failed fold never retries; `after=` omits enrich; builds starve | C4 |
| `ubereats-enrich` can never report `ok` | landing measured on the written table |
| 8 entries for one platform; hidden `--no-enrich` divergence | one parameterized program |
| "I can't tell what we have" | stage inspection |

## Open decisions — deliberately not made here

1. **Are discovery and catalog one function or two?** Everything else hangs off this. Two
   functions means `scope` is a discovery parameter and catalog always takes a store-set. One
   means catalog takes `scope` directly and discovery is an internal detail.
2. **Read-time merge, or fold?** A stage-2 view over parts (latest-per-key at query time)
   removes the fold entirely and makes staleness zero, at query cost. A fold keeps reads cheap
   and accepts one promotion of latency. The bucketed layout (`__b=<hex>`, already implemented)
   supports either.
3. **Is `retail_observations` stage 5 fed from stage 1, or its own stage-2 aggregate?** This
   decides whether a source may declare it in `tables=[...]` at all.
4. **Is Postmates' inline enrich intentional?** If yes it is an explicit override; if no it is a
   live defect. The registry cannot currently tell you which.

## What this is not

This does not make the data *correct*. It makes the pipeline *checkable*: one path per source,
one writer per table, a watermark at every hop, and a surface showing real rows at each stage.
A structurally sound table can still hold bad values from a broken scraper — that is a separate
problem, and it is one you can only work on once you can see where the data is.
