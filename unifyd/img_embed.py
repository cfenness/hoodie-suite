#!/usr/bin/env python3
"""img_embed.py — product-image embeddings as a SOFT clustering signal for the master matcher.

WHY (proven, not assumed): perceptual hashing (dHash) is useless here — every amber bottle hashes alike, so
same-product-across-sources scores as far apart as different products. Semantic CLIP embeddings DO separate
them: on a labeled set (same-UPC-different-photo vs different-UPC) ViT-B-32 gave same-product cosine median
~0.76 vs different-product ~0.35, with only tail overlap. So image similarity is a HELPER — it corroborates a
candidate pair, it is NEVER a merge decision (people photograph the wrong size, images are display-only).

WHAT: embed every captured product image once (CLIP ViT-B-32, local, free, deterministic vectors) into `img_vec`
(source, sku, upc, image, vec=base64 float16). Then `cross_match` finds cross-source pairs with cosine >= a
threshold → an `img_matches` signal the matcher/steward reads. It stays a SOFT booster: the validated failure
mode is same-brand/different-varietal look-alikes (Bogle Merlot vs Bogle Cab share a label and score ~0.96), so
the matcher must gate every image pair on name/attribute agreement, and we NEVER borrow a UPC across an image
match (that would assign a wrong identity — the opposite of the "help, not decide" goal).

    python img_embed.py build --source offprem_products --limit 2000
    python img_embed.py match --threshold 0.9

CPU inference is ~10-20 img/s, so `build` is incremental (accumulates; skips already-embedded sku) — run it
per source in the background. Cross-source nearest-neighbour is brute-force here (fine for the candidate set);
swap in FAISS if it ever needs the full 1M.
"""
import argparse
import base64
import io
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

IMG_FLD = ["source", "sku", "upc", "image", "vec"]
MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
_M = {}                                   # cached (model, preprocess)
# sources that carry a product image + a stable per-product key + (ideally) a upc
DEFAULT_SOURCES = ["specs_products", "abc_products", "offprem_products", "binnys_products"]


def _model():
    if "m" not in _M:
        import open_clip
        import torch
        model, _, pre = open_clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAINED)
        model.eval()
        _M["m"] = (model, pre)
        _M["torch"] = torch
    return _M["m"]


def _fetch_pil(url, timeout=15):
    from PIL import Image
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    return Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=timeout).read())).convert("RGB")


def _pack(v):
    """512-d float32 vector -> base64(float16) (~1.4KB) for compact, exact-enough storage."""
    import numpy as np
    return base64.b64encode(np.asarray(v, dtype=np.float16).tobytes()).decode("ascii")


def unpack(s):
    import numpy as np
    return np.frombuffer(base64.b64decode(s), dtype=np.float16).astype(np.float32)


def embed_urls(urls, workers=12, batch=32, log=print):
    """Download (concurrent) + CLIP-embed (batched) a list of image URLs. Returns {url: vec} for the ones that
    fetched + decoded; failures are simply absent."""
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor
    model, pre = _model()
    torch = _M["torch"]
    import torch.nn.functional as F
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        imgs = list(ex.map(lambda u: (u, _safe(pre, u)), urls))
    ok = [(u, t) for (u, t) in imgs if t is not None]
    for i in range(0, len(ok), batch):
        chunk = ok[i:i + batch]
        with torch.no_grad():
            v = model.encode_image(torch.stack([t for _, t in chunk]))
            v = F.normalize(v, dim=-1).cpu().numpy().astype(np.float32)
        for (u, _), row in zip(chunk, v):
            out[u] = row
    return out


def _safe(pre, url):
    try:
        return pre(_fetch_pil(url))
    except Exception:
        return None


