"""The ladder must have somewhere to escalate TO.

Today's failure in one line: the system correctly decided the cold path was closed, and had nothing
behind that decision. A ladder whose upper rung is unimplemented is a ladder that reports a plan instead
of executing one — which is the same class of problem as a status that reports health instead of health.

browser_warm already solved the hard part, and names this adversary in its own docstring: PerimeterX
binds _px3 to the TLS FINGERPRINT, so a requests/curl_cffi session carrying a warmed cookie gets
re-challenged. Fetching from INSIDE the page inherits the real fingerprint and the auto-refreshing token
together, so there is nothing left to re-challenge.

These are wiring assertions, not network tests — they prove the rung is connected, that a browser is not
launched for sources that do not need one, and that the ladder is fed the verdict that would move it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocks  # noqa: E402
import getstore  # noqa: E402
import ladder  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "getstore.py")).read()

# ── the rung exists and is reachable from the fetch path ─────────────────────────────────────────
check("browser fetcher exists", hasattr(getstore, "_fetch_browser"), True)
check("browser factory exists", hasattr(getstore, "_browser"), True)
check("fetch routes on the ladder", 'rung == "browser"' in SRC, True)
check("uses the in-page POST", "post_json" in SRC, True)

# ── the loop is closed: outcomes feed the thing that decides the rung ────────────────────────────
check("ladder is fed the verdict", "ladder.report(site, cls)" in SRC, True)
check("sessions still fed too", "sessions.policy_for(site).report" in SRC, True)
check("pacer still fed too", "_p.report(not blocks.is_throttle(cls))" in SRC, True)

# ── cost discipline: one browser per PROCESS, and only on demand ─────────────────────────────────
check("single shared browser", "_BR = {" in SRC, True)
check("guarded by a lock", "_BR_LOCK" in SRC, True)
# A browser must never be constructed merely by importing or by fetching on a cheap rung.
check("not launched at import", getstore._BR["w"], None)
check("residential exit pinned", "resi.isp_pool()" in SRC.split("def _browser")[1][:800], True)

# ── and the ladder actually WOULD escalate on today's signal ─────────────────────────────────────
ladder.reset()
ladder.current("t-src", default=ladder.IMPERSONATE)
# Costumes are tried first (a free TLS profile change), so reaching the browser takes several rounds.
for _ in range(ladder.WINDOW * (ladder.MAX_COSTUMES + 1)):
    ladder.report("t-src", blocks.CAPTCHA)
check("a persistent block eventually reaches browser", ladder.current("t-src"), ladder.BROWSER)
# ...and that a healthy source is never sent to one
ladder.reset()
ladder.current("ok-src", default=ladder.IMPERSONATE)
for _ in range(ladder.WINDOW * 3):
    ladder.report("ok-src", blocks.OK)
check("healthy source never gets a browser", ladder.current("ok-src"), ladder.IMPERSONATE)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- browser rung: the ladder has somewhere to go, and only goes when pushed (13 checks)")
