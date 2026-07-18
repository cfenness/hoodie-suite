#!/bin/bash
# Orchestrator: wait for the national COVERAGE crawl to finish (both platforms), refresh the coverage map, then
# auto-launch the 3-worker proxied STORE-DRIVEN deep crawl. Frees the coverage browser first so the deep workers
# can start clean. Logs to /tmp/ue_orchestrator.log.
cd "$(dirname "$0")"
LOG=/tmp/ue_orchestrator.log
echo "$(date): orchestrator armed — waiting for coverage to finish" > "$LOG"
# coverage is done for both sites once the national runner reaches its DEEP phase (or 5h cap as a safety net)
for i in $(seq 1 300); do
  grep -q "DEEP ubereats" /tmp/ue_national.log 2>/dev/null && { echo "$(date): coverage complete (runner hit deep)" >> "$LOG"; break; }
  ps -p 40408 >/dev/null 2>&1 || { echo "$(date): national runner exited" >> "$LOG"; break; }
  sleep 60
done
# stop the national runner's OLD sequential deep + free every hoodie browser
pkill -f run_ue_national.sh 2>/dev/null
pkill -9 -f hoodie_browser_profiles 2>/dev/null
sleep 6; rm -f ~/.hoodie_browser_profiles/*/Singleton* 2>/dev/null
# refresh the coverage map with the FULL coverage footprint (background — headless, won't block the browsers)
echo "$(date): refreshing coverage map (normalize + dim_outlet)" >> "$LOG"
nohup ./run_coverage_refresh.sh > /tmp/coverage_refresh_final.log 2>&1 &
sleep 5
# launch the 3-worker proxied store-driven deep crawl (ubereats) — each worker its own IP + profile
echo "$(date): launching 3-worker proxied deep crawl (ubereats)" >> "$LOG"
./run_ue_deep_parallel.sh 3 ubereats >> "$LOG" 2>&1
echo "$(date): deep workers launched — tail /tmp/ue_deep_w*.log" >> "$LOG"
