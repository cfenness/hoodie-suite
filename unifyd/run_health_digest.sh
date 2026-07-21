#!/bin/bash
# run_health_digest.sh — the daily data-health pass (launchd: com.hoodie.health, 07:30, after the 03:00 sources run).
#
# Layers, in order of trust:
#   1. health_digest.py  — DETERMINISTIC verdict (the only layer that decides pass/fail; exit 2=critical, 1=warn)
#   2. claude -p triage  — OPTIONAL judgment on top (is this delta a promo-week artifact or a broken selector?);
#                          writes latest_triage.md, NEVER changes the verdict. Disable with HEALTH_TRIAGE=0.
#   3. macOS notification when anything is critical (always) or newly-warn (so a known warn doesn't nag daily).
#
# Env: same sourcing as .claude/launch.json — warehouse.env (production S3 creds) + repo .env + unifyd/.env
# (scraper creds like KROGER_COOKIE, so `requires=` checks reflect what the real runs see).
set -u

SUITE="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-suite"
BACKEND="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend"
PY="$BACKEND/venv/bin/python"
HEALTH_DIR="$SUITE/unifyd/agent_state/health"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"

cd "$SUITE/unifyd" || exit 1
set -a
. "$BACKEND/warehouse.env" 2>/dev/null
. "$SUITE/.env" 2>/dev/null
. ./.env 2>/dev/null
set +a

# ── 1. the deterministic digest (writes agent_state/health/latest.{json,txt} + a dated copy) ──────────────────
"$PY" health_digest.py
VERDICT=$?    # 0 clear · 1 warn · 2 critical
echo "[health] digest exit=$VERDICT"

# ── 2. optional Claude triage — judgment layer, bounded, never blocks the verdict ─────────────────────────────
if [ "$VERDICT" -ne 0 ] && [ "${HEALTH_TRIAGE:-1}" = "1" ] && [ -x "$CLAUDE_BIN" ]; then
  (
    "$CLAUDE_BIN" -p "You are triaging the Hoodie data-health digest (JSON on stdin). Every finding in it is \
deterministic and already verified — do NOT re-litigate the numbers. For each critical/warn finding, give a \
one-line triage: most likely cause (broken selector / anti-bot block / creds expired / launchd not firing / \
expected seasonal move) and the single next diagnostic step. Lead with the one finding to fix first. \
Under 25 lines, plain text." \
      < "$HEALTH_DIR/latest.json" > "$HEALTH_DIR/latest_triage.md" 2>/dev/null
  ) &
  TRIAGE_PID=$!
  # bound it (no coreutils timeout on stock macOS): give it 4 minutes, then move on
  for _ in $(seq 1 48); do kill -0 "$TRIAGE_PID" 2>/dev/null || break; sleep 5; done
  kill "$TRIAGE_PID" 2>/dev/null
  wait "$TRIAGE_PID" 2>/dev/null
  [ -s "$HEALTH_DIR/latest_triage.md" ] && echo "[health] triage written -> latest_triage.md"
fi

# ── 3. notify — critical always; warn only when something NEW appeared (no daily nagging on known warns) ──────
if [ "$VERDICT" -ne 0 ]; then
  SUMMARY=$("$PY" - <<'EOF'
import json, os
d = json.load(open(os.path.join("agent_state", "health", "latest.json")))
c = d["counts"]
new = sum(1 for f in d["findings"] if f.get("new") and f["severity"] != "info")
top = next((f["summary"] for f in d["findings"] if f["severity"] == "critical"),
           next((f["summary"] for f in d["findings"] if f["severity"] == "warn"), ""))
print("%d|%d critical, %d warn (%d new) — %s" % (new, c["critical"], c["warn"], new, top[:120]))
EOF
)
  NEW="${SUMMARY%%|*}"; MSG="${SUMMARY#*|}"
  if [ "$VERDICT" -eq 2 ] || [ "$NEW" != "0" ]; then
    osascript -e "display notification \"$MSG\" with title \"Hoodie data health\" sound name \"Basso\"" 2>/dev/null
  fi
  echo "[health] $MSG"
else
  echo "[health] all clear"
fi

exit "$VERDICT"
