# VIP Brand Builder distributor census — `vip-brandbuilder-census`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `vip-brandbuilder-census` |
| Runs | `import vip_brandbuilder_census as m; m.pull(argv=['--deadline', '3000'])` |
| Module | `unifyd/vip_brandbuilder_census.py` — 418 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | — |
| Memory / timeout | 4096 MB / 3600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | none |
| Unit test | `unifyd/vip_brandbuilder_census_test.py` |


**Registry note.** enumerates sourceCode 00000-99999 (100k), confirming each /info hit against /products before counting it — resumable, checkpointed bite per run. Smoke-tested live 2026-08-02: 18 confirmed distributor catalogs found in just the first 200 codes probed.


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://products.vtinfo.com/bbs/v1/distributor` |


**Depends on** `warehouse`


## 3. What it lands


### `vip_brandbuilder_directory`

365 rows · 8 columns


| column | type | filled |
|---|---|---|
| `source_code` | `VARCHAR` | 100.0% |
| `status` | `VARCHAR` | 100.0% |
| `distributor_name` | `VARCHAR` | 100.0% |
| `vip_source_id` | `BIGINT` | 100.0% |
| `vip_customer_id` | `BIGINT` | 100.0% |
| `n_products` | `BIGINT` | 100.0% |
| `first_seen` | `BIGINT` | 100.0% |
| `last_seen` | `BIGINT` | 100.0% |

Fill measured over **full table** (365 rows).

**Written by** `vip_brandbuilder_census.py:347` (write_accumulate)


## 4. `vip_brandbuilder_census.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
vip_brandbuilder_census.py — enumerate the VIP Brand Builder distributor keyspace.

WHAT THIS IS
  `vtinfo_bbs.py` pulls one distributor's whole catalog once you know its Brand Builder
  `sourceCode` — but that code has always been hand-harvested one distributor at a time
  (`DISTRIBUTORS = {"01191": "Columbia Distributing - WA"}`, a single entry). The sourceCode is a
  **5-digit zero-padded numeric string** ("00177", "01191") — the same shape as the numeric
  `custID` `vtinfo.py`'s finder side already uses for distributor-style tenants (Florida
  Distributing = "00177"), and verified live 2026-08-02: that exact custID is ALSO a valid Brand
  Builder sourceCode, returning "Florida Distributing Company". Same VIP identifier space, two
  different products. That makes the whole 100,000-value space (00000-99999) enumerable, the same
  way vip_finder_census.py turned its 46,656-value alnum space into a census instead of a
  hand-harvested dict.

HOW IT WORKS (stdlib only, no auth, no cookie, no proxy pool needed)
  GET bbs/v1/distributor/<code>/info
    → HTTP 200 {"success": true, "data": {"distributorName": "...", ...}}   = a real VIP
      distributor entity exists at this code.
    → HTTP 400 {"errorMessage": "Distributor ID is invalid"}                = confirmed miss.
    → HTTP 500 {"errorMessage": "SSO Service Error"}                        = inconclusive — the
      SAME code returns this consistently on retry (measured live: 3/3 identical), so it is not
      pure request noise, but it is also not the clean "invalid" signal — recorded as its own
      state, never folded into either hit or miss.

  STAY SOFT ON THE HIT (this is the point, not an afterthought): a 200 on /info only proves VIP
  has a distributor RECORD at this code — Total Wine's own vtinfo_bbs.py history shows a
  distributor entity existing does not guarantee a populated Brand Builder catalog. Every /info
  hit is confirmed with a SECOND, independent check — GET .../products — and only promoted to
  status="confirmed" if that returns a non-empty product array (i.e. it actually carries
  dist_item_code rows, the reason this census exists at all). A code that resolves on /info but
  returns zero products lands as status="info_only": real, but not yet a pull target.

RATE LIMITING — measured, not assumed, and NOT the same target as vip_finder_census.py
  finder.vtinfo.com 429s a single IP after ~5 rapid requests; this is a DIFFERENT VIP backend
  (products.vtinfo.com/bbs). Measured live 2026-08-02: 200 requests at 60 concurrent threads from
  one IP, zero 429s, zero connection errors, ~20 req/s sustained (30→60 workers barely moved
  throughput, so ~20 req/s looks like a server-side ceiling, not a client one). No proxy pool
  machinery here — plain threaded requests, capped at a conservative worker count.

RESUME
  A 100k sweep is checkpointed to agent_state/vip_brandbuilder_census.json (+ the warehouse, so a
  scheduled bite on a fresh machine picks up where the last one left off) after every batch.

DEGRADED (never silently emit garbage)
  Self-reports degraded when 200 is never seen across a real batch (parser/keyspace drift —
  every code would read as a miss) or when the info+error rate exceeds --max-bad-rate (the origin
  is failing outright, not just returning clean misses).

TABLES
  vip_brandbuilder_directory — one row per probed code with a hit (key: source_code); status is
  'confirmed' (real catalog) or 'info_only' (VIP record, no products yet).
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
