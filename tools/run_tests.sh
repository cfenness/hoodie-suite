#!/usr/bin/env bash
# run_tests.sh — the engine's test suite, run HERE instead of on GitHub Actions.
#
# Actions is a metered service this project does not buy, so `.github/workflows/tests.yml` was deleted:
# every PR touching warehouse/run_sources/source_registry/server queued a job the account has no
# minutes for, and the only output was a red ✗ that meant "no budget", not "broken code". A CI signal
# that always fails teaches you to ignore CI. These are the exact tests it used to run.
#
#   ./tools/run_tests.sh            # all of them
#   ./tools/run_tests.sh warehouse  # only tests whose name matches
#
# Needs pyarrow + duckdb. Set PYBIN to pick an interpreter that has them.
set -uo pipefail
cd "$(dirname "$0")/.."

PYBIN="${PYBIN:-python3}"
if ! "$PYBIN" -c "import pyarrow, duckdb" 2>/dev/null; then
  for c in "$HOME/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python" \
           ./venv/bin/python ./.venv/bin/python; do
    if [ -x "$c" ] && "$c" -c "import pyarrow, duckdb" 2>/dev/null; then PYBIN="$c"; break; fi
  done
fi
"$PYBIN" -c "import pyarrow, duckdb" 2>/dev/null || {
  echo "!! $PYBIN lacks pyarrow/duckdb — set PYBIN=/path/to/python"; exit 2; }

TESTS=(
  warehouse_compat_test        # storage contract — v1/v2 layouts stay readable
  warehouse_rowcount_test      # row counts must SEE partitioned tables (landing verification)
  run_sources_due_test         # due-ness / scheduling
  dispatch_guard_test          # /api/run must dispatch through source_registry, never a drifted copy
  abc_fws_test                 # ABC: batch landing, resume, partial-vs-drift completeness
  selfheal_classes_test        # failure classes keep their point of view (structural, not prose-matched)
  sipsource_test
  cost_ledger_test
  obs_quality_test
  velocity_test
  velocity_calibrate_test
  velocity_signals_test
  master_quality_test
  representativeness_test
)

FILTER="${1:-}"
pass=0; fail=0; failed=()
for t in "${TESTS[@]}"; do
  [ -n "$FILTER" ] && [[ "$t" != *"$FILTER"* ]] && continue
  [ -f "unifyd/$t.py" ] || { printf "%-30s SKIP (missing)\n" "$t"; continue; }
  printf "%-30s " "$t"
  if out=$("$PYBIN" "unifyd/$t.py" 2>&1); then
    echo "PASS"; pass=$((pass+1))
  else
    echo "FAIL"; echo "$out" | tail -8 | sed 's/^/    /'; fail=$((fail+1)); failed+=("$t")
  fi
done

echo
echo "── $pass passed, $fail failed"
[ $fail -eq 0 ] || { echo "   failed: ${failed[*]}"; exit 1; }
