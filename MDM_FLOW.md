# Hoodie MDM — the master-building flow & verification model

> Status: design + first engine slice. The engine (`unifyd/flow.py`) is built and self-tested;
> the surface (`apps/mdm-flow.html`) and the verification checks are staged below. This doc is the
> shared reference — build against it, don't re-derive it.

## Thesis

Master data is **built by a visible flow of steps** and **resolved by a verification cascade that
treats human attention as the scarcest resource.** Two ideas carry the whole design:

1. **Line of sight.** Every step is inspectable — the data at that step, its profile, the rules
   applied, and the exact SQL it compiles to. The backend logic is good; the tool's job is to let
   you *see and explain* each step. (This is why it's Tableau-Prep-shaped, not a config form.)
2. **Verify, don't guess.** A match/merge decision isn't "do these strings look alike" — it's
   "what is the verifiable fact, and which authoritative source proves it." Similarity only *routes*
   work; **authoritative sources decide it.** The human is the last tier, and rarely reached.

## The flow model (`unifyd/flow.py`)

A flow is a **DAG of typed nodes** that compiles to **nested DuckDB SQL over Parquet** — the whole
flow is one query DuckDB runs in place, no per-row Python, reusing `derive.compile_rule` for every
field expression (same engine as the Mapping tab). Node types:

| Node | Does | Compiles to |
|---|---|---|
| `input` | a landed dataset | `SELECT *, '<ds>' AS _source FROM read_parquet('<uri>')` |
| `clean` | project source cols → master schema (derive rules + filters) | `SELECT <exprs> FROM (input)` |
| `union` | stack sources feeding one master | `… UNION ALL BY NAME …` |
| `resolve` | dedup to golden records + per-attribute survivorship + conflict flags | `GROUP BY <identity>` |
| `output` | materialize to a warehouse table | endpoint `COPY`s the SQL to Parquet |

Each node exposes four line-of-sight views: **Data** (sample rows), **Profile** (fill/distinct/top
values), **Changes** (the rules), **Explain** (plain-language + the compiled SQL).

**Scale posture** (DuckDB is not the edge — interactive latency and rebuild cost are): profiling is
**sampled** (`TABLESAMPLE` + hard `LIMIT`, aggregation always pushed into DuckDB, never a full table
into Python); node outputs are **cacheable** to their own Parquet so downstream steps and re-clicks
read the intermediate, not the whole nested query from raw. Big datasets (TTB by year, outlets by
state) should be **partitioned** so `read_parquet` prunes. The exit ramp when single-node DuckDB is
outgrown is MotherDuck / ClickHouse pointed at the *same Parquet* — a query-engine swap, not a data
migration.

## Seeding a master from what we have

`flow.propose_flow(entity, datasets, master_fields)` inspects every landed dataset, scores how well
it feeds the target entity (share of the entity's identity fields it can map), and drafts the whole
flow: `input(each feeding source) → clean(auto-mapped) → union → resolve → output(dim_<entity>)`.
**Propose, then dispose:** every auto-mapped field is a visible, editable node — you start from a
working draft, not a blank canvas, and correct every decision with its profile/preview in front of
you. Nothing is hidden.

## Redundancy, conflict, golden records

The `resolve` node is the golden-record loop:

- **Redundancy (dedup).** Identity = the strong key (canonical UPC for product; `source_ref` for
  outlet) when present, else the normalized natural key (product: brand+name+size; outlet:
  name+address+zip). Rows sharing an identity collapse to one golden record carrying `_rows`,
  `_sources`, `_source_list`. The node shows the collapse ("18,400 rows → 12,100 golden") so you see
  what merged. Exact-on-normalized-key today; the fuzzy layer (below) plugs in on top.
- **Conflict (survivorship).** When merged sources disagree on an attribute, a **rule** picks the
  winner and `<field>__conflict` marks it for stewardship. Rules: `first` (any_value), `authority`
  (`arg_max(v, source_rank)` — trust sources in a ranked order), `frequency` (`mode`), `recency`
  (`arg_max(v, date)`), `longest` (most complete), `min|max|sum`. `flow.conflict_sql()` produces the
  stewardship queue: the identities that disagree, with the competing values and their sources.

### Hoodie IDs (canonical, stable)

Every golden entity gets a stable Hoodie ID — `HO-O-` outlet, `HO-I-` item, `HO-P-` party. **Stable
is the load-bearing word:** a rebuild must not remint an ID because an attribute changed. So IDs are
backed by an **identity registry** in `agent_state` (identity-signature → hoodie_id); rebuilds keep
existing IDs and mint only for genuinely-new entities. This is "resolve identity once, stable across
refreshes" made real, and it's what lets the master project cleanly downstream.

## The verification cascade — a series of Checks

The resolver's *routing* is deterministic; the *decisions* are verified. The unifying abstraction is
a **Check** — `hard-rule | claude-verify(whitelist) | external-oracle` — each emitting a
**verdict + evidence + confidence**, composed into workflows. Tiers, human last and rarest:

```
T0  DETERMINISTIC   hard rules + exact keys        free, bulk of the volume
T1  CLAUDE-VERIFY   adjudicate by fetching the FACT from a whitelist of authoritative sources
                    (brand page, TTB COLA class/type, label / bottle image) → verdict + evidence
T2  EXTERNAL ORACLE Google Places etc. — "which business is OPERATIONAL at this address now"
T3  HUMAN (steward) only the residue the tiers above couldn't verify
```

- **Claude verifies facts, it doesn't vote.** For a category conflict (one source says vodka, another
  rum — common), Claude checks TTB COLA / the brand page and *concludes*, carrying the evidence. Even
  a confident deterministic or human call can be cross-checked against the brand page. The **whitelist
  / wildcards** configure which sources may be consulted per field.
- **Evidence & provenance.** Every non-trivial decision stores its evidence (source, snippet/URL,
  confidence) — the audit trail that makes "zero restatements" defensible.
- **External data rights (compliance).** Google is a **live oracle, not a store**: persist only the
  derived decision and the durable `place_id`, never Google's payload. The whitelist governs both
  what may be *fetched* and what may be *stored*. The open/closed signal is the ideal disambiguator
  ("one open, one closed" resolves a same-address name conflict).
- **Law & trade treaties.** Where regulation defines a fact (country-of-origin, labeling,
  appellation), encode it as a hard rule / allowed authority rather than inferring it.

## Edge cases & common strings — the empirical core

A matching model is built from how the data is actually dirty. Grounded in the real connectors:

**Name is structural, not incidental.** `name` means different things per source: FL/IL/CT `dba`,
TX `trade_name`, **NY `legalname` (also used as owner)**, some `licensee_name`. The same outlet is
"Tipsy Tavern" in FL and "SMITH ENTERPRISES LLC" in NY. → **Never collapse `dba` and `legal_name`.**
Match on address+geo; use name only dba-to-dba / legal-to-legal, never dba-to-legal.

**Premise vs mailing address.** CT `backer_address` is the licensee's address (often HQ/lawyer); NY
`actualaddressofpremises` is the store. A chain filing 50 licenses at one HQ address collapses to a
fake outlet under naive blocking. → Require geo agreement *or* a premise-typed address before an
address auto-merge; "one address, many DBAs" is a mailing smell → verify, don't merge.

**Licensee ≠ establishment.** TX `master_file_id` groups many stores under one owner. → Party is its
own master (`HO-P-`), linked to outlet (`HO-O-`) by a license. Don't merge a parent into its store.

**Missing keys degrade, don't force.** Some sources have no street address (city/state/zip only) or
no geo. → Never treat ZIP-centroid coords as "geo agreement"; never force-merge on ZIP alone.

**Lifecycle is an identity axis.** CT carries `effective`/`expire`/`status`. New operator at the same
address after a gap = a **new** Hoodie outlet + the old one closed, not a merge. Google open/closed
resolves it.

**Common strings → normalization transforms** (each a visible, reusable `derive.py` transform):

| Pattern | Where | Rule |
|---|---|---|
| `LLC · L.L.C. · INC · CORP · CO · LP · LTD · PLLC` | legal-name sources | strip to `legal_name_core`; keep raw |
| `N/A · NONE · SAME · SEE ABOVE · UNKNOWN · . · 0 · XXX` | hand-keyed fields | → NULL |
| `ST/AVE/BLVD/RD/HWY`, `N/S/E/W`, `STE/#/UNIT/APT` | addresses | USPS-standardize; parse suite into a separate unit component |
| `#1234 · STORE 1234 · NO. 1234` | chains | extract `store_number`, strip from name |
| `& ↔ AND`, possessives, diacritics, case/ws | names | canonical compare form |

## First cascade instance: catch-all density → COLA label backfill

The catch-all detector and the verification cascade compose into the first concrete Check. Two
triggers, both firing a label lookup:

- **Record-level (lazy-entry, the VIP signature):** once a record reaches the **identifier level** —
  identifiable enough to resolve to a COLA product cluster — if **≥40% of its *responded* fields are
  catch-all**, verify it. The denominator is responded (filled) fields, not all rows: this isolates
  the lie (a field *answered with a non-answer*) from ordinary blankness. The identifier gate is a
  precondition (you can't read a label for a product you can't identify) and a filter (don't spend a
  check on a record too sparse to be real) — and because you must identify on *real* fields, you
  identify on the clean ones and verify the junk. Defaults: 40% + resolvable-to-cluster, both config.
- **Field-level (unreliable source):** a field whose catch-all share across the dataset crosses a
  threshold signals the *source can't be trusted for that field* → verify that field from its authority.

The `verify` node then goes to the **authority the field maps to** — for product class/type, ABV,
origin, net contents, that's the **TTB COLA label**:

1. resolve the record to its COLA product *cluster* (brand + fanciful + size, or UPC / TTB ID — a
   filing system has many filings per product, so cluster first; `cola_cluster.py` already does this);
2. read the authoritative value off the label;
3. **backfill** where the source said "other"/NULL (safe, pure gain); where a source's *specific* value
   **conflicts** with the label, the label wins as higher authority and it's flagged with evidence (the
   vodka-vs-rum case);
