# Handoff — locator, geo pipeline, DoorDash Texas

Written 2026-07-29 at the end of a long session. Everything below is **measured on the deployed app**,
not inferred. Where a number has no evidence behind it, it says so.

The recurring theme, and the thing to hold onto: nearly every defect here was **something reporting
success while doing nothing**. A cap printed as a total, a fetch discarded on timeout, an OOM recorded
as `delta=0`, a broken feed landing one store as a complete run, a failed pin as an empty market, a
zero-yield sweep as `status=current`. When something looks fine but the number never moves, distrust
the report before the data.

---

## 1. Shipped, deployed, verified

| what | evidence |
|---|---|
| Locator reads **our 1.76M outlets**, not VIP's 150-row scrape | 1,025 accounts within 15 mi of Houston (was 150) |
| Houston resolves to Houston | `zcta.resolve("Houston, TX")` → ZIP 77009, 1.4 mi from centre |
| Coverage map no longer samples by storage order | true `COUNT(*)` + uniform hash sample + per-source breakdown |
| Aggregator drain lands rows | ubereats 28,856 → 29,845; backlog 769,744 → 768,321 |
| Ephemerals sized from the registry | `spawn()` resolves `mem` itself; caller no longer has to remember |
| Aggregator write is shard-safe | per-shard `write_partition` + one serialized `merge_stage` |
| DoorDash sweep is off Bright Data | local Chromium + standard CDP geolocation + flat ISP pool, $0 |

PRs: #666, #668, #670, #672, #673, #676, #678, #680, #682, #683, #685, #687, #688, #690, #691, #692, #694.

---

## 2. FIXED — the health digest was reading the wrong ledger

**Was ranked above Texas, and it was right to be.** Confirmed and fixed — but the original diagnosis
above was measured against a local warehouse, and production says something slightly different.
Measured on the app machine (`8609e5ceedd258`) 2026-07-29:

```
source_runs      42 rows   17 of 53 enabled sources   newest ts_end 8.0 DAYS old
source_runs_log 316 rows   53 of 53 enabled sources   newest ts_end current
```

So `source_runs` was **not empty** — which is worse than empty, because a stale non-empty table reads
as a real answer. `health_digest._latest_source_runs()` queried it alone, so `run-failed`,
`run-degraded` and `run-no-change` were judging a third of the fleet on a week-old snapshot and the
other two thirds not at all. Seven enabled sources sat `failed`/`timeout` in the log, invisible to the
digest: `cityhive`, `doordash-full`, `geo`, `outlet-union`, `sevennow`, `tax-rates`, `ubereats-enrich`.

**Root cause:** `_land_runs` moved to the append-only `source_runs_log` partitions on 2026-07-21, and
every other consumer — `ledger_last`, `monitor`, `selfheal`, `cost_ledger` — was given the dual-read.
The digest was the one reader that got left behind.

**Fix:** union both ledgers with newest-`ts_end`-wins, the same idiom as the other four. The schema
caution recorded here originally does not apply — the digest reads only `status`, `error` and `ts_end`,
all three in `SR_FIELDS`, and `fail_class` appears **zero** times in `health_digest.py`.

Pinned by `unifyd/health_digest_runs_test.py` (stdlib-only, always runs): 6 of its 10 checks fail
against the pre-fix code.

---

## 3. OPEN — DoorDash Texas is still at zero stores

### Why Texas is broken at all (not our bug)

DoorDash's own `sitemap-doordash-tx-stores.xml` serves a **270-byte stub containing ONE store**
(`kan-sushi-austin`). California's carries **103,811**. No alternate URL exists — `tx-1`, `texas`,
`.gz`, `hou`, `dal` all 403. Verified live.

So `doordash_stores` has **TX=1** against CA=74,208, and `src_outlets` has zero DoorDash outlets inside
the Houston bbox. The geographic sweep exists because their feed cannot be fixed from our side.

`doordash_sitemap.py` now self-reports `degraded` when a US state comes back below a plausibility floor
(300; the thinnest real states are VT 747 / WY 938 / AK 957), so **if their feed recovers we will
notice** — worth re-checking before investing more in the sweep.

### Where the sweep actually stands

Ported off Bright Data and running on its own ephemeral machine (`doordash-geo-tx`, `klass="mac"`,
8GB, Xvfb + patchright). Five runs. **Zero stores harvested.**

Progress made, in order — each fix revealed the next:

