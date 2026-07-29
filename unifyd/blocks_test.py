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

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- blocks: a closed store is not a block, a hollow 200 is (30 checks)")
