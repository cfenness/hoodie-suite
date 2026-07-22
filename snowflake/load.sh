#!/usr/bin/env bash
# load.sh — the morning drop. Regenerate the build from the LIVE warehouse, then mirror it into
# Snowflake. Idempotent: re-running any morning fully refreshes the source catalogs and appends the
# new day of time-series — no duplicates, no manual steps. Designed to "easily drop 20M records".
#
# Run this on a host that can reach the warehouse (the Fly machine, or a box with the Tigris env set)
# AND can reach Snowflake via snowsql. It does NOT embed credentials.
#
# Prereqs:
#   • snowsql on PATH, with a named connection (account/user/auth/role/warehouse) in ~/.snowsql/config
#     → export SNOW_CONN=<that connection name>
#   • the Tigris values the engine uses (unifyd/warehouse.py): so --live can read footers AND the
#     stage can point at the bucket:
#       export TIGRIS_BUCKET=<bucket>            # required
#       export TIGRIS_KEY_ID=<key>  TIGRIS_SECRET=<secret>   # required
#       export TIGRIS_PREFIX=warehouse           # optional (default)
#       export TIGRIS_ENDPOINT=fly.storage.tigris.dev        # optional (default)
#       export AWS_ENDPOINT_URL_S3=https://$TIGRIS_ENDPOINT BUCKET_NAME=$TIGRIS_BUCKET \
#              AWS_ACCESS_KEY_ID=$TIGRIS_KEY_ID AWS_SECRET_ACCESS_KEY=$TIGRIS_SECRET  # for --live
#   • python3 with pyarrow + duckdb (only for the --live regen step)
#   • envsubst (gettext) — substitutes the stage credentials without writing them to a committed file
#
# Flags:
#   --offline   skip the --live regen; load the committed sql/ as-is (v1 single-file assumptions)
#   --dry-run   regenerate + print the plan, run nothing against Snowflake
set -euo pipefail
cd "$(dirname "$0")"

REGEN_LIVE=1 ; DRY=0
for a in "$@"; do
  case "$a" in
    --offline) REGEN_LIVE=0 ;;
    --dry-run) DRY=1 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done

: "${TIGRIS_BUCKET:?set TIGRIS_BUCKET}" ; : "${TIGRIS_KEY_ID:?set TIGRIS_KEY_ID}" ; : "${TIGRIS_SECRET:?set TIGRIS_SECRET}"
export TIGRIS_PREFIX="${TIGRIS_PREFIX:-warehouse}"
export TIGRIS_ENDPOINT="${TIGRIS_ENDPOINT:-fly.storage.tigris.dev}"

# 1) Regenerate from the ACTUAL warehouse: present tables only, bucketed→active parts, row counts.
if [ "$REGEN_LIVE" = 1 ]; then
  echo "→ regenerating build from the live warehouse (--live)…"
  python3 build_snowflake_sql.py --live
else
  echo "→ using the committed offline build (sql/*.sql)"
fi

if [ "$DRY" = 1 ]; then
  echo "→ dry run: build is staged in sql/. Nothing run against Snowflake."
  exit 0
fi

: "${SNOW_CONN:?set SNOW_CONN to a snowsql named connection (see ~/.snowsql/config)}"
run() { echo "→ $1"; snowsql -c "$SNOW_CONN" -o exit_on_error=true -o friendly=false -f "$1"; }

# 2) Setup + stage (creds substituted in-memory; never written to disk).
run sql/00_config.template.sql
run sql/01_database.sql
echo "→ sql/02_stage.sql (credentials via envsubst)"
# restrict substitution to exactly our tokens, so nothing else in the SQL is touched
envsubst '$TIGRIS_BUCKET $TIGRIS_PREFIX $TIGRIS_ENDPOINT $TIGRIS_KEY_ID $TIGRIS_SECRET' \
  < sql/02_stage.sql | snowsql -c "$SNOW_CONN" -o exit_on_error=true -o friendly=false

# 3) The load. RAW catalogs full-refresh; time-series appends; master rebuilt; marts; validate.
run sql/03_raw_tables.sql
run sql/04_master.sql
run sql/05_marts.sql
run sql/06_validate.sql

echo "✓ morning drop complete — see the 06_validate output above for per-source row counts."
