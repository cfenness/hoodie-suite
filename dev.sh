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
    echo "• starting Unifyd agent (http://127.0.0.1:8765) — its logs stream below as [agent]"
    # live + visible: unbuffered, prefixed to this terminal, and tee'd to /tmp/unifyd-agent.log
    ( cd unifyd && python3 -u server.py 2>&1 | tee /tmp/unifyd-agent.log | sed 's/^/  [agent] /' ) &
    AGENT_PID=$!
    sleep 1
    if curl -fsS http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
      echo "• agent is LIVE ✓  (Hoodie Pulls will show 'engine live'; real pulls run for real)"
    else
      echo "• agent didn't answer /api/health yet — watch the [agent] lines above for errors"
    fi
  else
    echo "• ⚠ Unifyd agent SKIPPED — Flask not installed, so NO real pulls will run and apps"
    echo "    fall back to EMBEDDED PREVIEW DATA (stale sample, NOT a live pull). Install once:"
    echo "    pip3 install -r unifyd/requirements.txt   # then re-run ./dev.sh"
  fi
fi

echo "• serving the suite — open http://localhost:${PORT}"
PORT="$PORT" python3 scripts/dev_server.py
