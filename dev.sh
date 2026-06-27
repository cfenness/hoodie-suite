#!/usr/bin/env bash
# dev.sh — the local visual-feel loop. One command: serve the suite, edit, refresh.
#
#   ./dev.sh
#
# Serves the whole suite on http://localhost:8000 with /api/* live (it starts the
# Unifyd agent too, if its deps are installed). Edit any HTML and refresh — no build,
# no deploy, no file shuffling. Ctrl-C stops everything.
#
# Flags:
#   --no-api      skip the Unifyd agent; serve static only (apps use embedded data)
#   --port N      serve on port N (default 8000)
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
START_AGENT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --no-api) START_AGENT=0; shift ;;
    --port)   PORT="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

AGENT_PID=""
cleanup() { [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

if [ "$START_AGENT" = "1" ]; then
  if python3 -c "import flask" 2>/dev/null; then
    echo "• starting Unifyd agent (http://127.0.0.1:8765)"
    ( cd unifyd && python3 server.py ) >/tmp/unifyd-agent.log 2>&1 &
    AGENT_PID=$!
    sleep 1
  else
    echo "• Unifyd agent skipped — deps missing. Install once with:"
    echo "    pip install -r unifyd/requirements.txt"
    echo "  (apps will use embedded preview data until then)"
  fi
fi

echo "• serving the suite — open http://localhost:${PORT}"
PORT="$PORT" python3 scripts/dev_server.py
