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

## 2. OPEN — `source_runs` is empty; the health digest may be blind

**This matters more than Texas.** Measured:

```
source_runs_log: 313 rows
source_runs:       0 rows
```

`health_digest._latest_source_runs()` queries **`source_runs`**. If that table is genuinely empty in
production, then every run-outcome check in the daily verdict — `run-failed`, `run-degraded`,
`run-no-change` — is evaluating an empty set and can never fire. The digest would look healthy because
it has nothing to judge, which is the same failure class as everything else in this session.

**Do first:**
1. Confirm on Fly: `warehouse.query_parts("source_runs", "SELECT count(*) FROM t")`.
2. If 0, find who writes each table — `run_sources.py` logs `-> source_runs_log`, but the digest reads
   `source_runs`. One of the two names is wrong, or a rename left the reader behind.
3. Do **not** "fix" it by pointing the digest at `source_runs_log` until you know which table is
   authoritative — the schemas differ (`source_runs_log` has no `fail_class`, which the digest uses).

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

### The immediate bug — and it is mine

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

**Do next:**
1. Make `run_group` raise when **every** market fails (mirror `_point_harvest`). Then the error field
   carries the actual reason and this stops being invisible.
2. Re-run and read `source_runs_log.error` for `doordash-geo-tx`.
3. Only then run the full 240-pin grid.

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
