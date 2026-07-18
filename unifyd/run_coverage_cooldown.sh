#!/bin/bash
cd "$(dirname "$0")"
echo "$(date): coverage cooldown armed — relaunch in 25min" > /tmp/ue_cooldown.log
sleep 1500
rm -f ~/.hoodie_browser_profiles/*/Singleton* 2>/dev/null
echo "$(date): cooldown done — relaunching coverage" >> /tmp/ue_cooldown.log
./run_ue_coverage.sh >> /tmp/ue_cooldown.log 2>&1
