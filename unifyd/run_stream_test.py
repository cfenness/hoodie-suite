"""The live console must actually be LIVE — output has to arrive while the run is still going.

The bug this pins: the child writes to a PIPE, not a tty, so CPython block-buffers its stdout (~8KB).
Every plain print() in a scraper then sits in that buffer and reaches the streamer only when the buffer
fills or the process EXITS. Measured before the fix: three lines printed 0.6s apart all arrived together
at exit. On a 4-hour crawl that is a console that shows nothing for four hours and then dumps
everything — indistinguishable from a hung run, and the opposite of the feature.

Nastier still, HOODIE_PROGRESS lines use flush=True and leaked through regardless, so the counters
would tick while the log stayed blank: a half-working state that looks like a scraper problem rather
than a plumbing one.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_sources  # noqa: E402

# A child that prints on a slow cadence WITHOUT flush=True — i.e. what an ordinary scraper does.
CODE = "import time\nfor i in range(3):\n    print('tick %d' % i)\n    time.sleep(0.6)\n"

seen = []
t0 = time.time()
env = dict(os.environ, PYTHONUNBUFFERED="1")     # exactly what run_one now passes
r = run_sources._exec(CODE, 30, env, on_line=lambda l: seen.append((time.time() - t0, l.strip())))

assert r.returncode == 0, "child should exit clean, got %r" % r.returncode
assert len(seen) == 3, "expected 3 streamed lines, got %d: %r" % (len(seen), seen)
for t, l in seen:
    print("   %5.2fs  %s" % (t, l))

# The point: they must arrive SPREAD OUT, not in one clump at exit.
gaps = [seen[i + 1][0] - seen[i][0] for i in range(len(seen) - 1)]
assert min(gaps) > 0.4, (
    "lines arrived in a clump (min gap %.2fs) — stdout is being buffered, so the console is not live"
    % min(gaps))
# And the first line must appear almost immediately, not after the whole child has run.
assert seen[0][0] < 0.5, "first line took %.2fs — it waited for the child instead of streaming" % seen[0][0]
print()
print("PASS: output streams line-by-line while the run is in flight (gaps %s)"
      % ", ".join("%.2fs" % g for g in gaps))

# Guard the mechanism itself: without PYTHONUNBUFFERED the same child DOES clump. If this ever stops
# being true (a future Python defaulting to line-buffered pipes), the env var is no longer load-bearing
# and this test should be revisited rather than silently protecting nothing.
seen2 = []
t0 = time.time()
env2 = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
run_sources._exec(CODE, 30, env2, on_line=lambda l: seen2.append((time.time() - t0, l.strip())))
gaps2 = [seen2[i + 1][0] - seen2[i][0] for i in range(len(seen2) - 1)]
if gaps2 and min(gaps2) > 0.4:
    print("NOTE: this interpreter streams even without PYTHONUNBUFFERED — the env var is belt-and-braces here")
else:
    print("PASS: confirmed buffered without PYTHONUNBUFFERED (min gap %.2fs) — the fix is load-bearing"
          % (min(gaps2) if gaps2 else 0))

print()
print("ALL ASSERTIONS PASSED")
