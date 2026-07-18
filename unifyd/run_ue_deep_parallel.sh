#!/bin/bash
# Parallel DEEP UberEats crawl — N browser workers (default 3), each its own Chrome profile + zone shard, so
# getStore/getMenuItem throughput scales ~Nx (getStoreV1 is navigation-bound → parallelize the browsers).
# Home IP, resumable, dedup across shards (write_accumulate). Usage: ./run_ue_deep_parallel.sh [N] [site]
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
[ -x "$PY" ] || PY=python3
N="${1:-3}"; SITE="${2:-ubereats}"
rm -f ~/.hoodie_browser_profiles/*/Singleton* 2>/dev/null
echo "launching $N parallel deep workers for $SITE …"
for i in $(seq 0 $((N-1))); do
  BROWSER_PROFILE_SUFFIX="_w$i" nohup "$PY" -u - "$i" "$N" "$SITE" > "/tmp/ue_deep_w$i.log" 2>&1 <<'EOF' &
import sys; sys.path.insert(0, ".")
import resi; resi._load_env_file()
import kroger_api; kroger_api._load_creds()
import ue_crawl
i, n, site = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
zones = [ln.strip() for ln in open("zones_us.txt") if ln.strip() and not ln.startswith("#")]
shard = zones[i::n]
print("[w%d/%d] %d zones" % (i, n, len(shard)), flush=True)
ue_crawl.crawl_zones(shard, site=site, max_stores=120, max_items_enrich=25, bevalc_only=False, resume=True)
EOF
  echo "  worker $i → /tmp/ue_deep_w$i.log (profile _w$i)"
  sleep 3
done
echo "all $N workers launched. tail -f /tmp/ue_deep_w*.log"
