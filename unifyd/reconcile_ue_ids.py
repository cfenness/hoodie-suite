#!/usr/bin/env python3
"""reconcile_ue_ids.py — one-time fix: adopt the URL id as the canonical UberEats account identity (matches the
sitemap), removing the double-count caused by the feed's storeUuid (a DIFFERENT id) being counted separately.

  1. re-key ubereats_stores by the URL id parsed from its actionUrl (address); keep the old storeUuid as api_uuid.
  2. drop the UUID-format ubereats rows from src_outlets (the double-counts) — the 254k URL-id sitemap accounts stay,
     and the fast refresh re-adds coverage geo keyed by URL id (matching the sitemap → one account per store).

MUST run with the fast-refresh loop PAUSED (it also writes src_outlets — concurrent writers race).
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kroger_api
kroger_api._load_creds()
import warehouse
import ue_crawl
import refresh_fast

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", re.I)     # feed storeUuid form (the double-count)
URLID = re.compile(r"/store/[^/]+/([A-Za-z0-9_\-]{15,})")


def rekey_stores(site="ubereats"):
    rows = warehouse.query("%s_stores" % site, "SELECT * FROM t")
    rk = {}
    for r in rows:
        m = URLID.search(r.get("address") or "")
        uid = m.group(1) if m else r["store_uuid"]
        d = dict(r)
        d["api_uuid"] = r.get("api_uuid") or r["store_uuid"]
        d["store_uuid"] = uid
        rk[uid] = d                                              # dedup by URL id
    warehouse.write_parquet("%s_stores" % site, list(rk.values()), ue_crawl.STORE_FIELDS)
    print("[reconcile] %s_stores re-keyed by URL id: %d rows (%d distinct)" % (site, len(rows), len(rk)))
    return len(rk)


def drop_doublecounts():
    allrows = warehouse.query("src_outlets", "SELECT * FROM t")
    keep = [r for r in allrows if not (r.get("source") in ("ubereats", "postmates") and UUID.match(r.get("store_id") or ""))]
    warehouse.write_parquet("src_outlets", keep, refresh_fast.FLD)
    print("[reconcile] src_outlets %d -> %d (dropped %d storeUuid double-counts)" % (len(allrows), len(keep), len(allrows) - len(keep)))


if __name__ == "__main__":
    rekey_stores("ubereats")
    drop_doublecounts()
    ue = warehouse.query("src_outlets", "SELECT count(*) n, count(*) FILTER (WHERE lat IS NOT NULL) g "
                         "FROM t WHERE source='ubereats'")[0]
    print("[reconcile] src_outlets ubereats now: %d accounts, %d geocoded (clean, URL-id keyed)" % (ue["n"], ue["g"]))
