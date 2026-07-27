# Scraping platform — all the data, when we need it, reliable enough to SELL

The bar changes here. Everything so far proved "we *can* get the data." Selling it means we can
**guarantee** it: known coverage, a freshness contract, uptime, and provenance a buyer will trust. This
is the program to get there. It is a strangler-fig on what we already run — not a rewrite.

## Sellable-grade — the bar, made concrete

A data feed is sellable when, per product (source × geo × entity), we can state and *hold*:

1. **Coverage** — "we have N of the M outlets/SKUs/menus that exist here" — a measured %, not a hope.
2. **Freshness** — "refreshed every X; ≤ Y stale at the p95" — an SLA, tracked, alerted on breach.
3. **Reliability** — the pull runs unattended and recovers itself; failures are loud, never silent
   (no mocked rows, no empty-clobbers, no stale-served-as-fresh).
4. **Provenance & quality** — every row carries source + fetched_at + method; DQ gates it before it ships.
5. **Delivery** — the buyer gets it as a stable, versioned feed/API with the SLA attached.

Anything short of a *number* on 1–3 is not sellable; it's a demo.

## What we already have (the platform is ~60% built)

- **Registry as the single source of truth** — `unifyd/source_registry.py`: what runs, its entrypoint,
  cadence (`interval_h`), creds gating, `after` dependency chains. Builds + sources both dispatch through it.
- **SLO dispatcher** — `run_sources.py --due`: runs only what's past its interval, ledger-backed
  (`source_runs`), fcntl-locked, `--builds` gate. The spine of "when we need it."
- **Warehouse v2** — Tigris + DuckDB, bucketed/manifested, anti-join merges, `write_accumulate`; empty-clobber
  + row-count-false-zero guards. Never-mocked, warehouse-stamped.
- **Proxy pool** — ~20 ISP IPs (`resi.py`/IPRoyal) + `curl_cffi` cracks (DoorDash $0/Forter, warmed-cookie
  recipes). **The 20 IPs are the throughput unit: ~20 safe paced concurrent streams — the crawl ceiling.**
- **Crack map** — `retail-api-crack-map`: per-source anti-bot posture (open → headless vs browser-token → headful).
- **Observability** — `health_digest.py` (daily verdict, staleness, row-collapse, degraded), `monitor.py`,
  `smoke_check.py`. Failures are already loud.
- **Cloud runner (defined)** — `fly.toml [processes].runner` (8gb, off the serving box) + `#609` self-scheduling.

The gap to sellable is not "can we scrape" — it's **throughput at scale**, an **explicit SLA/coverage layer**,
and **delivery**.

## The phases

### P1 — Throughput: a persistent fleet that saturates the pool 24/7 (nothing local)
The 20 IPs only pay off if they run continuously. Today the crawlers lean on the Mac / intermittent windows,
so most of the proxy capacity we already pay for sits idle.
- Stand up the **always-on runner** (persistent worker driving all ~20 IP lanes, paced, 24/7). One runner
  saturates the pool; the IPs — not compute — are the scarce resource.
- **Fleet-ready:** shard the big universes (DoorDash ~1M, UberEats ~1.3M sitemaps) by zone across K runners
  when we widen the IP pool. National sweeps go from weeks → days.
- Retire the Mac `--due` tick (`--no-builds` → unload `com.hoodie.due`). Nothing local.
- **Open infra task:** the `runner` process group is in `fly.toml` but not registered on the deployed app
  (`scale count runner=1` → "unknown process group"); a full deploy of the runner process (not `--ha=false`)
  or an explicit `flyctl machine run` registers + creates it. Auto-deploy is also off (no `FLY_API_TOKEN`).

### P2 — Reliability & SLA: the layer that makes it *sellable* (mostly code, no infra)
- **Per-source SLA in the registry** — a freshness target + a coverage target beside `interval_h`.
- **SLA ledger + breach detection** — extend `source_runs` into an SLA view: is each product within its
  freshness SLA right now? Surface breaches (health digest already has the plumbing).
- **Self-healing** — bounded retries, warmed-cookie refresh on auth expiry, degraded-mode quarantine
  (serve last-good, flag stale) instead of silent gaps.
- **Anti-bot budget** — pace-per-IP + reputation tracking so a burned IP is detected and rested, not
  hammered (Total Wine already blocks the pool at reputation).

### P3 — Coverage completeness: prove the "all the data" claim
- **Universe vs captured, per source × geo** — extend `representativeness` beyond outlets to every product:
  N captured / M known (sitemaps, census, first-party counts) → a coverage %.
- **Gap queue** — the uncaptured M-minus-N becomes prioritized crawl work the dispatcher consumes.

### P4 — Delivery: turn it into a product
- **Versioned feeds/API** per product, each row carrying source + fetched_at + method + a freshness stamp,
  DQ-gated, with the SLA attached — what a buyer actually purchases.

## The proxy math (the lever that governs P1/P3)
- **20 IPs ≈ 20 safe concurrent paced streams** — the current crawl throughput ceiling.
- The crawler routes *through* the IPs wherever it runs, so the machine changes **uptime**, not the IP.
  Always-on = the 20 lanes we already rent run 24/7 instead of a few hours a day.
- **Faster than 20 streams ⇒ buy more IPs**, not more compute. The next lever above always-on is a wider pool.

## Sequence
P1 (throughput, unblocks scale) and P2 (SLA/reliability, unblocks *selling*) run in parallel — P2 is pure
code and needs no infra, so it starts immediately while the runner infra lands. P3 rides on P2's coverage
numbers; P4 packages the result.