def build(source, limit=None, workers=12, log=print):
    """Embed a source's product images into `img_vec` (accumulates; skips sku already embedded)."""
    cols = set(warehouse.query(source, "SELECT * FROM t LIMIT 1")[0].keys())
    keyc = "sku" if "sku" in cols else ("tcin" if "tcin" in cols else "slug")
    upcc = "upc" if "upc" in cols else "NULL"
    rows = warehouse.query(source, "SELECT DISTINCT CAST(%s AS VARCHAR) sku, CAST(%s AS VARCHAR) upc, image "
                           "FROM t WHERE image IS NOT NULL AND image<>''%s"
                           % (keyc, upcc, (" LIMIT %d" % limit) if limit else ""))
    try:
        done = {r["sku"] for r in warehouse.query("img_vec", "SELECT sku FROM t WHERE source=?", [source])}
    except Exception:
        done = set()
    todo = [r for r in rows if r["sku"] not in done]
    log("[img_embed] %s: %d images, %d already embedded, %d to do" % (source, len(rows), len(done), len(todo)))
    landed = 0
    for i in range(0, len(todo), 256):                    # land in chunks so a long run is resumable
        chunk = todo[i:i + 256]
        vecs = embed_urls([r["image"] for r in chunk], workers=workers, log=log)
        recs = [{"source": source, "sku": r["sku"], "upc": r.get("upc") or "", "image": r["image"],
                 "vec": _pack(vecs[r["image"]])} for r in chunk if r["image"] in vecs]
        if recs:
            warehouse.write_accumulate("img_vec", recs, key=lambda x: (x["source"], x["sku"]), fields=IMG_FLD)
            landed += len(recs)
            log("[img_embed] %s: +%d (%d/%d)" % (source, len(recs), min(i + 256, len(todo)), len(todo)))
    log("[img_embed] %s DONE: %d embedded" % (source, landed))
    return landed


_STOP = {"the", "and", "of", "wine", "wines", "spirits", "liqueur", "bottle", "ml", "l", "red", "white",
         "napa", "valley", "reserve", "vineyards", "winery", "co", "kentucky", "straight"}
_MATCH_FIELDS = ["a_source", "a_sku", "a_upc", "b_source", "b_sku", "b_upc", "cosine", "name_sim",
                 "class_agree", "varietal_agree", "flavor_agree", "size_agree", "confidence", "verdict"]


def _toks(s):
    import re
    return set(t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 1 and t not in _STOP)


def _jacc(a, b):
    A, B = _toks(a), _toks(b)
    return round(len(A & B) / len(A | B), 3) if (A | B) else 0.0


def _agree(x, y):
    """1 = both present + equal, -1 = both present + differ (a CONFLICT), 0 = unknown (either side missing).
    Coerces non-strings (e.g. size_ml floats) and treats 750 == 750.0."""
    def norm(v):
        if v is None:
            return ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip().lower()
    x, y = norm(x), norm(y)
    if not x or not y or x == "none" or y == "none":
        return 0
    return 1 if x == y else -1


