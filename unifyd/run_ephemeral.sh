#!/bin/bash
# Entrypoint of an EPHEMERAL pull machine (spawned via `flyctl machine run --rm ... --command
# "bash /app/unifyd/run_ephemeral.sh <source_id>"`). Starts a virtual display for the anti-bot sources
# that drive headful Chrome, then runs exactly one source and exits so the machine self-destroys.
# See run_ephemeral.py for the isolation contract.
set -uo pipefail
SRC="${1:?source id required}"
shift
# Everything after the source id is forwarded verbatim to run_ephemeral.py — that's how Hoodie Collect
# passes --run-id (the journal to stream into) and --days/--all (the time window). Quoted "$@" so an
# empty arg list stays empty instead of becoming one blank argument.

# Headful Chrome (UberEats/Kroger/TotalWine/Albertsons/Ahold) needs a display + the container flags.
# Harmless for headless sources (they never launch a browser). Xvfb backgrounds; DISPLAY is exported.
export DISPLAY=:99 BROWSER_NO_SANDBOX=1
if command -v Xvfb >/dev/null 2>&1; then
  Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
  sleep 1
fi

cd /app/unifyd
python3 run_ephemeral.py "$SRC" "$@"
CODE=$?
echo "run_ephemeral.sh: $SRC exited $CODE"
exit $CODE
