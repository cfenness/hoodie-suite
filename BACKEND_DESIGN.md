# Backend Design — the Hoodie engine

One Python service (`unifyd/`) serves the whole product — the static apps, the `/api`
backend, the data, the scrapers, and the model brains — from a single origin on Fly.io.
This is what it does, how it's assembled, and **why** each piece is the way it is.

> A richer, rendered version of this doc exists as an Artifact. This is the version-controlled
> reference. Excluded from the public deploy (see `deploy.sh` / `deploy.yml`).

## Three principles everything hangs on

1. **One origin, one process.** The static apps and `/api` are served by the *same process
   on the same domain*. No CORS, no second host, no cross-service token plumbing — the app
   just fetches `/api/…`. When scale eventually demands a split, the contract (`/api/*`)
   doesn't change, only where it's hosted.
2. **Contract-first, not code-first.** The API shape is written down (OpenAPI), served by the
   backend at `/api/openapi.json`, and the app's typed client is *generated from it*. The
   contract is the source of truth; the client can't silently drift.
3. **Degrade honestly — deterministic vs. inference.** Every number is tagged by how it was
   produced. A deterministic fact (a license record, a summed depletion) presents as fact; an
   inference (a model's facing count, a generated pitch) presents as a suggestion. When a
   source drifts, the run marks itself `degraded` with warnings instead of emitting bad data.

## Runtime & deploy

One Flask app under `gunicorn`, **one worker**, in a Docker image that bundles the engine
*and* the static suite (`SUITE_ROOT` flips on all-in-one mode). Runs on **Fly.io**, one
machine, pinned.

- **Why Fly, all-in-one:** the original target was AWS (S3+CloudFront static, a container
  service for `/api`); new-account verification gated the container service indefinitely. Fly
  runs the same Docker image today with the same-origin design intact — no AWS, no CORS. The
  AWS path stays built and independent for whenever it clears.
- **Why one worker / one machine:** run state (pulled datasets, the job log) is in-process, so
  two workers/machines would fork it. `fly.toml` pins `min_machines_running = 1`. Durable data
  lives in object storage, not here, so pinning the compute costs nothing real.
- **Gotcha:** secrets set by `fly storage create` reported "Deployed" but weren't injected into
  the process; a deploy and a restart both failed. Re-setting a benign secret forced
  re-injection. `/api/health` now reports `warehouse: "tigris:…"` vs `"local"` so this is visible.

## Request path & security

A single `before_request` gate covers the whole origin.

```
Browser / App ──▶ Fly edge (HTTPS) ──▶ OIDC gate (cookie ∥ bearer) ──▶ Flask /api
                                                                          ├─ Warehouse (Tigris Parquet, DuckDB)
                                                                          ├─ Connectors (licenses · POI · scrapers)
                                                                          └─ Model brains (analyze · planogram · book)
```

- **Browsers** use **Google OIDC**: redirect → callback verifies the token → signed session
  cookie. Access is keyed to an **email allowlist**.
- **Native apps** have no cookie jar, so they exchange a Google ID token for one of our tokens:
  `POST /api/auth/mobile` verifies the ID token (via Google `tokeninfo` + audience + allowlist),
  mints a bearer signed with the session secret. The gate accepts a valid cookie *or* bearer.
  - *Why the exchange:* verifying Google's token on every request means fetching Google's keys +
    RS256 on the hot path, and it expires hourly. Exchanging once for a short-signed token we
    control means one cheap local check per request, and expiry/revocation are ours.
- **Static allowlist:** in all-in-one mode only an allowlist of public top-level entries is
  served; the engine source, scrapers, and secrets are in the image but never web-served. The
  check runs on the *resolved* path so `/apps/../unifyd/x` can't traverse out.
- **CORS is opt-in** (per-origin env var, off by default) for cross-origin web clients.

## The data spine — a canonical star schema

The prototype's mistake: every app invented its own data shape, so numbers never reconciled.
The backend now has **one canonical model** and every analytics surface is a query over it.

```
dim_product     (SKU · brand hierarchy · category / tier / size)
dim_account     (on/off-premise outlets in a market)
dim_date        (monthly calendar)
fact_depletion  product × account × month → cases, revenue   # the "book"
```

`book.cuts(dim, measure)` is a DuckDB join over the star, grouped by any dimension. Ask the
same place for a number and it reconciles everywhere.

- **Why a star schema:** the questions *are* cuts of one fact table ("revenue by category,"
  "velocity by channel"). A new screen is a `GROUP BY`, not a new contract. And it's the shape
  real depletion/POS data comes in, so swapping synthetic facts for real ones later doesn't
  change the schema or any query written against it.
- **Synthetic but coherent:** the book is generated deterministically (hash-seeded) and
  coherently (`revenue == cases × price × 12`, no orphan facts), so aggregations reconcile
  across every cut. A real **$62M book** (40 products × 60 accounts × 24 months) to build
  against until real facts flow.

## Storage — Parquet in object storage, queried in place

Data lands as **Parquet** in **Tigris** (Fly's S3-compatible store) and is queried directly by
**DuckDB** (`read_parquet('s3://…')`). No always-on database, no per-GB warehouse bill.

- **Why not Snowflake / Postgres / Firebase:** a warehouse bills compute+storage and is
  overkill for this volume; a relational DB is a server to keep alive. Object storage + DuckDB
  is the cheapest shape that still gives real SQL — cheap columnar files, query cost is a
  short-lived in-process scan. Parquet column pushdown reads only the columns a cut needs.
- **Why Tigris:** native to Fly (one command provisions + wires credentials), S3-compatible
  (drops into the existing `boto3` + DuckDB `httpfs` paths), free tier. Same code runs against
  a **local-disk fallback** when no bucket is set, so dev and tests exercise the identical path.

## Ingestion & connectors — authority first, ToS respected

- **Spine = license registries.** The authoritative, storable, free source for "places licensed
  to pour" is state liquor-license data (FL DBPR/ABT). The connector pulls it, filters to a
  county, normalizes to canonical **accounts**, classifies on-/off-premise by license series.
- **Enrichment = open POI, then Google carefully.** Open POI (Overture, Foursquare OS) is free
  *and storable*, read straight from public Parquet with DuckDB, matched by name+ZIP. Google
  Places is on-demand only — its ToS **forbids bulk-storing** Places content (only `place_id`
  is storable), so it's a live-lookup layer, never the store-everything source.
- **Datacenter-IP wall:** FL DBPR 403s datacenter IPs, so the fetch tries direct first and falls
  back to a residential proxy (Bright Data) when a key is set — inert without it.
- **Self-healing scrapers:** map columns by header name, locate rows by a stable id; when they
  can't map (no table, zero in-market rows, unknown headers) the run is `degraded` with
  warnings rather than emitting silently-empty data.

## The intelligence layer — brains that stay out of the way

The LLM pieces (`analyze`, planogram `shelf-vision`/`pitch`/`benchmark`, the book model):

- **Off unless keyed.** `anthropic` is lazy-imported, every job gated on `ANTHROPIC_API_KEY`.
  No key → the engine still runs, endpoints answer gracefully (`503 · llm-disabled`).
- **Injectable clients.** Each brain takes an optional `_client`, so tests drive them with a
  fake and assert on structure — no network, no key, in CI.
- **Numbers deterministic, words generated.** The pitch narrates only the figures passed in; the
  vision brain returns integers under a strict JSON schema. The model shapes language, never
  invents quantities. The benchmark and book model are pure functions (no key, always answer).

## The API contract — the backend describes itself

The API shape lives in an OpenAPI spec the backend **owns and serves** (`GET /api/openapi.json`).
The app's typed client is generated from that served spec.

```
edit endpoint shape → update openapi.yaml → backend serves it
   → npm run gen (openapi-typescript ← /api/openapi.json)
   → TypeScript flags every call site that no longer matches
```

Hand-kept client types are a silent liability (API changes, client compiles, mismatch surfaces
as a runtime bug). Generating from the served spec makes drift a *compile error*. It's also the
seam that lets a second surface be built against the same contract for free — and the reason a
later move to FastAPI (which emits OpenAPI natively) changes nothing on the client side.

## Testing philosophy

Tests run **without network, without keys, deterministically**. Scrapers validate against
captured fixtures; brains against injected fakes; the data layer round-trips real Parquet
through real DuckDB on local disk. The star-schema tests assert the thing that matters most —
**revenue reconciles across every cut** (no orphans, no double-counting) — through the actual
storage path. Coverage: DQ 80+23 · auth 14 · places 20 · POI 11 · book 11 · prism 10 — all green, offline.

## Endpoint reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/api/health` | public | Liveness + warehouse mode |
| POST | `/api/auth/mobile` | public | Exchange a Google ID token for a bearer |
| GET  | `/api/openapi.json` | gated | The API contract (client generated from it) |
| GET  | `/api/book/summary` | gated | Book totals |
| GET  | `/api/book/cuts` | gated | Aggregate the book by dimension × measure |
| POST | `/api/seed/build` | gated | Generate the synthetic book |
| GET  | `/api/places` | gated | Query pulled on-premise accounts |
| POST | `/api/places/enrich` | gated | Match accounts to open POI |
| GET  | `/api/prism` | gated | Deterministic book cuts + pulse |
| POST | `/api/analyze` | gated | Read an unfamiliar dataset (LLM) |
| GET  | `/api/benchmark` | gated | Market shelf norm (deterministic) |
| POST | `/api/shelf-vision` | gated | Photo → facing counts (LLM vision) |
| POST | `/api/pitch` | gated | Narrate gaps into a buyer pitch (LLM) |
| POST | `/api/run` | gated | Trigger a connector pull |

## The forward path

- **Real facts replace synthetic ones** into the *same* `fact_depletion` schema — no query,
  screen, or type changes.
- **FastAPI** when end-to-end types are worth it — removes the hand-kept spec; the client
  already generates from the served contract, so the frontend doesn't notice.
- **The origin can split** — CORS is already opt-in, the contract unchanged.
- **Durable run-state** — point the in-process job state at object storage (same abstraction)
  to remove the one-machine pin.

The through-line: the **contracts are the durable part** — the star schema, the `/api` shape,
the deterministic-vs-inference discipline. Everything else is swappable underneath them.
