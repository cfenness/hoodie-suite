#!/usr/bin/env python3
"""outlet_union.py — PRE-MASTER the on-premise outlets across every source, then judge menu freshness per source.

Each source (DoorDash, Toast, soon UberEats/Postmates) publishes its own store universe; we union them and
resolve the SAME physical outlet across sources via outlet_ident (phone / address / geo — the identity signals
we capture on the page during the menu pull). The mastered outlet then carries, per source, WHICH has it and
HOW FRESH its menu is — so the pipeline can pick the freshest source and target stale re-pulls.

CONSERVATIVE by design: only STRONG keys (phone/address/geo) collapse two records into one outlet — never
name alone (a chain is a hundred bars). Unenriched sitemap outlets stay their own outlet until a page fetch
gives them identity, so cross-source links grow as menu-pull coverage grows. Better to under-merge than to
falsely fuse two different bars.

DERIVED build ($0, no network). Reads the source outlet + menu-account tables, writes outlet_master.
    python outlet_union.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import outlet_ident

MASTER_FIELDS = ["outlet_id", "name", "street", "city", "state", "phone", "lat", "lng",
                 "sources", "source_count", "doordash_id", "toast_guid",
                 "doordash_menu_date", "toast_menu_date", "freshest_source", "freshest_date"]


def _q(table, sql):
    try:
        return warehouse.query(table, sql)
    except Exception:
        return []


def _date_from_runid(rid):
    m = re.search(r"(\d{4})(\d{2})(\d{2})", rid or "")
    return "%s-%s-%s" % m.groups() if m else ""


def _collect(log=print):
    """One record per source-outlet: rid, source, source_id, identity fields (enriched from the menu-account
    table where a page has been fetched, else the sitemap row), and this source's last menu-capture date."""
    recs = []
    naop = {}
    for r in _q("naop_accounts", "SELECT * FROM t"):
        sid = str(r.get("store"))
        d = _date_from_runid(r.get("run_id"))
        cur = naop.get(sid)
        if cur is None or d > cur.get("_date", ""):
            naop[sid] = dict(r, _date=d)
    for r in _q("doordash_stores", "SELECT store_id, name, city, state FROM t WHERE store_id IS NOT NULL"):
        sid = str(r["store_id"])
        e = naop.get(sid, {})
        recs.append({"rid": "dd:" + sid, "source": "doordash", "source_id": sid,
                     "name": e.get("clean_name") or r.get("name") or "",
                     "street": e.get("street") or "", "city": e.get("city") or r.get("city") or "",
                     "state": e.get("state") or r.get("state") or "", "phone": e.get("phone") or "",
                     "lat": None, "lng": None, "menu_date": e.get("_date", "")})
    tacct = {}
    for r in _q("toast_menu_accounts", "SELECT * FROM t"):
        g = str(r.get("guid"))
        if g not in tacct or (r.get("captured") or "") > (tacct[g].get("captured") or ""):
            tacct[g] = r
    for r in _q("toast_outlets", "SELECT guid, name, state FROM t"):
        g = str(r["guid"])
        e = tacct.get(g, {})
        recs.append({"rid": "toast:" + g, "source": "toast", "source_id": g,
                     "name": e.get("clean_name") or r.get("name") or "",
                     "street": e.get("street") or "", "city": e.get("city") or "",
                     "state": e.get("state") or r.get("state") or "", "phone": e.get("phone") or "",
                     "lat": e.get("lat"), "lng": e.get("lng"), "menu_date": e.get("captured") or ""})
    log("[union] collected %d source-outlets (dd=%d, toast=%d)"
        % (len(recs), sum(1 for x in recs if x["source"] == "doordash"),
           sum(1 for x in recs if x["source"] == "toast")))
    return recs


def _pick(vals):
    return next((v for v in vals if v), "")


def build_master(log=print):
    recs = _collect(log=log)
    if not recs:
        log("[union] no source outlets — nothing to master")
        return []
    clusters = outlet_ident.resolve(recs)                       # rid -> cluster_id (strong-key union-find)
    by_id = {r["rid"]: r for r in recs}
    groups = {}
    for rid, cid in clusters.items():
        groups.setdefault(cid, []).append(by_id[rid])

    rows = []
    for cid, members in groups.items():
        srcs = sorted({m["source"] for m in members})
        dd = next((m for m in members if m["source"] == "doordash"), {})
        toast = next((m for m in members if m["source"] == "toast"), {})
        dates = {"doordash": dd.get("menu_date", ""), "toast": toast.get("menu_date", "")}
        dated = {s: d for s, d in dates.items() if d}
        fresh = max(dated, key=dated.get) if dated else ""
        rows.append(dict(
            outlet_id=cid,
            name=_pick([m.get("name") for m in members]),
            street=_pick([m.get("street") for m in members]),
            city=_pick([m.get("city") for m in members]),
            state=_pick([m.get("state") for m in members]),
            phone=_pick([m.get("phone") for m in members]),
            lat=next((m["lat"] for m in members if m.get("lat") is not None), None),
            lng=next((m["lng"] for m in members if m.get("lng") is not None), None),
            sources=",".join(srcs), source_count=len(srcs),
            doordash_id=dd.get("source_id", ""), toast_guid=toast.get("source_id", ""),
            doordash_menu_date=dates["doordash"], toast_menu_date=dates["toast"],
            freshest_source=fresh, freshest_date=(dated.get(fresh, "") if dated else "")))
    if rows:
        warehouse.write_parquet("outlet_master", rows, fields=MASTER_FIELDS)
        multi = sum(1 for r in rows if r["source_count"] > 1)
        withmenu = sum(1 for r in rows if r["freshest_date"])
        log("[union] %d mastered outlets — %d MULTI-SOURCE (matched across sources), %d with a captured menu"
            % (len(rows), multi, withmenu))
    return rows


def run(log=print):
    return build_master(log=log)


if __name__ == "__main__":
    sys.exit(0 if build_master() else 1)
