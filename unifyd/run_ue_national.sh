#!/bin/bash
# National UberEats + Postmates. PHASE 1 (fast, ~90min/site): coverage — harvest every merchant's geo from the
# feed to fill the map. PHASE 2 (slow, days): deep — getStore + getMenuItem catalog/UPC/recipe per store.
# Home residential IP (how Uber was cracked), resumable, dedup across zones. Logs to /tmp/ue_national.log.
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
[ -x "$PY" ] || PY=python3
rm -f ~/.hoodie_browser_profiles/*/Singleton* 2>/dev/null
"$PY" -u - <<'EOF'
import sys, time; sys.path.insert(0, ".")
import resi; resi._load_env_file()
import kroger_api; kroger_api._load_creds()
import warehouse, ue_crawl
zones = [ln.strip() for ln in open("zones_us.txt") if ln.strip() and not ln.startswith("#")]

# PHASE 1 — fast national coverage (fills the map) for both platforms first
for site in ("ubereats", "postmates"):
    bs = warehouse.row_count("%s_stores" % site); t0 = time.time()
    print("=== COVERAGE %s: %d zones | before %d stores ===" % (site, len(zones), bs), flush=True)
    try:
        ue_crawl.crawl_coverage(zones, site=site, resume=True)
    except Exception as e:
        print("[%s coverage] error: %s" % (site, str(e)[:200]), flush=True)
    print("=== COVERAGE %s DONE in %dm: stores %d->%d ===" % (
        site, int((time.time()-t0)/60), bs, warehouse.row_count("%s_stores" % site)), flush=True)

# PHASE 2 — deep catalog/UPC/recipe (slow) for both platforms
for site in ("ubereats", "postmates"):
    b = warehouse.row_count("%s_products" % site); t0 = time.time()
    print("=== DEEP %s: %d zones | before %d items ===" % (site, len(zones), b), flush=True)
    try:
        ue_crawl.crawl_zones(zones, site=site, max_stores=120, max_items_enrich=25, bevalc_only=False, resume=True)
    except Exception as e:
        print("[%s deep] error: %s" % (site, str(e)[:200]), flush=True)
    print("=== DEEP %s DONE in %dm: items %d->%d ===" % (
        site, int((time.time()-t0)/60), b, warehouse.row_count("%s_products" % site)), flush=True)
EOF
