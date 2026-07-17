"""identity_resolve.py — union-find over category-discriminator clusters to recover the AMBIGUOUS over-splits.

The hard category key (category_tree.key) is deliberately conservative: it omits a MISSING discriminator, so a
less-complete listing ("Macallan Single Malt", no age stated) keys APART from the fully-specified one ("Macallan
12 Single Malt"). Measured, that's a ~13% over-split. This pass reunites those WITHOUT ever merging genuinely
different items: inside a (family, brand) block it pairwise-runs category_tree.compare and unions two keys only
when they are compatible-but-AMBIGUOUS and a REDUNDANT signal confirms them the same — a shared UPC
(authoritative) or an agreeing size-normalized price band ([[price-clustering-signal]]). 'different' pairs are
never unioned; 'same' pairs always are. This is the redundancy Chris asked for: the category tree PROPOSES a
merge, price/UPC CONFIRM it — more than one way to skin the cat, and no single rule decides identity alone.
"""
import category_tree
import price_signal


class DSU:
    """Union-find (path-halving)."""
    def __init__(self, keys):
        self.p = {k: k for k in keys}

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def corroborated(a, b, tol=0.35):
    """A REDUNDANT confirmation that two ambiguous-compatible keys are the same item. Returns the winning
    signal name ('upc' | 'price') or None. UPC is authoritative; price is size-normalized agreement."""
    if a.get("upcs") and b.get("upcs") and (a["upcs"] & b["upcs"]):
        return "upc"
    ma = price_signal.cluster_stats(a.get("unit_prices"))
    mb = price_signal.cluster_stats(b.get("unit_prices"))
    if ma and mb and price_signal.agrees(ma["median"], mb["median"], tol):
        return "price"
    return None


def resolve_block(entries, tol=0.35):
    """entries: {category_key: {vec, sources:set, unit_prices:list, upcs:set, n, name}} for ONE (family,brand)
    block. Unions ambiguous-compatible keys that a redundant signal confirms. Returns
    (roots {key: root}, merges [(a, b, reason)])."""
    ks = list(entries)
    dsu = DSU(ks)
    merges = []
    for i in range(len(ks)):
        ei = entries[ks[i]]
        for j in range(i + 1, len(ks)):
            ej = entries[ks[j]]
            verdict, _ = category_tree.compare(ei["vec"], ej["vec"])
            if verdict == "same":
                dsu.union(ks[i], ks[j]); merges.append((ks[i], ks[j], "same"))
            elif verdict == "ambiguous":
                why = corroborated(ei, ej, tol)
                if why:
                    dsu.union(ks[i], ks[j]); merges.append((ks[i], ks[j], why))
    return {k: dsu.find(k) for k in ks}, merges


def _selftest():
    def entry(name, brand, prices=(), upcs=(), **kw):
        vec = category_tree.vector(name, brand=brand, **kw)
        return {"vec": vec, "sources": {"s"}, "unit_prices": list(prices), "upcs": set(upcs),
                "n": 1, "name": name}
    def K(name, brand, **kw):
        return category_tree.key(name, brand=brand, **kw)[0]

    # over-split: "Macallan 12" vs "Macallan" (no age) → ambiguous; price agreement CONFIRMS the merge
    k12 = K("The Macallan 12 Year Single Malt Scotch 750ml", "Macallan")
    kna = K("Macallan Single Malt Scotch 750ml", "Macallan")
    block = {k12: entry("The Macallan 12 Year Single Malt Scotch 750ml", "Macallan", prices=[62.0]),
             kna: entry("Macallan Single Malt Scotch 750ml", "Macallan", prices=[65.0])}   # within band
    roots, merges = resolve_block(block)
    assert roots[k12] == roots[kna] and merges and merges[0][2] == "price", (roots, merges)

    # same ambiguity but NO corroboration (prices far apart, no UPC) → stays SPLIT (conservative)
    block2 = {k12: entry("The Macallan 12 Year Single Malt Scotch 750ml", "Macallan", prices=[62.0]),
              kna: entry("Macallan Single Malt Scotch 750ml", "Macallan", prices=[300.0])}
    roots2, merges2 = resolve_block(block2)
    assert roots2[k12] != roots2[kna] and not merges2

    # genuinely different (12 vs 18) → NEVER unioned even with agreeing price
    k18 = K("The Macallan 18 Year Single Malt Scotch 750ml", "Macallan")
    block3 = {k12: entry("The Macallan 12 Year Single Malt Scotch 750ml", "Macallan", prices=[62.0]),
              k18: entry("The Macallan 18 Year Single Malt Scotch 750ml", "Macallan", prices=[62.0])}
    roots3, merges3 = resolve_block(block3)
    assert roots3[k12] != roots3[k18] and not merges3

    # UPC confirms the ambiguous merge even with no price
    block4 = {k12: entry("The Macallan 12 Year Single Malt Scotch 750ml", "Macallan", upcs=["12345678901"]),
              kna: entry("Macallan Single Malt Scotch 750ml", "Macallan", upcs=["12345678901"])}
    roots4, merges4 = resolve_block(block4)
    assert roots4[k12] == roots4[kna] and merges4[0][2] == "upc"
    print("identity_resolve self-test OK")


if __name__ == "__main__":
    _selftest()
