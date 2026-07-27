#!/usr/bin/env python3
"""cv/trainset.py — build the Hoodie Vision training manifest from the COLA registry.

WHY: the federal COLA data is a huge, free, structured (label image -> fields) corpus — our
teacher. `ttb_cola_detail` has ~534k "strong" rows (image + brand + class-type + at least one
of ABV/varietal/net), spanning 399 class-types / 244 origins / 354k brands. `ttb_cola_labels`
has ~23.5k rows with the image URL already RESOLVED (front/back) + OCR + UPC — the subset we
can fetch and train on immediately. This turns those into one normalized, split, provenance-
stamped manifest that Phase-2 fine-tuning (Florence-2 / OCR->LLM) reads. See docs/PROPRIETARY-CV.md.

WHAT: read COLA detail (strong pairs) + enrich from COLA labels (resolved URL, UPC, OCR) by
ttb_id, deterministically normalize (ABV -> float %, net_contents -> mL, vintage -> int|NV),
choose an image reference (a resolved http(s) URL when we have one, else TTB's `imageWindow:<id>`
token flagged `needs_fetch`), and split BRAND-DISJOINT (a brand never spans train/val/test, so
the model can't memorize a label). Emits JSONL and (optionally) lands `cv_trainset` in the
warehouse. Never overwrites a source; the manifest is a derived, rebuildable artifact.

    python cv/trainset.py build --limit 40000 --land         # sample + write cv_trainset parquet
    python cv/trainset.py build --resolved-only --out cv_out/trainset.jsonl   # only fetchable-now rows
    python cv/trainset.py stats                               # coverage of the current cv_trainset

Runs where the warehouse creds are (Fly machine or a creds'd env); local-disk mode works too.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> unifyd/
import warehouse

try:
    import upc as _upc            # UPC-resolution engine: classify barcode digits
except Exception:
    _upc = None

FIELDS = ["ttb_id", "image_ref", "needs_fetch", "brand", "fanciful", "class_type", "class_code",
          "origin", "varietal", "abv", "net_ml", "vintage", "upc", "upc_status", "ocr_chars",
          "raw_abv", "raw_net", "split", "source", "built_at"]

# ---- deterministic normalizers (values also keep their raw form for auditability) ----------

def norm_abv(s):
    """'13.5% Alc./Vol.' / '40' / 'Alc. 5.5% by vol' -> 13.5 / 40.0 / 5.5, else None."""
    if s is None:
        return None
    m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", str(s))
    if not m:
        m = re.fullmatch(r"\s*(\d{1,2}(?:\.\d+)?)\s*", str(s))   # a bare number
    if not m:
        return None
    try:
        v = float(m.group(1))
        return v if 0 < v <= 100 else None
    except ValueError:
        return None

_ML = {"ml": 1.0, "milliliter": 1.0, "millilitre": 1.0, "cl": 10.0, "l": 1000.0,
       "liter": 1000.0, "litre": 1000.0}

def norm_net_ml(s):
    """'750 mL' / '1.5 L' / '1.75L' / '50 ml' / '12 fl oz' -> milliliters (float), else None."""
    if s is None:
        return None
    t = str(s).lower().strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(ml|milliliters?|millilitres?|cl|l|liters?|litres?)\b", t)
    if m:
        return round(float(m.group(1)) * _ML[m.group(2).rstrip("s")], 2)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:fl\.?\s*)?oz\b", t)      # (fluid) ounces
    if m:
        return round(float(m.group(1)) * 29.5735, 2)
    return None

def norm_vintage(s):
    if s is None:
        return None
    t = str(s).strip()
    if t.upper() in ("NV", "N/V", "NON-VINTAGE", "NONVINTAGE"):
        return "NV"
    m = re.fullmatch(r"(19|20)\d{2}", t)
    return int(t) if m else None

def _s(v):
    return "" if v is None else str(v).strip()

def _split_for(brand):
    """Brand-disjoint 90/5/5 split: bucket 0 -> test, 1 -> val, else train (stable per brand)."""
    h = int(hashlib.md5((brand or "").strip().lower().encode("utf-8")).hexdigest(), 16) % 20
    return "test" if h == 0 else ("val" if h == 1 else "train")

def _is_url(s):
    return isinstance(s, str) and s.lower().startswith(("http://", "https://"))

# ---- build --------------------------------------------------------------------------------

_STRONG = ("SELECT ttb_id, label_image_url, brand_name, fanciful_name, class_type_desc, "
           "class_type_code, origin_code, grape_varietal, alcohol_content, net_contents, wine_vintage "
           "FROM t WHERE COALESCE(CAST(label_image_url AS VARCHAR),'')<>'' "
           "AND COALESCE(CAST(brand_name AS VARCHAR),'')<>'' "
           "AND COALESCE(CAST(class_type_desc AS VARCHAR),'')<>'' AND ("
           "COALESCE(CAST(alcohol_content AS VARCHAR),'')<>'' OR "
           "COALESCE(CAST(grape_varietal AS VARCHAR),'')<>'' OR "
           "COALESCE(CAST(net_contents AS VARCHAR),'')<>'')")

def _labels_index(log):
    """{ttb_id -> {front,back,upc,abv,net,ocr}} from ttb_cola_labels (small, ~23.5k)."""
    try:
        rows = warehouse.query("ttb_cola_labels",
            "SELECT ttb_id, front_label_url, back_label_url, upc, abv, net_contents, ocr_chars FROM t")
    except Exception as e:
        log("[trainset] no ttb_cola_labels enrich (%s)" % str(e)[:60]); return {}
    idx = {}
    for r in rows:
        idx[str(r.get("ttb_id"))] = r
    log("[trainset] labels enrich index: %d resolved-URL rows" % len(idx))
    return idx

def build(limit=None, resolved_only=False, sample=True, land=False, out=None, log=print):
    lab = _labels_index(log)
    sql = _STRONG + ((" ORDER BY random()" if sample else "") + (" LIMIT %d" % int(limit)) if limit else "")
    log("[trainset] reading ttb_cola_detail strong rows%s ..." % (" (LIMIT %d)" % limit if limit else ""))
    rows = warehouse.query("ttb_cola_detail", sql)
    log("[trainset] %d strong detail rows" % len(rows))
    built_at = int(time.time())
    out_rows, resolved, token_only, splits = [], 0, 0, {"train": 0, "val": 0, "test": 0}
    for r in rows:
        tid = str(r.get("ttb_id"))
        L = lab.get(tid, {})
        # image: prefer a resolved http(s) URL from the labels table; else the imageWindow token
        img = _s(L.get("front_label_url")) or _s(r.get("label_image_url"))
        needs_fetch = not _is_url(img)                       # imageWindow:<id> must be fetched from TTB
        if resolved_only and needs_fetch:
            token_only += 1; continue
        if needs_fetch:
            token_only += 1
        else:
            resolved += 1
        brand = _s(r.get("brand_name"))
        # detail carries NO abv and only a UNITLESS net_contents code; the labels subset has the
        # real, unit-bearing values (OCR fills the rest at serve time) — so prefer labels here.
        raw_abv = _s(L.get("abv")) or _s(r.get("alcohol_content"))
        raw_net = _s(L.get("net_contents")) or _s(r.get("net_contents"))
        code = _s(L.get("upc"))
        rec = {
            "ttb_id": tid, "image_ref": img, "needs_fetch": needs_fetch,
            "brand": brand, "fanciful": _s(r.get("fanciful_name")),
            "class_type": _s(r.get("class_type_desc")), "class_code": _s(r.get("class_type_code")),
            "origin": _s(r.get("origin_code")), "varietal": _s(r.get("grape_varietal")),
            "abv": norm_abv(raw_abv), "net_ml": norm_net_ml(raw_net),
            "vintage": norm_vintage(r.get("wine_vintage")), "upc": code,
            "upc_status": (_upc.classify(code) if (_upc and code) else ""),
            "ocr_chars": L.get("ocr_chars"), "raw_abv": raw_abv, "raw_net": raw_net,
            "split": _split_for(brand), "source": "ttb_cola", "built_at": built_at,
        }
        splits[rec["split"]] += 1
        out_rows.append(rec)
    log("[trainset] built %d rows | resolved-URL %d | token-only(needs_fetch) %d | split %s"
        % (len(out_rows), resolved, token_only, splits))
    _fill_report(out_rows, log)
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for rec in out_rows:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log("[trainset] wrote JSONL -> %s" % out)
    if land and out_rows:
        res = warehouse.write_parquet("cv_trainset", out_rows, fields=FIELDS)
        log("[trainset] landed cv_trainset: %s" % res)
    return out_rows

def _fill_report(rows, log):
    if not rows:
        return
    n = len(rows)
    def pct(k, pred):
        c = sum(1 for r in rows if pred(r))
        return "%s %d (%.0f%%)" % (k, c, 100.0 * c / n)
    log("[trainset] field fill: " + " | ".join([
        pct("abv", lambda r: r["abv"] is not None),
        pct("net_ml", lambda r: r["net_ml"] is not None),
        pct("varietal", lambda r: r["varietal"]),
        pct("vintage", lambda r: r["vintage"] is not None),
        pct("upc_valid", lambda r: r["upc_status"] == "valid"),
    ]))

def stats(log=print):
    try:
        r = warehouse.query("cv_trainset",
            "SELECT split, COUNT(*) n, COUNT(*) FILTER (WHERE needs_fetch=false) resolved, "
            "COUNT(*) FILTER (WHERE abv IS NOT NULL) has_abv FROM t GROUP BY split ORDER BY split")
        for x in r:
            log("  %-6s n=%-8s resolved=%-8s has_abv=%s" % (x["split"], x["n"], x["resolved"], x["has_abv"]))
    except Exception as e:
        log("[trainset] no cv_trainset yet (%s)" % str(e)[:80])

def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the Hoodie Vision COLA training manifest.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--limit", type=int, default=None, help="cap rows (omit = all ~534k strong)")
    b.add_argument("--resolved-only", action="store_true", help="only rows whose image URL is already fetchable")
    b.add_argument("--no-sample", action="store_true", help="don't ORDER BY random() when limiting")
    b.add_argument("--land", action="store_true", help="write cv_trainset parquet to the warehouse")
    b.add_argument("--out", default=None, help="also write a JSONL manifest to this path")
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(limit=a.limit, resolved_only=a.resolved_only, sample=not a.no_sample, land=a.land, out=a.out)
    elif a.cmd == "stats":
        stats()
    return 0

if __name__ == "__main__":
    sys.exit(main())
