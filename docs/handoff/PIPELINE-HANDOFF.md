# Handoff — data pipeline trust review

Branch: `claude/hoodie-warehouse-egress-gdrive-b2gnap` (all work committed and pushed).

## What happened

Started as a Tigris → Google Drive egress job. The egress could not run (see Environment
below), and the session turned into a data-architecture review driven by one question:
**why can't anyone say what data we have?**

The answer, and everything downstream of it, is below. Every claim cites file:line and was
verified by reading the code.

## Artifacts on the branch

| file | what it is | runs where |
|---|---|---|
| `tools/data_inventory.py` | static map: tables, writers, trust tiers. `ast` only — **no credentials needed** | anywhere |
| `docs/handoff/DATA-INVENTORY.md` | generated snapshot of the above | — |
| `docs/PIPELINE-DESIGN.md` | the target design: 6 stages, 4 contracts, open decisions | — |
| `tools/warehouse_egress.py` | Tigris → Drive copy + **live** inventory (row counts, sizes) | needs Tigris |
| `docs/handoff/DATA-EGRESS-RUNBOOK.md` | how to run the egress, incl. the Fly path | — |

`data_inventory.py` is tested and run. `warehouse_egress.py` compiles and its preflight is
verified, but **everything past preflight is unrun** — there were never any Tigris credentials
available to test it against.

## The core finding

**The system is not statically inspectable.** 62 of 208 warehouse write sites (30%) do not name
their table in the source — the name is computed at run time (`"%s_products" % site`, f-strings,
variables). No complete table map can be derived by reading the code. The only thing binding a
computed name to a real table is a hand-typed `tables=[...]` on a registry entry, so adding a
site leaves the code working while the map goes silently wrong.

Everything else follows from that.

## Verified defects

1. **`ubereats-enrich` can never report success.** It declares `tables=["ubereats_products"]`
   but writes `ubereats_products_parts` (`ue_enrich.py:97`). Landing is graded on the row-count
   delta of *declared* tables (`run_sources.py:449-456`), so delta is always 0 and it reports
   `current` whether it lands 500,000 rows or none. `due_builds` only advances on `ok`, so it
   also can't trigger the fold.

2. **Two writers on `ubereats_products`.** The fold, and any zone crawl via `ubereats.land()`
   (`ubereats.py:652`, called at `ue_crawl.py:412/414/520/525`). `write_accumulate` is
   read-modify-write with no lock — `warehouse.py` documents that concurrent callers silently
   drop each other's rows (observed live: 33,250 → 8,798). The existing guard only fires when
   `HOODIE_SHARD`/`UE_SHARD` is set, so unsharded zone runs pass straight through.

3. **The fold cannot scale.** `ue_catalog.consolidate` reads the entire parts history into a
   Python dict every run and prunes nothing. Cost grows with total history, not new data.

4. **The catalog is not a function of its inputs.** The registered fold uses the additive merge,
   not `rebuild=True`, so `ubereats_products` carries ~98k rows whose provenance — the module's
   own words — "cannot be stated".

5. **Schema drift.** 12 of 16 `write_partition` sites don't pin `dtypes`. Two tables have
   already been made fully unreadable by this (`ubereats_products_parts` 2026-07-30;
   `retail_observations`, 13 schemas / 3,622 partitions / 51.7M rows, 2026-08-03).

6. **Enrich is already a parameter of catalog.** `ue_catalog.py:997` defines `--no-enrich` and
   `:799` calls `enrich_items` inline. UberEats runs catalog `--no-enrich` plus a separate enrich
   job; Postmates runs catalog with no flag, doing both in one pass. Same capability, two
   topologies, decided by a flag in a hand-typed string. **Unresolved: is Postmates' inline
   enrich intentional or a copy-paste slip?** The registry cannot tell you.

## Trust tiers (from `data_inventory.py`)

| tier | n | meaning |
|---|---:|---|
| corruptible | 10 | unpinned `write_partition` |
| lossy | 5 | `write_accumulate` from >1 module |
| accumulating | 55 | single-writer merge — **working as designed** |
| unverifiable | 22 | declared, no traceable writer |
| **sound** | **54** | single-writer full rebuild, reproducible |

**Fix-first set is 15 tables** (corruptible + lossy), not the warehouse. Worst single entry:
`src_outlets` — 8 writing modules doing unlocked merge, 1.76M rows, and the coverage book
everything geographic depends on.

**The `dim_*` master chain is in the sound tier** — reproducible by construction. It is blocked
by its inputs, not by itself. The CRM and analytics are not waiting on a master rewrite.

## The design (`docs/PIPELINE-DESIGN.md`)

Six stages: discover → capture (parts) → consolidate (fold) → normalize (`src_*`) → master
(`dim_*`) → facts.

Four contracts: one writer per table; schema belongs to the table not the call site; every
promotion has a watermark; a stage advances on its own backlog rather than an upstream's status.

One parameterized program per source — `scope` (a Florida run is a parameter, not a dataset),
`refresh` (preserves the catalog/enrich economics: `ue_catalog.py:182` notes re-enriching
resolved items daily would cost ~30M requests), `shard`. Collapses eight UberEats registry
entries into one program. `ue_crawl` gets archived, its zone discovery becoming stage 0's
`scope`.

Inspection is the load-bearing part: each stage answers rows-arrived, backlog waiting, what a
row looks like *here*, what was dropped and why, which run produced it. Existing apps
(`runs.html`, `mdm-sources.html`, `mdm-provenance.html`) are run- and source-oriented; none
shows data moving between stages.

## Open decisions — not made

1. **Are discovery and catalog one function or two?** The rest hangs off this.
2. **Read-time merge, or fold?** A view over parts removes the fold and makes staleness zero at
   query cost. The bucketed `__b=<hex>` layout supports either.
3. **Is `retail_observations` stage 5 fed from stage 1, or its own stage-2 aggregate?** Decides
   whether a source may declare it in `tables=[...]` at all.
4. **Postmates inline enrich — intentional or defect?**

## Next steps

1. Settle open decision 1.
2. Run the **live** inventory to get real row counts and, critically, the parts backlog —
   how much UberEats data is sitting unfolded:
   ```bash
   set -a; source warehouse.env; set +a
   python3 tools/warehouse_egress.py inventory
   ```
   Cross it against `DATA-INVENTORY.md` (static) — together they are the full map.
3. The two safe, testable fixes, if wanted before the redesign: pin `dtypes` at
   `ue_enrich.py:97` (matching `ue_catalog.py:478`), and add a ratchet test failing any
   `write_partition` without `dtypes`. Both verifiable offline — `warehouse_dtype_test.py`
   runs in local mode with no network.

## Environment

- **Run this on the Mac, not in a Claude Code web session.** Web sessions get placeholder
  `AWS_*` values (the literal string `proxy-injected`) and their egress policy 403s
  `fly.storage.tigris.dev`, `api.fly.io` and `rclone.org`. `warehouse.env` is gitignored, so it
  is never in a fresh clone. Nothing expired — it is simply a different computer.
- **The local repo path breaks tools:**
  `/Users/chrisfennessey/Desktop/Desktop - Chris's MacBook Pro/Projects/hoodie-suite` —
  spaces, a dash, an apostrophe, and iCloud sync. `claude --teleport` fails with "Failed to
  stash changes" there while a direct `git stash push -u` may not. Moving the repo to
  `~/Projects/hoodie-suite` would likely stop this recurring across tools.
