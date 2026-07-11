"""cola_tiering.py — TTB COLA product-identity tiering + promotion.

COLA is a FILING system, not a market-presence system: suppliers file many COLAs per eventual
SKU (label iteration, decoy filings) and many never reach market. So we seed candidate
identities from COLA (Tier 0, quarantined), collapse the filing noise into FILING CLUSTERS,
and only promote a cluster to CONFIRMED (Tier 1) when an INDEPENDENT market-presence source
(ABC FWS, chain scrapes) corroborates it — reusing the UPC engine's cross-source matching.

Agreed defaults: 90-day filing-cluster window · CONSERVATIVE promotion (exact brand + size
auto-confirms; anything weaker -> review, never silent) · 180-day corroboration decay.

Pure stdlib (+ sibling upc.py). `python cola_tiering.py` runs the self-test.
"""
import os, re, sys, datetime, hashlib
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upc

DROP_STATUS = ("expired", "surrendered", "revoked")
PROMOTE_THRESHOLD = 0.85          # conservative: only exact brand + size (0.9) clears this
WINDOW_DAYS = 90
DECAY_DAYS = 180
ABBREV = {"rsv": "reserve", "res": "reserve", "orig": "original", "ltd": "limited",
          "sel": "select", "spcl": "special", "brl": "barrel", "brrl": "barrel",
          "cab": "cabernet", "sauv": "sauvignon", "chard": "chardonnay",
          "whisky": "whiskey", "btl": "bottle", "pf": "proof", "vint": "vintage"}


# ---------- normalization ----------
def _norm(s):
    n = re.sub(r"[^a-z0-9 ]+", " ", str(s or "").lower())
    return " ".join(ABBREV.get(w, w) for w in n.split()).strip()


def _size_ml(s):
    """Net contents / bottle capacity -> milliliters (str int) when parseable, else ''."""
    t = str(s or "").lower().replace(" ", "")
    m = re.search(r"([\d.]+)(ml|l|liter|litre|oz|floz|gal)", t)
    if not m:
        return ""
    try:
        v = float(m.group(1))
    except Exception:
        return ""
    u = m.group(2)
    if u in ("l", "liter", "litre"):
        return str(int(round(v * 1000)))
    if u in ("oz", "floz"):
        return str(int(round(v * 29.5735)))
    if u == "gal":
        return str(int(round(v * 3785.41)))
    return str(int(round(v)))     # already ml


def _day(s):
    """MM/DD/YYYY or YYYY-MM-DD -> proleptic-Gregorian ordinal (int), or None."""
    s = str(s or "").strip()
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime.date(y, mo, d).toordinal()
    except Exception:
        return None


# ---------- field access (enriched COLA has varied key names) ----------
def _f(r, *keys):
    for k in keys:
        v = r.get(k)
        if v not in (None, ""):
            return v
    return ""

def _brand(r):  return _f(r, "Brand Name", "brand", "Brand")
def _fanc(r):   return _f(r, "Fanciful Name", "fanciful")
def _cls(r):    return _f(r, "Class/Type Code", "Class/Type", "class_type")
def _size(r):   return _f(r, "Net Contents", "Total Bottle Capacity", "net_contents", "size")
def _holder(r): return _f(r, "Vendor Code", "Permit Number", "permit", "vendor_code")
def _date(r):   return _f(r, "Completed Date", "Approval Date", "completed_date")
def _status(r): return _f(r, "Status", "status")
def _ttbid(r):  return _f(r, "TTB ID", "ttbid", "ttb_id")


def kept(r):
    """Cheap pre-filter: drop expired / surrendered / revoked before quarantine."""
    st = _norm(_status(r))
    return not any(bad in st for bad in DROP_STATUS)


# ---------- filing clusters ----------
def cluster_key(r):
    return (_norm(_brand(r)), _norm(_fanc(r)), _norm(_cls(r)), _size_ml(_size(r)), _norm(_holder(r)))


def _mk_cluster(key, members):
    brand_n = key[0]
    days = [d for d in (_day(_date(r)) for r in members) if d is not None]
    cid = "K" + hashlib.sha1(("|".join(key) + "|" + (str(min(days)) if days else "")).encode()).hexdigest()[:11]
    return {"cluster_id": cid, "brand": _brand(members[0]), "brand_n": brand_n,
            "fanciful": _fanc(members[0]), "class_type": _cls(members[0]), "size_ml": key[3],
            "supplier": key[4] or "unknown", "members": [_ttbid(r) for r in members],
            "count": len(members), "first_day": (min(days) if days else None),
            "last_day": (max(days) if days else None), "tier": 0}


