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
- **There is wear even at 2 workers.** The first arm, on a rested pool, fell 97% → 56% over 400
  requests. That component is independent of the bursts.
- **The number that governs the 3-hour goal:** arms 1 and 2 delivered *identical usable data* — 3.09 vs
  3.03 stores/s — across an 8× difference in request rate. Going 2 → 8 workers bought **nothing**; 16
  destroyed it. Failures were almost entirely `captcha` (359/400, then 399/400), not 429s.

**So the ceiling is identity capacity, not pacing.** More workers per identity is a ~1:1 trade of
success rate for request rate. Reaching 46.5/s needs roughly 15× more *independent* identity capacity,
not a better rate controller — which also retires the framing in §1 that "nothing structural blocks it".

**RETRACTION — there is no "recovery constant".** The first version of this section reported
24.8% → 27.5% "recovering" over two minutes. That was wrong: every one of those probes ran at
`exit_offset=0`, so all three readings sampled the SAME two addresses, not the pool. It wasn't a curve
recovering — it was one pair of exits re-measured three times. Retracted rather than left standing;
see §3 for why. The two follow-on measurements below replace it.

**Follow-on #1 — the pool is BINARY, not degraded.** `pool_health.py --fire 4`, `safari17_0` pinned,
adaptation frozen, all 50 exits, run through the (now-fixed) real pin: **31/200 usable (16%)**, and not
one exit in between — **42/50 exits scored 0/4, 7/50 scored 4/4**, one scored 3/4. At n=4 there is no
gradient; an address is either working or it isn't. Survivors: `9.142.197.10`, `9.142.199.221`,
`9.142.23.133`, `64.52.29.9`, `193.160.82.111`, `138.226.89.232`, `63.246.153.143`, `45.58.244.98` (3/4).
**Unresolved:** whether these 8 are durably clean or merely not-yet-exhausted at n=4 — see §7.

**Follow-on #2 — `--arms 2,2,2,2`, run on `edge101` (rested), constant 2 workers throughout, no burst
anywhere in the plan:**

| arm | usable | within-arm |
|---|---|---|
| 1 | 97.5% | 97.7 → 96.2 |
| 2 | 96.8% | 97.0 → 97.0 |
| 3 (fresh exits) | **31.0%** | 30.1 → 31.6 |
| 4 = control (fresh exits) | **26.2%** | 27.8 → 26.3 |

Control fell **71.3 pp with zero concurrency variation anywhere on this plan** — so cumulative decay is
real independent of bursts, confirming Follow-on item 1 above. But the informative part is arms 3–4:
those exits had **never been touched before** in the run and got hit anyway, at the same moment arms
1–2's equally-fresh exits stayed clean. That is not "these specific addresses are burned" — it looks
like a **step change partway through the run** (~800 requests in) after which every newly-introduced
identity gets caught, which reads as an aggregate, volume-or-time-triggered defense rather than a
per-identity budget. One run, one costume, one threshold crossing — a lead, not a settled fact; wants
replication (§7).

- **Costume and exit look like two independent dimensions.** In the same minute, on the same pool,
  `edge101` read 100% and `safari18_0` 95% while `safari17_0` and `firefox133` both read 27.5%. That
  reframes identity capacity: not 50 exits, roughly 7 profiles × 50 exits, and costume rotation is a
  lever nobody was spending. It plausibly explains why buying more proxies tested as useless (§5) —
  worth relitigating now that the tooling is trustworthy.

**All three items above were run down 2026-07-29 and DISSOLVED rather than resolved individually — the
answer to all three turned out to be the same fact:**

**Survivor durability (item 1): REFUTED "not-yet-exhausted".** 14 requests through each of the 7
confirmed-present survivors: `9.142.197.10` 14/14, `64.52.29.9` 13/14, `9.142.199.221` 14/14 — no cliff
anywhere in 14 requests. `193.160.82.111`, `9.142.23.133`, `138.226.89.232`, `63.246.153.143` — **0/14,
dead from request #1**, no gradual decay. Not a spent budget: **4 of 7 addresses that scored 4/4 an hour
earlier were now fully dead**, with no partial state in between. No concurrent UberEats production run
was found to explain it (the ephemeral machines running at the time were `ttb-enrich`, `abc-fws`,
`bottlecapps`, `doordash-full` — a different site each).

**Step-change replication (item 2): DID NOT REPRODUCE.** A second `edge101` `2,2,2,2` (rested pool, same
plan) came back 64.0% → 62.2% → 56.8% → 57.0% — a mild decline, control drop 7.0pp, **not significant**
(under the 10pp bar). No cliff. `costume_probe`'s own `concurrency arms: []` on this run confirms the
#700 fix behaved correctly — nothing here was ever mislabeled.

