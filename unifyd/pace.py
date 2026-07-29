#!/usr/bin/env python3
"""pace.py — pace requests by RATE, and let the endpoint tell us what the rate is.

WHY RATE AND NOT WORKERS. We tuned concurrency three times today and were wrong three times, because
worker count is a proxy that breaks whenever the work per worker changes. The clearest measurement:
120 workers spread over 20 ISP IPs — 6 per IP, modest by any reading — collapsed after **5,578 requests
in 85 seconds (~59 req/s)**, with 5,025 of those responses empty. If the limit were per-IP, twenty IPs
would have absorbed that easily. It is the aggregate REQUEST RATE that gets throttled, which also means
buying more IPs would not have bought throughput.

WHY IT SELF-TUNES. Every fixed number we have picked has been wrong within a day: 3/IP was calibrated
during a throttle (too low), 6/IP was calibrated with enrichment padding each worker's think-time (too
high the moment the sweep got lean). A constant cannot track a limit that moves with time of day, recent
history, and how hot the exit IPs are. So the pacer runs AIMD — the same control law TCP uses:

  * ADDITIVE INCREASE: while responses look healthy, creep the rate up. Finds headroom without lunging.
  * MULTIPLICATIVE DECREASE: the moment the empty ratio crosses the threshold, halve it. Backs off fast,
    because the cost of overshooting (a poisoned pass) is far higher than the cost of running slow.

That asymmetry is deliberate: slow is recoverable, throttled-and-lying is not.

WHAT IT RECORDS. The rate it settles on is the measurement we have been guessing at. `stats()` reports
it so a run can print the sustainable rate it found, and the next run can start there instead of
rediscovering it.

    pacer = pace.Pacer(rate=5.0)          # this shard's share of the fleet budget
    pacer.acquire()                       # blocks until a token is free
    pacer.report(ok=bool(data))           # feed the outcome back
"""
import os
import threading
import time

# Fleet-wide request budget, split across shards. Deliberately conservative: the measured collapse was
# ~59 req/s aggregate, so we start well under it and let additive increase find the real ceiling.
FLEET_RATE = float(os.environ.get("UE_FLEET_RATE", "40"))
# TRIP ON A RISE ABOVE THE BASELINE, NOT AN ABSOLUTE RATIO. A fixed 0.25 was the fourth constant in this
# system to be wrong, and it was wrong in the most damaging way: ~25% of stores return no catalog because
# they are CLOSED OR DELISTED — a permanent property of the universe, not a signal about our request rate.
# So every window tripped, AIMD halved every time, and the controller walked itself to the floor: the
# paced run fell to ~2 stores/s (a 35-hour projection) while being throttled by nothing but itself.
#
# The two states separate cleanly in the measurements: healthy sits near 25% empty, a real throttle hit
# 90% (5,025 of 5,578). So learn the baseline from early windows and trip well above it.
EMPTY_TRIP = float(os.environ.get("UE_EMPTY_TRIP", "0"))      # 0 = derive it; >0 pins it explicitly
TRIP_MARGIN = float(os.environ.get("UE_TRIP_MARGIN", "0.25"))  # how far above baseline is "throttled"
TRIP_FLOOR = float(os.environ.get("UE_TRIP_FLOOR", "0.5"))     # never trip below this, whatever baseline
BASELINE_WINDOWS = int(os.environ.get("UE_BASELINE_WINDOWS", "3"))
# A LEARNED BASELINE NEEDS A CEILING, or calibrating during a block teaches the controller that being
# blocked is normal. Observed live: a run began at 82% challenged, learned 0.82 as its background, set
# the trip at 0.82+0.25 = 1.07, and became mathematically incapable of ever firing — the rate CLIMBED
# to 6.65/s while 82% of responses were CAPTCHAs. This is the same error as calibrating 3/IP during a
# throttle, which the code above explicitly warns about; the warning was written and then not applied
# to the calibration itself. Above this, we are not measuring a background, we are measuring a wall.
BASELINE_MAX = float(os.environ.get("UE_BASELINE_MAX", "0.35"))
# THE WINDOW MUST BE SMALL ENOUGH TO ADAPT WITHIN A RUN. At 200 outcomes and ~1.4 stores/s per shard, a
# control decision happened only every ~2.3 minutes — so after the pre-rotation collapse drove the rate
# from 5.0 to 1.25, climbing back took half an hour and the run read 11h ETA while succeeding at 90%+.
# The endpoint was no longer the limiter; the controller's own reaction time was. 50 outcomes keeps the
# ratio statistically meaningful while making both backoff AND recovery four times more responsive.
WINDOW = int(os.environ.get("UE_PACE_WINDOW", "50"))          # outcomes per control decision


