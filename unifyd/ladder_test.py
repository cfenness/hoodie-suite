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
# COSTUMES FIRST. A TLS profile change is free; the browser rung costs 10-50x throughput and a Chromium
# per process. Today proved the cheap move was the entire answer — UberEats blocked the desktop-Chrome
# family and every other profile worked — so the ladder must exhaust costumes before it escalates.
feed("s1", B.CAPTCHA, L.WINDOW)
check("first block rotates the costume, not the rung", L.current("s1"), L.IMPERSONATE)
check("costume was tried", L._L["s1"]._costumes, 1)
feed("s1", B.CAPTCHA, L.WINDOW * L.MAX_COSTUMES)
check("only after costumes run out does it escalate", L.current("s1"), L.BROWSER)
check("costumes are bounded", L._L["s1"]._costumes, L.MAX_COSTUMES)
# History records BOTH costume changes and rung moves — the costume entries are the cheap attempts
# that preceded the expensive one, and they are exactly what you want in the record when asking later
# "what did we try before we started paying for browsers?".
_mv = L._L["s1"].stats()["moves"]
check("attempts are recorded", len(_mv) >= 1, True)
check("...with the evidence", _mv[0]["from"], L.IMPERSONATE)
check("costume attempts are visible", any("costume" in str(m["to"]) for m in _mv), True)
check("and the rung move too", any(m["to"] == L.BROWSER for m in _mv), True)

# soft blocks are the quieter version of the same event and must also escalate
L.reset(); L.current("s2", default=L.IMPERSONATE)
feed("s2", B.SOFT_BLOCK, L.WINDOW * (L.MAX_COSTUMES + 1))
check("hollow 200s escalate too, after costumes", L.current("s2"), L.BROWSER)

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
# from DIRECT there is no costume to change, so it steps immediately
feed("s7", B.HTTP_BLOCK, L.WINDOW)
check("one step at a time", L.current("s7"), L.MOBILE)

# ── LADDER_MAX_RUNG: forbid escalating past a named rung ─────────────────────────────────────────
# Grounded in a real incident: UberEats escalated to `browser` (real Chromium) on an isolated Fly
# machine — a datacenter host, where the browser recipe is only proven from a residential exit — and
# 6+ concurrent Chromium instances exhausted the machine. LADDER_MAX_RUNG=impersonate is the fix: cap
# escalation at the plain HTTP rung, never reaching browser at all.
try:
    os.environ["LADDER_MAX_RUNG"] = "impersonate"
    check("browser is excluded from allowed_rungs() when capped",
          L.BROWSER in L.allowed_rungs(), False)
    check("impersonate itself stays allowed (the cap is inclusive)",
          L.IMPERSONATE in L.allowed_rungs(), True)
    check("everything below the cap stays allowed",
          L.DIRECT in L.allowed_rungs() and L.MOBILE in L.allowed_rungs(), True)

    L.reset(); L.current("s8", default=L.IMPERSONATE)
    feed("s8", B.CAPTCHA, L.WINDOW * (L.MAX_COSTUMES + 2))   # exhaust every costume rotation too
    check("capped at impersonate, sustained blocks cannot climb to browser",
          L.current("s8"), L.IMPERSONATE)

    # THE GAP THAT WOULD HAVE SILENTLY UNDONE THE CAP: a source that already escalated to `browser`
    # and PERSISTED that choice (ladder_state) must not boot straight back into it on the next
    # process just because _load() predates the cap being set.
    L.reset()
    real_load = L._load
    L._load = lambda source: L.BROWSER    # simulate: this is what persisted state says
    try:
        check("a persisted `browser` choice is clamped down to what's currently allowed",
              L.current("s9"), L.IMPERSONATE)
    finally:
        L._load = real_load               # restore the module's real _load for anything after this
finally:
    os.environ.pop("LADDER_MAX_RUNG", None)

check("without the cap, browser is reachable again (no leftover global state)",
      L.BROWSER in L.allowed_rungs(), True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- ladder: a closed rung escalates itself, a closed store does not (23 checks)")
