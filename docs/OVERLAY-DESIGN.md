# Overlay Your Data — full design

**The upload → match → cleanse → derive → diagnose → report engine, and the sale it closes by itself.**

This is the design for the thing the whole funnel points at: a page where a prospect drops *any* export
of their items — "structure doesn't matter, can be a simple sales report" — and gets back, in seconds,
their own file matched to Hoodie identity, cleansed, enriched, diagnosed with named root causes, and
annotated with a small report of what the join to canon just made visible about *their* brands in the
real market. No demo script, no sales call, no claims. The returned file is the pitch, and every line
in it is checkable by the person reading it against ground truth they already hold.

The strategic contrast it demonstrates without saying it: this happens **in real time**. The incumbent
version of this is a services queue — an analyst hand-maps the file against a master over about a week,
once, expensively. Ours is a system. The speed *is* the proof that the matching is infrastructure and
not a services engagement dressed up as a product.

Governing rules, inherited and non-negotiable:

- **Nothing gets faked in production.** Every finding is DETERMINISTIC (rule + threshold inspectable,
  stated as fact) or INFERENCE (confidence-scored, overridable) — the dq.js governing rule, applied
  end-to-end. The LLM narrates deterministic outputs; it never generates findings.
- **The honest boundary is load-bearing.** "20 of your 140 fields appear to be proprietary RNDC
  measurements — those are yours, we don't touch them" is what makes the other 120 believable.
- **Ship only detectors that travel.** A small number of highly accurate insights beats breadth. Every
  detector is precision-gated before it can appear in output.

---

## 1. What already exists (this is a composition, not a build-from-zero)

The repo already contains ~70% of the engine. The design below is mostly *wiring*, plus one genuinely
new server pass (match-against-master for an uploaded file) and the returned-artifact format.

| Piece | Where | State |
|---|---|---|
| Overlay app shell | `apps/overlay.html` (registered in `apps.registry.json` as `overlay`) | Live as a thin LLM read (`/api/ai-read` → `unifyd/analyze.py`). v2 replaces LLM-first with deterministic-first. |
| Deterministic DQ engine | `dq.js` + `dq_frontier.js` | Built, tested, browser + headless. Role inference, candidate keys, mixed-unit clusters, misfire suppression, coverage continuity, claims coherence. |
| Deterministic profiling | `unifyd/analyze.py` | Column profiling + aggregation in Python; LLM only interprets. |
| UPC engine | `unifyd/upc.py` | classify (valid/placeholder/bad_check/restricted/coupon/malformed), zero-strip heal proven by check digit, GS1 prefix→brand-owner crosswalk from COLA filings. |
| UPC health pass | `unifyd/upc_heal.py` | Runs the engine over any dataset's UPC column via DuckDB distinct-values; owner-mismatch detection. |
| Field mapping engine | `unifyd/derive.py` + `master_apply.py` | Five rule modes compiling to DuckDB SQL; per-field normalizers; preview-before-commit. |
| Precleanse | `unifyd/precleanse.py` | Brand canonicalization, demojibake, name cleansing. |
| SKU identity | `unifyd/sku_match.py` | UPC-first identity, UPC propagation across clusters, multi-UPC split. |
| Served identity | `unifyd/build_item_identity.py` → `item_identity` | UPC-deterministic canon (R=1.000 on UPC gold vs 0.269 for string keys). |
| Match quality proof | `unifyd/master_quality.py` | Deterministic gold set, P/R/F1 scored per run, anti-regression ratchet. The overlay's match-rate claims cite these numbers. |
| Distributor masters | `unifyd/vtinfo_bbs.py` (VIP Brand Builder, ~300 distributors reachable), `unifyd/bbg_salsify.py` (Breakthru: 55,774 products, supplier attribution, UPC) | Built recipes. §6 scales them. |
| Source analyzer pattern | `unifyd/source_analyzer.py` | The "point it at a thing, get structure back" UX pattern the overlay reuses for files instead of URLs. |
| Retail/menu observations | `retail_observations`, menu analytics, fact tables (`master_facts.py`) | The data the join report (§5) reads. |