def cross_match(threshold=0.9, log=print):
    """Cross-source image pairs with cosine >= threshold, each SCORED against the item/product information so the
    look-alikes get demoted. Image similarity alone confuses same-brand/different-varietal bottles (Bogle Merlot
    vs Bogle Cab both ~0.96 — same label design); comparing the normalized `_stage_product` attributes fixes it:

      • varietal / class_type / flavor CONFLICT (both present, differ) -> `look_alike`  (same brand, diff product)
      • name-token agreement corroborates (catches the off-premise store-brand-name bug: the two names still
        share "Johnnie Walker Black" even when the brand column is a store name)
      • size is INFORMATIONAL, never a gate — a wrong-size photo must not reject a real match (user's rule)

    Lands `img_matches` with cosine + the agreement signals + a combined `confidence` + `verdict`
    (`same_item` / `review` / `look_alike` / `weak`). Still a SOFT signal — never an identity/UPC assignment."""
    import numpy as np
    rows = warehouse.query("img_vec", "SELECT source, sku, upc, vec FROM t")
    if not rows:
        log("[img_embed] img_vec empty — run build first"); return 0
    M = np.nan_to_num(np.stack([unpack(r["vec"]) for r in rows]), nan=0.0)   # a bad/empty embed -> 0 -> excluded
    src = [r["source"] for r in rows]
    pairs = []
    for i in range(len(rows)):
        sims = M[i + 1:] @ M[i]
        for off, c in enumerate(sims):
            j = i + 1 + off
            if c >= threshold and src[i] != src[j]:       # cross-source only
                pairs.append((rows[i], rows[j], float(c)))
    # batch-load the normalized item/product attributes for every (source, sku) in the pairs
    need = {(r["source"], r["sku"]) for p in pairs for r in (p[0], p[1])}
    stg = _stage_lookup(need)
    out = []
    for a, b, c in pairs:
        sa = stg.get((a["source"], a["sku"]), {})
        sb = stg.get((b["source"], b["sku"]), {})
        name_sim = _jacc(sa.get("product_name") or sa.get("core_name"),
                         sb.get("product_name") or sb.get("core_name"))
        cls = _agree(sa.get("class_type"), sb.get("class_type"))
        var = _agree(sa.get("varietal"), sb.get("varietal"))
        flav = _agree(sa.get("flavor"), sb.get("flavor"))
        size_agree = _agree(sa.get("size_ml"), sb.get("size_ml"))     # recorded, NOT gated
        conflict = cls < 0 or var < 0 or flav < 0
        if conflict:                                       # image says same, product info says NO
            verdict, conf = "look_alike", round(c * 0.3, 3)
        elif c >= 0.9 and name_sim >= 0.5:
            verdict = "same_item"
            conf = round(min(1.0, c * 0.55 + name_sim * 0.35 + (0.06 if size_agree == 1 else 0) + 0.08), 3)
        elif c >= 0.9 or name_sim >= 0.4:
            verdict, conf = "review", round(c * 0.5 + name_sim * 0.3, 3)
        else:
            verdict, conf = "weak", round(c * 0.4, 3)
        out.append({"a_source": a["source"], "a_sku": a["sku"], "a_upc": a.get("upc") or "",
                    "b_source": b["source"], "b_sku": b["sku"], "b_upc": b.get("upc") or "",
                    "cosine": round(c, 4), "name_sim": name_sim, "class_agree": cls, "varietal_agree": var,
                    "flavor_agree": flav, "size_agree": size_agree, "confidence": conf, "verdict": verdict})
    warehouse.write_parquet("img_matches", out, fields=_MATCH_FIELDS)
    tally = {}
    for r in out:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    log("[img_embed] img_matches: %d cross-source pairs >= %.2f  %s" % (len(out), threshold, tally))
    return len(out)


def _stage_lookup(pairs):
    """Batch-load normalized item/product attributes from `_stage_product` for a set of (source, sku)."""
    if not pairs:
        return {}
    by_src = {}
    for s, k in pairs:
        by_src.setdefault(s, set()).add(str(k))
    out = {}
    for source, skus in by_src.items():
        skus = list(skus)
        for i in range(0, len(skus), 900):               # chunk the IN() list
            chunk = skus[i:i + 900]
            ph = ",".join("?" * len(chunk))
            try:
                rows = warehouse.query("_stage_product",
                    "SELECT CAST(_source_id AS VARCHAR) sid, product_name, core_name, brand, varietal, "
                    "class_type, flavor, size_ml FROM t WHERE _source=? AND CAST(_source_id AS VARCHAR) IN (%s)"
                    % ph, [source] + chunk)
            except Exception:
                rows = []
            for r in rows:
                out[(source, r["sid"])] = r
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Product-image CLIP embeddings — soft clustering signal.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--source", default=None); b.add_argument("--limit", type=int, default=None)
    b.add_argument("--workers", type=int, default=12)
    m = sub.add_parser("match"); m.add_argument("--threshold", type=float, default=0.9)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        srcs = [a.source] if a.source else DEFAULT_SOURCES
        for s in srcs:
            try:
                build(s, limit=a.limit, workers=a.workers)
            except Exception as e:
                print("[img_embed] %s skipped: %s" % (s, str(e)[:80]))
    elif a.cmd == "match":
        cross_match(threshold=a.threshold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
