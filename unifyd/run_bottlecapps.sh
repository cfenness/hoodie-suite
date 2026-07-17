#!/bin/bash
# Full-capture Bottlecapps national sweep — Mac residential IP, headful patchright (no Bright Data for the pull).
cd "/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-suite/unifyd" || exit 1
pkill -9 -f "hoodie_browser_profiles" 2>/dev/null; sleep 2
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
echo "=== bottlecapps national $(date) ==="
"$PY" -c "
import sys; sys.path.insert(0,'.')
import kroger_api; kroger_api._load_creds()
import bottlecapps
bottlecapps.national()
"
echo "=== done $(date) ==="
