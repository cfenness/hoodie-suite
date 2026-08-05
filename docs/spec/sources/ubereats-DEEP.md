# Uber Eats — the rebuild document

**What this is.** Everything an engineer needs to rebuild Uber Eats acquisition from nothing: the
identifiers, the endpoints, the exact request, the anti-bot posture, the concurrency and where its
numbers came from, every field we land, and — stated plainly — how much of the universe we actually
cover and which declared fields are empty.

**Companion pages.** [`ubereats`](ubereats.md) · [`ubereats-sitemap`](ubereats-sitemap.md) ·
[`ubereats-enrich`](ubereats-enrich.md) · [`ubereats-full`](ubereats-full.md) ·
[`build-ue-catalog`](build-ue-catalog.md) · [`postmates`](postmates.md).
Those are generated from the registry and the warehouse; this page is hand-written and is the only
one in `docs/spec/` that is.

Measurements below are from a live warehouse read on **2026-08-05**.

---

## 1. The shape of the thing

Uber Eats is not scraped as a website. It is a React front end over a first-party JSON BFF
(backend-for-frontend), and every number we take comes from that BFF. There are three passes and
they are deliberately on separate clocks:

| pass | source id | cadence | what it answers |
|---|---|---|---|
| **Universe** | `ubereats-sitemap` | weekly | which stores exist |
| **Catalog** | `ubereats` | daily, 8 shards | what each store sells, at what price, in stock or not |
| **Enrich** | `ubereats-enrich` | daily, 8 shards | the identifiers (UPC/GTIN) the catalog call doesn't return |
| *(fold)* | `build-ue-catalog` | every 6h | collapses the append-only parts into one current catalog |

The passes are split because their data has different half-lives. Price and stock change daily; a
store's existence changes weekly; an item's UPC never changes. Running them on one clock would mean
re-fetching immutable attributes 365 times a year.

`postmates` is the **same code** against `postmates.com` — one module, a `--site` flag.

---

## 2. Identifiers, and the trick that makes the whole thing addressable

A store URL looks like:

```
https://www.ubereats.com/store/the-throwback-710-amsterdam-avenue/3GYoBDgAU6-me98dDz_kSw
                                                                  └────── url id ──────┘
```

That 22-character token **is base64url of the 16 raw bytes of the store's UUID**. So:

```python
def url_id_to_uuid(url_id):
    b = base64.urlsafe_b64decode(url_id + "=" * (-len(url_id) % 4))
    return str(uuid.UUID(bytes=b)) if len(b) == 16 else None
# '3GYoBDgAU6-me98dDz_kSw' -> 'dc662804-3800-53af-a67b-df5d0f3fe44b'
```

**This is the load-bearing fact of the entire capability.** The sitemap publishes url ids; the BFF
wants dashed UUIDs. Because the conversion is pure arithmetic and not a lookup, every one of the
755,032 stores in the sitemap is *directly addressable* without ever visiting a store page. Without
it you would need a page fetch per store just to learn its API id, and the crawl would be ~750k
extra requests against the HTML surface that is actually defended.

Verified at scale: all 755,032 sitemap tokens decode to well-formed UUIDs (100%). Implementation and
the verification are in `unifyd/ue_ids.py`.

---

## 3. The universe — `ubereats-sitemap`

`robots.txt` lists 26 gzipped store sitemaps:

```
https://www.ubereats.com/sitemap-store-771af823-%03d.xml.gz     # 000..025
```

These are **public and robots-permitted** — explicitly listed in `robots.txt` — so they are fetched
**direct from the home IP with no proxy**. Runtime ~3 minutes for the full US universe.

Lands `ubereats_sitemap` — **755,032 rows**, `write_accumulate` keyed on `store_uuid`:

| column | type | notes |
|---|---|---|
| `store_uuid` | VARCHAR | the 22-char url id (NOT the dashed uuid — convert at call time) |
| `store_name` | VARCHAR | from the slug |
| `slug` | VARCHAR | url path segment |
| `url` | VARCHAR | full store URL |
| `source` | VARCHAR | `ubereats` \| `postmates` |
| `captured_at` | BIGINT | epoch seconds |

A second step, `sitemap_to_src_outlets()`, merges the universe into `src_outlets` as accounts
(name + uuid, no geo). **Order matters** — it must run after the geo-bearing crawl rows, or the
no-geo sitemap rows would overwrite coordinates we already have.

> Postmates equivalent: **269,007** stores.

---

## 4. The catalog call — `getStoreV1`

```http
POST https://www.ubereats.com/_p/api/getStoreV1
```

**Headers — this is the complete set. There is no cookie, no token, no captured session.**

