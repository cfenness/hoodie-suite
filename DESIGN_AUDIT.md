# Design Audit — `dashboard.html` ↔ Hoodie Analytics Suite

A head-to-head of our `apps/dashboard.html` against the shipped **Hoodie Analytics Suite**
(hoodieanalytics.com), to find where Hoodie is genuinely better and what to take.

> A rendered version exists as an Artifact. This is the version-controlled reference.
> Excluded from the public deploy.

**Verdict in one line:** `dashboard.html` is the stronger **analytical explorer**; Hoodie is the
stronger **operational product** with a more consistent grid design system. Adopt Hoodie's
operational discipline + consistency; keep our analytical depth.

## Scoreboard

**Adopt from Hoodie (7):**

| Dimension | dashboard.html today | Hoodie | Move |
|---|---|---|---|
| **Editable grids & bulk actions** | Read-only analytical output; you explore/export but can't act on a row | Operational grids: row select → bulk actions (Publish/Export/Set status/Download/Delete), per-row actions, inline edit | A reusable editable-grid component (the backbone Catalog + Assortment need) |
| **Product / Brand Catalog (MDM)** | None — SKUs exist only as report dimensions | Attribute library, per-SKU completeness scoring, lifecycle (Planning→Staging→Live→Retired), variants/cloning/import, publish-to-channels | **Net-new build** — the Catalog on `dim_product` |
| **State & status encoding** | Has DQ/Trust but no operational status vocabulary on records | On Track / At Risk / Off Track pills + completeness meters, everywhere | Shared status-pill + completeness component |
| **Filters, Views & personalization** | Filters + Favorites, filter cards — but views aren't saved objects | Saved **Views** (named filter+column config), **Tags**, column chooser | Promote Views to a first-class saved object; add Tags + column chooser |
| **Planning (targets vs actuals)** | Purely retrospective — no target-setting | Assortment Manager: set targets, track On/At-Risk/Off, Copy Market/Actual→Target, roll-ups | A planning layer over the book |
| **Scheduled delivery & alerts** | Strong on generation (PPTX) but email-a-report loop unfinished | Scheduled Reports (Daily/Weekly/Monthly → recipients → CSV/XLSX email) + threshold Alerts | Finish the delivery loop + measure alerts |
| **Visual-system consistency** | dashboard.html polished, but CRM/MDM/Planogram/Pulls each drift | One design language across all 9 modules | Extract a shared design system, roll across every page |

**Keep — we're ahead (4):**

| Dimension | Why we're ahead |
|---|---|
| **Analytical depth** | Tableau-style Report Builder (shelves, Show Me) + natural-language "Build it" query; Hoodie has fixed reports |
| **Data quality & trust** | Real DQ engine — deterministic-vs-inference, certified tables, `N≥10` anonymization, Trust view; Hoodie surfaces none |
| **Presentation / decks** | PPTX Studio (one master → slide per cut + Slide Coach); Hoodie has no deck builder |
| **(IA / nav)** | Roughly parity — dark left-nav + breadcrumb + tool tabs already close to Hoodie |

## The net-new build — Product Catalog

The operational spine we're missing. Every analytical surface leans on products, but nothing
lets you *manage* them.

- **What it is:** an attribute library (the schema per product type), per-SKU records with
  completeness scoring, lifecycle status (Planning → Staging → Live → Retired), variants,
  cloning, CSV import, publish/export to channels, bulk actions.
- **How it fits us:** it backs `dim_product` — the Catalog *is* the product dimension, made
  editable. It reuses the shared editable-grid + status-pill + completeness components. Only
  "Live" SKUs count toward the book, so completeness/lifecycle gate what's analyzed.

## The alignment — one design system across every page

The seven adopts collapse into a small shared component layer. Build once; every page (Catalog,
CRM, MDM, Planogram, Pulls, mobile) uses them:

- **Power data-grid** — column chooser, saved Views, filters, Tags, sort, row selection,
  bulk-action bar, per-row menu, inline edit. The single most reused surface.
- **Status pill + completeness** — On Track/At Risk/Off Track chips + completeness meter.
  Semantic color, separate from the brand accent.
- **Saved View object** — name + filters + columns + sort; also feeds Scheduled Reports.
- **Module shell** — nav + breadcrumb + module-home cards + help slot, shared.
- **One token set** — extend Prism's Tableau-crisp palette/type into shared tokens; retire the
  per-file CSS drift.
- **Schedule + Alerts** — email any saved View on a cadence; alert on a measure threshold.

**Stance:** adopt Hoodie's operational discipline and consistency; keep our analytical depth
(the builder, DQ/Trust, decks, NL query). The winning product is Hoodie's clean, editable,
status-aware grid system *plus* the explore-and-explain power Hoodie doesn't have.
