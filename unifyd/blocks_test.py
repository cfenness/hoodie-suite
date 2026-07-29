"""Name the failure, and never let a closed store look like a block.

Every failure used to collapse into one counter, `unreachable`, covering a throttled shell, a closed
store, a timeout and an undecodable id. With one number for four causes, four models of the UberEats
limit got fitted in a single day and three were wrong — each disproved only by the next run, because
nothing recorded which failure was which.

Two distinctions carry the weight:

  not_found vs soft_block — both look like "no data". One is permanent and cheap and not our fault; the
  other is a 200 with the payload hollowed out, which reads as SUCCESS to anything checking status codes
  and is exactly how a throttled sweep marked 242,503 stores covered while landing data for 10,040.

  throttle vs not-throttle — a rate controller must react to pushback and ignore the target's catalogue.
  Backing off because stores are closed is how a healthy run throttles itself to a standstill; that was
  live behaviour, not a hypothetical.

Pure classification — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blocks as B  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


# ── the core distinctions ────────────────────────────────────────────────────────────────────────
check("closed store", B.classify(status=404), B.NOT_FOUND)
check("gone store", B.classify(status=410), B.NOT_FOUND)
check("throttled 200, no structure", B.classify(status=200, has_payload=False), B.SOFT_BLOCK)
check("real store, zero items", B.classify(status=200, has_payload=True, has_items=False), B.EMPTY)
check("real store with items", B.classify(status=200, has_payload=True, has_items=True), B.OK)

# ── explicit refusals ────────────────────────────────────────────────────────────────────────────
check("rate limited", B.classify(status=429), B.RATE_LIMITED)
check("plain 403", B.classify(status=403), B.HTTP_BLOCK)
check("captcha inside a 403", B.classify(status=403, body="<div id='px-captcha'>"), B.CAPTCHA)
check("captcha in a 200 body", B.classify(status=200, body="please verify you are human",
                                          has_payload=False), B.CAPTCHA)
check("datadome", B.classify(status=200, body="datadome challenge", has_payload=False), B.CAPTCHA)
check("named block in a 200", B.classify(status=200, body="Access Denied",
                                         has_payload=False), B.HTTP_BLOCK)
check("server error", B.classify(status=503), B.SERVER)

# ── transport ────────────────────────────────────────────────────────────────────────────────────
check("timeout", B.classify(exc=TimeoutError("read timed out")), B.TIMEOUT)
check("connection reset", B.classify(exc=OSError("connection reset by peer")), B.NETWORK)
check("odd exception", B.classify(exc=ValueError("weird")), B.UNKNOWN)

# ── never guess OK. An unclassified failure must show as a GAP, not get absorbed as healthy ──────
check("no information at all", B.classify(), B.UNKNOWN)
check("status only, unknowable body", B.classify(status=200), B.UNKNOWN)

# ── the controller signal: pushback only ─────────────────────────────────────────────────────────
check("soft_block throttles", B.is_throttle(B.SOFT_BLOCK), True)
check("captcha throttles", B.is_throttle(B.CAPTCHA), True)
check("rate_limited throttles", B.is_throttle(B.RATE_LIMITED), True)
check("NOT_FOUND does NOT throttle", B.is_throttle(B.NOT_FOUND), False)
check("EMPTY does NOT throttle", B.is_throttle(B.EMPTY), False)
check("timeout does NOT throttle", B.is_throttle(B.TIMEOUT), False)

# ── retry policy: a block is retryable, a missing store is not ───────────────────────────────────
check("blocks are retryable", B.SOFT_BLOCK in B.RETRYABLE, True)
check("missing stores are not", B.NOT_FOUND in B.RETRYABLE, False)
check("real empties are not", B.EMPTY in B.RETRYABLE, False)

# ── per-method tallies are what an escalation router would read ──────────────────────────────────
t = B.Tally()
for _ in range(70):
    t.record(B.OK, "isp")
for _ in range(10):
    t.record(B.EMPTY, "isp")
for _ in range(20):
    t.record(B.SOFT_BLOCK, "isp")
for _ in range(5):
    t.record(B.OK, "direct")
f = t.flat()
check("counts per method+class", f["isp.soft_block"], 20)
check("success counts ok+empty", f["isp.success_pct"], 80.0)
check("methods are separate", f["direct.success_pct"], 100.0)
check("summary names the top cause", "isp/ok" in t.summary(), True)

# ── the per-exit VERDICT: which fix does this distribution call for? ─────────────────────────────
# A pool average cannot tell "retire six addresses" from "the addresses were never the problem", and
# acting on the wrong one costs either a month of proxy spend or a day of fingerprint work.
t2 = B.Tally()
for i in range(200):
    ip = "10.0.0.%d" % (i % 10)
    burned = (i % 10) in (0, 1)                      # two dead exits, eight healthy
    t2.record(B.CAPTCHA if burned else B.OK, "isp", exit=ip)
v = t2.exit_verdict()
check("a burned subset is named", v["pattern"], "burned_subset")
check("and the exits are named", sorted(v["below_median_25pp"]), ["10.0.0.0", "10.0.0.1"])
check("worst-first ordering for the log", v["worst"][0]["pct"], 0.0)

t3 = B.Tally()
for i in range(200):
    # Outcome independent of exit — a genuinely even decline across the whole pool.
    t3.record(B.OK if ((i * 2654435761) % 97) < 48 else B.CAPTCHA, "isp", exit="10.0.0.%d" % (i % 10))
check("an even decline is named uniform", t3.exit_verdict()["pattern"], "uniform")

# The mirror case: most of the pool is dead and a few exits still work. Nothing can sit 25 points below
# a median of zero, so the burned-subset rule goes silent here — this is the one that has to name the
# SURVIVORS, because they are the only lead left.
t5 = B.Tally()
for i in range(200):
    ip = "10.0.0.%d" % (i % 10)
    alive = (i % 10) in (0, 1)                       # eight dead exits, two healthy
    t5.record(B.OK if alive else B.CAPTCHA, "isp", exit=ip)
v5 = t5.exit_verdict()
check("a surviving subset is named", v5["pattern"], "surviving_subset")
check("and the survivors are named", sorted(v5["above_median_25pp"]), ["10.0.0.0", "10.0.0.1"])
check("a dead median does not read as a burned subset", v5["below_median_25pp"], [])

t6 = B.Tally()
for i in range(200):
    t6.record(B.CAPTCHA, "isp", exit="10.0.0.%d" % (i % 10))
check("a uniformly dead pool is uniform, not a subset", t6.exit_verdict()["pattern"], "uniform")

t4 = B.Tally()
for i in range(6):
    t4.record(B.OK, "isp", exit="10.0.0.%d" % i)
check("too little per exit is 'insufficient', never 'uniform'",
      t4.exit_verdict()["pattern"], "insufficient")
check("a tally with no exit attribution does not invent one",
      t.exit_verdict()["pattern"], "insufficient")

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- blocks: a closed store is not a block, a hollow 200 is (30 checks)")