**Global vs per-costume (item 3): the comparison is confounded by drift, and that IS the finding.** The
follow-up `safari17_0` `2,2,2,2` — the costume flagged as bad all day — came back 62.8% → 62.0% → 79.5%
→ **97.0%**, ending clean. One interior arm dipped significantly below trend along the way (correctly
filed as `trajectory`, never as `concurrency` — the #700 fix again behaving as intended), but the run
as a whole *improved*. Meanwhile the "clean" costume (`edge101`) had just posted its worst run of the
day. The two costumes did not hold still long enough between measurements to compare.

**The actual conclusion:** there is no stable fact of the form "exit X is good," "costume Y is safe," or
"the block trips at N requests." **Exit health and costume health are both continuously volatile on an
hour timescale, in both directions, for a cause not yet identified.** A discrete trigger would look like
a one-way step that persists; what's actually here is noise that can improve as easily as it degrades.

**What this changes going forward:** stop trying to characterise a fixed threshold or cache a "good
identity" list — by the time either is acted on, a meaningful fraction of it has already flipped. The
next concrete step is a **live health check in the routing path** (recent-window success rate for the
exact exit×costume pair about to be used, checked at request time), not a bigger one-time map. That
replaces items 1–3 as the priority item in §7.

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
- `costume_probe`'s own verdict logic mislabeled a real finding: an interior arm run at the SAME worker
  count as the control was scored against a straight-line trend and, when it deviated, filed under
  "concurrency" — even on a `2,2,2,2` plan where concurrency never varied at all. Fixed 2026-07-29 (the
  check now only fires "concurrency" for arms that actually differ in worker count from the control;
  same-worker deviation is reported separately as `trajectory` — a fact about the SHAPE of the decline,
  never about concurrency).
- I reported a "recovery constant" (24.8% → 27.5%) in an earlier draft of this section that was an
  artifact: three probes in a row used `exit_offset=0` and all sampled the same two addresses. It read
  as a curve. It was one pair, measured three times. Retracted below rather than left standing.

**If a number looks good, check what would have to be true for it to be false.** The instrumentation
lying was consistently more expensive than the scrapers breaking. Note the shape of the first entry
above: the docstring asserted the property, and nothing checked that the property held. Note the shape
of the third: a plausible trend from three data points was reported as a finding before checking
whether the three points were independent.

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

- **Do NOT buy more proxies** — *stated with even less confidence after the volatility finding in §1.*
  200 requests through 50 fresh, correctly-geolocated US residential IPs returned **zero** successes
  while the cold path was blocked; the conclusion drawn was that addresses were never the problem. That
  number (n=200, 50 exits) is the same shape as today's `pool_health --fire 4` map, which on the
  now-fixed pin returned **31/200 (16%)**, not zero — and which costume the earlier run used is not
  recorded here. But re-testing this cleanly may not even be possible in the way originally proposed:
  §1 found that exit and costume health both drift substantially within an hour, so a single "known-good
  costume, real pin" re-run would itself be one more noisy sample, not a clean answer. Don't spend money
  on this decision either way without a health-check mechanism (§1's proposed next step) that can tell
  a persistent problem from an hour's noise.
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

# fire real requests per exit — burned vs fresh identity comparison. live_fire does NOT freeze
# adaptation on its own: over 200 requests the ladder can rotate costume mid-sweep, and given §1's
# costume finding that silently blends two different measurements into one "per-exit" map. Freeze it
# and pin the costume you actually want measured, e.g. the flagged one:
flyctl ssh console -a hoodie-suite -C "python3 -c \"
import sys; sys.path.insert(0,'/app/unifyd')
import adapt, getstore, pool_health
adapt.freeze(); getstore._PROF['ubereats'] = 'safari17_0'
pool_health.live_fire(4, 'ubereats')\""

# concurrency vs cumulative — arms of equal volume, last arm repeats the first as the control.
# Every controller is frozen for the duration; a null result reports what it could have detected.
flyctl ssh console -a hoodie-suite -C \
  "python3 /app/unifyd/costume_probe.py --arms 2,8,16,2 --per-arm 400 --out /tmp/probe.json"

# LONG RUNS: detach, don't pipe through `tail`. Two failures hit this exact shape on 2026-07-29 —
# an ssh session dropped mid-run and killed the process (another session's deploy restarted the
# machine; /tmp does not survive that), and separately `| tail -N` swallowed a hard failure and the
# harness reported exit code 0 for a command that never finished. Detach, and check the LOG for
# completion, not the exit code of the pipe:
flyctl ssh console -a hoodie-suite -C "sh -c 'cd /tmp && nohup python3 /app/unifyd/costume_probe.py \
  --arms 2,8,16,2 --per-arm 400 --out /tmp/probe.json > /tmp/probe.log 2>&1 & echo started pid=\$!'"
flyctl ssh console -a hoodie-suite -C "tail -20 /tmp/probe.log"     # poll this, not the launch command

# rebuild coverage from landed evidence after a bad run
flyctl ssh console -a hoodie-suite -C "python3 -c \"
import sys; sys.path.insert(0,'/app/unifyd'); import ue_catalog as U; U.repair_checkpoints('ubereats')\""
```

**After any deploy touching `source_registry.py`, re-pin the dispatcher** (`tools/repin_dispatcher.sh`)
— the release train does this automatically, a manual deploy does not.

**Concurrent sessions deploy this same app.** A deploy restarts the machine mid-experiment with no
warning to whoever is running one; the release train reports "complete" whether or not it clobbered
someone else's in-flight run. Verify a long run's process is still alive before trusting its progress,
and write intermediate results somewhere that survives a restart if the run is expensive to repeat.

---

## 7. What I'd do next, in order

The original §1 items — concurrency-vs-cumulative, surface per-exit attribution, separate volume-wear
from burst-damage, and (as of later the same day) survivor durability / step replication / global-vs-
costume — are **all done**. The last three didn't resolve individually; they dissolved into one finding
(§1): exit and costume health both drift substantially within an hour, in either direction, for a cause
not yet identified. That finding is what drives the list below.

1. **Build a live health check into the routing path**, not another one-time map. Before a session picks
   an exit×costume pair, check its recent-window success rate (the data already exists in `blocks.Tally`
   — this is a consumption problem, not a new-data problem) and route around anything trending bad RIGHT
   NOW. A cached "good identity" list from even an hour ago is demonstrably unsafe (§1). This replaces
   the three now-closed items as the top priority.
2. **Find out WHY health drifts on an hour timescale.** Not required to ship item 1, but worth knowing:
   is it the target's own reputation scoring cycling, interference from other Hoodie traffic sharing the
   pool, or genuine randomness in their defense? Correlating drift timestamps against dispatcher activity
   logs (other sources' scheduled runs) is the cheapest first cut.
3. **Re-check the "don't buy proxies" decision (§5)** — but note it may not be answerable with a
   single clean test anymore; it needs the health-check mechanism from item 1 to distinguish a real
   problem from an hour's noise.
4. **Stop trying to buy throughput with workers.** 2 and 8 workers deliver the same usable stores/s.
   The lever is independent identity capacity — exit × costume, on current evidence — and reaching
   46.5/s needs roughly 15× more of it, not a better rate controller.
5. **Prove `ue_enrich` end-to-end.** It has never completed a run — it died on a schema bug every time,
   so everything past its first query is unexercised. UPC backfill is unproven.
6. **`pool_health` on a schedule.** A pool silently drifting to 52% foreign looked exactly like "the
   target got harder". That should page, not wait for someone to check — and given §1, a schedule alone
   isn't enough; whatever consumes its output needs to treat an hour-old reading as stale, not current.
7. **Then** revisit the 3-hour target — but note §1 retires the old framing. It is not a pacing problem.

---

## 8. Known-unresolved

- The **browser rung works** (3/3 real catalogs) but is unproven at volume and is 10–50× slower.
  It's a fallback, not a bulk path.
- **`ue_catalog._ck_save` re-serializes the entire done-set every batch** — ~123 MB per shard per pass,
  O(n²) in universe size. Wasteful, not incorrect. Delta checkpoints are the fix.
- **16+ test files exist that the curated runner never ran.** They pass now, but the runner's list is
  hand-maintained and will drift again; it prints unlisted files as a warning.
- **Per-exit findings from before 2026-07-29's pin fix are void** — `pool_health.live_fire` was
  attributing to the wrong address. Fixed and re-measured the same day (§1): the pool is binary at n=4,
  and separately volatile hour-to-hour, so even the fixed instrument's readings expire fast.
- **The `decay_sig` branch's evidence string can be factually wrong.** It always prints "arm averages
  are flat but usable% falls WITHIN arms" whenever it fires, but on the `safari17_0` replication (§1)
  the arm averages clearly trended UP (62.8% → 97.0%), not flat. Cosmetic — it doesn't affect the
  hypothesis or the trajectory/concurrency split, only the printed wording — but worth tightening in
  `costume_probe.py` if that branch gets touched again.
- **The exit-health-drift mechanism (§1) is unexplained.** Ruled out same-process production
  interference (checked concurrent ephemeral machines: none were UberEats). Not ruled out: the target's
  own scoring, or interference from other Fly-hosted traffic sharing the same proxy pool.
- **A `pool_health --fire` run over ~200 requests can itself take 10+ minutes when the costume is
  mostly blocked** (soft_block/captcha round-trips are not fast failures) — budget for that when
  scheduling it (§7 item 6), and detach long runs per §6's guidance rather than holding an ssh session.
