# Hoodie Vision — a proprietary, marginal-cost computer-vision layer

**Thesis:** replace per-call vision spend (Claude Opus in `unifyd/label_vision.py`) with a
small set of **owned models** we train once and run forever on our own hardware at
electricity cost. Do the hard work up front; drive marginal cost to ≈$0 so we can run it at
national-crawl scale without watching a meter.

Status: **Phase 0 tiered engine + Phase 1 corpus builder landed** under `unifyd/cv/`, wired into
`label_vision.py --engine tiered`. Tier-1's brain is still deterministic rules over OCR text;
the Florence-2 fine-tune that replaces it (same seam) is Phase 2.

---

## 1. The corpus reality (measured on the Fly warehouse, 2026-07-21)

The original plan assumed we'd distill Claude's label reads. **We don't need to** — the
federal COLA registry already gives us a huge, structured, free training set. What's actually
in the warehouse:

**Supervision splits by field** — no single table is complete, so each field is taught by its
best source:

| Table | Rows | What it reliably supervises |
|---|---|---|
| `ttb_cola_detail` | **1,858,075** | image token (`imageWindow:<id>`) + **identity fields at scale**: `brand_name` (100%), `class_type_desc` (100%), `origin_code`, `grape_varietal` (~76%), `wine_vintage` (~77%), `fanciful_name`. **Does NOT** carry ABV (column exists but is **empty table-wide**) and its `net_contents` is a **unitless number** (`'375'`, `'1.75'`) we refuse to guess units for. |
| — *"strong" rows* | **534,328** | image + brand + class-type + (varietal or net) — the identity-supervised training set. **Wine-dominant** (~76% carry a grape varietal), so spirits/RTD/beer need retail data to balance. |
| — *taxonomy breadth* | 399 class-types, 244 origins, 354k brands | the label vocabulary the extractor learns |
| `ttb_cola_labels` | **23,624** | the **label-read fields** the registry lacks: unit-bearing `net_contents` (13,156, e.g. `'720 ML'`), `abv` (5,769, e.g. `'11.5'`), `upc` (8,292) + **resolved** `front_label_url` (23,563)/`back_label_url` (9,544) + `ocr_chars`. **Immediately fetchable** (real URLs). |
| `img_vec` | 29,297 | CLIP ViT-B-32 embeddings already built (matcher warm-start) |
| `offprem_products` | 508,546 | **13,218 same-UPC image groups** → contrastive positives for the matcher, **and** retail ABV/size/UPC to balance the wine skew (plus binnys 1.53M, kroger, walmart, target, totalwine…) |
| `label_extract` / `label_reads` / `bottle_dims` | **0 / 0 / 0** | the Claude-vision paths exist as code but were never run at scale — so there is **no** existing teacher corpus; COLA replaces it |

**Takeaway:** COLA-detail supervises **brand/class/origin/varietal/vintage at 534k scale for
free**; **ABV, net contents, and UPC** — the fields the structured registry *doesn't* have —
are exactly what the model must **read off the pixels**, supervised by the 23.5k labels subset +
OCR + retail cross-refs. That is not a gap in the plan; it is the plan. We can fine-tune a v1
identity extractor **today** on the resolved label images, and scale to ~534k as we fetch the
`imageWindow:` images from TTB (the warmed-cookie recipe in `ttb-fast-scrape`).

---

## 2. "Vision" is four jobs — only two need training

| Job | Approach | Marginal cost | Train? | Have today |
|---|---|---|---|---|
| **UPC / barcode → digits** | `pyzbar` decode → `upc.classify` | ~$0, deterministic | No | ✅ `ttb_cola_labels.py` |
| **Label text OCR** | PaddleOCR **or** Surya, self-hosted | ~$0 (compute) | Pretrained | ❌ (currently Claude) |
| **Structured bottle facts** | fine-tuned small VLM (distilled from COLA) | ~$0 (compute) | **Yes** | ❌ (this is the spend) |
| **Visual product match** | fine-tuned CLIP → ANN index | ~$0 | **Yes** | ⚠️ soft CLIP in `img_embed.py` |

### Recommended models (all open, self-hostable, Jan 2026)
- **Extractor (the big win):** fine-tune **Florence-2** (0.23B base / 0.77B large, Apache-2.0,
  purpose-built for OCR-with-region + structured visual extraction). Alternative:
  **Qwen2-VL-2B** / **Qwen2.5-VL-3B** end-to-end. Fallback route: **PaddleOCR → small
  fine-tuned LLM** (Qwen2.5-1.5B / Llama-3.2-1B) that maps OCR text → our schema, with our
  existing **dictionary layer** + **normalization scout** doing the deterministic mapping for
  free. Either way, the model emits **our** schema natively (COO vs bottled-in, our
  class/type mnemonics, our shelf-discriminators) — that is the proprietary edge.
- **Matcher:** contrastive fine-tune the **open_clip ViT-B-32** we already run (or move to
  **SigLIP2** / **DINOv2**) on same-UPC positive pairs; add a **FAISS** index for ANN at 1M
  scale (flagged at `img_embed.py:22`).
- **Barcode:** keep `pyzbar`; add **zxing-cpp** fallback + a small **YOLO** barcode-localizer
  so shelf/planogram photos yield UPCs, not just clean label scans.
