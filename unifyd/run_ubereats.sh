#!/bin/bash
# Daily UberEats alcohol pull — Mac residential IP, headful real Chrome (the no-BD path). Run by launchd.
cd "/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-suite/unifyd" || exit 1
rm -f ~/.hoodie_browser_profiles/ubereats_com_chrome/Singleton* 2>/dev/null
PY="/Users/chrisfennessey/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/venv/bin/python"
echo "=== ubereats daily $(date) ==="
"$PY" -c "
import sys; sys.path.insert(0,'.')
import kroger_api; kroger_api._load_creds()
import ubereats
ubereats.main(['--max-stores','12'])
"
echo "=== done $(date) ==="