def shard_rate(nshard=1, fleet_rate=None):
    """This shard's share of the fleet budget. Shards do not coordinate — the split is arithmetic, the
    same way the universe split is a stable hash."""
    return max(0.2, (fleet_rate if fleet_rate is not None else FLEET_RATE) / max(1, int(nshard)))


class Pacer:
    """Token bucket + AIMD controller. Thread-safe; one instance shared by all workers in a process."""

    def __init__(self, rate, min_rate=0.5, max_rate=None, window=WINDOW, empty_trip=EMPTY_TRIP):
        # empty_trip <= 0 means DERIVE it: watch the first BASELINE_WINDOWS windows, take the best
        # (lowest) ratio seen as the universe's dead-store background, and trip above that.
        self._derive = (empty_trip or 0) <= 0
        self._baseline = None
        self._seen_windows = 0
        self.rate = float(rate)
        self.min_rate = float(min_rate)
        # Never let increase run away past a sane multiple of where we started.
        self.max_rate = float(max_rate if max_rate is not None else rate * 4)
        self.window = int(window)
        self.empty_trip = float(empty_trip)
        self._tokens = 1.0
        self._last = time.time()
        self._lock = threading.Lock()
        self._n = self._empty = 0
        self.backoffs = 0
        self.increases = 0
        self._floor_hits = 0

    def acquire(self):
        """Block until a token is available. The wait happens OUTSIDE the lock so workers don't serialize
        on each other — holding a mutex through a sleep would turn the pool into a single thread."""
        while True:
            with self._lock:
                now = time.time()
                # CAPACITY IS AT LEAST ONE TOKEN. Capping the bucket at `self.rate` deadlocks every
                # worker the moment the rate drops below 1.0/s: tokens can never reach the 1.0 needed to
                # issue a request, acquire() spins forever, and because the heartbeat only fires when a
                # store completes, the run FREEZES while still reporting itself alive — 8 shards, zero
                # progress for 4 minutes, every counter static. min_rate is 0.5, so a single backoff to
                # the floor was enough to hang a shard permanently.
                self._tokens = min(max(1.0, self.rate),
                                   self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                need = (1.0 - self._tokens) / max(0.001, self.rate)
            time.sleep(min(need, 1.0))

    def report(self, ok):
        """Feed one outcome back. `ok` False means the response came back empty/unusable — the signal
        that we are over the line."""
        with self._lock:
            self._n += 1
            if not ok:
                self._empty += 1
            if self._n < self.window:
                return
            ratio = self._empty / float(self._n)
            if self._derive:
                self._seen_windows += 1
                self._baseline = ratio if self._baseline is None else min(self._baseline, ratio)
                if self._seen_windows <= BASELINE_WINDOWS:
                    # Still learning the background — do not act on it yet. Acting during calibration is
                    # exactly how 3/IP got measured during a throttle and came out far too low.
                    self._n = self._empty = 0
                    return
                if self._baseline is not None and self._baseline > BASELINE_MAX:
                    # Calibration happened under duress. Refuse the learned value and fall back to the
                    # fixed floor, so the controller can still act instead of being talked out of it.
                    self.empty_trip = TRIP_FLOOR
                else:
                    self.empty_trip = max(TRIP_FLOOR, (self._baseline or 0.0) + TRIP_MARGIN)
            if ratio >= self.empty_trip:
                # MULTIPLICATIVE DECREASE — back off hard and immediately.
                self.rate = max(self.min_rate, self.rate * 0.5)
                self.backoffs += 1
                if self.rate <= self.min_rate:
                    self._floor_hits += 1
            else:
                # ADDITIVE INCREASE — creep, don't lunge. The step is proportional to the CURRENT rate
                # so recovery from a deep backoff isn't a flat crawl: at 1.25/s a fixed +0.5 needs ~8
                # windows to reach 5/s, which is how a healthy run stayed slow for half an hour. Still
                # additive (never doubling), so it cannot lunge back into the wall it just found.
                self.rate = min(self.max_rate, self.rate + max(0.5, self.rate * 0.1))
                self.increases += 1
            self._n = self._empty = 0

    def stats(self):
        with self._lock:
            return {"rate": round(self.rate, 2), "backoffs": self.backoffs,
                    "increases": self.increases, "at_floor": self._floor_hits,
                    "trip": round(self.empty_trip, 3),
                    "baseline": (round(self._baseline, 3) if self._baseline is not None else None)}


_GLOBAL = {"pacer": None}


def install(rate):
    """Install the process-wide pacer every request path consults."""
    _GLOBAL["pacer"] = Pacer(rate)
    return _GLOBAL["pacer"]


def get():
    return _GLOBAL["pacer"]
