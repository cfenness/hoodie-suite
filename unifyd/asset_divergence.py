#!/usr/bin/env python3
"""asset_divergence.py — where do chains disagree about what a product LOOKS like?

THE QUESTION
  One item (one UPC) is listed by a dozen retailers, each with its own product image. Usually those
  images are the same supplier pack shot passed down the syndication chain. Sometimes they are not —
  one chain is still showing packaging the brand retired two years ago. That gap is invisible to the
  supplier (they see what they published, not what the trade executed) and invisible to the retailer
  (they see their own set), and it is visible here because this is the only place both sides land.

WHAT THIS DOES AND, MORE IMPORTANTLY, WHAT IT REFUSES TO DO
  It clusters an item's images across sources and reports the DIVERGENCE — how many distinct looks
  are live, who shows which, since when. That part is measurable and lands.

  It does NOT tell you which one is stale. Not yet, and not by default. The reason is specific: at
  this threshold "two photographs of the same bottle" and "two different packs" are not reliably
  separable. `img_embed` measured same-product-different-photo at cosine median ~0.76 — that is the
  distribution a *benign* difference already occupies, so a repack sits somewhere on top of it and
  nobody has labelled where. Calling the difference `stale` on an unmeasured threshold would mean
  telling a brand team their retail execution is broken on the strength of a lighting change.

  So `stale_candidate` is None until `backtest()` measures precision against a labelled set, exactly
  the gate `overlay_detect` applies to its heuristics: a rule with no measured precision RUNS BUT
  STAYS SILENT. `withheld_reason` says so on every row.

WHAT IS DETERMINISTIC HERE, AND THEREFORE SAFE TO SHOW
  Not the similarity verdict — the EVIDENCE around it:
    • how many distinct clusters an item's images form, at a stated threshold
    • which sources are in each cluster, and how many
    • first-seen / last-seen per cluster, from the observation history
  "Five chains show look A since 2024, one chain shows look B and has not been re-observed since
  2022" is a defensible sentence built from counts and dates. It is also the sentence that makes a
  supplier conversation concrete, without ever claiming which pack is correct — which is the
  supplier's own data to supply.

IDENTITY IS THE MASTER'S, NOT A RAW UPC
  The first cut grouped by the UPC on the source row, and it could not see the sources that matter:
  binnys_products, abc_products and total_wine_products carry 35k images between them and have NO
  upc column at all — they key on a retailer SKU. Measured live, that version reached 1,172 items.

  Identity therefore comes from the master, which exists to answer exactly this:
  `xwalk_source_sku` (source + product_id -> item_key) then `dim_sku.resolved_id`. Measured on the
  same data:

      item_key    (md5 hard key)          251,193 items ->   515 on >=3 sources,  3,366 images
      resolved_id (collapsed identity)     89,016 items -> 1,104 on >=3 sources, 18,302 images

  `resolved_id` wins because the md5 key OVER-SPLITS — the same product stated differently by two
  sources gets two keys, and reuniting them is the whole job of the resolved identity. Divergence is
  a cross-source measure, so it lives or dies on that collapse.

  Every row records `identity_method`, because a divergence found under `upc` and one found under
  `resolved_id` are not equally trustworthy and must not be pooled silently.

WHAT THIS MEASUREMENT ACTUALLY EXPOSED
  98.5% of items with an image are seen by ONE source. That is not a fact about retail — Kroger and
  Total Wine plainly both sell Absolut Citron 1750 — it is the master's fan-out
  ([[master-fanout-brand-resolution]]). So the ceiling on this detector is master identity
  resolution, not images, not embeddings, and not compute: the whole current working set hashes in
  under two hours.
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TABLE = "asset_divergence"
PRECISION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_divergence_precision.json")

FIELDS = [
    "item_id", "identity_method", "cluster_id", "n_clusters", "cluster_rank", "n_sources_in_cluster",
    "total_sources", "cluster_share", "sources", "representative_image",
    "first_seen", "last_seen", "verdict", "stale_candidate", "withheld_reason",
    "threshold", "method", "precision_measured", "computed_at",
]

# The clustering threshold. Set at the measured same-product-different-photo median from img_embed
# (~0.76), which is deliberately GENEROUS: at 0.76 two photos of one pack stay together, so a split
# means something beyond photography. It is a divergence detector, not a duplicate detector — the
# cost of splitting a benign pair is a false alarm, and that is the expensive direction here.
DEFAULT_THRESHOLD = 0.76

# The dHash tier. A hash within this many bits is the SAME FILE re-encoded/resized; beyond it the
# images are different files, which is NOT the same claim as different packs (see `_tier`). 10 of 64
# bits is generous on purpose — re-encoding and thumbnailing move a handful of bits, and splitting a
# benign pair is the expensive direction.
HASH_MAX_BITS = 10

# An item seen by fewer sources than this cannot support a breadth argument: "1 chain disagrees with
# 1 chain" is not evidence of anything. Reported as insufficient_data, never as aligned.
MIN_SOURCES = 3

# The crosswalk names sources short; the image tables are named `<source>_products`. Measured live —
# `total-wine` uses a hyphen while the table is `total_wine_products`, so this cannot be derived by
# string munging and is written out.
_SRC_ALIAS = {"binnys": "binnys_products", "abc": "abc_products",
              "total-wine": "total_wine_products", "offprem": "offprem_products",
              "haskells": "haskells_products", "specs": "specs_products",
              "cityhive": "cityhive_products", "target": "target_products",
              "kroger": "kroger_atlas_products", "walmart": "walmart_products"}


# ── pure similarity + clustering (stdlib; numpy only used for scale in build()) ───────────────────
def cosine(a, b):
    """Cosine similarity of two equal-length vectors. Returns None when either is empty or zero —
    an undefined similarity must never read as 0.0, which would look like a confident disagreement."""
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return None
    return dot / (na * nb)


def cluster_hashes(items, max_bits=HASH_MAX_BITS):
    """Greedy clustering by dHash bit distance. `items` = [(key, dhash)] → {key: cluster_id}.

    Same greedy, order-stable shape as the vector clustering, for the same reason: a supplier may
    act on this, so the partition must be reproducible."""
    import img_hash
    assigned, reps = {}, []
    for key, h in items:
        placed, best = None, 10 ** 9
        for cid, rep in reps:
            d = img_hash.hamming(h, rep)
            if d is not None and d <= max_bits and d < best:
                best, placed = d, cid
        if placed is None:
            placed = len(reps)
            reps.append((placed, h))
        assigned[key] = placed
    return assigned


def cluster(vectors, threshold=DEFAULT_THRESHOLD):
    """Greedy agglomerative clustering by cosine. `vectors` is [(key, vec)] → {key: cluster_id}.

    Greedy and order-stable rather than optimal: an item has a handful of images, and a
    reproducible answer matters more than a marginally better partition — this feeds a report a
    supplier may act on, so the same input must always give the same clusters."""
    assigned, reps = {}, []          # reps: [(cluster_id, representative_vector)]
    for key, vec in vectors:
        placed = None
        best = -2.0
        for cid, rep in reps:
            c = cosine(vec, rep)
            if c is not None and c >= threshold and c > best:
                best, placed = c, cid
        if placed is None:
            placed = len(reps)
            reps.append((placed, vec))
        assigned[key] = placed
    return assigned


def analyze_item(upc, images, threshold=DEFAULT_THRESHOLD, precision=None, identity="upc"):
    """One item's images → divergence rows. PURE.

    `images` = [{source, image, vec, first_seen, last_seen}]. Returns [] when the item cannot
    support a conclusion, rather than a row asserting agreement it has not earned."""
    # TIER SELECTION. Prefer CLIP where the vectors exist, because it is the only tier that can
    # collapse a benign photography difference. Fall back to dHash, which needs nothing beyond
    # pillow — that fallback is the whole reason this runs at all today, since `img_vec` requires
    # torch and has never been populated.
    has_vec = [i for i in images if i.get("vec")]
    has_hash = [i for i in images if i.get("dhash")]
    if has_vec and len(has_vec) >= len(has_hash):
        usable, tier = has_vec, "clip"
    elif has_hash:
        usable, tier = has_hash, "dhash"
    else:
        usable, tier = has_vec, "clip"
    sources = {i["source"] for i in usable}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not usable:
        return []
    if len(sources) < MIN_SOURCES:
        return [{
            "item_id": upc, "identity_method": identity, "cluster_id": None, "n_clusters": None, "cluster_rank": None,
            "n_sources_in_cluster": len(sources), "total_sources": len(sources),
            "cluster_share": None, "sources": ",".join(sorted(sources)),
            "representative_image": usable[0].get("image"),
            "first_seen": None, "last_seen": None,
            "verdict": "insufficient_data", "stale_candidate": None,
            "withheld_reason": "only %d source(s); a breadth argument needs %d"
                               % (len(sources), MIN_SOURCES),
            "threshold": threshold, "method": "clip-cosine-greedy" if tier == "clip" else "dhash-hamming-greedy",
            "precision_measured": bool(precision), "computed_at": now,
        }]

    if tier == "dhash":
        assign = cluster_hashes([(i["image"], i["dhash"]) for i in usable])
    else:
        assign = cluster([(i["image"], i["vec"]) for i in usable], threshold=threshold)
    groups = {}
    for i in usable:
        groups.setdefault(assign[i["image"]], []).append(i)
    n_clusters = len(groups)
    total_sources = len(sources)

    # Rank by source BREADTH — how many distinct retailers show this look. Breadth is the honest
    # proxy for "what the trade is actually running": one chain's outlier is not the norm, and a
    # look carried by six chains did not get there by accident.
    ranked = sorted(groups.items(),
                    key=lambda kv: (-len({x["source"] for x in kv[1]}), str(kv[0])))

    rows = []
    for rank, (cid, members) in enumerate(ranked):
        csrc = sorted({m["source"] for m in members})
        firsts = [m.get("first_seen") for m in members if m.get("first_seen")]
        lasts = [m.get("last_seen") for m in members if m.get("last_seen")]
        rows.append({
            "item_id": upc, "identity_method": identity, "cluster_id": int(cid), "n_clusters": n_clusters,
            "cluster_rank": rank, "n_sources_in_cluster": len(csrc), "total_sources": total_sources,
            "cluster_share": round(len(csrc) / total_sources, 3),
            "sources": ",".join(csrc), "representative_image": members[0].get("image"),
            "first_seen": min(firsts) if firsts else None,
            "last_seen": max(lasts) if lasts else None,
            # A hash MATCH is strong (same file); a hash SPLIT is weak (same pack, different photo
            # hashes apart). So a dHash split is `divergent_unconfirmed` — a candidate for CLIP or a
            # human, never presented as an established packaging difference.
            "verdict": ("aligned" if n_clusters == 1
                        else ("divergent" if tier == "clip" else "divergent_unconfirmed")),
            # THE WITHHELD VERDICT. Everything above is counted; this is the one field that would be
            # a claim about somebody's business, so it stays None until precision is measured.
            "stale_candidate": _stale(rank, n_clusters, len(csrc), total_sources, precision, tier),
            "withheld_reason": ("hash tier: a dHash split is not evidence of a packaging change "
                                "(same pack, different photo hashes apart) — needs CLIP or a human"
                                if tier != "clip" else None) if precision else
                               "staleness withheld: clustering precision not measured "
                               "(run backtest() against a labelled set)",
            "threshold": threshold if tier == "clip" else HASH_MAX_BITS,
            "method": "clip-cosine-greedy" if tier == "clip" else "dhash-hamming-greedy",
            "precision_measured": bool(precision), "computed_at": now,
        })
    return rows


def _stale(rank, n_clusters, n_src, total_src, precision, tier="clip"):
    """The staleness call — None unless precision has been MEASURED, and then only on a real breadth
    asymmetry. A minority look on a minority of chains is the candidate; a near-even split is a
    genuine two-pack situation (regional SKUs, transition periods) and must not be called stale."""
    if not precision or n_clusters is None or n_clusters < 2:
        return None
    if tier != "clip":
        return None            # a hash split is unconfirmed by construction — never a stale verdict
    if rank == 0:
        return False
    return (n_src / total_src) <= 0.34


def load_precision():
    """Measured precision, or None. Absent by design — nothing ships a staleness verdict until a
    human has labelled a set and this file exists."""
    try:
        with open(PRECISION_FILE, encoding="utf-8") as f:
            p = json.load(f)
        return p if p.get("precision") is not None else None
    except Exception:
        return None


def backtest(labelled, threshold=DEFAULT_THRESHOLD):
    """Measure clustering precision against a labelled set and return the score.

    `labelled` = [{upc, images:[{source,image,vec}], same_pack: bool}] — pairs a human has judged.
    This is the ONLY thing that turns the staleness verdict on, and it is deliberately a separate,
    manual act: writing the precision file is a person saying "I checked."""
    tp = fp = tn = fn = 0
    for case in labelled:
        imgs = [i for i in case["images"] if i.get("vec")]
        if len(imgs) < 2:
            continue
        assign = cluster([(i["image"], i["vec"]) for i in imgs], threshold=threshold)
        together = len(set(assign.values())) == 1
        if case["same_pack"]:
            tp, fn = (tp + 1, fn) if together else (tp, fn + 1)
        else:
            fp, tn = (fp + 1, tn) if together else (fp, tn + 1)
    split_calls = fn + tn                       # cases we called "different pack"
    precision = round(tn / split_calls, 3) if split_calls else None
    return {"precision": precision, "n": len(labelled), "threshold": threshold,
            "true_split": tn, "false_split": fn, "kept_together": tp + fp,
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ── the build ─────────────────────────────────────────────────────────────────────────────────────
def build(limit=None, threshold=DEFAULT_THRESHOLD, land=True, log=print):
    """Read `img_vec`, group by UPC, land `asset_divergence`."""
    import warehouse
    seen, tiers = {}, []
    ident = _identity_map(log=log)
    identity = "resolved_id" if ident else "upc"
    if not ident:
        log("asset_divergence: master identity unavailable — falling back to raw UPC, which cannot "
            "see the retailer catalogs that have no upc column")

    def _key(row):
        if ident:
            return ident.get((row["source"], str(row["sku"])))
        u = (row.get("upc") or "").strip().lstrip("0")
        return u if len(u) >= 8 else None

    # CHEAP TIER FIRST — it is the one that actually has coverage. img_vec needs torch, which the
    # image does not ship, so a build that only knew about CLIP would report degraded forever.
    try:
        for r in warehouse.query("img_hash", "SELECT source, sku, upc, image, dhash FROM t "
                                             "WHERE dhash IS NOT NULL"):
            k = _key(r)
            if not k:
                continue
            seen.setdefault(k, []).append({
                "source": r["source"], "image": r["image"], "dhash": r["dhash"],
                "vec": None, "first_seen": None, "last_seen": None})
        tiers.append("dhash")
    except Exception as e:
        log("asset_divergence: img_hash unavailable (%s)" % str(e)[:80])

    # Upgrade with CLIP wherever it exists, matched onto the same (source, image).
    try:
        import img_embed
        idx = {}
        for imgs in seen.values():
            for i in imgs:
                idx[(i["source"], i["image"])] = i
        for r in warehouse.query("img_vec", "SELECT source, sku, upc, image, vec FROM t"):
            v = list(img_embed.unpack(r["vec"]))
            hit = idx.get((r["source"], r["image"]))
            if hit:
                hit["vec"] = v
                continue
            k = _key(r)
            if k:
                seen.setdefault(k, []).append({
                    "source": r["source"], "image": r["image"], "dhash": None,
                    "vec": v, "first_seen": None, "last_seen": None})
        tiers.append("clip")
    except Exception as e:
        log("asset_divergence: img_vec unavailable (%s) — running on the hash tier only" % str(e)[:80])

    if not seen:
        log("asset_divergence: neither img_hash nor img_vec has data — run `img_hash build --all` first")
        return [], {"status": "degraded", "reason": "no image tier populated", "tiers": tiers}
    _attach_dates(seen, log=log)

    precision = load_precision()
    if not precision:
        log("asset_divergence: NO measured precision — divergence lands, staleness is WITHHELD")

    items = list(seen.items())
    if limit:
        items = items[:limit]
    out = []
    for upc, imgs in items:
        out += analyze_item(upc, imgs, threshold=threshold, precision=precision,
                            identity=identity)

    div = {r["item_id"] for r in out if r["verdict"] == "divergent"}
    thin = {r["item_id"] for r in out if r["verdict"] == "insufficient_data"}
    aligned = {r["item_id"] for r in out if r["verdict"] == "aligned"}
    unconf = {r["item_id"] for r in out if r["verdict"] == "divergent_unconfirmed"}
    cov = {"tiers_available": tiers, "identity": identity,
           "by_method": {m: sum(1 for r in out if r["method"] == m)
                         for m in sorted({r["method"] for r in out})},
           "divergent_unconfirmed": len(unconf),
           "items": len(items), "aligned": len(aligned), "divergent": len(div),
           "insufficient_data": len(thin), "rows": len(out),
           "precision_measured": bool(precision),
           "stale_candidates": sum(1 for r in out if r["stale_candidate"])}
    log("asset_divergence: %d items — %d aligned, %d DIVERGENT, %d divergent-unconfirmed (hash tier), "
        "%d too thin to judge%s"
        % (len(items), len(aligned), len(div), len(unconf), len(thin),
           "" if precision else " | staleness withheld (no measured precision)"))

    if land and out:
        try:
            warehouse.write_accumulate(TABLE, out,
                                       key=lambda r: "%s|%s" % (r["item_id"], r["cluster_id"]),
                                       fields=FIELDS, coverage=False)
            log("landed %s: %d rows" % (TABLE, len(out)))
        except Exception as e:
            log("%s land skipped: %s" % (TABLE, str(e)[:100]))

    print("HOODIE_RESULT " + json.dumps(dict({"status": "success", "items_done": len(out),
                                              "items_total": len(out)}, **cov)))
    return out, cov


def _identity_map(log=print):
    """{(source, product_id): resolved_id} from the master. None when it isn't readable.

    `xwalk_source_sku` maps a SOURCE's own sku to the master's `item_key`; `dim_sku.resolved_id`
    then collapses the md5 key's over-splits. COALESCE keeps an item that has no resolved_id on its
    item_key rather than dropping it — a missing collapse should cost overlap, never the row.

    The crosswalk names sources SHORT (`binnys`, `total-wine`) while the image tables are named
    `binnys_products` — `_SRC_ALIAS` bridges that, and an unmapped source simply contributes nothing
    rather than silently keying on a name that matches nothing."""
    import warehouse
    try:
        con = warehouse.connect()
        con.execute("SET memory_limit='2GB'")
        con.execute("SET preserve_insertion_order=false")
        xw = warehouse.uri("xwalk_source_sku").strip("'")
        ds = warehouse.uri("dim_sku").strip("'")
        rows = con.execute(
            "SELECT x.source, CAST(x.product_id AS VARCHAR) pid, "
            "COALESCE(d.resolved_id, x.item_key) rid FROM read_parquet('%s') x "
            "LEFT JOIN read_parquet('%s') d ON d.item_key = x.item_key "
            "WHERE x.product_id IS NOT NULL" % (xw, ds)).fetchall()
    except Exception as e:
        log("asset_divergence: master identity unreadable (%s)" % str(e).split("\n")[0][:90])
        return None
    out = {}
    for src, pid, rid in rows:
        if rid:
            out[(_SRC_ALIAS.get(src, src), pid)] = rid
            out[(src, pid)] = rid                    # accept either naming on the image side
    log("asset_divergence: master identity map: %d (source, sku) -> resolved item" % len(out))
    return out or None


def _attach_dates(seen, log=print):
    """First/last observation per (source, image) from retail_observations. Best-effort: without it
    the divergence still lands, the recency evidence just isn't there — and the row shows NULL dates
    rather than implying we checked and found nothing."""
    try:
        import warehouse
        obs = warehouse.query(
            "retail_observations",
            "SELECT source, image, MIN(observed_at) AS first_seen, MAX(observed_at) AS last_seen "
            "FROM t WHERE image IS NOT NULL AND image<>'' GROUP BY source, image")
    except Exception as e:
        log("asset_divergence: no observation dates (%s) — recency evidence absent" % str(e)[:70])
        return
    idx = {(o["source"], o["image"]): o for o in obs or []}
    for imgs in seen.values():
        for i in imgs:
            o = idx.get((i["source"], i["image"]))
            if o:
                i["first_seen"], i["last_seen"] = o.get("first_seen"), o.get("last_seen")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Detect cross-retailer product-image divergence.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    rows, cov = build(limit=a.limit, threshold=a.threshold, land=not a.no_land)
    print(json.dumps(cov, indent=2))


if __name__ == "__main__":
    main()
