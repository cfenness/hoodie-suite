"""sku_match.py — SKU-level identity resolution, UPC-first. The unit of value is the SKU (the fully-specified
sellable unit), because SKU is the only grain where price/inventory/distribution are comparable across sources.
A product-level match is commercially useless; this resolves SKUs.

Two things a good SKU key needs that the string hierarchy alone doesn't give:

  1. UPC-FIRST identity — a UPC is the SKU's GLOBAL identity. Two rows with the same UPC are the same SKU no
     matter how mangled their brand/name strings are. So UPC, not a string key, is the primary sku_id where present.
  2. UPC PROPAGATION — the multiplier. Our UPC coverage is thin and concentrated in a few sources (Kroger/Maine/
     BC/off-premise ~80-100%; Binny's/Oregon/control-states 0%). But you don't need every source to carry a UPC —
     you need ONE source per item to, then push it across the matched cluster. So: cluster by item signature
     (brand + canonical product + size + container), and where a cluster has exactly ONE distinct UPC, propagate
     it to every member that lacks one. A cluster with MULTIPLE distinct UPCs is genuinely multiple SKUs (UPC
     splits an over-merged item into real SKUs) — leave those for size/pack + Claude, never blindly merge.

Run AFTER precleanse + canonicalize (product_name already canonical), BEFORE the hierarchy shred.
"""
import collections
import re

_PLACEHOLDER = {"000000000000", "111111111111", "999999999999", "123456789012"}


def norm_upc(u):
    """Canonicalize a UPC/EAN/GTIN to a comparable 12-digit core (strip non-digits, drop leading zeros from
    EAN-13/GTIN-14 packaging, zero-pad to 12). Returns None for junk (empty, all-same, placeholder, bad length)."""
    d = re.sub(r"\D", "", str(u or ""))
    if not (8 <= len(d) <= 14):
        return None
    if len(set(d)) == 1:                       # all-same-digit (0000…, 1111…)
        return None
    core = d.lstrip("0")
    core = core.zfill(12) if len(core) <= 12 else core
    return None if core in _PLACEHOLDER else core


def _item_sig(r):
    # product_name is already canonical here (precleanse + canonicalize ran); size+container complete the item
    return (r.get("brand"), r.get("product_name"), r.get("size_ml"), r.get("container"))


def propagate_upcs(staged, log=print):
    """Fill missing UPCs across single-UPC item clusters; leave multi-UPC clusters as distinct SKUs. Mutates
    `upc` in place to the normalized value and returns (staged, stats)."""
    for r in staged:
        r["upc"] = norm_upc(r.get("upc"))

    groups = collections.defaultdict(list)
    for r in staged:
        groups[_item_sig(r)].append(r)

    single = multi = filled = 0
    for g in groups.values():
        upcs = {r["upc"] for r in g if r["upc"]}
        if len(upcs) == 1:
            u = next(iter(upcs))
            single += 1
            for r in g:
                if not r["upc"]:
                    r["upc"] = u
                    filled += 1
        elif len(upcs) > 1:
            multi += 1                          # item spans real distinct SKUs — do NOT collapse

    stats = {"single_upc_clusters": single, "multi_upc_clusters": multi, "rows_filled": filled}
    log("[sku] UPC propagation: %d single-UPC item-clusters (filled %d no-UPC rows), "
        "%d multi-UPC clusters kept as distinct SKUs" % (single, filled, multi))
    return staged, stats


def sku_id(r):
    """The SKU's identity: its UPC when present (global, string-proof), else the resolved string key."""
    if r.get("upc"):
        return "upc:" + r["upc"]
    return "key:%s|%s|%s|%s|%s" % (r.get("brand"), r.get("product_name"),
                                   r.get("size_ml"), r.get("container"), r.get("pack"))


def coverage(staged):
    """UPC coverage + SKU corroboration snapshot for reporting."""
    n = len(staged)
    with_upc = sum(1 for r in staged if r.get("upc"))
    by_sku = collections.defaultdict(set)
    for r in staged:
        by_sku[sku_id(r)].add(r.get("_source"))
    upc_skus = [k for k in by_sku if k.startswith("upc:")]
    return {"rows": n, "rows_with_upc": with_upc, "upc_pct": round(100 * with_upc / max(1, n), 1),
            "distinct_skus": len(by_sku), "upc_anchored_skus": len(upc_skus),
            "corroborated_skus": sum(1 for v in by_sku.values() if len(v) >= 2),
            "corroborated_upc_skus": sum(1 for k in upc_skus if len(by_sku[k]) >= 2)}
