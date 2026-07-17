#!/usr/bin/env python3
"""sevennow_warm.py — pull 7NOW with NO BD and NO cookie hand-off. Incapsula's reese84 is TLS-fingerprint-bound
(a valid cookie replayed over urllib still 403s — same lesson as PerimeterX/Cloudflare), so we do the API calls
IN-PAGE through browser_warm (real Chrome, Mac residential IP): the fetch inherits the browser's trusted session
+ TLS. A BrowserFetcher (same .get(url)->json interface as sevennow.Fetcher) is monkeypatched into the built
scraper, so all its parsing/landing is reused. This is the durable Mac-side template (UberEats-style in-page)."""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kroger_api; kroger_api._load_creds()
import browser_warm
import sevennow


class BrowserFetcher:
    """Drop-in for sevennow.Fetcher, but fetches THROUGH the warmed browser (in-page), not urllib."""
    Blocked = sevennow.Fetcher.Blocked

    def __init__(self, warmer):
        self.w = warmer

    def get(self, url, timeout=25):
        st, obj = self.w.fetch(url, as_json=True, timeout_ms=timeout * 1000)
        if st != 200 or obj is None:
            raise BrowserFetcher.Blocked("in-page fetch %s -> %s" % (st, url[:70]))
        return obj


def main(argv=None):
    with browser_warm.Warmer("7now.com", channel="chrome", headful=True, patchright=True) as w:
        p = w.prime("https://www.7now.com/", settle_ms=8000,
                    challenge_gone="() => !/Incapsula|_Incapsula_|Request unsuccessful|Pardon the/i"
                                   ".test(document.body ? document.body.innerText : '')")
        body = p.evaluate("() => document.body ? document.body.innerText.slice(0,100) : ''") or ""
        if re.search(r"Incapsula|Request unsuccessful|Pardon the", body, re.I):
            print("[7now] still challenged after warm: %r — aborting" % body[:80]); return 1
        print("[7now] warmed (in-page fetch mode). page: %r" % body[:60])
        sevennow.Fetcher = lambda *a, **k: BrowserFetcher(w)   # route all fetches through the browser
        snap = sevennow.run("browser-backed", land=True)       # non-empty arg passes run()'s guard
    print("[7now] full pull: %d store-cells landed" % (len(snap) if snap else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
