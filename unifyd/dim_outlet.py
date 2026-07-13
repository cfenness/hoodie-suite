"""dim_outlet.py — consolidate the raw src_outlets book (~280k per-source records) into DISTINCT real outlets.

The same physical store shows up under many sources (a Total Wine appears in ca-abc AND ab-inbev; a 7-Eleven in
ca-abc AND the retail observations). We block on the match keys and union the records that are the same outlet:
  1. exact addr_key (cleansed street + zip5)  — same address = same outlet (strong)
  2. exact phone_norm (10-digit)               — same phone = same outlet (strong)
  3. same geo_cell (~110m) + name similarity   — same spot + similar name (fuzzy)

Each cluster becomes one dim_outlet with OUR OWN spine id (hoodie_outlet_id — deterministic hash of the
canonical key), the best name/address/geo, chain, the UNION of sell-flags, and `sources` = who sees it
(corroboration). vpid (from AB InBev / VTInfo) is carried as an ATTRIBUTE for cross-reference — it is NEVER the
spine or the merge anchor (hard rule).

    python dim_outlet.py     # rebuild dim_outlet from src_outlets
"""
import argparse
import hashlib
import os
import sys
from collections import Counter
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

_FIELDS = ["source", "store_id", "store_name", "chain", "is_chain", "f_beer", "f_wine", "f_spirits", "f_hemp",
           "f_cannabis", "f_rtd_spirits", "address", "city", "state", "zip", "lat", "lng", "phone", "name_key",
           "phone_norm", "addr_key", "geo_cell", "county_fips", "hoodie_outlet"]
NAME_THRESH = 0.72          # name similarity to merge two outlets in the same ~110m cell
_CELL_CAP = 250             # skip fuzzy in pathologically dense cells (compare cost + ambiguity)


def _oid(key):
    return "HO" + hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def build(log=print):
    rows = warehouse.query("src_outlets", "SELECT %s FROM t" % ", ".join(_FIELDS))
    n = len(rows)
    log("[dim_outlet] loaded %d src_outlets records" % n)
    parent = list(range(n))

    def find(x):
        r = x
        while parent[r] != r:
            r = parent[r]
        while parent[x] != r:
            parent[x], x = r, parent[x]
        return r

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # ── blocking + union ──────────────────────────────────────────────
    for key in ("addr_key", "phone_norm"):        # exact-key blocks: same key => same outlet
        g = {}
        for i, r in enumerate(rows):
            v = r.get(key)
            if v:
                g.setdefault(v, []).append(i)
        merged = 0
        for members in g.values():
            for j in members[1:]:
                union(members[0], j); merged += 1
        log("[dim_outlet] %s: %d unions" % (key, merged))
    # geo_cell + name similarity (fuzzy) — same ~110m spot, similar name
    cells, fuzz = {}, 0
    for i, r in enumerate(rows):
        if r.get("geo_cell") and r.get("name_key"):
            cells.setdefault(r["geo_cell"], []).append(i)
    for members in cells.values():
        if len(members) < 2 or len(members) > _CELL_CAP:
            continue
        for a in range(len(members)):
            ia = members[a]
            if find(ia) == find(members[0]) and a:      # small prune
                pass
            for b in range(a + 1, len(members)):
                ib = members[b]
                if find(ia) == find(ib):
                    continue
                if SequenceMatcher(None, rows[ia]["name_key"], rows[ib]["name_key"]).ratio() >= NAME_THRESH:
                    union(ia, ib); fuzz += 1
    log("[dim_outlet] geo_cell+name: %d unions" % fuzz)

    # ── canonical dim_outlet per cluster ──────────────────────────────
    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    def _best(recs, k):
        vals = [r[k] for r in recs if r.get(k)]
        return Counter(vals).most_common(1)[0][0] if vals else ""

    out = []
    for members in clusters.values():
        recs = [rows[i] for i in members]
        geo = next((r for r in recs if r.get("lat") is not None), None)
        addr = _best(recs, "addr_key") or (geo and geo.get("geo_cell")) or ""
        name = max((r["store_name"] for r in recs if r.get("store_name")), key=len, default="")
        canon = addr or (_best(recs, "geo_cell") + "|" + _best(recs, "name_key")) or (name + "|" + _best(recs, "state"))
        srcs = sorted({r["source"] for r in recs})
        chain = _best(recs, "chain")
        # vpid — CARRIED as a cross-reference attribute, never the spine/anchor
        vpid = next((r["store_id"] for r in recs if r["source"] in ("ab-inbev", "vtinfo-titos") and r.get("store_id")), "")
        out.append({
            "hoodie_outlet_id": _oid(canon), "outlet_name": name,
            "address": _best(recs, "address"), "city": _best(recs, "city"),
            "state": _best(recs, "state"), "zip": _best(recs, "zip"),
            "lat": (geo.get("lat") if geo else None), "lng": (geo.get("lng") if geo else None),
            "county_fips": _best(recs, "county_fips"), "phone": _best(recs, "phone"),
            "chain": chain, "is_chain": bool(chain) or any(r.get("is_chain") for r in recs),
            "f_beer": any(r.get("f_beer") for r in recs), "f_wine": any(r.get("f_wine") for r in recs),
            "f_spirits": any(r.get("f_spirits") for r in recs), "f_hemp": any(r.get("f_hemp") for r in recs),
            "f_cannabis": any(r.get("f_cannabis") for r in recs),
            "f_rtd_spirits": any(r.get("f_rtd_spirits") for r in recs),
            "sources": srcs, "source_count": len(srcs), "record_count": len(recs), "vpid": vpid,
        })
    fld = ["hoodie_outlet_id", "outlet_name", "address", "city", "state", "zip", "lat", "lng", "county_fips",
           "phone", "chain", "is_chain", "f_beer", "f_wine", "f_spirits", "f_hemp", "f_cannabis", "f_rtd_spirits",
           "sources", "source_count", "record_count", "vpid"]
    warehouse.write_parquet("dim_outlet", out, fields=fld)
    corr = sum(1 for o in out if o["source_count"] >= 2)
    log("[dim_outlet] %d raw records -> %d distinct outlets (%d corroborated by 2+ sources)" % (n, len(out), corr))
    return {"raw": n, "distinct": len(out), "corroborated": corr}


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    build()
