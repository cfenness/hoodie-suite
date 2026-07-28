"""Failure classes must keep their POINT OF VIEW: different errors get different retry policies.

The thing this pins down: one backoff curve for every failure is a policy with no opinion. A crawl that
ran out of clock should come back in minutes (it resumes); a source being 403'd by an anti-bot wall must
back off hard (retrying fast deepens the block); an OOM kill or a selector drift reproduce exactly, so
auto-retrying them just hides a real defect behind a permanently red source.

Pure logic — no ledger, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import selfheal as sh  # noqa: E402

# ── classification: the status is structured, the error text disambiguates ───────────────────────
CASES = [
    # (status, error, expected class, why)
    ("timeout", "exceeded 5400s", "timeout", "plain clock exhaustion"),
    ("incomplete", "INCOMPLETE: 900/13900 captured", "incomplete", "productive but partial"),
    ("failed", "HTTP 403 Access Denied ... edgesuite.net", "blocked", "Akamai wall"),
    ("failed", "PerimeterX captcha challenge", "blocked", "PX wall"),
    ("failed", "killed by SIGKILL (9) — OOM likely", "oom", "OOM kill"),
    ("failed", "AttributeError: 'NoneType' object has no attribute 'text'", "parse", "selector drift"),
    ("empty", "", "empty", "ran clean, landed nothing"),
    ("failed", "something nobody has seen before", "unknown", "unclassified"),
    ("ok", "", None, "success is not a failure"),
    ("no-creds", "missing env: KROGER_CLIENT_ID", None, "honest skip, never retried"),
]
for status, err, want, why in CASES:
    got = sh.classify(status, err)
    assert got == want, "classify(%r, %r) -> %r, expected %r (%s)" % (status, err, got, want, why)
print("PASS: %d classification cases" % len(CASES))

# A timeout that is REALLY a block must read as a block — the wall is the story, not the clock.
assert sh.classify("timeout", "timed out after 403 Access Denied") == "blocked", \
    "a 403 inside a timeout is still a block"
print("PASS: a block disguised as a timeout classifies as blocked")

# STRUCTURAL beats inferential: when the run RECORDED its class, that is authoritative and the prose
# is not consulted. This is the whole point — an error message is written for a human, so its wording
# drifts, and a classifier that reads prose drifts silently with it.
assert sh.classify("failed", "403 Access Denied", fail_class="oom") == "oom", \
    "a recorded fail_class must win over whatever the error text looks like"
assert sh.classify("timeout", "", fail_class="incomplete") == "incomplete"
assert sh.classify("failed", "", fail_class="nonsense-not-a-class") == "unknown", \
    "an unrecognised recorded class degrades to unknown, never crashes"
print("PASS: recorded fail_class is authoritative; prose matching is the legacy fallback only")

# ── policy: productive failures come back fast, hostile ones slowly ──────────────────────────────
inc = sh.backoff_s(1, "incomplete")
blk = sh.backoff_s(1, "blocked")
unk = sh.backoff_s(1, "unknown")
print("backoff @1 fail — incomplete=%.0fs unknown=%.0fs blocked=%.0fs" % (inc, unk, blk))
assert inc < unk < blk, "incomplete must retry sooner than unknown, which must retry sooner than blocked"
assert blk >= 6 * inc, "a hostile block must back off far harder than a resumable shortfall"

# ── deterministic failures are NOT auto-retried at all ───────────────────────────────────────────
for k in ("oom", "parse"):
    assert sh.backoff_s(1, k) == float("inf"), "%s must never auto-retry" % k
    assert not sh.retry_due(now=1e12, last_attempt=0, cf=1, klass=k), \
        "%s must never come due, however long we wait" % k
print("PASS: oom + parse never auto-retry (they need a human, not persistence)")

# ── a resumable shortfall DOES come back on its own ──────────────────────────────────────────────
assert sh.retry_due(now=1000 + inc, last_attempt=1000, cf=1, klass="incomplete"), \
    "an incomplete crawl must become due once its backoff elapses"
assert not sh.retry_due(now=1000 + inc - 5, last_attempt=1000, cf=1, klass="incomplete"), \
    "…but not before"
print("PASS: incomplete becomes due after its (short) backoff, not before")

# ── the state machine carries the STORY, not just a number ───────────────────────────────────────
st = sh.states(now=1e9,
               statuses={"s_block": ["failed"], "s_inc": ["incomplete"], "s_ok": ["ok"]},
               last_attempt={"s_block": 1e9 - 10, "s_inc": 1e9 - 10, "s_ok": 1e9 - 10},
               last_error={"s_block": "403 Access Denied", "s_inc": "INCOMPLETE: 10/100"},
               last_class={"s_inc": "incomplete"})
assert st["s_block"]["klass"] == "blocked" and "anti-bot" in st["s_block"]["story"]
assert st["s_inc"]["klass"] == "incomplete" and "resume" in st["s_inc"]["story"]
assert st["s_ok"]["consecutive_failures"] == 0 and st["s_ok"]["klass"] is None
print("PASS: states() reports the class AND the human-readable story per source")

print()
print("ALL ASSERTIONS PASSED")