4. the value now traces to **TTB COLA + filing id** and re-materializes on every rebuild — a rule, not
   a row fix (first law holds).

Guards on the guard: match authority to field (COLA answers product facts, never outlet address —
those route to Google/state license); backfill-empty silently before overwrite-conflict (which needs
the evidence trail); the threshold keeps it economical (verify a systematically-failing field, not
every one-off). The trigger is already computed — `profile_sql` reports catch-all share per field —
so this is mostly wiring the existing COLA data/clusters as an authority, not new scraping. It is the
**first real piece of the cascade in code**, not just the doc.

## Split-case (repack) explosion — a secondary workflow

Distributors repack mixed cases in the warehouse (3 Grey Goose + 3 Bacardi in one box) that ship as
one real item with its own repack number/UPC. MDM must **explode** the case into component lines,
each matched to the right canonical SKU, with quantities allocated to the fact tables (kit-to-
component). This is a **secondary workflow** (an `explode` node between `clean` and `resolve`), not
the main resolve path.

- **Detection is aggressive.** A legit single item isn't supposed to carry two brands, so *multiple
  (usually abbreviated) brand tokens in one item field* is the tell — the abbreviation is a
  character-limit artifact ("3 GG / 3 BAC"). Lower the confidence threshold and flag on that signal,
  via either a detector agent sweeping the item stream or an explicit prompt callout; expand the
  abbreviations against the brand dictionary.