```python
{
  "accept":               "*/*",
  "accept-language":      "en-US,en;q=0.9",
  "content-type":         "application/json",
  "origin":               "https://www.ubereats.com",
  "x-csrf-token":         "x",          # literally the letter x — presence is checked, value is not
  "x-uber-client-gitref": "x",
  "user-agent":           "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
}
```

Body is `{"storeUuid": "<dashed-uuid>"}`. A delivery target (lat/lng) can be supplied but **is not
required** — a store's own address and catalog are intrinsic and come back regardless.

**The transport is `curl_cffi` with `impersonate="safari17_0"`.** That is the whole anti-bot story:
Uber's edge fingerprints the TLS/JA3 handshake, so a stock `requests` session is refused while a
real-browser TLS fingerprint is not. No Bright Data, no residential proxy, no browser.

`LADDER_MAX_RUNG=impersonate` in the registry's `code` **caps the escalation ladder** so the source
can never silently climb to a metered proxy rung. That is deliberate and load-bearing — see the
free-first rule in `CLAUDE.md`.

### Concurrency — measured, not guessed

A 240-store calibration on this endpoint:

| workers | stores with a catalog | items |
|---:|---:|---:|
| 32 | 132 | 10,677 |
| 64 | **0** | 0 |
| 128 | 1 | — |
| 192 | 1 | — |

Above ~32 the BFF stops answering and **returns empty fast** — so the higher worker counts look
three times quicker while collecting nothing. This is the trap worth knowing about: throughput
*appears* to rise as coverage collapses. Throughput comes from **shards across machines**, never
threads on one.

Sessions are re-primed every `UE_SESSION_BUDGET=40` requests (collapse was observed around ~50).

### The arithmetic

> 755,032 stores ÷ 86,400 s = **8.7 stores/second sustained** to cover the universe in a day.

That is why the design is 8 ephemeral Fly machines, each `--shard i/8`, splitting the universe by a
stable hash of the store id. Shards need no coordination because the split is deterministic — they
cannot overlap or leave a gap, and a shard resuming tomorrow still owns the same stores even after
the sitemap grows.

Each shard lands in batches of `UE_BATCH=400` stores and checkpoints completed store ids, so a
killed shard keeps everything it fetched.

---

## 5. The detail call — `getMenuItemV1`

```http
POST https://www.ubereats.com/_p/api/getMenuItemV1
```

Same minimal headers. This is where **UPC/GTIN** live — `getStoreV1` does not return them.

**It requires section context.** The request needs the item's `sectionUuid`/`subsectionUuid`, which
only the catalog pass knows. An earlier belief that "getMenuItemV1 accepts empty section ids" is
false; `ue_enrich.py` carries the section context forward and logs a loud warning if it finds items
with none, because without it every request is rejected.

Enrichment is a **once-ever** pass per item: UPC and GTIN are static, so resolved items go into a
`KNOWN` set and are never re-fetched.

---

## 6. What we land

### `ubereats_products_parts` — the append-only capture
**29,901,954 rows · 3,832 partitions · 21 columns.** Every shard appends; nothing merges here.
Shards must never merge into a shared object or they lose each other's updates.

### `ubereats_products` — the folded catalog
**2,160,806 rows · 2,062,871 distinct items · 16 columns.** Produced by `build-ue-catalog` every 6h
(`fold.py`: watermarked, set-based, per-column merge, single writer).

| column | type | fill (folded catalog) | notes |
|---|---|---|---|
| `store_uuid` | VARCHAR | 100% | joins to `ubereats_sitemap` and `src_outlets` |
| `store_name` | VARCHAR | 100% | |
| `item_uuid` | VARCHAR | 100% | Uber's item id — the grain |
| `name` | VARCHAR | 100% | |
| `price` | DOUBLE | 100% | current price, cents→dollars |
| `list_price` | DOUBLE | — | pre-promo price where present |
| `in_stock` | BOOLEAN | — | |
| `stock_label` | VARCHAR | **8.9%** | free-text availability |
| `upc` | VARCHAR | **3.1%** | 66,192 of 2,160,806 — from the enrich pass only |
| `abv` | DOUBLE | **0.8%** | 17,350 rows |
| `gtin` | INTEGER¹ | **0%** | declared, never populated |
| `brand` | INTEGER¹ | **0%** | declared, never populated |
| `size` | INTEGER¹ | **0%** | declared, never populated |
| `promo` | INTEGER¹ | **0%** | declared, never populated |
| `category` | INTEGER¹ | **0%** | declared, never populated |
| `source` | INTEGER¹ | **0%** | declared, never populated |

