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

IDENTITY IS UPC, NOT FUZZY
  Items are grouped by UPC only. Cross-item fuzzy identity is hoodie-canon's cascade
  ([[matching-convergence]]); pulling two different products into one divergence group would
  manufacture a disagreement that does not exist.
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
    "item_upc", "cluster_id", "n_clusters", "cluster_rank", "n_sources_in_cluster",
    "total_sources", "cluster_share", "sources", "representative_image",
    "first_seen", "last_seen", "verdict", "stale_candidate", "withheld_reason",
    "threshold", "method", "precision_measured", "computed_at",
]

# The clustering threshold. Set at the measured same-product-different-photo median from img_embed
# (~0.76), which is deliberately GENEROUS: at 0.76 two photos of one pack stay together, so a split
# means something beyond photography. It is a divergence detector, not a duplicate detector — the
# cost of splitting a benign pair is a false alarm, and that is the expensive direction here.
DEFAULT_THRESHOLD = 0.76

# An item seen by fewer sources than this cannot support a breadth argument: "1 chain disagrees with
# 1 chain" is not evidence of anything. Reported as insufficient_data, never as aligned.
MIN_SOURCES = 3


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


def analyze_item(upc, images, threshold=DEFAULT_THRESHOLD, precision=None):
    """One item's images → divergence rows. PURE.

    `images` = [{source, image, vec, first_seen, last_seen}]. Returns [] when the item cannot
    support a conclusion, rather than a row asserting agreement it has not earned."""
    usable = [i for i in images if i.get("vec")]
    sources = {i["source"] for i in usable}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if not usable:
        return []
    if len(sources) < MIN_SOURCES:
        return [{
            "item_upc": upc, "cluster_id": None, "n_clusters": None, "cluster_rank": None,
            "n_sources_in_cluster": len(sources), "total_sources": len(sources),
            "cluster_share": None, "sources": ",".join(sorted(sources)),
            "representative_image": usable[0].get("image"),
            "first_seen": None, "last_seen": None,
            "verdict": "insufficient_data", "stale_candidate": None,
            "withheld_reason": "only %d source(s); a breadth argument needs %d"
                               % (len(sources), MIN_SOURCES),
            "threshold": threshold, "method": "clip-cosine-greedy",
            "precision_measured": bool(precision), "computed_at": now,
        }]

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
            "item_upc": upc, "cluster_id": int(cid), "n_clusters": n_clusters,
            "cluster_rank": rank, "n_sources_in_cluster": len(csrc), "total_sources": total_sources,
            "cluster_share": round(len(csrc) / total_sources, 3),
            "sources": ",".join(csrc), "representative_image": members[0].get("image"),
            "first_seen": min(firsts) if firsts else None,
            "last_seen": max(lasts) if lasts else None,
            "verdict": "aligned" if n_clusters == 1 else "divergent",
            # THE WITHHELD VERDICT. Everything above is counted; this is the one field that would be
            # a claim about somebody's business, so it stays None until precision is measured.
            "stale_candidate": _stale(rank, n_clusters, len(csrc), total_sources, precision),
            "withheld_reason": None if precision else
                               "staleness withheld: clustering precision not measured "
                               "(run backtest() against a labelled set)",
            "threshold": threshold, "method": "clip-cosine-greedy",
            "precision_measured": bool(precision), "computed_at": now,
        })
    return rows


def _stale(rank, n_clusters, n_src, total_src, precision):
    """The staleness call — None unless precision has been MEASURED, and then only on a real breadth
    asymmetry. A minority look on a minority of chains is the candidate; a near-even split is a
    genuine two-pack situation (regional SKUs, transition periods) and must not be called stale."""
    if not precision or n_clusters is None or n_clusters < 2:
        return None
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
    import img_embed
    try:
        rows = warehouse.query("img_vec",
                               "SELECT source, sku, upc, image, vec FROM t WHERE upc IS NOT NULL AND upc<>''")
    except Exception as e:
        log("asset_divergence: img_vec unreadable (%s) — run `img_embed build` first" % str(e)[:90])
        return [], {"status": "degraded", "reason": "img_vec unavailable"}

    seen = {}
    for r in rows:
        seen.setdefault(r["upc"], []).append({
            "source": r["source"], "image": r["image"],
            "vec": list(img_embed.unpack(r["vec"])), "first_seen": None, "last_seen": None})
    _attach_dates(seen, log=log)

    precision = load_precision()
    if not precision:
        log("asset_divergence: NO measured precision — divergence lands, staleness is WITHHELD")

    items = list(seen.items())
    if limit:
        items = items[:limit]
    out = []
    for upc, imgs in items:
        out += analyze_item(upc, imgs, threshold=threshold, precision=precision)

    div = {r["item_upc"] for r in out if r["verdict"] == "divergent"}
    thin = {r["item_upc"] for r in out if r["verdict"] == "insufficient_data"}
    aligned = {r["item_upc"] for r in out if r["verdict"] == "aligned"}
    cov = {"items": len(items), "aligned": len(aligned), "divergent": len(div),
           "insufficient_data": len(thin), "rows": len(out),
           "precision_measured": bool(precision),
           "stale_candidates": sum(1 for r in out if r["stale_candidate"])}
    log("asset_divergence: %d items — %d aligned, %d DIVERGENT, %d too thin to judge%s"
        % (len(items), len(aligned), len(div), len(thin),
           "" if precision else " | staleness withheld (no measured precision)"))

    if land and out:
        try:
            warehouse.write_accumulate(TABLE, out,
                                       key=lambda r: "%s|%s" % (r["item_upc"], r["cluster_id"]),
                                       fields=FIELDS, coverage=False)
            log("landed %s: %d rows" % (TABLE, len(out)))
        except Exception as e:
            log("%s land skipped: %s" % (TABLE, str(e)[:100]))

    print("HOODIE_RESULT " + json.dumps(dict({"status": "success", "items_done": len(out),
                                              "items_total": len(out)}, **cov)))
    return out, cov


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
