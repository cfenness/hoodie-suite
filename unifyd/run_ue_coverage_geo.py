#!/usr/bin/env python3
"""run_ue_coverage_geo.py — national UberEats coverage through GEO-MATCHED proxy IPs, PARALLEL by state.

UberEats' feed is location-based: a proxy exit IP whose geo conflicts with the pl= zone returns an empty feed
(that's why the home IP being flagged AND a random-geo proxy both gave 0 markers). So each state's zones are
crawled by a worker routed through a proxy IP IN THAT STATE — which also means workers don't share an IP, so we
run several states CONCURRENTLY (each its own Chrome profile + regional IP). Dodges flagging + parallel speed.

  python run_ue_coverage_geo.py                 # ubereats, ~4 states at once
  UE_CONC=5 python run_ue_coverage_geo.py postmates
"""
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resi
resi._load_env_file()
import kroger_api
kroger_api._load_creds()
import warehouse
import ue_crawl

SITE = sys.argv[1] if len(sys.argv) > 1 else "ubereats"
CONC = int(os.environ.get("UE_CONC", "4"))          # concurrent headful browsers (Mac tops out ~4-5)
PY = sys.executable
_MERGE_LOCK = threading.Lock()                       # only ONE thread writes <site>_stores at a time (no race)


def run_state(item):
    state, zlist = item
    tag = state or "us"
    # each worker writes to its OWN state table — concurrent write_accumulate to one shared table races/clobbers
    state_table = "%s_stores__%s" % (SITE, tag)
    code = ("import sys; sys.path.insert(0, %r); import resi; resi._load_env_file(); "
            "import kroger_api; kroger_api._load_creds(); import ue_crawl; "
            "ue_crawl.crawl_coverage(%r, site=%r, geo_state=%r, land_table=%r, resume=True)"
            % (os.path.dirname(os.path.abspath(__file__)), zlist, SITE,
               (state if state != "us" else None), state_table))
    env = dict(os.environ, BROWSER_PROFILE_SUFFIX="_%s" % tag)
    for lk in __import__("glob").glob(os.path.expanduser("~/.hoodie_browser_profiles/*_%s/Singleton*" % tag)):
        try: os.remove(lk)
        except Exception: pass
    t0 = time.time()
    r = subprocess.run([PY, "-c", code], env=env, capture_output=True, text=True)
    # merge this state's table into <site>_stores — SERIALIZED by the lock so parallel merges never clobber
    merged = 0
    try:
        rows = warehouse.query(state_table, "SELECT * FROM t")
        if rows:
            with _MERGE_LOCK:
                warehouse.write_accumulate("%s_stores" % SITE, rows, key=lambda x: x["store_uuid"],
                                           fields=ue_crawl.STORE_FIELDS)
            merged = len(rows)
    except Exception as e:
        return "%-16s MERGE ERR %s" % (tag, str(e)[:60])
    tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
    return "%-16s %d zones  %ds  merged %d  %s" % (tag, len(zlist), int(time.time() - t0), merged, tail[0][:60])


def main():
    zones = [l.strip() for l in open("zones_us.txt") if l.strip() and not l.startswith("#")]
    groups = sorted(ue_crawl.zones_by_state(zones).items(), key=lambda kv: -len(kv[1]))
    b = warehouse.row_count("%s_stores" % SITE)
    print("[geo-cov] %s | %d states, %d zones | %d stores before | %d workers at once"
          % (SITE, len(groups), len(zones), b, CONC), flush=True)
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        futs = {ex.submit(run_state, g): g[0] for g in groups}
        for fut in as_completed(futs):
            try:
                print("[geo-cov] done:", fut.result(), flush=True)
            except Exception as e:
                print("[geo-cov] %s ERR %s" % (futs[fut], str(e)[:80]), flush=True)
    print("[geo-cov] ALL DONE — %s_stores %d -> %d" % (SITE, b, warehouse.row_count("%s_stores" % SITE)), flush=True)


if __name__ == "__main__":
    main()