¹ **These columns are typed `INTEGER` because they are entirely NULL.** In the parts table the same
columns are `VARCHAR`. pyarrow infers the type from the data, and an all-null column infers to
`int64` — so the fold has silently changed the type of six columns. This is exactly the schema-drift
class `unifyd/table_spec.py` exists to prevent, and `ubereats_products` is not one of the 6 tables
that declares a spec. A future writer that emits a real string `brand` into this table will fail the
Parquet write with `Could not convert '<brand>' with type str: tried to convert to int64`.

The parts table carries five extra columns the fold drops: `section`, `subsection`, `section_name`,
`subsection_name`, `category_path`.

---

## 7. What is actually covered today — read this part

| | |
|---|---|
| Stores in the universe | **755,032** |
| Stores with a catalog landed | **24,845** |
| **Coverage** | **3.3%** |
| Distinct items | 2,062,871 |
| Items with a UPC | 66,192 (**3.1%**) |
| Items with ABV | 17,350 (0.8%) |
| Items with brand / size / category / GTIN | **0** |

**Postmates is effectively not running**: 269,007 stores in the universe, **35** with a catalog. It
is registered `enabled=True`, daily, 8 shards, and has been landing essentially nothing.

So the honest one-line summary of what Uber Eats gives us today is:
**store, item, name, price and stock, for 3.3% of US stores.** Not brand, not size, not category,
and a UPC on one item in thirty.

That is not a reason to distrust the pipeline — the transport works, and 2.06M items is real. It is
a statement of where the work is: **coverage** (3.3% of stores) and **attribute capture** (five
declared fields at zero).

---

## 8. Known gaps and their causes

| gap | evidence | likely cause |
|---|---|---|
| 3.3% store coverage | 24,845 / 755,032 | the daily sweep is not completing its shards; check `source_runs_log` for shard exit status and whether all 8 dispatch |
| `brand`/`size`/`category`/`gtin` at 0% | 0 non-null across 29.9M parts rows | `ubereats._items_from_store` declares these in `PRODUCT_FIELDS` but the getStoreV1 parse never assigns them — the parse walks `catalogSectionsMap` and takes title/price/uuid only |
| UPC at 3.1% | 66,192 items | the catalog sweep runs `--no-enrich` by design; `ubereats-enrich` is the backfill and has not covered the item book |
| Postmates at 35 stores | 35 / 269,007 | same code path as ubereats; needs a run-log check before any code change |
| Six columns typed INTEGER | live schema read | all-null inference in `fold.py`; fixed by declaring `ubereats_products` in `table_spec.py` |
| No unit test | `unifyd/ue_catalog_test.py` absent | a parse regression is caught only in production |

**None of these are inferred from the code alone — every one is a measured number from the live
warehouse.** The fill rates in particular were invisible before this pass: the table has 21 columns
and reads as a rich capture until you count the non-nulls.

---

## 9. Rebuild checklist

1. Fetch the 26 gzipped sitemaps direct, no proxy → store url ids.
2. `url_id_to_uuid()` — base64url decode, 16 bytes → dashed UUID.
3. `curl_cffi` session, `impersonate="safari17_0"`, the seven headers above, `x-csrf-token: x`.
4. `POST /_p/api/getStoreV1` with `{"storeUuid": ...}` → walk `catalogSectionsMap` for items.
5. ≤32 workers per process. More returns empty. Shard across machines instead.
6. Re-prime the session every 40 requests.
7. `POST /_p/api/getMenuItemV1` **with section context** for UPC/GTIN — once per item, ever.
8. Land in batches of 400 stores with a completed-store checkpoint; append-only parts.
9. Fold parts → catalog with a watermarked single writer.

**Cost: $0.** No proxy, no browser, no third-party unblocker, on a bare datacenter IP.

---

## 10. Source files

| file | lines | role |
|---|---:|---|
| `unifyd/ue_catalog.py` | 1,009 | the sweep — sharding, pacing, batching, checkpointing |
| `unifyd/getstore.py` | 520 | the `getStoreV1` transport and the escalation ladder |
| `unifyd/ubereats.py` | 694 | `_items_from_store` — the catalog payload parse |
| `unifyd/ue_enrich.py` | 221 | the `getMenuItemV1` UPC/GTIN backfill |
| `unifyd/ue_sitemap.py` | 108 | the universe |
| `unifyd/ue_ids.py` | 107 | url id ↔ uuid, and its at-scale verification |
| `unifyd/ue_crawl.py` | 577 | the bounded zone crawler (`ubereats-full`, disabled) |
| `unifyd/fold.py` | 278 | parts → catalog |

Tests present: `ue_ids_test.py`, `ue_enrich_backlog_test.py`. **No test covers `ue_catalog` or the
`_items_from_store` parse.**