1. `playwright` imported directly → image ships **patchright** (#688)
2. Chromium **ignores `user:pass@host`** in a proxy URL → every nav `ERR_INVALID_AUTH_CREDENTIALS` (#690)
3. `except: pass` per search term → a totally failed pin reported as an **empty market** (#690)
4. 90s nav timeout → a hung pin said nothing for ~9 min (#692)
5. `channel="chrome"` **blocked without raising**, so the `for ch in (...)` fallback never fired (#694)

Fix 5 turned an infinite hang into a **14.7s clean exit**. That is real progress: the failure is now
fast and therefore debuggable.

### The immediate bug — and it is mine — FIXED, and there were two

`doordash_geo.run_group()`:

```python
except Exception as e:          # noqa: BLE001 — one metro must not
    log(...)                    # abort the rest
```

Reasonable intent, wrong result: when **all five** metros fail, it logs each, returns 0, and exits
normally. The harness sees a clean exit with no delta and records:

```
status='current'  delta=0  ERROR: ''
```

I fixed exactly this shape one level down (`_point_harvest` now raises when all six search terms fail)
and reintroduced it one level up. The per-market reasons *were* logged — into subprocess stdout that
`run_sources` captures and discards when there is no error.

**A second defect was hiding behind the first**, same swallow, and it inverted the report outright:

```python
total += (run(m, points=points, log=log) or 0)     # run() returns the ROW LIST, not a count
```

`total` is an `int` and `run()` returns a `list`, so `int += list` raised `TypeError` — caught by that
same `except` and logged as `market <m> failed`. So a metro that **worked** was reported failed, while
a metro that found **nothing** fell through `[] or 0` and was counted fine. Exactly backwards, and it
would have made the first real successful sweep look like a total failure. The new test measures it:
against the pre-fix code, five markets each returning three merchants totalled **0**.

**Both fixed:** `len(run(...) or [])` for the count, and `run_group` now raises when every market
fails (mirroring `_point_harvest`), so the reason reaches `source_runs_log.error` — where the digest,
now that §2 is fixed, can actually see it. Pinned by 5 new checks in `unifyd/doordash_geo_test.py`
(4 fail against the pre-fix code); the stub keeps it offline, so no browser or proxy is involved.

**Do next:**
1. Re-run and read `source_runs_log.error` for `doordash-geo-tx` — it will now carry the real reason.
2. Only then run the full 240-pin grid.

Likely cause once visible: the page loads but renders no tiles because DoorDash wants an **address set
through the UI**, not just a geolocation override. `_set_location()` grants the permission and sets
coordinates, which may not be sufficient. Unverified — do not treat as fact.

### Running it

```bash
# on the app machine
python3 -c "import sys; sys.path.insert(0,'/app/unifyd'); import dispatch_ephemeral as d; \
print(d.spawn('doordash-geo-tx', d.current_image(), 'mac', trigger='manual'))"

# capture logs to a FILE — they roll off, and that is how two OOMs went undiagnosed
flyctl logs -a hoodie-suite -i <machine-id> > /tmp/tx.log
```

Useful env: `DD_GEO_NAV_MS` (25000), `DD_GEO_SETTLE_S` (4), `DD_GEO_VERBOSE` (1),
`DD_GEO_CHANNEL` (empty = bundled Chromium; set `chrome` to re-enable the system browser),
`BROWSER_HEADFUL` (tested both; made no difference).

---

## 4. OPEN — smaller, known

- **`instacart.py` imports `playwright` directly**, which is not installed on the image — the same
  latent break as #688. CLAUDE.md says that source runs in-app, so it is probably broken. Flagged in
  #688, not touched (different lane).
- **44,071 stranded outlets** — no city+state, no address, not an aggregator source, so no pass can
  reach them. Sampled as **Australian** (Warrawong, Campbelltown, Lismore), keyed by name slug. Our
  geo stack is Census-based and US-only. `mappability.NEEDS_KEY_BRIDGE` records the DoorDash
  slug↔numeric key mismatch that blocks the obvious backfill.
- **The 6-shard aggregator fleet is unproven live.** The partition is verified against DuckDB's real
  hash and the merge against a stubbed warehouse, but no 6-machine run has happened. First dispatch is
  the next daily tick — check that `agg_geo_stage` gains parts and `merge_stage` folds them in.
- **Aggregator throughput:** ~450 rows/min at 64 workers measured ⇒ ~28h for the full backlog. Sharding
  6 ways should bring it inside a nightly window; confirm rather than assume.

---

## 5. Things worth not re-deriving

- **Null Island.** `ubereats`/`postmates` write `lat=0.0` as a sentinel. Excluded in `geo_gap`,
  `outlet_locator`, and `/api/master/outlets/geo`. Do not count it as geocoded.
- **`retail_observations.date` is VARCHAR.** `date >= CURRENT_DATE - INTERVAL` raises a DuckDB binder
  error; compute the cutoff in Python and bind an ISO string.
- **`write_accumulate` rewrites the whole table.** Never call it concurrently from shards — that is why
  `geo_all` serializes and why the aggregator stages instead.
- **Memory must scale with the chunk, not the backlog.** The aggregator's `SELECT * ... LIMIT 800000`
  OOM'd a 4GB machine before doing any work.
- **Always capture ephemeral logs to a file.** Two OOMs went undiagnosed because the evidence rolled
  off before anyone looked.
