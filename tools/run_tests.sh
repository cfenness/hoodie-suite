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
  run_stream_test              # the live console must actually stream, not dump at exit
  self_report_test             # a run is graded against its JOB, never against its own watermark
  taxonomy_test                # the retailer's own category tree is captured, not discarded
  journal_metrics_test         # the journal keeps every metric a run reports about itself
  raw_capture_test             # payloads kept in full, kept OUT of the whole-table merge path
  idset_test                   # full-universe membership stays bounded (8 bytes/id, not 1GB)
  coverage_guard_test          # a throttled 200 must never count as a covered store
  pace_test                    # pace by RATE, back off hard, creep up slow
  session_rotate_test          # the limit is a per-session QUOTA — re-prime on a request budget
  blocks_test                  # name WHY a fetch failed; a closed store is not a block
  sessions_test                # session budget is per-source policy, learned from real burns
  extract_qa_test              # validate the OUTPUT — a 200 that is wrong is worse than a 403
  value_rules_test             # plausible-but-wrong values: right type, wrong number
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

# A curated list means a new test file can sit there never running — indistinguishable from a test that
# passes. Name the unlisted ones out loud rather than letting the suite quietly under-report itself.
if [ -z "$FILTER" ]; then
  unlisted=()
  for f in unifyd/*_test.py; do
    n=$(basename "$f" .py)
    printf '%s\n' "${TESTS[@]}" | grep -qx "$n" || unlisted+=("$n")
  done
  [ ${#unlisted[@]} -eq 0 ] || printf "\n   note: %d test file(s) not in the suite list: %s\n" \
    "${#unlisted[@]}" "${unlisted[*]}"
fi

echo
echo "── $pass passed, $fail failed"
[ $fail -eq 0 ] || { echo "   failed: ${failed[*]}"; exit 1; }
