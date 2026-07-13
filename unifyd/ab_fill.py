"""ab_fill.py — complete the AB InBev locator sweep for UNDER-SWEPT regions (the west), ACCUMULATING into the
existing ab_outlets (never clobbering the east). The national sweep was seeded with too few western zips, so
WA/OR/AZ/NV/ID/… came out thin (7 WA metros alone yield ~600 outlets the table was missing).

No zip database is needed: BFS. Seed each target state's metros, sweep all AB brands (radius 25mi), harvest the
returned outlets' OWN zips, sweep the new ones, repeat — tiling the populated area organically. Merge by vpid
(union brand carriage + zips-hit), checkpoint to the warehouse periodically so a long run survives interruption.

    python ab_fill.py --cap 900        # BFS up to 900 western zips, then stop (re-run to go deeper)
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ab_locator
import warehouse

TARGET = {"WA", "OR", "NV", "AZ", "ID", "MT", "UT", "WY", "CO", "NM", "AK", "HI"}
FLD = ["VPID", "Name", "Address", "City", "State", "Zip", "Lat", "Lng", "AB_Brands", "Zips_Hit"]
SEED = ["98101", "98402", "99201", "98661", "98004", "98801", "98901", "98225", "98501",   # WA
        "97201", "97401", "97501", "97701", "97801", "97301", "97420",                      # OR
        "89101", "89501", "89701", "89014", "89406",                                        # NV
        "85001", "85701", "86001", "85201", "85301", "85364",                               # AZ
        "83702", "83201", "83814", "83301", "83402",                                        # ID
        "59101", "59801", "59401", "59701", "59901",                                        # MT
        "84101", "84601", "84770", "84401", "84005",                                        # UT
        "82001", "82601", "82901", "82070",                                                 # WY
        "80202", "80903", "80525", "81001", "81501", "80301",                               # CO
        "87101", "88001", "87501", "87401", "88201",                                        # NM
        "99501", "99701", "99801", "99654",                                                 # AK
        "96813", "96720", "96793", "96766"]                                                 # HI


def _z5(z):
    z = str(z or "").strip()
    return z[:5] if len(z) >= 5 and z[:5].isdigit() else ""


def _flush(existing, acc, log):
    """Merge the accumulated sweep into `existing` (by vpid, unioning brands+zips) and rewrite ab_outlets."""
    added = 0
    for vp, rec in acc.items():
        if vp in existing:
            ex = existing[vp]
            eb = set(x.strip() for x in str(ex.get("AB_Brands") or "").split(",") if x.strip()) | rec["_b"]
            ez = set(x.strip() for x in str(ex.get("Zips_Hit") or "").split(",") if x.strip()) | rec["_z"]
            ex["AB_Brands"] = ", ".join(sorted(eb)) + ", "
            ex["Zips_Hit"] = ", ".join(sorted(ez))
        else:
            existing[vp] = {"VPID": vp, "Name": rec["Name"], "Address": rec["Address"], "City": rec["City"],
                            "State": rec["State"], "Zip": rec["Zip"], "Lat": rec["Lat"], "Lng": rec["Lng"],
                            "AB_Brands": ", ".join(sorted(rec["_b"])) + ", ", "Zips_Hit": ", ".join(sorted(rec["_z"]))}
            added += 1
    warehouse.write_parquet("ab_outlets", [{k: e.get(k) for k in FLD} for e in existing.values()], fields=FLD)
    return added


def run(cap=900, radius=25.0, delay=0.15, ckpt=250, log=print):
    existing = {str(r["VPID"]): dict(r) for r in warehouse.query("ab_outlets", "SELECT * FROM t")}
    base = len(existing)
    log("[ab_fill] existing ab_outlets: %d" % base)
    acc = {}                                     # vpid -> {schema fields + _b(brands) + _z(zips hit)}
    swept, queue, t0 = set(), list(dict.fromkeys(SEED)), time.time()
    while queue and len(swept) < cap:
        z = queue.pop(0)
        if z in swept:
            continue
        swept.add(z)
        for b in ab_locator.DEFAULT_BRANDS:
            try:
                rows = ab_locator.locate(b, z, radius=radius, delay=delay, log=lambda *a: None)
            except Exception:
                continue
            for r in rows:
                vp = str(r.get("vpid") or "")
                if not vp:
                    continue
                st = (r.get("state") or "").strip().upper()
                rec = acc.get(vp)
                if not rec:
                    rec = acc[vp] = {"Name": r.get("name", ""), "Address": r.get("address", ""),
                                     "City": r.get("city", ""), "State": st, "Zip": r.get("zipCode", ""),
                                     "Lat": r.get("latitude"), "Lng": r.get("longitude"), "_b": set(), "_z": set()}
                rec["_b"].add(b); rec["_z"].add(z)
                oz = _z5(r.get("zipCode"))         # harvest: a new zip in a target state extends the frontier
                if st in TARGET and oz and oz not in swept and oz not in queue:
                    queue.append(oz)
        if len(swept) % 50 == 0:
            log("[ab_fill] swept %d | queue %d | distinct sweep-outlets %d | %.0fs"
                % (len(swept), len(queue), len(acc), time.time() - t0))
        if len(swept) % ckpt == 0:                # checkpoint so a long run survives interruption
            _flush(existing, acc, log)
            log("[ab_fill] checkpoint at %d zips -> ab_outlets %d" % (len(swept), len(existing)))
    _flush(existing, acc, log)
    added = len(existing) - base                  # cumulative new (checkpoints already merged some), so diff base
    log("[ab_fill] DONE: swept %d western zips -> +%d NEW outlets (ab_outlets %d -> %d)"
        % (len(swept), added, base, len(existing)))
    return {"swept": len(swept), "added": added, "total": len(existing)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=900)
    ap.add_argument("--radius", type=float, default=25.0)
    ap.add_argument("--delay", type=float, default=0.15)
    a = ap.parse_args()
    run(cap=a.cap, radius=a.radius, delay=a.delay)
