"""upc_master.py — cross-source UPC confirmation + heal (Layer 3 of the UPC engine).

After upc.py (deterministic QC) and upc_crosswalk.py (owned prefix->owner crosswalk), this
unifies REAL UPCs across the OWNED sources — COLA labels, ABC FWS, chain scrapes, the hemp feed
— into a confirmed-UPC master, and HEALS a COLA placeholder/missing UPC by borrowing a real UPC
seen for the same brand in a retailer source.

  confirm — a UPC seen in >=2 independent sources is high-confidence (nobody typos the same
            wrong barcode twice across a filing AND a shelf).
  heal    — COLA gives brand + (often) a placeholder UPC; a retailer gives brand + the REAL
            UPC. Match on brand -> propose the real UPC, with provenance + confidence.

Record shape: {"source","upc","brand","owner"(opt)}. Pure stdlib; `python upc_master.py` self-tests.
"""
import os, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upc


def norm_brand(s):
    n = re.sub(r"[^A-Za-z0-9 ]+", " ", str(s or "")).upper()
    return re.sub(r"\s+", " ", n).strip()


def ingest(records):
    """records: iterable of {source, upc, brand, owner?}. Counts REAL upcs only.
    Returns {upc: {sources:set, brands:Counter, owners:Counter, n:int}}."""
    master = {}
    for r in records:
        code = upc.normalize(r.get("upc"))
        if not upc.is_real(code):
            continue
        m = master.setdefault(code, {"sources": set(), "brands": Counter(), "owners": Counter(), "n": 0})
        m["sources"].add(r.get("source", "?"))
        if r.get("brand"):
            m["brands"][norm_brand(r["brand"])] += 1
        if r.get("owner"):
            m["owners"][str(r["owner"]).strip().upper()[:40]] += 1
        m["n"] += 1
    return master


def confirmed(master, min_sources=2):
    return {u: m for u, m in master.items() if len(m["sources"]) >= min_sources}


def brand_index(master):
    """norm_brand -> [(upc, n_sources, n_hits)] sorted best-first (multi-source, then frequency)."""
    idx = defaultdict(list)
    for u, m in master.items():
        for b in m["brands"]:
            idx[b].append((u, len(m["sources"]), m["n"]))
    for b in idx:
        idx[b].sort(key=lambda t: (-t[1], -t[2]))
    return dict(idx)


def heal(brand, bidx):
    """Best real UPC for a brand from the cross-source index. Exact brand match first; else fall
    back to token-containment (COLA 'River Oak' vs retailer 'River Oak Red') at reduced confidence.
    Returns {upc, sources, confidence, match, provenance} or None. Confidence: exact multi→0.9 /
    single→0.6; containment multi→0.75 / single→0.5."""
    nb = norm_brand(brand)
    if not nb:
        return None
    cands, match = bidx.get(nb), "exact"
    if not cands:
        toks, best = set(nb.split()), None
        for b, lst in bidx.items():
            bt = set(b.split())
            if toks and (toks <= bt or bt <= toks):        # one brand's tokens contain the other's
                if best is None or lst[0][1] > best[1]:     # prefer most-sourced
                    best = (lst[0][0], lst[0][1], lst[0][2])
        if best:
            cands, match = [best], "partial"
    if not cands:
        return None
    u, nsrc, nhit = cands[0]
    base = (0.9 if nsrc >= 2 else 0.6) if match == "exact" else (0.75 if nsrc >= 2 else 0.5)
    return {"upc": u, "sources": nsrc, "confidence": base, "match": match,
            "provenance": ["heal:%s-brand-match(%s src, %s hits)" % (match, nsrc, nhit)]}


def annotate_cola(cola_rows, bidx, brand_col="Brand Name"):
    """For each enriched COLA row lacking a real UPC, propose a heal from the cross-source index.
    Yields the row + upc_heal / upc_heal_conf / upc_heal_src (empty when the row already has a
    real UPC or no brand match exists)."""
    for r in cola_rows:
        have = upc.is_real(r.get("UPC") or r.get("upc_raw") or "")
        h = None if have else heal(r.get(brand_col, ""), bidx)
        r = dict(r)
        r["upc_heal"] = "" if (have or not h) else h["upc"]
        r["upc_heal_conf"] = "" if (have or not h) else ("%.2f" % h["confidence"])
        r["upc_heal_src"] = "" if (have or not h) else str(h["sources"])
        yield r


def _selftest():
    recs = [
        {"source": "cola", "upc": "036000291452", "brand": "Six and Twenty", "owner": "Six and Twenty Distillery"},
        {"source": "abc_fws", "upc": "036000291452", "brand": "SIX AND TWENTY BOURBON"},   # confirms across sources
        {"source": "abc_fws", "upc": "934567890128", "brand": "River Oak Red"},            # retailer-only real UPC
    ]
    m = ingest(recs)
    assert "036000291452" in m and len(m["036000291452"]["sources"]) == 2, m
    assert set(confirmed(m).keys()) == {"036000291452"}, confirmed(m)
    bidx = brand_index(m)
    # heal a COLA placeholder for River Oak from the retailer-only real UPC
    cola = [{"Brand Name": "River Oak", "UPC": "", "upc_raw": "000000000000"}]
    out = list(annotate_cola(cola, bidx))[0]
    assert out["upc_heal"] == "934567890128" and out["upc_heal_conf"] == "0.50", out  # partial, single-source
    # a row that already has a real UPC is NOT healed
    cola2 = [{"Brand Name": "Six and Twenty", "UPC": "036000291452"}]
    assert list(annotate_cola(cola2, bidx))[0]["upc_heal"] == ""
    print("upc_master.py self-test: OK  (confirmed=%d, brands=%d)" % (len(confirmed(m)), len(bidx)))


if __name__ == "__main__":
    _selftest()
