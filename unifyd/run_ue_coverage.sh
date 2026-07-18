#!/bin/bash
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"; [ -x "$PY" ] || PY=python3
rm -f ~/.hoodie_browser_profiles/*/Singleton* 2>/dev/null
"$PY" -u - <<'EOF'
import sys, time; sys.path.insert(0,".")
import resi; resi._load_env_file(); import kroger_api; kroger_api._load_creds()
import warehouse, ue_crawl
zones=[l.strip() for l in open("zones_us.txt") if l.strip() and not l.startswith("#")]
for site in ("ubereats","postmates"):
    b=warehouse.row_count("%s_stores"%site); t0=time.time()
    print("=== COVERAGE %s: before %d ==="%(site,b),flush=True)
    try: ue_crawl.crawl_coverage(zones, site=site, resume=True)
    except Exception as e: print("[%s] err %s"%(site,str(e)[:150]),flush=True)
    print("=== COVERAGE %s DONE %dm: %d -> %d ==="%(site,int((time.time()-t0)/60),b,warehouse.row_count("%s_stores"%site)),flush=True)
EOF
