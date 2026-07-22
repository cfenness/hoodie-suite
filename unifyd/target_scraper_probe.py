#!/usr/bin/env python3
"""target_scraper_probe.py — verify the FIXED target_scraper lands data FREE on a cloud runner.

Not the raw API (that's target_redsky_browser_probe) — this drives the real scraper: target_scraper.
nearby_stores() → _get() (browser-first, no proxy) → parse. FETCH_POLICY=free means no proxy tier is
even available, so if this returns stores, the shipped scraper works free from a datacenter. Exit 0 iff
it returned stores."""
import os
import sys

os.environ.setdefault("FETCH_POLICY", "free")        # prove it with NO proxy tier available at all
os.environ.setdefault("BROWSER_CHANNEL", "chrome")

import resi                                           # noqa: E402
import target_scraper as t                            # noqa: E402

print("FETCH_POLICY=%s  isp_pool=%d  paygo_allowed=%s  (no proxy tier available)" % (
    resi.fetch_policy(), len(resi.isp_pool()), resi.paygo_allowed()))
try:
    stores = t.nearby_stores("10001")                # real scraper call: RedSky nearby_stores via _get -> browser
except Exception as e:
    print("SCRAPER ERROR:", str(e)[:200])
    sys.exit(1)
print("target_scraper.nearby_stores('10001') -> %d stores" % len(stores))
if stores:
    s = stores[0]
    print("  e.g. #%s %s — %s, %s" % (s.get("store_id"), s.get("name"), s.get("city"), s.get("state")))
    print("\nVERDICT: FIXED SCRAPER LANDS TARGET FREE — datacenter runner, FETCH_POLICY=free, no proxy.")
    sys.exit(0)
print("\nVERDICT: scraper returned 0 — investigate (browser launch? parse?).")
sys.exit(1)