What is genuinely new: (a) the **match service** — resolving an *uploaded* file against the master with
tiered confidence, (b) the **detector registry** — the encoded-expertise layer as a governed, growing
catalog, (c) the **returned workbook** format, (d) the **join report**, (e) the free-page packaging.

---

## 2. Three skins, one pipeline

The same pipeline serves three commercial surfaces. Build it once; the skins differ only in
authentication, depth, and retention.

**A. The free public page** (the funnel entry, distributed via the Mark Brown report). Email-only gate,
no password. File processed ephemerally — *nothing stored* except the email, coarse telemetry (§8), and
the aggregate counters the ticker needs. Output: on-screen read + the returned workbook + the baby join
report, watermarked with coverage/freshness stamps. Depth: full cleanse/diagnose; join report limited to
launch cities at sellable-grade; enrichment fields capped (a taste, not the catalog).

**B. The in-suite app** (`apps/overlay.html`, authenticated). Same pipeline, full enrichment, saved runs,
re-run on refresh, connection to Report Builder specs (`analyze.py` already emits these), and the
scheduler ("re-diagnose this feed weekly").

**C. The enterprise engagement** (the forecast/master-services tier). Same pipeline as the *intake* step —
"let's get a baseline, send me a simple sales report" — but under contract, with tenant isolation,
retention terms, and their data feeding a standing crosswalk. The demo and the engagement intake being
*the same machine* is the point: the sale's first deliverable is produced by the thing being sold.

---

## 3. The pipeline

