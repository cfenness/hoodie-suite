#!/bin/bash
# Entrypoint of an EPHEMERAL egress machine — the Tigris -> Google Drive archive copy.
#
#   flyctl machine run --rm <image> -a hoodie-suite --vm-memory 2048 \
#     -e GDRIVE_TOKEN="$(cat token.json)" \
#     --command "bash /app/unifyd/run_egress.sh all --reference-csv"
#
# Runs ON FLY on purpose: the Tigris creds are already in the app's env there, and the machine
# sits next to the bucket, so the bytes go Tigris -> Drive without crossing anyone's laptop.
# Same one-shot/self-destroying shape as run_ephemeral.sh — it does one job and exits.
#
# READ-ONLY against Tigris. This never writes to, deletes from, or mutates the bucket.
set -uo pipefail

# rclone isn't in the app image (it's a web/scraper image). Fly has open egress, so install it
# here rather than forcing a Dockerfile change + full redeploy to run an archive job.
if ! command -v rclone >/dev/null 2>&1; then
  echo "[egress] installing rclone…"
  curl -fsSL https://rclone.org/install.sh | bash \
    || { apt-get update -qq && apt-get install -y -qq rclone; }
fi

# DuckDB is only touched by --reference-csv, but give it a ceiling + somewhere to spill for the
# same reason run_ephemeral.sh does: unbounded, it sizes to ~80% of RAM and gets the box OOM-killed.
if [ -z "${DUCKDB_MEMORY_LIMIT:-}" ]; then
  MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 2048)
  export DUCKDB_MEMORY_LIMIT="$((MEM_MB / 2))MB"
fi
export DUCKDB_TEMP_DIR="${DUCKDB_TEMP_DIR:-/tmp/duckdb}"
mkdir -p "$DUCKDB_TEMP_DIR"

cd /app
# -u (unbuffered): a multi-GB copy writes progress to a PIPE, so without this nothing reaches
# `flyctl logs` until the process exits — a long transfer would be invisible while it runs.
python3 -u tools/warehouse_egress.py "$@"
CODE=$?
echo "run_egress.sh: exited $CODE"
exit $CODE
