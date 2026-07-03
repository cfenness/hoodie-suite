# Places connector — restaurant / on-premise accounts (Orlando first)

Pulls the **authoritative** list of alcohol-licensed venues (bars, restaurants, hotels) and
lands it as **Parquet** in cheap object storage, queried in place by **DuckDB**. Orlando =
**Orange County, FL**, built on the Florida DBPR/ABT license extracts the engine already pulls.

## Pieces
- `places.py` — `pull()` → fetch FL ABT license extracts → `normalize()` to canonical accounts →
  `filter_market()` (Orange County) → `classify_premise()` (on/off/unknown by license series) →
  `dedupe()` → write Parquet. Plus `match_open_poi()` and `enrich_google()` (ToS-safe, key-gated).
- `warehouse.py` — Parquet on **Tigris** (Fly S3-compatible) or local disk, + DuckDB `query()`.
- Engine wiring: connId **`orlando-accounts`** (run via `POST /api/run {"connId":"orlando-accounts"}`),
  and **`GET /api/places?premise=on|off|unknown&q=<name>&limit=N`** to read it back.

## Run a pull (where Florida DBPR is reachable — the Fly machine or your local agent)
```bash
# local agent
python server.py                       # then:
curl -X POST localhost:8765/api/run -H 'content-type: application/json' \
     -d '{"connId":"orlando-accounts"}'
curl 'localhost:8765/api/places?premise=on&limit=20'
```
Without object storage configured it writes to `agent_state/warehouse/orlando_accounts.parquet`
(ephemeral on Fly). Turn on durable, ~free storage with Tigris:

## Turn on Tigris (durable, free tier, no warehouse bill)
```bash
fly storage create                     # provisions Tigris + sets AWS_ENDPOINT_URL_S3,
                                        # BUCKET_NAME, AWS_ACCESS_KEY_ID/SECRET on the app
fly deploy                             # picks up the new env -> warehouse switches to remote mode
```
`warehouse.remote()` then reports true and `/api/places` shows `"remote": true`. DuckDB queries the
Parquet directly from Tigris (`s3://<bucket>/warehouse/orlando_accounts.parquet`) — no compute to keep up.

## Google enrichment (optional, ToS-safe)
```bash
fly secrets set GOOGLE_MAPS_API_KEY=...   # off unless set
```
`enrich_google()` stores only the durable `place_id`; rating/price/phone are fetched live and may be
refreshed — we never bulk-warehouse Google content (Places ToS). Open POI (Overture/Foursquare OS) is
the storable enrichment source and is matched by name+ZIP via `match_open_poi()`.

## Notes
- **Deps:** `duckdb`, `pyarrow` (in requirements; lazy-imported — only loaded when a pull/query runs).
- **Self-healing:** a pull that parses rows but yields **0 in-market**, or can't map key columns, is
  marked `degraded` with `warnings[]` (source drift) instead of emitting silently-empty good data.
- **Scope today:** Orange County. Extend by passing `county=` to the pull, or add more state registries
  behind the same `normalize/filter/store` pattern.
