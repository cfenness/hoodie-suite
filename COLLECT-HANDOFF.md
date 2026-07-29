# Hoodie Collect — handoff

State as of 2026-07-29, end of a long session on the UberEats pull. Written so the next person (or
session) starts from evidence rather than from this conversation. Everything below is measured unless
it says otherwise.

---

## 1. Where the UberEats pull actually is

| | |
|---|---|
| Goal | one full pull of 502,212 stores in **under 3 hours** = **46.5 stores/sec** sustained |
| Best measured | **12 stores/s** sustained at 90%+ usable (controller was throttling itself) |
| Burst measured | **70 stores/s** at 98.8% usable, held ~60s |
| Right now | **51% usable** under fleet load on `safari17_0` |
| Captured | ~33k of 502,212 (6.5%) |

**The goal was not met and is not close on current evidence.** But nothing structural blocks it: at
46.5/s the fleet asks only ~2.3 req/s per exit IP and ~0.9 session primes/s, neither aggressive.

### The open problem — ANSWERED 2026-07-29, and the answer is a third thing

Run it yourself with `costume_probe.py` (see §6). Four arms, 400 requests each, equal volume, on
`safari17_0`, with every controller frozen so nothing adapted under the measurement:

| arm | workers | usable | rate | **usable/s** | within the arm |
|---|---|---|---|---|---|
| 1 | 2 | **84.0%** | 3.7/s | **3.09** | 97.0% → 56.4% (decaying, z=7.8) |
| 2 | 8 | 10.0% | 30.3/s | **3.03** | — |
| 3 | 16 | 0.2% | 70.2/s | 0.18 | — |
| 4 = control | 2 | 8.2% | 7.9/s | 0.65 | 0.0% → **24.8%** (recovering) |

Control replicate fell **75.8 pp** (z=21.5). Arms 2 and 3 ran **45.6** and **36.4 pp** below their
time-adjusted baselines. Verdict: **BOTH** — but the useful finding is the shape, not the label.

- **Concurrency is the dominant immediate variable.** 84% → 10% → 0.2% across 2 → 8 → 16 workers, one
  process, one machine.
- **The damage outlives the load.** Arm 4 was back at 2 workers and opened at **0%**.
- **It is NOT a consumed quota.** Arm 4 climbed to 24.8% *within* the arm, and a follow-up probe read
  27.5% two minutes later. A quota does not recover. This is a **penalty with a recovery constant** —
  a third model, and neither of the two hypotheses above named it.
- **There is wear even at 2 workers.** The first arm, on a rested pool, fell 97% → 56% over 400
  requests. That component is independent of the bursts.
- **The number that governs the 3-hour goal:** arms 1 and 2 delivered *identical usable data* — 3.09 vs
  3.03 stores/s — across an 8× difference in request rate. Going 2 → 8 workers bought **nothing**; 16
  destroyed it. Failures were almost entirely `captcha` (359/400, then 399/400), not 429s.

**So the ceiling is identity capacity, not pacing.** More workers per identity is a ~1:1 trade of
success rate for request rate. Reaching 46.5/s needs roughly 15× more *independent* identity capacity,
not a better rate controller — which also retires the framing in §1 that "nothing structural blocks it".

**What is still open**, in priority order:

1. **Volume-wear vs burst-damage are not yet separated.** The control replicate sits at the END, so it
   inherits whatever arms 2 and 3 did. Run `--arms 2,2,2,2 --per-arm 400` **on a rested pool**: if the
   decline reproduces with no high-concurrency arm, it is volume; if not, the bursts caused it.
2. **The recovery constant is unmeasured and looks long.** 24.8% → 27.5% over ~2.5 minutes against an
   84% rested baseline. That constant sets the real cadence for any fleet design, and nothing else can
   be sized until it is known. Probe at intervals after a burn.
3. **Do different costumes degrade differently?** `firefox133` and `edge101` were 2/2 clean and remain
   untested at scale. `costume_probe.py --costumes` runs the same plan per costume.

---

## 2. What got built today, and why each piece exists

Every one of these came from a specific failure. The failures matter more than the code.

