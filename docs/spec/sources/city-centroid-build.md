# City centroids (Census Gazetteer) — `city-centroid-build`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `city-centroid-build` |
| Runs | `import city_centroid as m; m.build_reference()` |
| Module | `unifyd/city_centroid.py` — 285 lines |
| Cadence | monthly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 2048 MB / 1800 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | **none** |


**Registry note.** build the $0 Census Gazetteer place/township centroid reference (state|city → lat/lng) the fast geo layer joins against. Refresh yearly; static otherwise


## 2. Transport

_No literal endpoint constant in `city_centroid.py`._ The transport is either inherited from a shared fetcher or built at run time — read the module.


**Depends on** `refresh_fast`, `warehouse`


## 3. What it lands


### `city_centroids`

68,747 rows · 5 columns


| column | type |
|---|---|
| `state` | `VARCHAR` |
| `city` | `VARCHAR` |
| `lat` | `DOUBLE` |
| `lng` | `DOUBLE` |
| `kind` | `VARCHAR` |


**Written by** `city_centroid.py:93` (write_parquet)


## 4. `city_centroid.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
city_centroid.py — the FAST geo layer: place any outlet with a city+state at its city centroid, instantly.

The precise geocoders (Census address batch, aggregator page-fetch) are exact but slow and need a street
address or a fetchable store page. Most outlets land with only city+state (DoorDash: 100% city, 93% state) —
so this layer maps them the moment they arrive, $0, no fetch, from a static reference:

  city_centroids  <- the free US Census Gazetteer (places + county subdivisions), state|city -> lat/lng.

Every outlet then carries a `geo_precision`: 'city' (this layer, ~city-accurate dot), 'exact' (a real
geocode/page fetch), 'city_miss'/'agg_miss' (tried, nothing found — don't retry forever). The precise passes
only ever UPGRADE a 'city' row to 'exact'; they never re-touch an 'exact' one. This is the layer that makes
"every outlet maps on the Coverage page" true immediately, with the exact crawl sharpening it over time.

geo_enrich_rows() is the INGESTION hook — call it in any sitemap/refresh path right before the write so no
outlet is ever taken in unplaced. fast_geo_pass() is the batch/registry entry that drains the existing backlog.
Reference build: build_reference() (stdlib urllib, run once / refresh yearly).
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