- **Serving / training:** inference on the **Mac via MLX** or **ONNX Runtime** (CPU, in the
  Fly image if needed). Training on a cheap **spot GPU** (RunPod/Lambda/vast, ~$20–150) or
  the M-series Mac overnight for the small Florence-2.

---

## 3. Target architecture — tiered inference, Claude demoted to escalation

```
image ─▶ [Tier 0] barcode decode (pyzbar)  ── hit ─▶ UPC engine ─▶ resolved identity
            │ miss / need facts
            ▼
       [Tier 1] PaddleOCR + fine-tuned extractor   (LOCAL, ≈$0)
            │  confidence ≥ τ ─▶ land facts + provenance + confidence
            │  low confidence
            ▼
       [Tier 2] Claude vision (existing label_vision.py)
            │  every escalation logged as NEW training data ─▶ retrain Tier 1
            ▼  Tier 2's share shrinks over time (the flywheel)
```

Parallel matcher: any product image → fine-tuned CLIP embed → FAISS ANN vs the master → candidate
SKU matches → the existing `img_matches` signal (still SOFT, still name-gated; **never** borrow a
UPC across an image match).

Two invariants, both already standing rules:
- **Everything lands with provenance + confidence, never overwrites** (`normalization-scout`:
  landed data is never rewritten; fixes are translation-layer rules). The model is a *source*.
- **The confidence gate already exists** — `label_vision.run(only_gaps=True)` is exactly this
  pattern. We extend it, we don't invent it.

---

## 4. Staged build

- **Phase 0 — self-host the free tiers (LANDED).** `unifyd/cv/{barcode,ocr,rules,read}.py` — the
  tiered engine (barcode → OCR+rules → confidence-gated Claude escalation), wired into
  `label_vision.py` as `--engine tiered` (`CV_TIERED=1`), landing to `cv_reads` with provenance +
  confidence + tier. Behavior unit-tested. OCR backend is pluggable/optional (PaddleOCR|Tesseract,
  runs on the Mac, kept out of the slim Fly image). Immediate cost cut once an OCR backend is
  installed; until then it degrades to barcode + escalation.
- **Phase 1 — corpus (this is what `cv/trainset.py` does).** Materialize the COLA `(image → fields)`
  training manifest: 23.5k resolved-URL rows now, scaling to ~534k via a bounded, polite TTB
  image fetch. Brand-disjoint train/val/test split (no leakage).
- **Phase 2 — train the specialists.** Fine-tune Florence-2 (or OCR→small-LLM) on the manifest;
  contrastive-fine-tune CLIP on same-UPC pairs + build the FAISS index.
- **Phase 3 — serve + close the loop.** Confidence-gated serving behind the existing
  `label_vision` seam; Steward corrections + Tier-2 escalations feed retraining. Claude becomes
  auditor + escalation only.

---

## 5. Cost model

- **Up-front (once):** COLA image fetch (compute + bandwidth, no API) + a few GPU-hours to a day
  of fine-tuning (~$20–150 spot, or free on the Mac overnight for small Florence-2) + wiring.
- **Marginal (forever after):** ≈ electricity. A fine-tuned Florence-2 + PaddleOCR runs on the
  Mac we already run headful scrapers on. A million inferences ≈ pennies vs. a million Opus
  vision calls.

That is the trade requested: pay the hard cost once, run it at scale at ≈$0/run.

---

## 6. Where it plugs into existing code

- `unifyd/cv/read.py` — **the permanent seam.** `read(image, escalate=…)` → tiers → gated result
  with provenance/confidence. Phase 2 swaps `rules.extract` for Florence-2 here; nothing else moves.
- `unifyd/cv/barcode.py` — Tier 0 (pyzbar → `upc.classify`); generalizes `ttb_cola_labels.py`.
- `unifyd/cv/ocr.py` — Tier 1 text; pluggable PaddleOCR|Tesseract, lazy/optional, Mac-side.
- `unifyd/cv/rules.py` — Tier 1 brain (Phase 0): deterministic OCR-text → fields; reuses trainset norms.
- `unifyd/cv/trainset.py` — Phase 1 corpus builder → `cv_trainset` (brand-disjoint split).
- `unifyd/label_vision.py` — `extract_tiered()` / `run_tiered()` / `--engine tiered`; Claude `extract()`
  is now the **escalation** path (`_claude_native` adapts its output to the normalized schema).
  Claude-only `run()` → `label_extract` is untouched; tiered → `cv_reads`.
- `unifyd/img_embed.py` — the matcher; fine-tuned weights replace `laion2b` pretrained; add FAISS.
- `unifyd/upc.py` — barcode digits → validated/healed UPC (UPC-resolution engine).
- Registry/archive hygiene per `scraper-archive-standard`: one active model per task, versioned.

## 7. Open decisions
1. **Extractor architecture:** Florence-2 end-to-end **(recommended)** vs. PaddleOCR→small-LLM.
2. **Where training runs:** spot GPU vs. the Mac (MLX).
3. **Image-fetch policy for the 534k:** how aggressively to pull `imageWindow:` images from TTB
   (warmed-cookie recipe; polite cadence).
