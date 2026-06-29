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

# Load local secrets if present — a gitignored .env (ANTHROPIC_API_KEY, BRIGHTDATA_*, …).
# Never commit .env; it's in .gitignore and excluded from deploy.
if [ -f .env ]; then set -a; . ./.env; set +a; echo "• loaded .env (local secrets)"; fi

PORT=8000
START_AGENT=1
DO_SETUP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --no-api) START_AGENT=0; shift ;;
    --setup)  DO_SETUP=1; shift ;;
    --port)   PORT="$2"; shift 2 ;;
    --*)      echo "• ignoring unknown flag: $1" >&2; shift ;;
    *)        shift ;;   # ignore stray args (e.g. a pasted '# comment') instead of crashing
  esac
done

# macOS Homebrew Python is "externally managed" (PEP 668), so deps live in a local .venv.
# `./dev.sh --setup` creates it + installs; afterwards plain `./dev.sh` auto-uses it.
PYBIN="python3"
[ -x ".venv/bin/python3" ] && PYBIN="$(pwd)/.venv/bin/python3"
if [ "$DO_SETUP" = "1" ]; then
  echo "• setup: creating .venv and installing the agent deps (one time)…"
  python3 -m venv .venv
  .venv/bin/python3 -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  .venv/bin/python3 -m pip install -r unifyd/requirements.txt
  PYBIN="$(pwd)/.venv/bin/python3"
  echo "• setup done ✓"
fi

# free a dev port from any prior ./dev.sh instance (orphaned agent/server hold the port)
freeport() {
  command -v lsof >/dev/null 2>&1 || return 0
  local pid; pid=$(lsof -ti tcp:"$1" -sTCP:LISTEN 2>/dev/null || true)
  [ -n "$pid" ] && { echo "• freeing port $1 (stopping prior PID $pid)"; kill $pid 2>/dev/null || true; sleep 0.5; }
  return 0
}

AGENT_PID=""
cleanup() {
  [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true
  # the agent runs under a pipe, so its python can outlive AGENT_PID — also stop whatever holds 8765
  if command -v lsof >/dev/null 2>&1; then
    _p=$(lsof -ti tcp:8765 -sTCP:LISTEN 2>/dev/null || true); [ -n "$_p" ] && kill $_p 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if [ "$START_AGENT" = "1" ]; then
  if "$PYBIN" -c "import flask" 2>/dev/null; then
    freeport 8765
    echo "• starting Unifyd agent (http://127.0.0.1:8765) — its logs stream below as [agent]"
    # live + visible: unbuffered, prefixed to this terminal, and tee'd to /tmp/unifyd-agent.log
    ( cd unifyd && "$PYBIN" -u server.py 2>&1 | tee /tmp/unifyd-agent.log | sed 's/^/  [agent] /' ) &
    AGENT_PID=$!
    sleep 1
    if curl -fsS http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
      echo "• agent is LIVE ✓  (Hoodie Pulls will show 'engine live'; real pulls run for real)"
    else
      echo "• agent didn't answer /api/health yet — watch the [agent] lines above for errors"
    fi
  else
    echo "• ⚠ Unifyd agent SKIPPED — Flask not found. macOS Python is externally-managed, so"
    echo "    install into a local venv ONCE, then re-run:"
    echo "        ./dev.sh --setup"
    echo "    (until then, apps fall back to EMBEDDED PREVIEW DATA — NOT a live pull)"
  fi
fi

freeport "$PORT"
echo "• serving the suite — open http://localhost:${PORT}"
PORT="$PORT" "$PYBIN" scripts/dev_server.py
