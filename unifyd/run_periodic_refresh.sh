#!/bin/bash
# Keep the coverage map fresh: rebuild src_outlets + dim_outlet every ~25min while a crawl runs (so it tracks the
# growing footprint instead of going stale at a one-time count). Runs until killed. Log: /tmp/periodic_refresh.log
cd "$(dirname "$0")"
while true; do
  echo "$(date): refreshing…" >> /tmp/periodic_refresh.log
  ./run_coverage_refresh.sh >> /tmp/periodic_refresh.log 2>&1
  echo "$(date): done; sleep 25m" >> /tmp/periodic_refresh.log
  sleep 1500
done