| module | catches | born from |
|---|---|---|
| `blocks.py` | why a fetch failed — 11 classes, per-method AND per-exit-IP | one `unreachable` counter covered 4 causes; four models were fitted to it and three were wrong |
| `ladder.py` | a closed access method — rotates costume, then escalates rung, persists the choice | the system correctly detected a closed path and had no reflex to try another |
| `sessions.py` | identity lifecycle — session budget learned from real burns | collapse tracked request COUNT not time; ~50 requests per primed cookie |
| `pace.py` | request rate — AIMD, trip threshold derived from observed background | every fixed rate constant was wrong within a day |
| `extract_qa.py` | a parser that stopped extracting — field fill vs trailing baseline | a 200 that is wrong is worse than a 403, and nothing watched for it |
| `value_rules.py` | plausible-but-wrong values — UPC check digit, ABV range, price sanity, cross-field | right type + wrong number survives every other check |
| `raw_capture.py` | full payloads, append-only | payloads inside an accumulating catalog made memory scale with the TABLE |
| `pool_health.py` | what every proxy exit actually is (geo, ISP, reachability) + live-fire per exit | production ran on an unaccounted pool; 26 of 50 exits were non-US |
| `adapt.py` | one switch that holds `pace`, `sessions` and `ladder` still | you cannot measure a system that adapts to your measurement — a ladder rotating costume mid-arm hands back a clean result for a reason the experiment never recorded |
| `costume_probe.py` | whether degradation tracks concurrency or accumulated volume | four models had been fitted to a blended signal, each disproved only by the next run |

### The design rules these share

- **Escalate/act on CLASSIFIED blocks only.** `not_found` and `timeout` must never move a rate, a
  session budget, or a rung. Treating a dead-store background as pushback is how a healthy run
  throttles itself to a standstill — that happened, twice.
- **Learn constants, don't pick them.** Five constants were wrong within a day. Anything describing a
  system we don't control (rate limits, quotas, block thresholds) must be derived from observation —
  *and* capped, because calibrating during an outage teaches the controller that being blocked is
  normal (that happened too).
- **Never let bookkeeping outrank evidence.** Coverage is derived from landed data, not from a
  self-calibrating watermark.
- **Cheapest move first.** A TLS costume change costs one string; a browser costs 10–50× throughput and
  a Chromium per process. The ladder exhausts costumes before escalating.
- **Never auto-escalate into spend.** The ladder stops at the last free rung unless `FETCH_POLICY`
  explicitly allows more.

---

## 3. The failure pattern to watch for

Nearly every bug this session was **a system reporting itself healthy while doing nothing useful**:

- a wrecked run graded itself **100% complete** against a watermark set by its own previous wreck
- a throttled 200 counted as coverage — **242,503 stores "covered", 10,040 with data**
- a hung fleet kept its last metrics and read as alive (**8 shards, 0 progress, 4 minutes**)
- a pacer **climbed** while 82% of responses were CAPTCHAs
- three tests asserted one layer away from where the bug lived, and passed while the feature did nothing
- `pool_health.live_fire` documented itself as pinning one thread to one exit "so every outcome is
  attributable", and pinned nothing: it assigned `_TL.exit` directly and `_session` overwrote it with
  its own round-robin pick on the next call. **Every result was filed against an address that had not
  carried the request** — in the one instrument built to tell a burned identity from a blown
  fingerprint. Fixed 2026-07-29 (`getstore.pin_exit`); any per-exit reading taken before that is void.

**If a number looks good, check what would have to be true for it to be false.** The instrumentation
lying was consistently more expensive than the scrapers breaking. Note the shape of the entry above:
the docstring asserted the property, and nothing checked that the property held.

---

## 4. The costume finding (the day's actual answer)

Measured on the live target, same IPs, same rate, same minute:

```
chrome / chrome124 / chrome131             BLOCKED
safari17_0 / safari18_0 / safari17_2_ios   OK
firefox133 / edge101 / chrome99_android    OK
```

UberEats fingerprints the **desktop-Chrome TLS family specifically**. We had been sending
`impersonate="chrome"`. A day went into modelling rate limits, session quotas, IP reputation and
geography for what was one string — **and the precedent was already in our own notes** (DoorDash:
"safari17_0 beats Forter, Chrome 403s").

**Lesson for next time: sweep the cheap identity variables FIRST.** TLS profile, UA, headers. Ten
minutes, before any modelling.

---

## 5. Decisions banked (don't re-litigate)

- **Do NOT buy more proxies.** 200 requests through 50 fresh, correctly-geolocated US residential IPs
  returned **zero** successes while the cold path was blocked. Addresses were never the problem.
  The $178/month 1000-IP plan would have bought nothing.
