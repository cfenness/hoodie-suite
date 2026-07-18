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


def run_state(item):
    state, zlist = item
    tag = state or "us"
    code = ("import sys; sys.path.insert(0, %r); import resi; resi._load_env_file(); "
            "import kroger_api; kroger_api._load_creds(); import ue_crawl; "
            "ue_crawl.crawl_coverage(%r, site=%r, geo_state=%r, resume=True)"
            % (os.path.dirname(os.path.abspath(__file__)), zlist, SITE, (state if state != "us" else None)))
    env = dict(os.environ, BROWSER_PROFILE_SUFFIX="_%s" % tag)
    for lk in __import__("glob").glob(os.path.expanduser("~/.hoodie_browser_profiles/*_%s/Singleton*" % tag)):
        try: os.remove(lk)
        except Exception: pass
    t0 = time.time()
    r = subprocess.run([PY, "-c", code], env=env, capture_output=True, text=True)
    tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
    return "%-16s %d zones  %ds  %s" % (tag, len(zlist), int(time.time() - t0), tail[0][:80])


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
