#!/bin/bash
# Parallel STORE-DRIVEN deep UberEats crawl. N workers (default 3), each: its own Chrome profile + zone shard +
# its OWN sticky residential proxy IP (UE_PROXY=1). Drives from coverage's store list (<site>_stores) — no feed
# scroll — and enriches only bev-alc items. getStoreV1 is nav-bound, so parallel workers on distinct IPs = ~Nx
# throughput without flagging one IP. Resumable + dedup (write_accumulate). Usage: ./run_ue_deep_parallel.sh [N] [site]
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
[ -x "$PY" ] || PY=python3
N="${1:-3}"; SITE="${2:-ubereats}"
rm -f ~/.hoodie_browser_profiles/*/Singleton* 2>/dev/null
echo "launching $N parallel store-driven deep workers for $SITE (each on its own proxy IP) …"
for i in $(seq 0 $((N-1))); do
  UE_PROXY=1 BROWSER_PROFILE_SUFFIX="_w$i" nohup "$PY" -u - "$i" "$N" "$SITE" > "/tmp/ue_deep_w$i.log" 2>&1 <<'EOF' &
import sys; sys.path.insert(0, ".")
import resi; resi._load_env_file()
import kroger_api; kroger_api._load_creds()
import ue_crawl
i, n, site = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
zones = [ln.strip() for ln in open("zones_us.txt") if ln.strip() and not ln.startswith("#")]
ue_crawl.crawl_stores(zones, site=site, shard="%d/%d" % (i, n), enrich=True, max_items_enrich=25)
EOF
  echo "  worker $i → /tmp/ue_deep_w$i.log (profile _w$i, proxy tag ued${i}_$N)"
  sleep 4
done
echo "all $N workers launched. tail -f /tmp/ue_deep_w*.log"