def build_clusters(rows, window_days=WINDOW_DAYS):
    """Group label-iteration / decoy filings that likely = one SKU: same brand+fanciful+class+
    size+permit-holder, filed within `window_days` of each other (a gap splits the cluster)."""
    groups = defaultdict(list)
    for r in rows:
        if kept(r):
            groups[cluster_key(r)].append(r)
    clusters = []
    for key, rs in groups.items():
        rs.sort(key=lambda r: (_day(_date(r)) or 0))
        cur, last = [], None
        for r in rs:
            dd = _day(_date(r))
            if cur and last is not None and dd is not None and (dd - last) > window_days:
                clusters.append(_mk_cluster(key, cur)); cur = []
            cur.append(r)
            if dd is not None:
                last = dd
        if cur:
            clusters.append(_mk_cluster(key, cur))
    return clusters


# ---------- market-presence corroboration (reuses upc-engine matching style) ----------
def build_market_index(market_rows):
    """market_rows: {source, brand, name, size, upc}. -> norm_brand -> [listings]."""
    idx = defaultdict(list)
    for m in market_rows:
        b = _norm(m.get("brand") or m.get("name"))
        if not b:
            continue
        idx[b].append({"source": m.get("source", "?"), "size": _size_ml(m.get("size")),
                       "upc": m.get("upc") or "", "name": _norm(m.get("name"))})
    return dict(idx)


def corroborate(cluster, midx):
    """Best independent market match for a cluster. Exact brand first, then token-containment.
    Confidence: exact brand + size 0.9 / exact brand no size 0.65 / partial + size 0.7 / partial 0.45."""
    b, sz = cluster["brand_n"], cluster["size_ml"]
    cands, kind = midx.get(b), "exact"
    if not cands:
        bt = set(b.split())
        for mb, lst in midx.items():
            mt = set(mb.split())
            if bt and (bt <= mt or mt <= bt):
                cands, kind = lst, "partial"; break
    if not cands:
        return None
    size_ok = [c for c in cands if sz and c["size"] == sz]
    pick = (size_ok or cands)[0]
    conf = (0.9 if size_ok else 0.65) if kind == "exact" else (0.7 if size_ok else 0.45)
    return {"source": pick["source"], "upc": pick["upc"], "confidence": conf,
            "match": kind, "size_matched": bool(size_ok), "name": pick.get("name", "")}


# ---------- top-level tiering ----------
def tier(cola_rows, market_rows, now_day=None, window_days=WINDOW_DAYS,
         decay_days=DECAY_DAYS, threshold=PROMOTE_THRESHOLD):
    """Seed Tier 0 from COLA, cluster, corroborate vs market presence, promote/queue. Returns
    {clusters, tier0, tier1, review, events, summary, innovation}. `now_day` (ordinal) stamps
    corroboration events for later decay."""
    clusters = build_clusters(cola_rows, window_days)
    midx = build_market_index(market_rows)
    tier0, tier1, review, events = [], [], [], []
    for cl in clusters:
        cor = corroborate(cl, midx)
        if cor and cor["confidence"] >= threshold:
            cl["tier"] = 1; cl["corroboration"] = cor
            if cor["upc"] and upc.is_real(cor["upc"]):
                cl["upc"] = cor["upc"]                       # promotion also HEALS the missing UPC
            events.append({"cluster_id": cl["cluster_id"], "source": cor["source"],
                           "confidence": cor["confidence"], "upc": cor.get("upc", ""),
                           "matched_day": now_day})
            tier1.append(cl)
        elif cor:
            cl["tier"] = 0; cl["candidate"] = cor
            cl["review"] = "low-confidence match %.2f (%s)" % (cor["confidence"], cor["match"])
            review.append(cl); tier0.append(cl)
        else:
            cl["tier"] = 0; tier0.append(cl)              # no market signal -> stays quarantined
    return {"clusters": clusters, "tier0": tier0, "tier1": tier1, "review": review,
            "events": events, "summary": summary(tier0, tier1, review),
            "innovation": innovation_pipeline(tier0)}


def summary(tier0, tier1, review):
    n = len(tier0) + len(tier1)
    return {"clusters": n, "tier0_quarantine": len(tier0), "tier1_confirmed": len(tier1),
            "review": len(review), "promotion_rate": round(len(tier1) / max(1, n), 3)}


