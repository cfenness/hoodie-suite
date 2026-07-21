#!/bin/bash
# The Mac's dispatcher tick (NRT-PLAN.md §3): run whatever is past its interval, nothing else.
# Fire this every 30 minutes (launchd — see launchd/com.hoodie.due.plist); the fcntl lock inside
# run_sources.py makes an overlapping tick a no-op, and the shared source_runs ledger means
# anything the cloud runner just landed shows fresh here and is skipped.
# Replaces the fixed daily run_sources_daily.sh + run_mac_queue.sh pair once installed.
cd "/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-suite/unifyd" || exit 1
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
echo "=== run_due tick $(date) ==="
"$PY" -u -c "import sys;sys.path.insert(0,'.');import kroger_api;kroger_api._load_creds();import run_sources;sys.exit(run_sources.main(['--due']))"
echo "=== tick done $(date) ==="
