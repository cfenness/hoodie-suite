# The data platform — model, masters, laws, pipeline

## The model

- **Star schema** (the book): `dim_product / dim_account / dim_date / fact_depletion` — one
  aggregation surface (`/api/book/cuts`), so numbers reconcile across every screen. Synthetic seed
  today; real facts swap in with zero schema change.
- **The masters** (MDM): golden `dim_<entity>` tables (outlet · product · party) built by the flow
  engine — deduped, survived, conflict-flagged, each record carrying a **stable Hoodie ID**
  (`HO-O-…` outlet, `HO-I-…` item, `HO-P-…` party) minted from a registry that survives rebuilds.
- **Warehouse**: Parquet queried in place by DuckDB — no always-on database. `real/` and
  `synthetic/` are physically separate namespaces; nothing synthetic is ever servable.

## The pipeline (the flow engine)

`input → clean → union → resolve → output`, compiled to ONE DuckDB SQL query — every step
inspectable in the Flow workbench (#mdm → Flow): its data, its profile, its conflicts, its exact
SQL. Resolve = identity key (strong key when present, else normalized natural key) + per-attribute
survivorship (any · authority · frequency · recency · longest · min/max/sum) + `__conflict` flags
feeding the steward queue. Provenance (`_source`, `_sources`, `_source_list`) rides every row from
first touch; click any golden record for the who-said-what drill-down.

## The laws (digest — canonical text in MDM_FLOW.md)

1. **Never fix the row — fix the rule.** The golden record is a derivation; every steward action is
   a rule that re-materializes. No surface edits a mastered value.
2. **Accuracy-first automatch, 90–95%.** Not a quota — the high end of what accuracy permits.
   Earned through better tiers, never bought with looser thresholds. Measured as the PAIR: automatch
   shares (auto / claude-verified / oracle / human) AND false-merge precision by audit sampling.
3. **The verification cascade**: deterministic → Claude-verify (fetch the fact from whitelisted
   authorities, verdict + evidence) → external oracle (Google = oracle-not-store) → human, last and
   rarest. The 5–10% residue reaching a human is the design working.
4. **Real ≠ synthetic, ever.** Separate namespaces, loud badge, real-only serve.
5. **Identity from public first principles.** No proprietary vendor IDs — name/address/geo/license,
   UPC/GTIN, TTB filings. Cross-source truth is always public-attribute-based.
6. **Catch-alls are populated-but-empty.** 'other'/'misc'/'n/a' never count as informative;
   catch-all density TRIGGERS verification (≥40% of responded fields at identifier level → check
   the COLA label).
7. **The engine is domain-agnostic; a domain is a config pack.** Bev-alc is field requirements,
   not tool capacity. A new domain = a new `_ENTITY_KEYS` row + schemas + vocabularies.

## Data quality machinery

- **Degrade honestly**: every connector self-reports `success | degraded | failed` with `warnings[]`
  — a silent layout change surfaces as needs-review, never as bad data.
- **Informative completeness**: profile reports filled, distinct, AND catch-all per column — a field
  100% filled but 80% 'other' screams.
- **Edge-case catalog** (from real connectors, in MDM_FLOW.md): dba ≠ legal name; premise vs
  mailing address; licensee ≠ establishment; outlet lifecycle; the common-string normalizers
  (USPS tokens, entity suffixes, store numbers, placeholders, accents).

## Sources

~14 live connectors (FL DBPR, TTB COLA, TX/CT/IL + generic Socrata states, ABC FWS, retailers,
census/geo enrichment) — see #mdm → Sources for the live estate, and `unifyd/README.md` for each
connector's contract and honesty behavior.
