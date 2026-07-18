#!/bin/bash
# Keep the coverage map current: FAST incremental merge of ubereats/postmates stores into src_outlets every 5min
# (refresh_fast, ~18s — not the ~1min full normalize). Runs until killed. Log: /tmp/periodic_refresh.log
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"; [ -x "$PY" ] || PY=python3
while true; do
  "$PY" -c "import sys; sys.path.insert(0,'.'); import kroger_api; kroger_api._load_creds(); import refresh_fast; refresh_fast.run()" >> /tmp/periodic_refresh.log 2>&1
  sleep 300
done