- **The IPRoyal subscription (15 Canadian ISP proxies, order #77366284) is unused** — zero traffic
  since 2026-07-26, not referenced by production. Cancel or repurpose.
- Production runs on a **Webshare** pool in the `ISP_PROXIES` Fly secret (50 entries, 49 US + 1 CA).
  Set it with `flyctl secrets set ISP_PROXIES="$(tr '\n' ',' < proxies.txt | sed 's/,$//')"`.
  Note: ~45KB is near Fly's secret limit, so a 1000-IP pool needs Tigris-backed loading instead.

---

## 6. Operating it

```bash
# deploy — NEVER `flyctl deploy` directly (it ships your working tree, not main)
python3 tools/release_train.py deploy

# every test (the curated list in run_tests.sh is NOT all of them)
for t in unifyd/*_test.py; do python3 "$t"; done

# what every proxy exit really is
flyctl ssh console -a hoodie-suite -C "python3 /app/unifyd/pool_health.py"

# fire real requests per exit — burned vs fresh identity comparison
flyctl ssh console -a hoodie-suite -C "python3 /app/unifyd/pool_health.py --fire 4"

# concurrency vs cumulative — arms of equal volume, last arm repeats the first as the control.
# Every controller is frozen for the duration; a null result reports what it could have detected.
flyctl ssh console -a hoodie-suite -C \
  "python3 /app/unifyd/costume_probe.py --arms 2,8,16,2 --per-arm 400 --out /tmp/probe.json"

# RUN IT ON A RESTED POOL. A probe fired straight after a burn measures the burn: the pool was at
# 27.5% two minutes after the run above, against an 84% rested baseline. Check with a cheap
# single arm first and only proceed when it is back near baseline.
flyctl ssh console -a hoodie-suite -C "python3 /app/unifyd/costume_probe.py --arms 2 --per-arm 40"

# rebuild coverage from landed evidence after a bad run
flyctl ssh console -a hoodie-suite -C "python3 -c \"
import sys; sys.path.insert(0,'/app/unifyd'); import ue_catalog as U; U.repair_checkpoints('ubereats')\""
```

**After any deploy touching `source_registry.py`, re-pin the dispatcher** (`tools/repin_dispatcher.sh`)
— the release train does this automatically, a manual deploy does not.

---

## 7. What I'd do next, in order

Items 1 and 2 of the previous list are **done** — see §1 for the measured answer and the run record now
carrying `exit_verdict`. What that answer opened up:

1. **Measure the recovery constant.** Everything downstream is sized by it, and it is currently one
   data point (24.8% → 27.5% over ~2.5 min against an 84% baseline). Burn the pool, then probe at
   intervals until it is back. Until this number exists, no fleet design can be justified.
2. **Separate volume-wear from burst-damage** — `--arms 2,2,2,2` on a rested pool (§1).
3. **Stop trying to buy throughput with workers.** 2 and 8 workers deliver the same usable stores/s.
   The lever is independent identity capacity; the next design question is what an identity actually
   is here (exit IP, session cookie, TLS costume, or a combination), because that determines what has
   to be multiplied 15×.
4. **Prove `ue_enrich` end-to-end.** It has never completed a run — it died on a schema bug every time,
   so everything past its first query is unexercised. UPC backfill is unproven.
5. **`pool_health` on a schedule.** A pool silently drifting to 52% foreign looked exactly like "the
   target got harder". That should page, not wait for someone to check.
6. **Then** revisit the 3-hour target — but note §1 retires the old framing. It is not a pacing problem.

---

## 8. Known-unresolved

- The **browser rung works** (3/3 real catalogs) but is unproven at volume and is 10–50× slower.
  It's a fallback, not a bulk path.
- **`ue_catalog._ck_save` re-serializes the entire done-set every batch** — ~123 MB per shard per pass,
  O(n²) in universe size. Wasteful, not incorrect. Delta checkpoints are the fix.
- **16+ test files exist that the curated runner never ran.** They pass now, but the runner's list is
  hand-maintained and will drift again; it prints unlisted files as a warning.
- **Per-exit findings from before 2026-07-29 are void** — `pool_health.live_fire` was attributing to
  the wrong address (§3). The pin is real now, but nothing has been re-measured through it, so the
  question it exists to answer — burned identity or blown fingerprint — is still formally open.
- **The probe's first run could not read its own per-exit marginal.** Exits were chosen round-robin at
  session prime, so a 2-worker arm touched ~2 addresses and a 16-worker arm ~16: exit identity was a
  function of worker count. Arms now pin one exit per worker and start at a different pool offset, but
  that pooled reading is only trustworthy from the next run onward.
