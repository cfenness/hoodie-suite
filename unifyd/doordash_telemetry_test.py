#!/usr/bin/env python3
"""doordash_telemetry_test.py — _fetch() must record what actually happened, not just eyeball log lines.

Grounded in a real incident: a doordash_chains.py run looked "stuck" (0 stores completed after 75+
minutes), but doordash_full.py/doordash.py carried no per-request telemetry — no way to tell "every
fetch is timing out" from "these category trees are just deep" short of doing arithmetic on log-line
timestamps by hand. Fixed by instrumenting _fetch() with the same blocks.classify()/Tally the UberEats
path already uses.

These tests fake the HTTP layer (a stand-in `session.get()`) so no real network call happens — same
discipline as the rest of this test suite.

    python3 unifyd/doordash_telemetry_test.py
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# _fetch() imports curl_cffi unconditionally (a compiled dependency only present in the deployed
# container, not this local test environment) even on the session-reuse path, which never touches it.
# Stub it so the retry/classify logic under test actually runs locally instead of short-circuiting on
# ImportError before the first attempt.
if "curl_cffi" not in sys.modules:
    fake_curl_cffi = types.ModuleType("curl_cffi")
    fake_curl_cffi.requests = types.ModuleType("curl_cffi.requests")
    sys.modules["curl_cffi"] = fake_curl_cffi
    sys.modules["curl_cffi.requests"] = fake_curl_cffi.requests

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


class FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """`.get()` returns whatever the test queues next — same shape curl_cffi's Session.get returns."""
    def __init__(self, proxies=None):
        self.proxies = proxies or {"https": "user:pass@9.9.9.9:8080"}
        self._queue = []
        self.calls = 0

    def queue(self, resp_or_exc):
        self._queue.append(resp_or_exc)

    def get(self, url):
        self.calls += 1
        item = self._queue.pop(0) if self._queue else FakeResp(200, '"__next_f":[1,"ok"]')
        if isinstance(item, Exception):
            raise item
        return item


OK_BODY = '"__next_f":[1,"data"]'          # has_payload True -> OK (has_items left unknown)


def main():
    print("_fetch() records real outcomes into an installed tally")
    import doordash as dd
    import blocks

    real_sleep = dd.time.sleep
    dd.time.sleep = lambda _s: None    # the retry backoff is real seconds in production; skip it here
    try:
        # --- a clean 200 records OK, no retry needed --------------------------------------------
        tally = blocks.install()
        s = FakeSession()
        s.queue(FakeResp(200, OK_BODY))
        html = dd._fetch("https://www.doordash.com/x", session=s)
        check("a healthy fetch returns the body", html == OK_BODY)
        check("one attempt only — no retry on success", s.calls == 1)
        check("it's recorded as isp.ok", tally.flat().get("isp.ok") == 1, tally.flat())

        # --- a 403 (no captcha markup) records http_block, retries, then succeeds ----------------
        tally = blocks.install()
        s = FakeSession()
        s.queue(FakeResp(403, "forbidden, plain"))
        s.queue(FakeResp(200, OK_BODY))
        html = dd._fetch("https://www.doordash.com/x", session=s)
        check("recovers on retry after a block", html == OK_BODY)
        check("the block attempt is recorded as isp.http_block",
              tally.flat().get("isp.http_block") == 1, tally.flat())
        check("the recovering attempt is recorded as isp.ok", tally.flat().get("isp.ok") == 1, tally.flat())

        # --- a captcha interstitial is classified as captcha, not a generic block ----------------
        tally = blocks.install()
        s = FakeSession()
        s.queue(FakeResp(200, "please verify you are human"))
        s.queue(FakeResp(200, OK_BODY))
        dd._fetch("https://www.doordash.com/x", session=s)
        check("a captcha interstitial is classified as captcha, not silently OK",
              tally.flat().get("isp.captcha") == 1, tally.flat())

        # --- a timeout exception is classified as timeout, not unknown --------------------------
        tally = blocks.install()
        s = FakeSession()
        s.queue(TimeoutError("Connection timed out after 60000ms"))
        s.queue(FakeResp(200, OK_BODY))
        dd._fetch("https://www.doordash.com/x", session=s)
        check("a timeout exception is classified as isp.timeout, not lumped into isp.unknown",
              tally.flat().get("isp.timeout") == 1, tally.flat())

        # --- exhausting all retries records every attempt, returns empty ------------------------
        tally = blocks.install()
        s = FakeSession()
        for _ in range(4):
            s.queue(FakeResp(429, "slow down"))
        html = dd._fetch("https://www.doordash.com/x", retries=4, session=s)
        check("returns '' after exhausting retries", html == "")
        check("every failed attempt is recorded (4 rate_limited)",
              tally.flat().get("isp.rate_limited") == 4, tally.flat())

        # --- no installed tally must never break a fetch -----------------------------------------
        import blocks as B
        B._GLOBAL["tally"] = None
        s = FakeSession()
        s.queue(FakeResp(200, OK_BODY))
        html = dd._fetch("https://www.doordash.com/x", session=s)
        check("with no tally installed, the fetch still works (telemetry is opt-in, not required)",
              html == OK_BODY)

        # --- the REAL bug, live 2026-07-30: full_catalog()'s ~166 fetches/store all go through
        # _unlock()/search_store(), not _fetch() directly. The tally still filled up correctly (record()
        # doesn't need log), but _unlock/search_store silently dropped `log` before this fix, so the
        # heartbeat and slow-fetch warnings — gated on `log` being callable — never fired: 0
        # HOODIE_DD_PROGRESS lines after 4+ minutes of real, healthy crawling. Assert the forward
        # actually happens, not just that the tally fills up (that part already passed before the fix).
        blocks.install()
        s = FakeSession()
        s.queue(FakeResp(200, OK_BODY))
        seen = []
        dd._last_beat[0] = 0.0    # past the 60s gate, so a forwarded log fires immediately
        dd._unlock("https://www.doordash.com/x", session=s, log=seen.append)
        check("_unlock() forwards log through to _fetch, so the heartbeat can actually fire",
              len(seen) == 1 and "HOODIE_DD_PROGRESS" in seen[0], seen)

        blocks.install()
        s = FakeSession()
        s.queue(FakeResp(200, OK_BODY))
        seen = []
        dd._last_beat[0] = 0.0
        dd.search_store("123", "wine", session=s, log=seen.append)
        check("search_store() forwards log through to _fetch too",
              len(seen) == 1 and "HOODIE_DD_PROGRESS" in seen[0], seen)
    finally:
        dd.time.sleep = real_sleep

    print("\n_beat() heartbeat is time-gated, not per-request")
    tally = blocks.install()
    logged = []
    dd._last_beat[0] = 0.0
    dd._beat(logged.append, tally)
    check("first call past the gate (last_beat=0) heartbeats", len(logged) == 1, logged)
    dd._beat(logged.append, tally)
    check("an immediate second call does NOT heartbeat again (60s gate)", len(logged) == 1, logged)
    dd._last_beat[0] = time.time() - 61
    dd._beat(logged.append, tally)
    check("once 60s has passed, it heartbeats again", len(logged) == 2, logged)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
