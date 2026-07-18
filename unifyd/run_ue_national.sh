#!/bin/bash
# National UberEats + Postmates crawl (bev-alc on+off premise). Home residential IP (how Uber was cracked),
# resumable, dedup across zones. Runs Uber over all US zones, then Postmates. Logs to /tmp/ue_national.log.
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
[ -x "$PY" ] || PY=python3
rm -f ~/.hoodie_browser_profiles/*/Singleton* 2>/dev/null
"$PY" - <<'EOF'
import sys, time; sys.path.insert(0, ".")
import resi; resi._load_env_file()
import kroger_api; kroger_api._load_creds()
import warehouse, ue_crawl
zones = [ln.strip() for ln in open("zones_us.txt") if ln.strip() and not ln.startswith("#")]
for site in ("ubereats", "postmates"):
    b = warehouse.row_count("%s_products" % site); bs = warehouse.row_count("%s_stores" % site)
    print("=== %s: %d zones | before: %d items, %d stores ===" % (site, len(zones), b, bs), flush=True)
    t0 = time.time()
    try:
        ue_crawl.crawl_zones(zones, site=site, max_stores=120, max_items_enrich=25, bevalc_only=False, resume=True)
    except Exception as e:
        print("[%s] crawl error: %s" % (site, str(e)[:200]), flush=True)
    a = warehouse.row_count("%s_products" % site); as_ = warehouse.row_count("%s_stores" % site)
    print("=== %s DONE in %dm: items %d->%d (+%d), stores %d->%d (+%d) ===" % (
        site, int((time.time()-t0)/60), b, a, a-b, bs, as_, as_-bs), flush=True)
EOF
