#!/usr/bin/env python3
"""dam_gallery.py — the CV reference gallery (P3): official studio imagery per SKU, scope-gated.

WHAT THIS IS FOR
  A shelf photo is a hard recognition problem partly because there is no clean reference to match
  against. A supplier's own media centre has exactly that: the studio bottle shot, lit and centred,
  produced by the brand. This turns those into per-SKU reference rows the CV pipeline can match to.

WHAT IT STORES, AND WHY THE POINTER IS THE DEFAULT
  Per the design: **pointer + licence + perceptual hash + embedding** for every image, and the FULL
  asset only where the source's scope permits it. So a gallery row always exists — it is a fact about
  a file we can see — and the derived artefacts appear only when `rights.may` says so. A row with a
  NULL phash and a `withheld_reason` is the honest shape for a source we may not derive from; a
  missing row would just look like the supplier had no imagery.

THE GATE RUNS PER ASSET, NOT PER RUN
  Every image goes through `rights.require(rec, "derive_hash")` / `"derive_embedding"` individually
  and every emission is logged. `build()` cannot be talked into a bulk exception, and it re-checks
  rather than caching a verdict from the top of the run — a record that goes stale mid-run stops the
  rest of it.

WHAT A pHASH IS AND IS NOT GOOD FOR HERE
  The dHash implemented below is for **identity within the gallery** — the same studio file uploaded
  five times under five names collapses to one reference, and a re-pull can tell "already have this"
  from "new asset". It is explicitly NOT the studio→shelf matcher: perceptual hashes fail on bottles
  photographed in the wild ([[image-match-signal]]), where the embedding is the real signal. Stating
  that here because a pHash column invites exactly the wrong assumption.

THE EMBEDDING IS PLUGGABLE AND ABSENT BY DEFAULT
  CLIP-class embedding needs torch, which is a heavy dependency this image does not carry and which
  is not being added on my own authority. `embedder()` resolves one if the host has it and otherwise
  reports `embedding_backend="unavailable"` — landing the row with a NULL embedding and SAYING so,
  rather than silently shipping a gallery with no vectors in it ([[quiet-degrades]]).

STATUS: BUILT, AND CURRENTLY EMPTY ON PURPOSE
  No supplier we have surveyed grants image reuse. Bacardi is `prohibited`; the three census
  candidates (AB InBev, William Grant, Heaven Hill) all turned out to be INBOUND user-content
  licences, not grants to us — they classify `silent`, which holds. So this pipeline runs, produces
  pointer rows, and derives nothing. That is the gate working. The first real gallery needs a
  supplier whose terms grant AND a `counsel_cleared` sign-off on that record.
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dam       # noqa: E402
import rights    # noqa: E402

TABLE = "dam_gallery"
GALLERY_FIELDS = [
    "gallery_id", "source_id", "asset_id", "asset_url", "vendor",
    "hoodie_brand_id", "brand", "brand_key", "sku_id", "sku_match_method",
    "image_kind", "width", "height", "size_bytes",
    "phash", "phash_algo", "embedding", "embedding_backend", "embedding_dim",
    "retention", "rights_ref", "image_use", "image_scope", "withheld_reason", "built_at",
]

# What a studio reference actually is. A logo or a lifestyle crowd shot is not a bottle reference, and
# letting them in would poison a nearest-neighbour lookup with confident wrong answers.
_BOTTLE_WORDS = ("bottle", "pack", "packshot", "pack shot", "product", "shot", "render",
                 "hero", "cutout", "cut-out", "transparent", "on white", "bottleshot")
_NOT_REFERENCE = ("logo", "wordmark", "icon", "font", "banner", "billboard", "crowd", "party",
                  "event", "portrait", "headshot", "team", "office", "distillery", "cocktail",
                  "serve", "lifestyle", "campaign", "poster")


def classify_image(asset):
    """(kind, is_reference). Deterministic read of the filename + folder, because that is all we have
    before we are allowed to look at the pixels — and for a source we may not derive from, it is all
    we will ever have."""
    hay = " ".join(str(asset.get(k) or "") for k in ("name", "title", "folder_path")).lower()
    if any(w in hay for w in _NOT_REFERENCE):
        return "non_reference", False
    if any(w in hay for w in _BOTTLE_WORDS):
        return "product_shot", True
    return "unclassified", False


# ── perceptual hash (identity within the gallery — see the module docstring) ───────────────────────
def dhash(data, size=8):
    """64-bit difference hash as 16 hex chars, via pillow. Returns None when pillow is absent (a
    declared capability) or the bytes are not a decodable image — never a fabricated value."""
    try:
        import io
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("L").resize((size + 1, size), Image.LANCZOS)
        px = list(im.getdata())
        bits = 0
        for row in range(size):
            for col in range(size):
                left = px[row * (size + 1) + col]
                right = px[row * (size + 1) + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return "%016x" % bits
    except Exception:
        return None


def image_dims(data):
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return (None, None)


def hamming(a, b):
    """Distance between two dhash hex strings — the dedupe primitive. None when either is missing,
    so an absent hash can never read as a perfect match (distance 0)."""
    if not a or not b or len(a) != len(b):
        return None
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ── embedding backend (pluggable; absent by default) ──────────────────────────────────────────────
def embedder():
    """(fn, backend_name, dim) or (None, "unavailable", 0).

    Resolved, never assumed. torch/open_clip is a heavy dependency this image does not ship and that
    is not being added unilaterally — so the gallery reports the backend it actually used and a run
    with no vectors is visibly a run with no vectors."""
    if os.environ.get("DAM_EMBED", "1") == "0":
        return None, "disabled", 0
    try:
        import io

        import open_clip
        import torch
        from PIL import Image
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k")
        model.eval()

        def _embed(data):
            with torch.no_grad():
                img = preprocess(Image.open(io.BytesIO(data)).convert("RGB")).unsqueeze(0)
                v = model.encode_image(img)[0]
                v = v / v.norm()
                return [round(float(x), 6) for x in v.tolist()]

        return _embed, "open_clip/ViT-B-32", 512
    except Exception:
        return None, "unavailable", 0


# ── the build ─────────────────────────────────────────────────────────────────────────────────────
def gallery_row(rec, asset, kind, data=None, embed=None, backend="unavailable", dim=0):
    """One gallery row. Derivation is gated PER ASSET; when the gate refuses, the row still lands as
    a pointer carrying the reason."""
    ok_hash, why_hash = rights.may(rec, "derive_hash")
    ok_emb, _why_emb = rights.may(rec, "derive_embedding")
    perms = rec.get("permissions") or {}
    phash = vec = None
    w = h = None
    if ok_hash and data:
        phash = dhash(data)
        w, h = image_dims(data)
    if ok_emb and data and embed:
        try:
            vec = embed(data)
        except Exception:
            vec = None
    return {
        "gallery_id": "G-" + hashlib.sha1(
            ("%s|%s" % (rec.get("source_id"), asset.get("asset_id"))).encode()).hexdigest()[:16],
        "source_id": rec.get("source_id"), "asset_id": asset.get("asset_id"),
        "asset_url": asset.get("asset_url"), "vendor": asset.get("vendor"),
        "hoodie_brand_id": asset.get("hoodie_brand_id"), "brand": asset.get("brand"),
        "brand_key": asset.get("brand_key"), "sku_id": asset.get("sku_id"),
        "sku_match_method": asset.get("sku_match_method"),
        "image_kind": kind, "width": w, "height": h, "size_bytes": asset.get("size_bytes"),
        "phash": phash, "phash_algo": "dhash8" if phash else None,
        "embedding": json.dumps(vec) if vec else None,
        "embedding_backend": backend if vec else ("withheld" if not ok_emb else backend),
        "embedding_dim": (dim if vec else 0),
        "retention": "derived" if (phash or vec) else "pointer_only",
        "rights_ref": rights.rights_ref(rec),
        "image_use": perms.get("image_use"), "image_scope": perms.get("scope"),
        "withheld_reason": None if ok_hash else why_hash,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build(source_id, assets=None, limit=None, land=True, log=print):
    """Build the reference gallery for one DAM source.

    Reads `dam_assets` (or an explicit list), keeps the images that look like product references, and
    derives ONLY what the source's rights record permits — per asset, re-checked each time."""
    rec = rights.load(source_id)
    perms = rec.get("permissions") or {}
    may_fetch, why = rights.may(rec, "fetch_asset")
    log("%s: rights %s / scope=%s — asset bytes %s"
        % (source_id, perms.get("image_use"), perms.get("scope"),
           "PERMITTED" if may_fetch else "DENIED (%s)" % why))

    if assets is None:
        try:
            import warehouse
            assets = warehouse.query(
                dam.ASSETS_TABLE,
                "SELECT * FROM t WHERE source_id = ? AND asset_type = 'image'", [source_id])
        except Exception as e:
            log("dam_gallery: %s unreadable (%s)" % (dam.ASSETS_TABLE, str(e)[:90]))
            assets = []

    refs = []
    for a in assets or []:
        kind, is_ref = classify_image(a)
        if is_ref:
            refs.append((a, kind))
    considered, matched = len(assets or []), len(refs)
    if limit:
        refs = refs[:limit]

    embed, backend, dim = (None, "unavailable", 0)
    if may_fetch:
        embed, backend, dim = embedder()
        if backend in ("unavailable", "disabled"):
            log("  embedding backend %s — rows land with NULL vectors and say so" % backend)

    rows, derived, fetched = [], 0, 0
    for a, kind in refs:
        data = None
        if may_fetch:
            try:
                # The chokepoint re-checks the gate itself; this is not a second authorisation.
                data = dam.asset_bytes(rec, a)
                fetched += 1
            except rights.RightsViolation:
                raise
            except Exception as e:
                log("  asset %s: fetch failed: %s" % (a.get("asset_id"), str(e)[:80]))
        row = gallery_row(rec, a, kind, data=data, embed=embed, backend=backend, dim=dim)
        del data
        if row["phash"] or row["embedding"]:
            derived += 1
        rows.append(row)

    # Dedupe report — the pHash's actual job. Cheap, and it is the number that says whether a gallery
    # of N files is really N references.
    seen, dupes = {}, 0
    for r in rows:
        if r["phash"]:
            if r["phash"] in seen:
                dupes += 1
            seen.setdefault(r["phash"], r["gallery_id"])

    cov = {"assets_considered": considered, "reference_candidates": matched,
           "rows": len(rows), "bytes_fetched": fetched, "derived": derived,
           "duplicate_images": dupes, "embedding_backend": backend,
           "image_use": perms.get("image_use"), "scope": perms.get("scope")}
    log("%s gallery: %d/%d assets look like product references; %d rows, %d derived, %d fetched%s"
        % (source_id, matched, considered, len(rows), derived, fetched,
           " (%d duplicate images)" % dupes if dupes else ""))
    if rows and not derived:
        log("  NOTHING was derived — the record does not permit it. Rows are pointers carrying the "
            "reason; this is the gate working, not an empty run.")

    if land and rows:
        try:
            import warehouse
            warehouse.write_accumulate(TABLE, rows, key="gallery_id",
                                       fields=GALLERY_FIELDS, coverage=False)
            log("landed %s: %d rows" % (TABLE, len(rows)))
        except Exception as e:
            log("%s land skipped: %s" % (TABLE, str(e)[:100]))

    print("HOODIE_RESULT " + json.dumps(dict(
        {"status": "success", "items_done": len(rows), "items_total": len(rows)}, **cov)))
    return rows, cov


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the CV reference gallery for a DAM source.")
    ap.add_argument("source_id", nargs="?", default="dam-bacardi")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    rows, cov = build(a.source_id, limit=a.limit, land=not a.no_land)
    print(json.dumps(cov, indent=2))


if __name__ == "__main__":
    main()
