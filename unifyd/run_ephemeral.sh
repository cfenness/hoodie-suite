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

# DUCKDB MEMORY CEILING — the server sets DUCKDB_MEMORY_LIMIT, ephemeral machines never did. Unset,
# DuckDB sizes its buffer pool at ~80% of system RAM (3.2GB of a 4GB box) and, with no temp_directory,
# cannot spill — so it takes the memory Python needs and the kernel OOM-kills the pull. That is a
# whole-fleet defect, not an UberEats one: EVERY ephemeral source has been running unbounded.
# Give DuckDB half the box and somewhere to spill; leave the rest for the scraper itself.
if [ -z "${DUCKDB_MEMORY_LIMIT:-}" ]; then
  MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 4096)
  export DUCKDB_MEMORY_LIMIT="$((MEM_MB / 2))MB"
fi
export DUCKDB_TEMP_DIR="${DUCKDB_TEMP_DIR:-/tmp/duckdb}"
mkdir -p "$DUCKDB_TEMP_DIR"
echo "[ephemeral] duckdb memory_limit=$DUCKDB_MEMORY_LIMIT spill=$DUCKDB_TEMP_DIR"

cd /app/unifyd
# -u (unbuffered): a long pull writes progress to a PIPE, so Python block-buffers stdout and nothing
# reaches `flyctl logs` until the process exits. A 6h run is therefore invisible for 6h, and one that
# is SIGKILLed loses its buffer entirely — which is exactly how an aggregator-geo OOM presented as
# "started, then silence, then failed" with no clue what it had been doing. Streaming the output is
# what made that diagnosable; make it the default so the next long run is observable while it runs.
python3 -u run_ephemeral.py "$SRC" "$@"
CODE=$?
echo "run_ephemeral.sh: $SRC exited $CODE"
exit $CODE
