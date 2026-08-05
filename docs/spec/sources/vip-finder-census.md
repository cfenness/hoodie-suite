# VIP finder tenant census — `vip-finder-census`

> SOURCE (acquires data from outside the system)

## 1. The contract

|  |  |
|---|---|
| Registry id | `vip-finder-census` |
| Runs | `import vip_finder_census as m; m.pull(argv=['--deadline', '3000'])` |
| Module | `unifyd/vip_finder_census.py` — 608 lines |
| Cadence | weekly |
| Enabled | **yes** |
| Executor class | `headless` |
| Cost class | proxy |
| Memory / timeout | 4096 MB / 3600 s |
| Shards | 1 |
| Credentials required | none |
| Capabilities | `curl_cffi` |
| Unit test | **none** |


**Registry note.** enumerates custID 36^3 through the ISP pool; each run takes a 50min resumable bite (checkpoint in the warehouse) until the keyspace is walked. Pacing is adaptive — 1s/IP, doubling on 429 — so it self-throttles; --calibrate only makes it faster


## 2. Transport

| constant | value |
|---|---|
| `BASE` | `https://finder.vtinfo.com/finder/web/v2/` |


**Depends on** `resi`, `warehouse`


## 3. What it lands


### `vip_finder_tenants`

1,242 rows · 16 columns


| column | type |
|---|---|
| `cust_id` | `VARCHAR` |
| `theme_version` | `VARCHAR` |
| `show_captcha` | `VARCHAR` |
| `brand_code` | `VARCHAR` |
| `brand_description` | `VARCHAR` |
| `menu_fields` | `VARCHAR` |
| `n_brands` | `BIGINT` |
| `default_zip` | `VARCHAR` |
| `default_address` | `VARCHAR` |
| `default_miles` | `VARCHAR` |
| `analytics` | `VARCHAR` |
| `map_style_code` | `VARCHAR` |
| `use_online_vendor` | `VARCHAR` |
| `n_bytes` | `BIGINT` |
| `first_seen` | `BIGINT` |
| `last_seen` | `BIGINT` |


**Written by** `vip_finder_census.py:463` (write_accumulate)


### `vip_finder_brands`

33,196 rows · 4 columns


| column | type |
|---|---|
| `cust_id` | `VARCHAR` |
| `brand_value` | `VARCHAR` |
| `brand_label` | `VARCHAR` |
| `last_seen` | `BIGINT` |


**Written by** `vip_finder_census.py:466` (write_accumulate)


## 4. `vip_finder_census.py` — the module's own account

> Verbatim from the source. This is the design note, not a summary of it.


```text
vip_finder_census.py — enumerate the ENTIRE VIP "brand finder" tenant directory.

WHAT THIS IS
  `finder.vtinfo.com/finder/web/v2/iframe?custID=<CID>` is the widget every VIP-hosted
  brand/distributor "where to buy" page iframes. The custID is the tenant key — and it is a
  **3-character, case-INSENSITIVE alphanumeric mnemonic** (SNB = Sierra Nevada Brewing,
  TTO = Tito's). That is a 36^3 = 46,656 keyspace, and an unknown id is answered with a
  distinct "Invalid customer ID" page. So the whole tenant directory is enumerable.

  `vtinfo.py` reads ONE known brand's carriage (accounts near a ZIP). This module answers the
  prior question — *who is on the platform at all* — turning a hand-harvested 7-brand dict into
  a measured census. Each hit is then a ready-made vtinfo.py target: every discovered custID is
  a brand/distributor whose real VIP depletion-backed account carriage we can pull.

HOW IT WORKS (stdlib only, no auth, no cookie, no uuid)
  1. GET  iframe?custID=<CID>
       → miss: a 1.5 KB page containing the sentinel "Invalid customer ID".
       → hit : a ~20 KB finder page carrying an inline config block (themeVersion, showCaptcha,
               brandCode/brandDescription, menuConfiguration, defaults) + a stateless
               `CSRFToken` (JS-escaped: `\/` must be unescaped to `/` or the token is rejected).
     The uuid that vtinfo.py's BRANDS map carries is NOT required — custID alone drives it.
  2. POST iframe/filterMenu  {custID, action, source/target=BFBRCD, value=, CSRFToken}
       → the tenant's BRAND LIST as <option> rows. This is the identity payload: a single-brand
         finder returns its own SKUs, a distributor returns its whole book.

  Both stages are recorded. Stage 2 only runs on hits, so it costs ~1 extra request per tenant.

RATE LIMITING (the binding constraint — measured, not assumed)
  The origin 429s a single IP after roughly 5 requests in ~2 seconds. A 46,656-id sweep from one
  address is therefore not viable. This module runs ONE WORKER PER ISP-POOL IP (`resi.isp_pool()`)
  and paces each worker independently with AIMD: on a 429 the worker's interval doubles (and the
  id is requeued), on a clean streak it eases back down toward `--min-interval`. Run `--calibrate`
  first to measure the real safe rate on one IP before committing to a full sweep.

  With no proxy pool configured the sweep REFUSES to run (it would burn the origin IP) unless
  `--allow-direct` is passed. Probing/`--calibrate` on a handful of ids is always allowed.

RESUME
  A 46k sweep is checkpointed to agent_state/vip_finder_census.json after every batch, so a
  killed or rate-limited run resumes where it stopped instead of re-walking the keyspace.

DEGRADED (never silently emit garbage)
  Self-reports `degraded` when the "Invalid customer ID" sentinel is never seen (the miss
  discriminator moved → every id would look like a hit), when the config block stops parsing on
  hits, or when 429s exceed `--max-429-rate` of all requests (the sweep is being shaped by the
  origin, so coverage is not trustworthy). Confirm the parser offline against the captured pages
  in fixtures/ with `--selftest` — no network needed.

TABLES
  vip_finder_tenants  — one row per discovered custID (key: cust_id)
  vip_finder_brands   — one row per (custID, brand) from filterMenu (key: cust_id|brand_value)
```


## 5. Raw source fields

**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the verbatim fields a source emits and which of them we promote — it covers 13 of the 74 sources. Until this one is added, the landed columns above are what we know we keep, and what the source offers that we DROP is unrecorded.
