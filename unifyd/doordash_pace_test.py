#!/usr/bin/env python3
"""doordash_pace_test.py — DoorDash gets the same self-tuning AIMD rate control UberEats already proved.

Grounded in a real, documented UberEats incident (pace.py's own docstring): 120 workers spread over 20
ISP IPs collapsed at ~59 req/s aggregate, with most responses empty — proof the ceiling there is the
target's own aggregate REQUEST RATE, not IP count or worker count. DoorDash never had any adaptive rate
control at all (just DDFULL_WORKERS + a flat sleep), and its own safe rate has never been measured. This
ports the exact mechanism (pace.py) DoorDash never used, instead of guessing another worker-count
constant.

No real network call or real timing delay — pace.Pacer's acquire() is exercised directly against a fast
rate so tests run in milliseconds, and _fetch()'s pacer integration is tested via a stub session/pacer
object, same discipline as the rest of this suite.

    python3 unifyd/doordash_pace_test.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "curl_cffi" not in sys.modules:
    import types
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


def stub(real, value):
    """A test double that CANNOT drift from the function it stands in for.

    The break this closes: `all_stores` grew a `metro=` kwarg (#744, metro-area radius scoping) and
    this file's hand-written `lambda log=print: {}` did not, so a production change with nothing to
    do with pacing turned this suite red and it stayed red. The lazy repair is `lambda *a, **k:` —
    which is WORSE than the bug it fixes: it swallows the drift, and it equally swallows `run()`
    passing a kwarg the real function never accepted, which is a genuine break this test should
    catch. Binding the real signature keeps exactly one of those two red: the stub tracks whatever
    production grows, and a call the real function would reject still raises TypeError here.
    """
    sig = inspect.signature(real)

    def _stub(*a, **k):
        sig.bind(*a, **k)            # raises precisely when the REAL function would
        return value
    return _stub


class FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self):
        self.proxies = {"https": "user:pass@9.9.9.9:8080"}
        self._queue = []

    def queue(self, resp):
        self._queue.append(resp)

    def get(self, url):
        return self._queue.pop(0)


def main():
    for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
        os.environ.pop(v, None)
    import tempfile
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="dd_pace_")

    import doordash as dd
    import doordash_chains as ddc
    import pace

    print("doordash_chains.run() installs a pacer — DoorDash had NONE before this")
    real_pace_install = pace.install
    installed = {}

    def spy_install(rate, max_rate=None):
        installed["rate"] = rate
        installed["max_rate"] = max_rate
        return real_pace_install(rate, max_rate=max_rate)

    pace.install = spy_install
    real_all_stores = ddc.all_stores
    real_landed = ddc._landed_stores
    real_dd_run = ddc.doordash_full.run
    ddc.all_stores = stub(real_all_stores, {})
    ddc._landed_stores = stub(real_landed, set())
    ddc.doordash_full.run = stub(real_dd_run, ("run", 0))
    try:
        os.environ["DD_FLEET_RATE"] = "30"
        ddc.run(log=lambda *a: None)
        check("a pacer was installed at all", "rate" in installed, installed)
        check("single-shard run gets the FULL fleet rate (30), not a fraction",
              installed.get("rate") == 30.0, installed)
        check("an explicit max_rate is passed — NOT left to Pacer's own rate*4 default, which "
              "measured live converged with zero backoffs (the ceiling was the multiplier, not "
              "DoorDash pushing back)", installed.get("max_rate") == 500.0, installed)

        installed.clear()
        ddc.run(shard=0, nshard=3, log=lambda *a: None)
        check("a 3-shard run gets a THIRD of the fleet rate (10), splitting the same budget "
              "UberEats' shards already split", installed.get("rate") == 10.0, installed)

        installed.clear()
        os.environ["DD_FLEET_MAX_RATE"] = "50"
        ddc.run(log=lambda *a: None)
        check("DD_FLEET_MAX_RATE overrides the default cap", installed.get("max_rate") == 50.0, installed)
    finally:
        os.environ.pop("DD_FLEET_RATE", None)
        os.environ.pop("DD_FLEET_MAX_RATE", None)
        pace.install = real_pace_install
        ddc.all_stores = real_all_stores
        ddc._landed_stores = real_landed
        ddc.doordash_full.run = real_dd_run

    print("\n_fetch() consults an installed pacer: acquires before each attempt, reports the outcome")
    p = pace.install(1000.0)   # fast enough that acquire() never actually blocks in a test
    acquires, reports = [], []
    real_acquire, real_report = p.acquire, p.report
    p.acquire = lambda: (acquires.append(1), real_acquire())[-1]
    p.report = lambda ok: (reports.append(ok), real_report(ok))[-1]
    try:
        s = FakeSession()
        s.queue(FakeResp(200, '"__next_f" some real payload'))
        html = dd._fetch("https://www.doordash.com/x", session=s)
        check("the fetch still returns the real payload (pacing must never break a fetch)",
              "__next_f" in html, html)
        check("acquire() was called before the successful attempt", len(acquires) == 1, acquires)
        check("a successful (OK) attempt reports ok=True", reports == [True], reports)

        acquires.clear(); reports.clear()
        s2 = FakeSession()
        for _ in range(4):
            s2.queue(FakeResp(429, "rate limited"))
        real_sleep = dd.time.sleep
        dd.time.sleep = lambda s: None   # skip the real backoff sleep between retries
        try:
            dd._fetch("https://www.doordash.com/y", session=s2, retries=4)
        finally:
            dd.time.sleep = real_sleep
        check("acquire() was called once per retry attempt (4)", len(acquires) == 4, acquires)
        check("every failed/throttled attempt reports ok=False", reports == [False, False, False, False],
              reports)
    finally:
        pace.install(0.5)   # reset to a harmless idle pacer so later tests aren't affected

    print("\nwithout an installed pacer, _fetch() works exactly as before (pacing is opt-in)")
    pace._GLOBAL["pacer"] = None
    try:
        s3 = FakeSession()
        s3.queue(FakeResp(200, '"__next_f" payload'))
        html2 = dd._fetch("https://www.doordash.com/z", session=s3)
        check("fetch succeeds with no pacer installed at all", "__next_f" in html2, html2)
    except Exception as e:
        check("fetch succeeds with no pacer installed at all", False, str(e))

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
