# apps/_archive — superseded suite surfaces

Same standard as `unifyd/_archive/`: when a surface is replaced by a better
iteration, the old one is **archived, not deleted** (the work is expensive to
re-derive) and the replacement is noted here. Nothing in this directory is
referenced by the launcher (`index.html` `APPS`) or by any composite console.
*Parked* unfinished work stays in `apps/`; only *superseded* surfaces land here.

| File | What it was | Superseded by | When |
|---|---|---|---|
| `mdm-master.html` | The engine's `unifyd/hoodie_mdm.html` control plane re-served under suite chrome + spine (`/api/*` with embedded `DATASETS` fallback). Was the MDM console's Master tab. | `master-match.html` — the matching workbench became the Master page (#327) and stuck. | 2026 (#327) |
| `mdm-confirm.html` | MDM Confirm/Conform — fuzzy cross-source outlet reconciliation → Tier-1 truth (#276). | The master matching workbench (`master-match.html` + `steward.html` / `cluster-review.html`) is the confirmation surface now; the Confirm tab was dropped in #335. | 2026 (#335) |
| `estate-map.html` | Data & model layer map (2D). | `estate-3d.html` — the spinnable 3D estate model, renamed "Estate"; the 2D map tile was hidden in #227. Note: the planned "flatten view" of the 3D estate may crib from this. | 2026 (#227) |
| `tasting-room.html` | The Tasting Room standalone training app. | Inline-ported as a module of `training-suite.html` (The Bench). | 2026 |
| `perceptual-science-tutorial.html` | Perceptual Science standalone tutorial. | Inline-ported as a module of `training-suite.html` (The Bench) in f59a33c. | 2026 (f59a33c) |
