"""When a rung closes, take the next one — automatically, and remember it.

On 2026-07-29 UberEats stopped accepting the cold curl_cffi path. The system had every sense organ and
no reflex: `blocks` named the failure (CAPTCHA, 82% then 100%), `pace` backed off, `sessions` rotated —
and none of it could TRY SOMETHING ELSE. A human spent twelve hours fitting four models to a blended
signal while a known-good browser path sat unused in the same repository.

The lesson is not "tune better". Every constant tuned that day was wrong within hours, because a rate
limit, a session quota and a fingerprint policy all belong to someone else and all move. The durable
answer is a system that treats its access method as disposable, notices when one closes, moves, and
writes down what it learned.

Four properties, each earned from a specific failure:
  * escalate on CLASSIFIED BLOCK only — a dead-store background must never promote a source
  * persist the choice — a rung found at 3am is worthless if the next process re-learns it
  * decay back down — one bad afternoon must not permanently double a source's cost
  * never auto-escalate into SPEND — a system that can promote itself into a meter eventually will

Pure state machine — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocks as B  # noqa: E402
import ladder as L  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


def feed(src, cls, n):
    for _ in range(n):
        L.report(src, cls)


# ── the event: a rung closes, and the system moves without being told ────────────────────────────
L.reset()
check("starts on the declared rung", L.current("s1", default=L.IMPERSONATE), L.IMPERSONATE)
feed("s1", B.CAPTCHA, L.WINDOW)
check("a CAPTCHA storm escalates", L.current("s1"), L.BROWSER)
check("the move is recorded", len(L._L["s1"].stats()["moves"]), 1)
check("...with the evidence", L._L["s1"].stats()["moves"][0]["from"], L.IMPERSONATE)

# soft blocks are the quieter version of the same event and must also escalate
L.reset(); L.current("s2", default=L.IMPERSONATE)
feed("s2", B.SOFT_BLOCK, L.WINDOW)
check("hollow 200s escalate too", L.current("s2"), L.BROWSER)

# ── what must NOT move a source ──────────────────────────────────────────────────────────────────
L.reset(); L.current("s3", default=L.IMPERSONATE)
feed("s3", B.NOT_FOUND, L.WINDOW * 5)
check("closed stores never escalate", L.current("s3"), L.IMPERSONATE)
feed("s3", B.TIMEOUT, L.WINDOW * 5)
check("timeouts never escalate", L.current("s3"), L.IMPERSONATE)
feed("s3", B.EMPTY, L.WINDOW * 5)
check("real-but-empty never escalates", L.current("s3"), L.IMPERSONATE)
L.reset(); L.current("s4", default=L.IMPERSONATE)
feed("s4", B.OK, L.WINDOW * 5)
check("success never escalates", L.current("s4"), L.IMPERSONATE)

# a MINORITY of blocks is not a closed rung — otherwise every source ends up on a browser
L.reset(); L.current("s5", default=L.IMPERSONATE)
for _ in range(L.WINDOW * 2):
    L.report("s5", B.CAPTCHA if (_ % 5 == 0) else B.OK)      # 20% blocked
check("20% blocked does not escalate", L.current("s5"), L.IMPERSONATE)

# ── never climb into spend on its own ────────────────────────────────────────────────────────────
L.reset(); L.current("s6", default=L.BROWSER)
feed("s6", B.CAPTCHA, L.WINDOW * 3)
check("stops at the last free rung", L.current("s6"), L.BROWSER)
check("PAID is not in the allowed set", L.PAID in L.allowed_rungs(), False)
check("free rungs are ordered cheap-first", L.FREE_RUNGS[0], L.DIRECT)

# ── the ladder order is cheap/fast first ─────────────────────────────────────────────────────────
check("browser sits above impersonate", L.RUNGS.index(L.BROWSER) > L.RUNGS.index(L.IMPERSONATE), True)
check("impersonate above mobile", L.RUNGS.index(L.IMPERSONATE) > L.RUNGS.index(L.MOBILE), True)
check("paid is last", L.RUNGS[-1], L.PAID)

# ── escalation is stepwise, not a leap to the top ────────────────────────────────────────────────
L.reset(); L.current("s7", default=L.DIRECT)
feed("s7", B.HTTP_BLOCK, L.WINDOW)
check("one step at a time", L.current("s7"), L.MOBILE)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- ladder: a closed rung escalates itself, a closed store does not (17 checks)")
