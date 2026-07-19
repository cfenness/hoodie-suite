#!/bin/bash
# Geofill the FULL UberEats + Postmates universe onto the map from the store-page JSON-LD (exact geo + address),
# DIRECT home IP — no proxy, no Census guessing. Resumable (skips uuids already ok/gone), so safe to re-run after
# a stop or a block wave. refresh_fast (5-min loop) folds <site>_geo into src_outlets as it lands. Log: /tmp/ue_geofill.log
cd "$(dirname "$0")"
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"; [ -x "$PY" ] || PY=python3
WORKERS="${1:-4}"
for site in ubereats postmates; do
  echo "=== geofill $site @ $(date) ===" >> /tmp/ue_geofill.log
  # loop until a pass finishes with no remaining blocked (resume shrinks the target each pass)
  for pass in 1 2 3; do
    "$PY" -u -c "import sys; sys.path.insert(0,'.'); import kroger_api; kroger_api._load_creds(); \
import ue_geofill; ue_geofill.geofill('$site', workers=$WORKERS)" >> /tmp/ue_geofill.log 2>&1
    sleep 30
  done
done
echo "=== geofill ALL DONE @ $(date) ===" >> /tmp/ue_geofill.log
