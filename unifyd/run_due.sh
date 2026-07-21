#!/bin/bash
# The Mac's dispatcher tick (NRT-PLAN.md §3): run whatever is past its interval, nothing else.
# Fire this every 30 minutes (launchd — see launchd/com.hoodie.due.plist); the fcntl lock inside
# run_sources.py makes an overlapping tick a no-op, and the shared run ledger means anything the
# cloud runner just landed shows fresh here and is skipped.
#
# PRODUCTION RUNS FROM A DEDICATED RUNNER CLONE (~/hoodie-runner), never a working checkout —
# sessions switch branches in working checkouts, and a launchd tick must always execute
# origin/main (learned 2026-07-21: the shared checkout was on a feature branch while being the
# launchd exec target). The clone carries a `.hoodie-runner` marker; when present, the tick
# fast-forwards itself to origin/main first (checkout -B: refuses on conflict rather than
# discarding anything, and a refused update just runs the previous pin).
#
# One-time setup:
#   git clone https://github.com/cfenness/hoodie-suite.git ~/hoodie-runner
#   touch ~/hoodie-runner/.hoodie-runner
#   cp ~/hoodie-runner/unifyd/launchd/com.hoodie.due.plist ~/Library/LaunchAgents/ && launchctl load ...
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 1
if [ -f .hoodie-runner ]; then
  git fetch -q origin main && git checkout -q -B runner origin/main \
    || echo "[run_due] self-update failed — running previous pin $(git rev-parse --short HEAD)"
fi
cd unifyd || exit 1
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
echo "=== run_due tick $(date) @ $HERE ($(git rev-parse --short HEAD 2>/dev/null)) ==="
"$PY" -u -c "import sys;sys.path.insert(0,'.');import kroger_api;kroger_api._load_creds();import run_sources;sys.exit(run_sources.main(['--due']))"
echo "=== tick done $(date) ==="