- **Size is a fact to fetch, not guess.** "3 Grey Goose" is under-determined (50ml…1.75L). The
  system must *know it doesn't know* and resolve size via the cascade: total net volume on the line
  (count + total constrains size), the physical-vs-9L-accounting-case distinction (don't treat a 9L
  statistical case as a bottle count), gross weight as a backstop, the **repack UPC → a stored recipe**
  (repacks recur — solve once, auto-split forever), authoritative lookup, then you. Mixed-size cases
  (3×GG 750 + 3×Bacardi 1L) almost always end at the recipe or you — so *remembering* the answer
  matters more than solving it live.

## The match / steward surface — the human tier, made simple

A two-pane page: **source records on the left, master golden records on the right.** It shows the
residue the cascade couldn't auto-resolve (filtered to a block — e.g. same address — so you compare
like with like). Two match gestures, same effect — assign the source record(s) to a master's Hoodie
ID and persist the decision:

- **select** one-or-more source rows + one master row → **Match** (multi-select left = "these are all
  this one real entity");
- **drag** a single source row onto a master row.

Complements: **not a match** (reject → never resurfaces) and **new master** (genuinely new entity →
mint a fresh Hoodie ID). Every decision is remembered, feeds back to tune thresholds, and — like the
flow canvas — reads the same resolved tables. The flow *builds and auto-resolves*; this page *decides
the residue and repairs mistakes*.

## The first law: never fix the row — fix the rule

The golden record is a **derivation, not a document.** No surface in this system may edit a mastered
value directly. Fixing the row is the worst thing you can do:

- **it regenerates** — the next pull overwrites the fix (or the fix buries the truth); whack-a-mole forever;
- **it breaks reproducibility** — the master must be a deterministic function of (sources, rules); a
  hand-edit makes it un-rebuildable and creates a value that traces to nothing. That untraceable value
  *is* a restatement waiting to fire;
- **it destroys the lesson** — a row fix has no moral, trains no one, leaves no guard behind. A *class*
  fix does;
- **it creates silent divergence** — a patched cell contradicts every source with no rule to explain
  why: the "who owns the canonical record" wound.

**Invariant:** every steward action is a **rule that re-materializes on rebuild** — a survivorship
rule, a dictionary entry, a match/confirm decision, an authority order. A genuine one-off correction
is captured as an **override rule with provenance** (who · when · why · evidence) and a scope of one —
still a rule, still deterministic, still auditable, never a mutation of the output. The engine enforces
this by construction: it compiles to SQL over immutable source Parquet, so there is no row to fix —
only inputs and rules. No feature may add a "just fix this one value" path.

## Identity from public first principles — no proprietary vendor IDs

Identity is built only from **public, observable, or open-standard** signals — never a third party's
proprietary identifier. For outlets: name, address, geo, license number (public record), GLN. For
products: brand/name/size, UPC/GTIN (open GS1), the TTB COLA filing (public). This is an ethics rule
(we do not leverage privileged knowledge of any vendor's internal structure) *and* a better-
architecture rule: identity built on a vendor's proprietary key depends on something we don't own,
can't audit, and can't hand a customer — it's fragile and creates lock-in.

Consequence: a source's own id (`source_ref`) may aid **intra-source** stability, but **cross-source**
truth is always resolved on public attributes; no vendor's id is ever privileged as the spine. If such
a source flows in, it is matched on the same public signals as every other source. You could show
exactly how Hoodie resolves identity to anyone — none of it depends on inside knowledge.

## Generalization — the engine is domain-agnostic; a domain is a config pack

MDM is valuable only if it's **generalized**. Bev-alc is unique in its **field requirements** — never
in the tool's capacity. So the engine (flow · derive · normalizers · resolve · survivorship ·
verification cascade · Hoodie IDs · match UI) carries **zero domain assumptions**, and everything
domain-specific is a **pack of config/data**: the entity identity keys (`_ENTITY_KEYS`), master field
schemas, normalizer choices, synonym dictionaries, vocabularies, whitelist sources, survivorship
authority orders, and connectors.

The test: *"syndicate the lettuce industry."* Produce swaps UPC→PLU, brand/product/size→commodity/
variety/pack, ABV→grade/growing-region, TTB→PACA, "vodka vs rum"→"romaine vs green leaf", the
whiskey mixed case → a salad-kit pallet. **Not one line of the engine changes** — a location is a
location, dirty addresses are dirty the same way, split-case explodes identically. `produce_item` is
one new row in `_ENTITY_KEYS` (PLU strong key + commodity/variety/pack natural key), not a fork. The
guarantee we build to: **a domain pack is data, not code.**

(This is also why the identity normalizer had to be per-entity config, not the hardcoded product
token-stripping it started as — a hardcoded `identity_key` was an alcohol assumption leaking into the
generic core.)

## Low-level canonical → thousands of customers

Resolve identity **once** at an atomic, low-level grain (separate outlet / party / item; both `dba`
and `legal_name`; suite parsed out; store_number extracted) so the master **projects** cleanly onto
any customer's schema and locale rather than being pre-shaped for one. Resolve once, flow clean
downstream — the spine (`SPINE.md`) is the joint every customer view reads.

## Staged build plan (outlets first — outlets are easier than items)

1. **Engine + canvas (done / in progress).** `flow.py` DAG compiler (input/clean/union/resolve/
   output), self-tested against DuckDB. Next: `/api/flow/*` endpoints (seed, save, node preview/
   profile/sql, run) + `apps/mdm-flow.html` with the four line-of-sight views. Additive `Flow` tab
   in the MDM console; Mapping/Dictionary/Apply stay until we consolidate.
2. **Outlet identity + Hoodie IDs.** Address/suite normalization transforms; premise-vs-mailing and
   ZIP-centroid guards; exact address+geo and address+near-identical-name → auto-merge; `HO-O-`
   registry (stable across rebuilds).
3. **Verification cascade + the match/steward surface.** T1 Claude-verify against a whitelist (brand
   page / TTB); T2 Google oracle (open/closed, place_id only); T3 the two-pane match page (source left
   / master right; select-or-drag → match; reject; new-master) over the `outlet_confirms.json` pattern
   with evidence. Category (vodka-vs-rum) is the first item-side Check on the same model.
4. **Generalize.** Party master (`HO-P-`) + licensee↔outlet links; items (`HO-I-`); the `explode`
   node + repack-recipe store for split-cases; compliance rules (origin/labeling) as hard checks;
   per-customer / per-locale projections off the low-level canonical.

_Landed so far: `flow.py` (DAG compiler, self-tested) and the `derive.py` common-string normalizers
(entity-suffix strip, placeholder→NULL, USPS address + suite parsing, store-number extraction,
compare-form), self-tested against DuckDB._
