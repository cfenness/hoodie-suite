#!/usr/bin/env python3
"""outlet_union.py — PRE-MASTER the on-premise outlets from every source, then judge menu freshness per source.

The delivery aggregators (DoorDash, UberEats, Postmates) and the storefront platforms (Toast, …) each publish
their own store universe. Unioned + deduped to the physical OUTLET, they become one mastered on-premise
spine — and because every source stamps when we last captured that outlet's menu, the master can say, per
outlet, WHICH sources have it and HOW FRESH each one is. That freshness map is what lets the pipeline pick the
best/freshest menu per outlet and target re-pulls where a source has gone stale.

This is a DERIVED build (reads the source outlet/menu tables, writes `outlet_master`) — no network, $0.
Matching is first-pass: normalized name + state (state enriched as menus are pulled); it establishes the
spine + freshness structure and gets sharper as geo enrichment improves.

    python outlet_union.py
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

MASTER_FIELDS = ["outlet_key", "name", "state", "sources", "source_count",
                 "doordash_id", "toast_guid", "ubereats_id", "postmates_id",
                 "doordash_menu_date", "toast_menu_date", "freshest_source", "freshest_date"]


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())[:24]


def _q(table, sql):
    try:
        return warehouse.query(table, sql)
    except Exception:
        return []


def build_master(log=print):
    """Union all source outlet tables → outlet_master, attaching each source's last menu-capture date."""
    # per-source menu freshness: {source_id -> last captured date}. DoorDash (naop) stamps run_id
    # ('naop-YYYYMMDD-HHMMSS') not a captured column, so derive the date from it; Toast stamps `captured`.
    def _date_from_runid(rid):
        m = re.search(r"(\d{4})(\d{2})(\d{2})", rid or "")
        return "%s-%s-%s" % m.groups() if m else ""
    dd_menu = {}
    for r in _q("naop_accounts", "SELECT store, MAX(run_id) rid FROM t GROUP BY store"):
        d = _date_from_runid(r.get("rid"))
        if d:
            dd_menu[str(r["store"])] = d
    toast_menu = {str(r["guid"]): r["captured"] for r in _q("toast_menu_accounts", "SELECT guid, MAX(captured) captured FROM t GROUP BY guid") if r.get("captured")}

    master = {}   # outlet_key -> record

    def _slot(name, state):
        key = "%s|%s" % (_norm(name), (state or "").lower())
        r = master.get(key)
        if r is None:
            r = master[key] = dict(outlet_key=key, name=name, state=state or "", _srcs=set(),
                                   doordash_id="", toast_guid="", ubereats_id="", postmates_id="",
                                   doordash_menu_date="", toast_menu_date="")
        elif state and not r["state"]:
            r["state"] = state
        return r

    for r in _q("doordash_stores", "SELECT store_id, name, state FROM t WHERE store_id IS NOT NULL"):
        sid = str(r["store_id"])
        rec = _slot(r.get("name"), r.get("state"))
        rec["_srcs"].add("doordash"); rec["doordash_id"] = sid
        if sid in dd_menu:
            rec["doordash_menu_date"] = max(rec["doordash_menu_date"], dd_menu[sid])
    for r in _q("toast_outlets", "SELECT guid, name, state FROM t"):
        g = str(r["guid"])
        rec = _slot(r.get("name"), r.get("state"))
        rec["_srcs"].add("toast"); rec["toast_guid"] = g
        if g in toast_menu:
            rec["toast_menu_date"] = max(rec["toast_menu_date"], toast_menu[g])

    rows = []
    for rec in master.values():
        dates = {"doordash": rec["doordash_menu_date"], "toast": rec["toast_menu_date"]}
        dated = {s: d for s, d in dates.items() if d}
        fresh_src = max(dated, key=dated.get) if dated else ""
        rows.append(dict(outlet_key=rec["outlet_key"], name=rec["name"], state=rec["state"],
                         sources=",".join(sorted(rec["_srcs"])), source_count=len(rec["_srcs"]),
                         doordash_id=rec["doordash_id"], toast_guid=rec["toast_guid"],
                         ubereats_id=rec["ubereats_id"], postmates_id=rec["postmates_id"],
                         doordash_menu_date=rec["doordash_menu_date"], toast_menu_date=rec["toast_menu_date"],
                         freshest_source=fresh_src, freshest_date=(dated.get(fresh_src, "") if dated else "")))
    if rows:
        warehouse.write_parquet("outlet_master", rows, fields=MASTER_FIELDS)
        multi = sum(1 for r in rows if r["source_count"] > 1)
        withmenu = sum(1 for r in rows if r["freshest_date"])
        log("[union] %d mastered outlets (%d multi-source, %d with a captured menu) -> outlet_master"
            % (len(rows), multi, withmenu))
    else:
        log("[union] no source outlets found — nothing to master")
    return rows


def run(log=print):
    return build_master(log=log)


if __name__ == "__main__":
    sys.exit(0 if build_master() else 1)
