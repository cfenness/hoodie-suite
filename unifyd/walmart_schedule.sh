#!/bin/bash
# Recurring Walmart bev-alc pull. Runs walmart_scraper.py (self-loads Tigris creds, pulls via bdata,
# lands walmart_products + walmart_runs in the warehouse), then sleeps. Launch once with nohup:
#   nohup bash unifyd/walmart_schedule.sh > unifyd/walmart_sched.log 2>&1 &
# Tunables (env): WALMART_EVERY (seconds, default 24h), WALMART_PER (products/query, default 3),
#   WALMART_PY (python with pyarrow+duckdb; defaults to the hoodie-backend venv).
cd "$(dirname "$0")"
PY="${WALMART_PY:-$HOME/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python}"
EVERY="${WALMART_EVERY:-86400}"
PER="${WALMART_PER:-3}"
echo "[sched] Walmart scheduler up — every ${EVERY}s, --per ${PER}"
while true; do
  echo "[sched] $(date '+%Y-%m-%d %H:%M:%S') — pulling…"
  "$PY" walmart_scraper.py --per "$PER" --note scheduled 2>&1
  echo "[sched] $(date '+%Y-%m-%d %H:%M:%S') — sleeping ${EVERY}s"
  sleep "$EVERY"
done
