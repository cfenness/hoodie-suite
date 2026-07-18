#!/bin/bash
# Refresh the coverage map: rebuild src_outlets (now including UberEats/Postmates stores) + dim_outlet (the
# cross-source geo-match — "matched where address/geo coincide"). Run periodically as the crawl accumulates so
# apps/coverage-map.html (/api/coverage) reflects the growing national footprint. Logs to /tmp/coverage_refresh.log.
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
[ -x "$PY" ] || PY=python3
"$PY" - <<'EOF'
import sys, time; sys.path.insert(0, ".")
import resi; resi._load_env_file()
import kroger_api; kroger_api._load_creds()
import warehouse, normalize
t0 = time.time()
print("[refresh] rebuilding src_outlets (all sources incl. ubereats/postmates)…", flush=True)
r = normalize.normalize_outlets()
print("[refresh] src_outlets:", r, flush=True)
try:
    import dim_outlet
    d = dim_outlet.build()
    print("[refresh] dim_outlet (geo-matched):", d, flush=True)
except Exception as e:
    print("[refresh] dim_outlet skipped:", str(e)[:120], flush=True)
# report the by-source coverage now on the map
for row in warehouse.query("src_outlets", "SELECT source, count(*) n, count(*) FILTER (WHERE lat IS NOT NULL) geo "
                           "FROM t GROUP BY source ORDER BY n DESC LIMIT 25"):
    print("   %-16s %8d outlets (%d geocoded)" % (row["source"], row["n"], row["geo"]), flush=True)
print("[refresh] done in %dm" % int((time.time()-t0)/60), flush=True)
EOF
