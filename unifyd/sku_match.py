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


def _gs1_check(body):
    """GS1 mod-10 check digit for a GTIN body (all digits except the check)."""
    tot = 0
    for i, ch in enumerate(reversed(body)):
        tot += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - tot % 10) % 10


def norm_upc(u):
    """Canonicalize a UPC/EAN/GTIN to its GS1 SIGNIFICANT CORE (the manufacturer+item body, leading zeros and the
    check digit removed) so the same barcode matches across the padding/encoding noise the sources introduce:
    UPC-A vs EAN-13 vs GTIN-14, a stray leading zero, or a dropped/mis-shifted check digit. The move that matters:
    if the digits already end in a VALID check digit we strip it; otherwise we treat the digits AS the core — so
    `085000019498` (valid, core 08500001949) and `008500001949` (that core, leading-zero-padded, check dropped)
    both reduce to `8500001949` and finally match. Returns None for junk (empty, all-same, placeholder, bad length)."""
    d = re.sub(r"\D", "", str(u or ""))
    if not (8 <= len(d) <= 14) or len(set(d)) == 1:      # too short/long or all-same-digit (0000…, 1111…)
        return None
    if d.zfill(12) in _PLACEHOLDER or d.lstrip("0") in {p.lstrip("0") for p in _PLACEHOLDER}:
        return None                                      # sequential/placeholder barcode (123456789012 …)
    d = d.lstrip("0") or "0"
    if len(d) >= 9 and _gs1_check(d[:-1]) == int(d[-1]):  # ends in a valid check digit -> strip it to the core
        core = d[:-1]
    else:                                                 # no valid check -> the digits already ARE the core
        core = d
    core = core.lstrip("0") or "0"
    return None if (core in _PLACEHOLDER or len(core) < 5) else core


def _item_sig(r):
    # product_name is already canonical here (precleanse + canonicalize ran); size+container complete the item
    return (r.get("brand"), r.get("product_name"), r.get("size_ml"), r.get("container"))


def propagate_upcs(staged, log=print):
    """Fill missing UPCs across single-UPC item clusters; leave multi-UPC clusters as distinct SKUs. Mutates
    `upc` in place to the normalized value and returns (staged, stats)."""
    for r in staged:
        # GTIN is just a barcode in another encoding (gtin14 = the UPC, zero-padded) — norm_upc reduces both to
        # the same core, so fall back to gtin when upc is absent. Activates Kroger gtin14 / ABC gtin as match keys.
        r["upc"] = norm_upc(r.get("upc")) or norm_upc(r.get("gtin"))

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