Nine stages. S1 runs client-side before anything is uploaded; S2–S8 run server-side in one pass over
DuckDB; S9 assembles. Target wall-clock for a 50k-row file: **< 10 seconds** perceived (S1 instant,
server pass streamed as it completes). Nothing about the pipeline requires the file to be well-formed —
"structure doesn't matter" is a product promise and every stage must degrade honestly when a field is
absent (a stage that can't run reports *why* — "no UPC column found → identity resolved by
brand+size+name only, match confidence capped at INFERENCE").

### S0 — Ingest
- Accept CSV / TSV / XLSX / XLS / TXT / JSON-lines, pasted text. Sniff delimiter + encoding; demojibake
  (`precleanse.demojibake`) on read. Multi-sheet XLSX: pick the sheet that profiles as an item table
  (most rows × columns with role diversity), offer the others as tabs.
- Caps: free page 25 MB / 250k rows (beyond → "this is an engagement, talk to us" card — a qualifying
  signal, not an error). Suite tier higher.
- **Ephemerality contract (free page):** file lives in memory / tmp for the request, deleted on response;
  no row data persisted. This is stated on the page and it is true.
- PII posture: the expected content is item/sales data, but detect obvious PII columns (emails, names in
  a customer-shaped column) via role inference and *exclude them from every downstream stage and from
  telemetry* — reported to the user as "3 columns look like customer data; we didn't read them." That
  line is itself a trust-builder.

### S1 — Instant profile (client-side, `dq.js`)
The file is parsed and profiled **in the browser before upload** — the "it's already working" moment,
sub-second, zero server cost, works even if the visitor never clicks Analyze:
- Role per column (measure vs dimension, calibrated confidence), candidate keys, row/col counts,
  fill rates, distinct counts, mixed-unit clusters, obvious junk columns.
- The dq.js misfire guard (detector firing on >5% of rows at flat confidence ⇒ suppressed + meta-card)
  already enforces the "don't be a buggy tool" discipline client-side.
- UI: the dropped file renders as a live schema card — their column names, our read of each. First
  checkable claim, ~800ms in.

### S2 — Semantic mapping (their columns → master schema)
Map their header onto the master `FIELDS` (`build_product_master.py`): brand, product_name, abv, size_ml,
pack, upc, gtin, supplier, price, …
- **Deterministic first:** a column whose values pass UPC/GTIN structural classification *is* an
  identifier column regardless of its header (this is how "your UPC field is actually GTIN" gets caught
  — the values prove it, the header just gets corrected). Same for size (parse rate on size grammar),
  ABV (numeric in plausible range), price (currency shapes), dates.
- **LLM assist, labeled:** ambiguous headers ("ITM_DSC_2") get an LLM mapping *suggestion* carrying
  INFERENCE + confidence, shown as such, overridable in the UI. The mapping table itself is part of the
  output — it is the "of your 140 fields…" summary in embryonic form.
- Every unmapped column is classified: **derivable** (we can compute it from mapped fields), or
  **proprietary** (their measures — named honestly: "appear to be proprietary RNDC measurements").
  The three-way count — cleansed / derived / yours — is a headline stat of the whole run.

### S3 — Identity resolution (the match)
The core pass. Resolve every row to a Hoodie ID against `item_identity` + the product master, tiered:

| Tier | Method | Label | Notes |
|---|---|---|---|
| 1 | Exact normalized UPC/GTIN ↔ `item_identity` | DETERMINISTIC | `upc.normalize` + GTIN-14 collapse; same UPC ⇒ same item. |
| 2 | Zero-strip heal → match | DETERMINISTIC | Pad 10–11-digit codes to 12, accept only on mod-10 pass; the heal is itself a reported finding ("your export ate leading zeros on 1,204 UPCs — we restored them"). |
| 3 | Distributor item code ↔ crosswalk | DETERMINISTIC where the distributor is in the master | `dist_item_code` from the Brand Builder scrape (§6) — this is the tier that lands hardest with distributor buyers: we matched on *their own item numbers*. |
| 4 | Signature cluster (precleansed brand + canonical name + size + container) | INFERENCE, confidence-scored | `precleanse` + `sku_match` signature; UPC propagation where a cluster carries exactly one UPC. |
| 5 | Unmatched | — | Never hidden. Reported with a *why* histogram: no identifier + no dictionary brand / brand known but size unparseable / genuinely not in master (a coverage datum for us, and we say so: "these 62 items are not yet in our master — this is how it grows"). |

Match output per row: `hoodie_id`, `match_tier`, `match_method`, `match_confidence`, `matched_display`
(the master's name for it, so they can eyeball the match). Aggregate: match rate **by tier**, never one
blended number — the deterministic share is the number that matters and it is the honest one to lead with.
The page cites the standing `master_quality` P/R numbers next to the claim, because we measure the
matcher; we don't assert it.

### S4 — Cleanse
Per mapped field, apply the master normalizer (`derive.TRANSFORMS` + field normalizers from
`master_apply.source_select`): demojibake, brand canonicalization to the dictionary form, ALL-CAPS name
repair, size→ml, pack parsing, UPC→GTIN-14 canonical, title-case, whitespace. Output columns are *added*
(`clean_brand` beside their brand column) — **the original file is never modified**, only annotated.
Every cell-level change is countable, and the counts are the report line: "brand: 2,014 of 11,300 values
canonicalized; size: 96% parsed to ml; 41 unparseable (listed)."

### S5 — Derive & enrich
Fields added from the master via the matched Hoodie ID: category / class_type, varietal, origin vs
bottled_in, supplier, brand owner (GS1-prefix crosswalk), GTIN-14, image link, container, bottles-per-case
(BBG-attributed), ABV where theirs is missing. Each enriched column carries provenance (`source:
hoodie_master`, and DERIVED vs OBSERVED). Free page: 5–6 enrichment fields, the rest named-but-locked —
visible column headers, values withheld — which is the cleanest possible upgrade CTA because it isn't a
banner, it's their own file with columns they can see exist.

### S6 — Diagnose (the detector registry — §4)
The expertise layer: the registry of deterministic detectors runs over the mapped, matched table and
emits findings with **root causes**, not symptoms. Findings are ranked (severity × confidence ×
specificity), capped in the on-screen read (top ~8; full list in the workbook). This is where
UPC-vs-GTIN, proof-written-into-ABV, mixed-case splits, the Utah RTD SKU, and owner-mismatched UPCs
surface.

### S7 — Market read (their brands, our observations)
Matched Hoodie IDs join to the observation layer — `retail_observations`, menu analytics, fact tables —
scoped to **cities at sellable-grade only** (coverage % + freshness stamped on every claim):
- Distribution breadth: "Your items appear on N of M tracked shelves in Miami" (count | breadth | class
  of trade — the same cut the BI uses).
- Menu presence and *serve* patterns: **"40% of Jim Beam cocktails in Miami menus are served as a
  highball (n=412 menus, refreshed ≤7d)"** — deterministic aggregate over menu observations, keyed to
  the brands *found in their file*.
- Price position: their list/price columns vs observed shelf/menu price distribution for the same
  Hoodie IDs.
Every claim carries n, geography, freshness, and method — the sellable-grade bar (SCRAPING-PLATFORM.md)
applied to a free page. If a brand in their file has thin observation coverage, the block doesn't render.
Silence over stretch, always.

### S8 — The baby join report (§5)
A one-page auto-report: "what the join to canon just made visible." Assembled only from blocks whose
data supports them.

### S9 — Return
Two artifacts: the **on-screen read** (streamed as stages complete) and the **returned workbook** —
their file back, annotated. Format in §5.

---

## 4. The detector registry — encoded expertise as a governed catalog

The moat of the diagnose stage is not any single check; it is the *catalog* of bev-alc-specific
detectors and the discipline governing it. "Look for the common breaks with understood root causes" —
same unified engine philosophy as the ticker insights, pointed at a file instead of a market.

### Detector contract
Every detector is a registered object:

```
{
  id:            "abv_proof_double",
  domain:        "bev-alc",                 // bev-alc | hemp | cannabis | generic
  requires:      ["abv", "category"],       // mapped fields needed; absent ⇒ detector skips, silently
  fire:          <deterministic predicate over rows/aggregates>,
  label:         "DETERMINISTIC" | "INFERENCE(conf)",
  root_cause:    "Proof was written into the ABV field",   // the diagnosis, not the symptom
  evidence:      <row refs + the arithmetic shown>,         // every finding cites its rows
  fix:           "Divide by 2 → 12.5%; move original to `proof`",  // actionable, specific
  severity:      "blocks_match" | "corrupts_analysis" | "cosmetic",
  precision_measured: 0.99,                 // from the gold/backtest corpus — the gate
  travels:       true                       // fires correctly across sources, not just one quirky file
}
```

### The launch set (all near-certain, all root-caused)
- **`upc_is_gtin`** — values in the UPC-labeled column classify as EAN-13/GTIN-14 (`upc.classify` on
  structure, not header). Fix: corrected in a new `gs1`/`gtin14` column; theirs untouched. *The* demo
  finding — impressive, not insulting.
- **`zero_strip`** — 10–11-digit codes that pass mod-10 when re-padded. "Your export tool ate leading
  zeros on N codes; restored."
- **`placeholder_upc` / `bad_check` / `restricted_ns`** — dummy barcodes, failed check digits, and
  number-system 2/4/5 in-store codes acting as global keys (the measured 14,917-code / ~1,515-collision
  harm class from `sku_match._restricted`).
- **`upc_owner_mismatch`** — the UPC's GS1 company prefix disagrees with the brand/supplier on the row
  (COLA-built crosswalk). "This UPC belongs to <owner>; the row says <other> — likely a borrowed or
  recycled code."
- **`abv_proof_double`** — ABV ≈ 2× the category-expected range (a 25% "ABV" Cabernet against an
  expected 11–16). Root cause named: proof written into ABV.
- **`abv_out_of_category`** — ABV outside the deterministic expected range for the matched category
  (RTD 4–12, table wine 8–17, straight bourbon ≥40…). Table is versioned data, not code.
- **`mixed_case_grain`** — one item code carrying two size/pack signatures, **with an opinion on the
  split** (the master knows both children); case-vs-each breaks ("12-pack scanning as a single unit").
- **`size_pack_incoherence`** — `size_ml × pack` disagrees with a stated total-volume column.
- **`state_variant_sku`** — the regulatory-quirk family, launched with the exemplars: Utah RTD
  (flavored-malt rules force a state-specific SKU — flag when a national RTD brand's Utah variant is
  missing or conflated with the national code), control-state size restrictions, franchise-state
  duplicates. Each entry = state + rule + affected category + what-to-expect. This family is where
  category expertise compounds: every quirk encoded is tribal knowledge made deterministic.
- **`vintage_in_name`, `brand_repeated_in_name`, `mojibake`** — the string hygiene family (precleanse,
  reported rather than silent).

### Governance — how the catalog grows without rotting
- **Precision gate:** a detector ships to production output only when its measured precision on the
  backtest corpus (our own 60+ source catalogs — Kroger through Brand Builder — where truth is known)
  clears the bar: ≥0.98 to state as DETERMINISTIC fact, ≥0.90 to show as INFERENCE. Below: it runs
  silently, accumulating stats, invisible to users. **The demo only ever says things it is sure of.**
- **Misfire suppression at runtime** (inherited from dq.js): a detector firing on >5% of eligible rows
  at flat confidence is suppressed for that file and replaced by one meta-card ("this file's <field>
  pattern is systematic — likely a convention, not row-level errors"). One wrong-but-confident finding
  costs more credibility than ten right ones buy.
- **The encode playbook:** new quirk observed (by us, a customer, a rep) → written as
  rule + root cause + exemplar rows → backtested on the corpus → precision measured → gated in.
  Target cadence: +3–5 detectors/month. The registry version is stamped on every run, so a returned
  workbook is reproducible ("run 2026-08-03, registry v14, master build 2026-08-02").

---

## 5. The returned artifacts

### The workbook (what gets forwarded to the CIO)
One XLSX (or CSV bundle), engineered to be *forwarded internally* — it is the sales asset that travels
without us:

- **Sheet 1 — Your data, annotated.** Their file, column order preserved, original values untouched.
  Added: `hoodie_id`, `match_tier`, `match_confidence`, `matched_display`, `clean_*` columns beside
  each cleansed field, enrichment columns, and a per-row `findings` tag list. Findings-affected cells
  visually flagged.
- **Sheet 2 — The field map.** Every one of their columns → mapped master field | derivable |
  proprietary, with the method label (DETERMINISTIC/INFERENCE) per mapping. This sheet *is* the
  "of your 140 fields: 100 cleansed, 20 derived, 20 yours" summary, stated as a table they can audit.
- **Sheet 3 — Findings.** Every detector finding: root cause, evidence rows, arithmetic shown, fix.
- **Sheet 4 — The join report** (below).
- **Sheet 5 — Provenance.** Run stamp, registry version, master build date, match-quality citations
  (`master_quality` P/R), coverage + freshness for every market claim, the ephemerality statement, and
  the honest-boundary list. The trust sheet.

### The baby join report (Sheet 4 + the on-screen finale)
"Interesting things the join to canon enabled" — a one-pager assembled from blocks, each block rendering
**only when the data clears its own bar** (min match rate, min n, sellable-grade geography). Candidate
blocks, launch set:

1. **Who you are, resolved.** N items → M brands → K suppliers/owners (GS1 crosswalk); their portfolio
   pyramid drawn from their own flat file. First "we know exactly who this is" moment.
2. **Your catalog vs the market catalog.** Items matched that exist in X tracked retailers/menus;
   items in the master carrying attributes their file lacks (the derivable count, visualized).
3. **Breadth.** Per top brand: tracked-shelf presence by city (count | breadth | class of trade),
   against category median. "Your tequila line is on 61% of tracked Miami shelves; category median 34%."
4. **The serve.** Menu-observation aggregates keyed to their brands: **"40% of Jim Beam cocktails on
   Miami menus are highballs (n=412, ≤7d)"**; top cocktail contexts per brand; share-of-menu vs
   competitors *named only at category level* on the free page (competitor-brand-level detail is a
   subscription surface).
5. **Price position.** Their price columns vs observed shelf/menu price bands for the same IDs —
   percentile placement, outliers listed.
6. **Portfolio gaps.** Deterministic only: sizes/variants of their own matched brands that exist in the
   master and in-market but are absent from their file ("your competitor set carries a 375ml here;
   your file doesn't").
7. **A ticker-style forward note** (free page flourish, deterministic-insight library): the
   hurricane-pantry-load / event-annotation class, rendered only when geography matches ("Miami file +
   hurricane season → what to expect, based on the observed Hurricane Claude effect in Ft. Myers/Tampa
  last year").

Rules: every block carries n + geography + freshness; blocks below bar are *absent*, not padded;
maximum ~6 blocks rendered so the report reads as sharp, not exhaustive. The report ends with the
boundary statement and one CTA (the locked columns + the city-subscription card).

---

## 6. The master side — what the file matches against

The overlay is only as good as the master behind Tier 1–3 matching. The uploaded files will be
*distributor-shaped* (dist item codes, supplier codes, retail UPCs) — so the highest-leverage master
expansion is exactly the distributor-catalog layer, and both recipes are already proven:

- **VIP Brand Builder (`vtinfo_bbs.py`) — the ~300.** Open JSON API keyed by sourceCode; every
  distributor's full book: brands, products, packages, `dist_item_code` + `retail_upc`. Already scraped
  across ~300 distributors. Work: (a) finish the sourceCode sweep (directory + `--discover` against
  distributor sites), (b) land all books into a `dist_item_xwalk` table —
  `distributor_id | dist_item_code | retail_upc | hoodie_id` — which is precisely the Tier-3 match
  spine *and* the "we can take VIP data, we already built the translation key" proof.
- **Salsify public catalogs (`bbg_salsify.py`) — loop it like Brand Builder.** BBG proved the recipe:
  Salsify SSG microsites expose the full catalog at `/_next/data/<buildId>/…` — 55,774 products with
  supplier attribution, facets, ABV, bottles-per-case, UPC-in-image-filename. The recipe is
  parameterized by SITE; the work is **discovery**: enumerate `sites.salsify.com` tenants belonging to
  distributors (Reyes, RNDC, regional houses) via targeted search + their own site links, then run the
  same puller per site. Each new tenant is hours, not weeks.
- **Targeted majors** (Breakthru ✓, Reyes, RNDC, Southern outposts): whichever surface each exposes —
  Salsify tenant, Brand Builder sourceCode, or own portal — lands in the same `dist_item_xwalk` grain.
- **Already-standing spine:** `item_identity` (UPC-deterministic canon), the product master
  (`build_product_master`), the COLA GS1-prefix↔owner crosswalk, TTB/COLA label universe, and the
  retail/menu observation layer for §S7/§5.

Master-side bar for the overlay launch: **the Tier-3 crosswalk covers the distributors most likely to
touch the funnel** (the 300 + the majors we can reach), and Tier-1 UPC coverage is what it already is —
measured, cited, and growing with every scrape cycle. Every unmatched upload row is a coverage work
item; the gap queue (SCRAPING-PLATFORM P3) consumes it.

---

## 7. Trust, rights, and limits

- **Ephemeral by default; stated and true.** Free page stores no row data. What persists: email, coarse
  telemetry (§8), aggregate anonymous counters (match-rate distribution, detector fire rates — the
  registry's own precision telemetry), and nothing reconstructable to their file.
- **Tenant isolation on retained tiers.** Suite/enterprise runs persist only under the tenant, with
  explicit retention terms. Their data never feeds the shared master without contractual consent
  (the enterprise engagement is where that consent is negotiated — and priced).
- **Rights posture on our side of the join:** market-read claims are built only from our own collected
  observations (public retail/menu surfaces, sellable-grade gated). VIP/Nielsen-derived data never
  appears in overlay output; the Brand Builder/Salsify catalogs are open public surfaces pulled
  politely — counsel reviews the sellable-publicly basis per source, same as the feed products.
- **The honest failure mode.** Low match rate is reported as a number with a why-histogram, never
  disguised. The page's credibility strategy *is* its accuracy strategy; there is no version of this
  product that survives one caught exaggeration in front of a CIO.
- **PII:** detected identity-shaped columns excluded and disclosed (§S0).

---

## 8. Funnel mechanics (free page)

- **Gate:** email-only login, no password (magic-link session). The email is the CRM record.
- **Telemetry under the page** (the "massive user tracking"): what they clicked, which findings they
  expanded, which locked columns they hovered, whether the workbook was downloaded, report blocks
  viewed, return visits, re-uploads (a re-upload of a *bigger* file is the strongest buying signal in
  the funnel). All of it lands as CRM activity on the email.
- **The baby scheduler:** "email me this diagnosis weekly for this feed" — smallest possible standing
  relationship; its emails carry the availability announcements ("SF/Bay Area now live for
  subscribers — connect").
- **Ticker integration:** the page-top ticker cycles freshness/coverage/staff-insight lines; overlay
  runs feed it anonymously ("median match rate this week: 94% deterministic").
- **CTAs, in order of intent:** unlock columns (city subscription, card-on-page) → schedule the
  diagnosis → "this file wants an engagement" (row-cap/complexity triggers, books a call).
- **The sales motion this encodes:** *"Let's get a baseline — send me a quick export of your items,
  structure doesn't matter, can be a simple sales report."* The opener costs the buyer nothing, and the
  returned workbook does the talking. The rep version and the self-serve version are the same machine.

---

## 9. Build sequence — each phase exits on a number

**P1 — The match service (the spine).** Uploaded file → S2 mapping → S3 tiered match against
`item_identity` + `dist_item_xwalk` (Brand Builder 300 landed) → match-rate-by-tier output.
*Exit:* ≥85% deterministic (Tier 1–3) match on 5 real distributor exports we source ourselves; served
under 10s at 50k rows.

**P2 — Cleanse + derive + the workbook.** S4/S5 wired through `derive`/`master_apply` normalizers;
Sheets 1/2/5 rendered. *Exit:* the 100/20/20 field-map summary generated correctly on all 5 test files;
workbook opens clean in Excel.

**P3 — The detector registry.** Contract + runner + the launch set (§4) backtested on the source corpus.
*Exit:* every shipped detector ≥0.98 measured precision; zero misfires across the corpus at the
suppression thresholds.

**P4 — The join report + market read.** §S7 joins scoped to launch cities; §5 blocks with render bars.
*Exit:* every rendered claim carries n/geo/freshness; a seeded Jim-Beam-style serve stat reproduces from
raw observations exactly.

**P5 — The free page.** Skin A: email gate, ephemerality, telemetry, locked columns, ticker, scheduler.
*Exit:* stranger-test — 5 industry people run real files unassisted; ≥4 forward the workbook to someone
else (the forward is the KPI, not the compliment).

**P6 — Salsify/major-distributor sweep** (parallel from P1): tenant discovery + per-tenant pulls
landing `dist_item_xwalk`. *Exit:* crosswalk rows for the majors reachable without auth; Tier-3
coverage % published on the provenance sheet.

Sequencing note: P1/P6 start together (P6 feeds P1's exit bar); P3 backtesting rides the corpus that
already exists; P5 is deliberately last — the page ships only when the pipeline under it clears its
numbers, because the free page is the credibility bet.

---

## 10. Failure modes, named

| Failure | Guard |
|---|---|
| A confident wrong finding in front of an expert | Precision gate + runtime misfire suppression + INFERENCE labeling; the demo only says what it's sure of. |
| Low match rate on a real prospect file | Reported honestly with why-histogram; unmatched rows become coverage work; the provenance sheet cites measured P/R so one bad file doesn't read as a broken product. |
| Garbage/hostile uploads (wrong vertical, junk, adversarial) | S1 role profiling gates the pipeline ("this doesn't profile as an item table"); caps; ephemeral processing; no reflection of file content into telemetry. |
| Master staleness making enrichment wrong | Master build date stamped on every run; enrichment fields carry provenance; freshness SLAs from the scraping platform gate the market read. |
| The page becomes a free cleansing utility (no conversion) | Locked enrichment columns, subscription-gated market depth, scheduler capture, row caps routing big files to engagement — value visible, depth priced. |
| Detector catalog rots (rules drift as sources change) | Registry versioning + per-run fire-rate telemetry + backtest re-runs on master rebuilds; a detector whose live precision drifts below bar is auto-demoted to silent. |
| A copied UI by a competitor | The page is copyable; the master, the crosswalks, the observation layer, and the measured precision behind it are not — the moat is under the pipeline, not in it. |