def innovation_pipeline(tier0):
    """Tier-0 candidates with NO market signal, grouped by supplier — 'filed, unconfirmed'
    (a legitimate innovation-radar signal, just not market-live)."""
    by = defaultdict(list)
    for cl in tier0:
        if cl.get("review"):
            continue                                        # weak-match ones are in the review queue
        by[cl["supplier"]].append(cl)
    return sorted([{"supplier": s, "candidates": len(v),
                    "brands": sorted({c["brand"] for c in v if c["brand"]})[:8]}
                   for s, v in by.items()], key=lambda x: -x["candidates"])


def decay(events, now_day, decay_days=DECAY_DAYS):
    """Cluster_ids whose most-recent corroboration is older than `decay_days` -> flag for review
    (don't silently keep 'confirmed' forever). Runs across persisted events, not a single pass."""
    latest = {}
    for e in events:
        d = e.get("matched_day")
        if d is not None:
            latest[e["cluster_id"]] = max(latest.get(e["cluster_id"], -1), d)
    return [cid for cid, d in latest.items() if (now_day - d) > decay_days]


def _selftest():
    D = datetime.date
    o = lambda y, m, d: D(y, m, d).toordinal()
    cola = [
        # 3 label iterations of one SKU within 90 days -> ONE cluster
        {"TTB ID": "1", "Brand Name": "Six and Twenty", "Fanciful Name": "Bourbon", "Class/Type Code": "BOURBON WHISKY", "Net Contents": "750ML", "Vendor Code": "24008", "Completed Date": "01/05/2025", "Status": "APPROVED"},
        {"TTB ID": "2", "Brand Name": "SIX AND TWENTY", "Fanciful Name": "bourbon", "Class/Type Code": "BOURBON WHISKY", "Net Contents": "750 ML", "Vendor Code": "24008", "Completed Date": "02/10/2025", "Status": "APPROVED"},
        {"TTB ID": "3", "Brand Name": "Six and Twenty", "Fanciful Name": "Bourbon", "Class/Type Code": "BOURBON WHISKY", "Net Contents": "0.75L", "Vendor Code": "24008", "Completed Date": "03/01/2025", "Status": "APPROVED"},
        # a surrendered filing -> pre-filtered out
        {"TTB ID": "4", "Brand Name": "Dead Label", "Class/Type Code": "VODKA", "Net Contents": "1L", "Vendor Code": "99", "Completed Date": "01/01/2025", "Status": "SURRENDERED"},
        # a never-marketed candidate (innovation pipeline)
        {"TTB ID": "5", "Brand Name": "Ghost Cellars", "Fanciful Name": "Secret", "Class/Type Code": "RED WINE", "Net Contents": "750ML", "Vendor Code": "77", "Completed Date": "05/01/2025", "Status": "APPROVED"},
        # a weak-match candidate -> review
        {"TTB ID": "6", "Brand Name": "River Oak", "Class/Type Code": "RED WINE", "Net Contents": "750ML", "Vendor Code": "55", "Completed Date": "04/01/2025", "Status": "APPROVED"},
    ]
    market = [
        {"source": "abc_fws", "brand": "Six and Twenty", "name": "Six and Twenty Bourbon", "size": "750ml", "upc": "036000291452"},
        {"source": "binnys", "brand": "River Oak Red", "name": "River Oak Red Blend", "size": "750ml", "upc": "934567890128"},
    ]
    res = tier(cola, market, now_day=o(2025, 6, 1))
    s = res["summary"]
    assert s["clusters"] == 3, res["summary"]                       # SixAndTwenty, Ghost, River Oak (Dead Label filtered)
    assert s["tier1_confirmed"] == 1, s                             # Six and Twenty exact+size -> promoted
    assert s["review"] == 1, s                                      # River Oak partial -> review
    assert s["tier0_quarantine"] == 2, s                            # Ghost + River Oak
    six = [c for c in res["tier1"]][0]
    assert six["count"] == 3, six                                   # 3 filings collapsed to 1 cluster
    assert six.get("upc") == "036000291452", six                    # promotion healed the UPC
    innov = res["innovation"]
    assert any(p["supplier"] == "77" and "Ghost Cellars" in p["brands"] for p in innov), innov
    # decay: a year-old corroboration event -> stale
    old = [{"cluster_id": "Kold", "matched_day": o(2024, 1, 1)}]
    assert decay(old, o(2025, 6, 1)) == ["Kold"], decay(old, o(2025, 6, 1))
    print("cola_tiering self-test: OK —", s, "| innovation suppliers:", len(innov))


if __name__ == "__main__":
    _selftest()
