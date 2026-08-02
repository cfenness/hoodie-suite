#!/usr/bin/env python3
"""ue_catalog.py — the UberEats/Postmates catalog sweep, store-list driven, headless, SHARDABLE.

WHY THIS EXISTS
---------------
The registered `ubereats` source ran `ubereats.crawl(max_stores=1000)`: a headful browser discovering
stores by ZONE. Against a **502,212-store** universe that is 0.2%, and the bound lived in the registry
where nobody reads it — the source presented itself as "UberEats" while covering a rounding error of it.
We don't do caps: a scrape (or a designed series of them) covers its universe inside a day, and if one
worker can't, the answer is parallelism, never truncation.

THE ARITHMETIC (do this before writing a crawler)
    502,212 stores / 86,400s = ~5.8 stores/sec sustained to finish in a day.
A headful browser cannot approach that and cannot be sharded — it discovers stores by zone rather than
taking a list. So this uses the path that can:

  • `ubereats_sitemap` (502,212 rows) is the UNIVERSE — already harvested, $0, refreshes cleanly.
  • `getstore.fetch_store()` is a COLD curl_cffi POST to getStoreV1: no browser, no Bright Data, no
    warmed cookie. The sitemap's url id IS base64url(uuid bytes), so `url_id_to_uuid()` converts with
    no lookup — which is what makes the whole universe directly addressable.
  • `ubereats._items_from_store()` already parses the getStoreV1 catalog shape (price, promo, size/ABV,
    GTIN where present). Both halves existed; they had simply never been connected.

Being list-driven is what makes it SHARDABLE: `--shard i/N` splits the universe deterministically by a
stable hash of the store id, so N ephemeral machines cover disjoint slices with no coordination. That is
the "series of scrapes" — one job per shard, each resumable, all landing to the same tables.

RESUME + LANDING
Same contract as abc_fws_scraper, for the same reasons: land in BATCHES and checkpoint the completed
store ids, so a killed shard keeps everything it fetched and the next run continues instead of
restarting. A sweep that can only land at the end throws away hours on any interruption.

    python ue_catalog.py                      # whole universe, one process
    python ue_catalog.py --shard 3/16         # shard 3 of 16 (what the fleet runs)
    python ue_catalog.py --site postmates     # same recipe, different domain
"""
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import getstore
import observe
import ubereats
import warehouse

# MEASURED, not inherited. A 240-store calibration on this endpoint: 32 workers -> 132 stores with a
# catalog and 10,677 items; 64 -> ZERO; 128 -> 1; 192 -> 1. Above ~32 the BFF stops answering and
# returns empty FAST, so the higher counts look 3x quicker while collecting nothing. 64 came from
# aggregator_geo (a geo-only sweep with far smaller payloads) and does not transfer.
# Throughput comes from SHARDS across machines, not threads on one.
# CONCURRENCY IS DERIVED FROM THE POOL, not guessed — and the number is MEASURED, then re-measured.
#
# History, because the first number was wrong in a way worth remembering: 3/IP came from a run where
# the endpoint was already refusing us (8 shards x 24 workers = ~10/IP, 96% empty responses). Calibrating
# a ceiling while being throttled reads the floor of the failure, not the top of the healthy range, so
# it was far too conservative and quietly cost throughput for every run since.
#
# Re-measured 2026-07-28 with a deliberate probe: one machine at 40 workers alongside a healthy 8-shard
# fleet (~96 concurrent, ~4.8/IP). unreachable stayed 0 on BOTH the probe and every fleet shard — no
# throttling anywhere near the old limit. Raised to 6/IP, a graduated step past what is proven rather
# than a jump to the probe's implied ceiling.
#
# Scaling is SUB-LINEAR: the probe's 5.7x workers bought 3.6x throughput (per-worker efficiency fell
# ~40%). So more concurrency still pays, but do not extrapolate linearly — that arithmetic has been
# wrong every time today. Raise it, measure `unreachable`, and let the tripwire catch an overshoot.
PER_IP = float(os.environ.get("UE_PER_IP", "6"))
_last_beat = [0.0]                                         # wall-clock of the last heartbeat emitted
_last_rep_save = [0.0]                                     # wall-clock of the last reputation bank
REP_SAVE_S = float(os.environ.get("UE_REP_SAVE_S", "300"))  # how often exit reputation is persisted


def _rss_mb():
    """This process's resident memory, MB. Reported in every heartbeat so an OOM kill is diagnosable
    from the journal alone — the last beat before SIGKILL says how much was held and at what stage.
    Two OOM'd fleets were spent guessing at this; the run should simply state it."""
    try:
        with open("/proc/self/status") as f:
            for ln in f:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1]) // 1024
    except Exception:
        pass
    return None


def auto_workers(nshard=1, log=print):
    """Workers for THIS shard: (pool_size * PER_IP) / shards, floored at 4. An explicit UE_WORKERS
    still wins, but the default now scales with the resource that actually binds — exit IPs."""
    env = os.environ.get("UE_WORKERS")
    if env:
        return int(env)
    try:
        import resi
        pool = len(resi.isp_pool()) or 1
    except Exception:
        pool = 1
    w = max(4, int(pool * PER_IP / max(1, nshard)))
    log("[ue] workers=%d (pool=%d IPs x %.1f/IP / %d shards) — the exit-IP budget, not a guess"
        % (w, pool, PER_IP, nshard))
    return w


WORKERS = int(os.environ.get("UE_WORKERS", "24"))
BATCH_STORES = int(os.environ.get("UE_BATCH", "400"))  # stores per landed batch + checkpoint
# FLUSH ON BYTES, NOT ON COUNT. Every enriched item carries its full `raw_json` payload (DISCARD
# NOTHING), so item count says nothing about memory: 8,000 thin items is a few MB, 8,000 fat ones is
# gigabytes. Bounding the buffer by rows while paying for it in bytes is what OOM-killed the first
# un-throttled fleet — the shards finally got far enough to accumulate real volume. This caps the
# buffer by what actually consumes RAM, so the ceiling holds however fat the payloads turn out to be.
FLUSH_BYTES = int(os.environ.get("UE_FLUSH_MB", "192")) * 1024 * 1024
PRODUCT_FIELDS = ["store_uuid", "store_name", "source", "item_uuid", "name", "brand", "upc", "gtin",
                  "price", "list_price", "promo", "size", "abv", "in_stock", "stock_label", "category",
                  # THE WRITE SCHEMA IS THE ONLY SCHEMA THAT MATTERS. Parsing a field and not listing it
                  # here means it is computed and then silently dropped at the write — which is exactly
                  # what happened to the category path: captured in ubereats.UE_FIELDS, absent here, so
                  # every breadcrumb was thrown away while a test asserting on the PARSE list passed.
                  # section/subsection are the getMenuItemV1 request context enrichment needs; the *_name
                  # pair and category_path are the retailer's own hierarchy.
                  "section", "subsection", "section_name", "subsection_name", "category_path",
                  "raw_json"]
MENU_API = "https://www.ubereats.com/_p/api/getMenuItemV1"
ENRICH = os.environ.get("UE_ENRICH", "1") == "1"     # per-item UPC/GTIN detail — ON by default
KNOWN = set()                                        # items already resolved; enrichment skips these


def _menu_api(site):
    return MENU_API if site == "ubereats" else MENU_API.replace("www.ubereats.com", "postmates.com")


def known_items(site="ubereats", log=print):
    """item_uuids already carrying a UPC/GTIN — the set enrichment can SKIP.

    This is what makes a daily run possible. UPC, GTIN, brand, size, ABV and classifications are
    STATIC per item; price, promo and stock are the volatile fields, and those come from the catalog
    call we make anyway. Re-enriching a resolved item every day would spend ~30M requests re-learning
    immutable facts, which is precisely what would make a full daily pass impossible.

    So: day one is a backfill, and steady state is (every store's catalog) + (only genuinely NEW
    items). A store adding a product is cheap; a store that has not changed costs one call.
    """
    try:
        rows = warehouse.query(
            "%s_products" % site,
            "SELECT DISTINCT item_uuid FROM t WHERE item_uuid IS NOT NULL "
            "AND (COALESCE(upc,'') <> '' OR COALESCE(gtin,'') <> '')")
        # A COMPACT set, not a Python one. At full coverage this corpus is ~9.5M ids, which costs 1,078MB
        # as a set of strings on a 4GB box that has already given DuckDB half its memory — the same
        # "memory scales with the corpus" failure that killed three fleets, just one order of magnitude
        # out. Sorted 64-bit digests + binary search: 72MB for the same answer. See idset.py for the
        # collision trade (vanishingly rare, and it fails toward keeping data we already have).
        import idset
        known = idset.IdSet.from_ids(r["item_uuid"] for r in rows if r.get("item_uuid"))
        log("[ue] %s items already resolved (enrichment will skip them; index %s MB)"
            % (f"{len(known):,}", f"{known.bytes() // 1048576:,}"))
        return known
    except Exception as e:
        # No table yet (first run) or an unreadable read: enrich everything. Failing OPEN costs
        # requests; failing closed would silently skip enrichment and quietly ship UPC-less data.
        log("[ue] no prior item book (%s) — enriching all items this pass" % str(e)[:70])
        return set()


def enrich_items(su, store_name, items, idx, site="ubereats", known=None):
    """Upgrade catalog items (title+price, NO upc) to full getMenuItemV1 detail — UPC/GTIN plus
    classifications, itemAttributeInfo, customizations, promos, images.

    COLD. Proven live: getMenuItemV1 answers HTTP 200 with real UPCs using the same minimal header set
    as getStoreV1 — no browser, no captured x-uber-* headers, no proxy (Food Lion 00037700322286,
    Walgreens 00073854008089). The headful enrich_store() with its learned-header replay and
    max_items=250 exists only because nobody tested the cold path.

    Done in the SAME pass as the catalog, not a second sweep: we already hold each item's section
    context here, so re-crawling 502k stores later purely to add UPC would repeat the abc-catalog
    mistake — two full crawls of identical pages for data present in one visit.

    NO per-store item cap: every item the store lists gets enriched.

    PACED AND CLASSIFIED, same as fetch_store — this loop used to issue its own POST directly, with
    no pace.acquire()/report() and no blocks.classify()/identity_router.record()/ladder.report().
    That made real per-shard request rate an unmeasured multiple of the intended 5 req/s (proportional
    to unresolved items per store) and invisible to every health signal the fleet reacts to — a shard
    could be silently driving its exits well past the pacer's target while every diagnostic field
    (pace_rate, isp.success_pct) kept describing only the catalog call next to it.
    """
    s = getstore._session(site)
    H = dict(getstore._H)
    api = _menu_api(site)
    known = known or set()
    for rec in items:
        iu = rec.get("item_uuid")
        if not iu or iu in known:
            continue                      # already resolved — its UPC/dimensions cannot have changed
        ctx = idx.get(iu) or {}
        body = {"storeUuid": su, "menuItemUuid": iu, "sectionUuid": ctx.get("section", ""),
                "subsectionUuid": ctx.get("subsection", ""), "cbType": "EATER_ENDORSED"}
        # PACE BEFORE THE REQUEST — same control point as fetch_store (aggregate rate, not worker
        # count, is what the endpoint actually caps).
        _p = None
        try:
            import pace
            _p = pace.get()
            if _p:
                _p.acquire()
        except Exception:
            _p = None
        try:
            r = s.post(api, json=body, headers=H, timeout=25)
            data = ubereats._menu_item_data(r.json()) if r.content else None
        except Exception as e:
            try:
                import blocks
                cls = blocks.classify(exc=e)
                t = blocks.get()
                if t:
                    t.record(cls, method=getstore._method(), exit=getstore._exit_ip())
                try:
                    import identity_router
                    identity_router.record(getstore._exit_ip(), getstore._costume(site), cls)
                except Exception:
                    pass
                if _p:
                    _p.report(not blocks.is_throttle(cls))
            except Exception:
                if _p:
                    _p.report(False)
            continue
        # CLASSIFY, then feed the controller only REAL pushback — an item with no detail (e.g. 86'd,
        # or the endpoint legitimately has nothing more to add) is not itself a throttle signal; the
        # response's status/body is what tells blocks.classify() which one actually happened.
        try:
            import blocks
            cls = blocks.classify(status=getattr(r, "status_code", None),
                                  body=(r.text[:4000] if getattr(r, "text", None) else ""),
                                  has_payload=bool(data))
            t = blocks.get()
            if t:
                t.record(cls, method=getstore._method(), exit=getstore._exit_ip())
            try:
                import identity_router
                identity_router.record(getstore._exit_ip(), getstore._costume(site), cls)
            except Exception:
                pass
            try:
                import ladder
                ladder.report(site, cls)
            except Exception:
                pass
            if _p:
                _p.report(not blocks.is_throttle(cls))
        except Exception:
            if _p:
                _p.report(bool(data))
        if not data:
            continue
        try:
            full = ubereats.parse_item(data, su, store_name)
        except Exception:
            continue
        # DISCARD NOTHING: keep every modelled field the detail adds, and the whole payload beside it.
        for k, v in (full or {}).items():
            if v not in (None, "", []) and k in PRODUCT_FIELDS:
                rec[k] = v
        try:
            rec["raw_json"] = json.dumps(data, default=str)
        except Exception:
            pass
    return items


# A THROTTLED RESPONSE IS NOT AN EMPTY STORE. Under load the BFF returns a well-formed 200 with no
# catalog in it — not None, so the `if not data` guard sails straight past, the store is marked done,
# and the checkpoint records coverage for a store we never actually read. Measured on the first
# catalog-only fleet: 242,503 stores marked done, 10,040 with any items. 4%. The sweep was "fast"
# because it was mostly recording nothing at 390 stores/sec.
#
# The discriminator is STRUCTURAL: a real store response carries the catalog container (even when the
# store legitimately has nothing in it), while a throttled one has no catalog structure at all. So a
# store with a container and zero items is genuinely empty and counts as covered; a store with no
# container is unreachable and must be retried, never checkpointed.
_CATALOG_KEYS = ("catalogSectionsMap", "catalog", "sections", "catalogSections", "menuItems")


def has_catalog(data):
    """True if this payload actually contains a catalog structure (however empty)."""
    if not isinstance(data, dict):
        return False
    if any(k in data for k in _CATALOG_KEYS):
        return True
    # nested one level — the container is not always at the root
    for v in data.values():
        if isinstance(v, dict) and any(k in v for k in _CATALOG_KEYS):
            return True
    return False


# STOP PAYING FOR STORES THAT ARE GONE. ~25% of the universe returns no catalog container at all —
# closed, delisted, or never real. Against a FIXED request budget (the endpoint caps aggregate rate, so
# more machines buy nothing) every one of those is a request that could have fetched a live store. Skipping
# them is the largest speedup available: 502,212 -> ~377k requests, 3.5h -> 2.6h at 40 req/s.
#
# Three strikes on three SEPARATE DAYS, not three in a row within a run — a throttled window would
# otherwise blacklist thousands of perfectly good stores in one bad minute. Misses age out of a rolling
# window, so a store that comes back to life is retried automatically without any resurrection logic.
MISS_TABLE = "%s_store_misses"
MISS_DAYS = int(os.environ.get("UE_MISS_DAYS", "14"))
MISS_STRIKES = int(os.environ.get("UE_MISS_STRIKES", "3"))


def dead_stores(site="ubereats", log=print):
    """url_ids that have failed to produce a catalog on MISS_STRIKES separate days inside the window."""
    try:
        rows = warehouse.query_parts(
            MISS_TABLE % site,
            "SELECT url_id, count(DISTINCT day) AS days FROM t "
            "WHERE day >= strftime(now() - INTERVAL %d DAY, '%%Y-%%m-%%d') "
            "GROUP BY 1 HAVING count(DISTINCT day) >= %d" % (MISS_DAYS, MISS_STRIKES))
    except Exception as e:
        log("[ue] no miss history (%s) — trying every store this pass" % str(e)[:60])
        return set()                      # fail OPEN: never skip a store because bookkeeping is missing
    out = {r["url_id"] for r in (rows or []) if r.get("url_id")}
    if out:
        log("[ue] skipping %s stores dead on >=%d of the last %d days (saves that many requests)"
            % (f"{len(out):,}", MISS_STRIKES, MISS_DAYS))
    return out


def _progress(**kw):
    """Machine-readable progress for Hoodie Collect's live counters."""
    try:
        _last_beat[0] = time.time()
        print("HOODIE_PROGRESS " + json.dumps(kw), flush=True)
    except Exception:
        pass


def universe(site="ubereats", log=print):
    """Every store in the sitemap book for `site` → [(url_id, name)]. This is the DENOMINATOR; state it
    up front so completeness is answerable from the first log line."""
    try:
        rows = warehouse.query(
            "ubereats_sitemap",
            "SELECT store_uuid, store_name FROM t WHERE source = ? AND store_uuid IS NOT NULL",
            [site])
    except Exception as e:
        log("[ue] universe read failed: %s" % str(e)[:140])
        return []
    out, seen = [], set()
    for r in rows:
        u = r.get("store_uuid")
        if u and u not in seen:
            seen.add(u)
            out.append((u, r.get("store_name") or ""))
    return out


def _shard_of(store_id, n):
    """Deterministic, stable shard for a store id. A hash (not an index) so the assignment does not move
    when the sitemap grows — a shard resuming tomorrow still owns the same stores."""
    return int(hashlib.md5(str(store_id).encode()).hexdigest()[:8], 16) % n


# ── resume: completed store ids per (day, site, shard) ───────────────────────────────────────────
def _ck_key(day, site, shard, nshard):
    return "_collect/resume/ue_catalog_%s_%s_%s-%s.json" % (site, day, shard, nshard)


def _ck_load(day, site, shard, nshard, log=print):
    try:
        raw = warehouse.get_bytes(_ck_key(day, site, shard, nshard))
        done = set(json.loads(raw).get("done") or []) if raw else set()
        if done:
            log("[ue] resuming shard %s/%s — %s stores already done today" % (shard, nshard, f"{len(done):,}"))
        return done
    except Exception:
        return set()


def _ck_save(day, site, shard, nshard, done, batch):
    try:
        warehouse.put_bytes(_ck_key(day, site, shard, nshard),
                            json.dumps({"done": sorted(done), "batch": batch,
                                        "at": int(time.time())}).encode())
    except Exception:
        pass                              # checkpointing must never fail the sweep


def _land(site, day, idx, shard, items, log=print):
    """Land one batch: the per-store catalog rows + the dated observation time-series. Unique part name
    per (shard, batch) so concurrent shards never overwrite each other's partition — the failure mode
    write_partition warns about."""
    if not items:
        return 0
    tbl = "%s_products" % site
    # THE PAYLOAD IS AN EVENT, NOT AN ATTRIBUTE. raw_json goes to the append-only raw_payloads table, and
    # the catalog carries only modelled columns. write_accumulate merges by rewriting the WHOLE table, so
    # a payload column is re-read and re-written on every flush — memory scaling with the table instead of
    # the batch, and getting worse the more the crawl succeeds. Nothing is discarded: the full payload is
    # kept, in the place whose cost is O(batch) forever. It also becomes history rather than a value that
    # the next pull overwrites.
    try:
        import raw_capture
        raw_capture.record(site, day, "s%02d_b%04d" % (shard, idx),
                           [{"kind": "item", "entity_id": it.get("item_uuid"),
                             "parent_id": it.get("store_uuid"), "raw_json": it.get("raw_json")}
                            for it in items], log=log)
    except Exception as e:
        log("  [ue] raw capture failed: %s" % str(e)[:110])
    lean = [k for k in PRODUCT_FIELDS if k != "raw_json"]
    # NEVER MERGE FROM A SHARD. write_accumulate is read-modify-write with no lock: it reads the whole
    # table, drops the keys it is replacing, and OVERWRITES. Two shards doing that at once is a lost
    # update — the second writer silently discards everything the first just landed. Observed live the
    # moment the fleet got healthy enough to overlap: ubereats_products went 33,250 -> 8,798 DOWN while
    # eight shards were writing. It was always broken; shards used to die before they could collide.
    #
    # Shards write their own append-only PART instead — no read, no overwrite, no race, the same shape
    # that has never lost a row in raw_payloads or retail_observations. A single-writer consolidation
    # (consolidate(), run as a build) folds the parts into the canonical catalog afterwards.
    try:
        import pyarrow as pa
        # Pin every field's type explicitly. Without this, a batch where e.g. `brand` happens to be
        # None for every item in THIS batch infers as an int64/null column, while another batch with
        # real brand strings infers VARCHAR for the same field — and query_parts()'s union_by_name
        # across all partitions then has to reconcile VARCHAR against INTEGER for one column, which
        # corrupts the read (an invalid-UTF8 decode error) instead of failing loudly. Measured live
        # 2026-07-30: exactly this, across 5 distinct per-file schemas in ubereats_products_parts — the
        # registered consolidation build had been silently broken, "no accounts landing" traced back to
        # a write-time schema bug, not a scheduling gap.
        _numeric = {"price": pa.float64(), "list_price": pa.float64(), "abv": pa.float64()}
        _bool = {"in_stock": pa.bool_()}
        dtypes = {f: _numeric.get(f, _bool.get(f, pa.string())) for f in lean}
        warehouse.write_partition(
            tbl + "_parts", "%s_s%02d_b%04d" % (day, shard, idx),
            [{k: it.get(k) for k in lean} for it in items], fields=lean, dtypes=dtypes)
    except Exception as e:
        log("  [ue] %s land failed: %s" % (tbl, str(e)[:110]))
        return 0
    try:
        observe.record(site, [{"source": site, "store": it.get("store_name") or "",
                               "store_id": it.get("store_uuid"), "product_id": it.get("item_uuid"),
                               "upc": it.get("upc") or "", "gtin": it.get("gtin") or "",
                               "brand": it.get("brand") or "", "name": it.get("name") or "",
                               "price": it.get("price"), "promo": it.get("promo"),
                               "on_promo": bool(it.get("promo")),
                               "in_stock": bool(it.get("in_stock", True)),
                               "stock_level": it.get("stock_label") or ""}
                              for it in items],
                      date=day, part="%s_%s_s%02d_b%04d" % (day, site, shard, idx), log=log)
    except Exception as e:
        log("  [ue] observe failed: %s" % str(e)[:110])
    return len(items)


def repair_checkpoints(site="ubereats", day=None, nshard=8, log=print):
    """Rebuild today's checkpoints from WHAT ACTUALLY LANDED, dropping phantom coverage.

    A checkpoint is a claim ("we've done these stores"); the parts table is evidence. When those two
    disagree the evidence wins — otherwise the claim silently becomes truth on the next run, because a
    checkpointed store is SKIPPED. Measured after the first catalog-only fleet: 242,503 stores
    checkpointed, 10,040 with any landed rows. Left alone, the next sweep would skip a quarter of a
    million stores it never read and report high coverage over them, compounding the lie instead of
    correcting it.

    So: keep a store only if it produced rows today. Everything else returns to the work-list. This is
    deliberately lossy in the safe direction — a genuinely-empty store gets re-fetched, which costs one
    request; a phantom left in place costs us the store entirely, silently, forever.
    """
    day = day or time.strftime("%Y-%m-%d")
    tbl = "%s_products_parts" % site
    try:
        rows = warehouse.query_parts(tbl, "SELECT DISTINCT store_uuid FROM t")
    except Exception as e:
        log("[repair] cannot read %s (%s) — refusing to touch checkpoints" % (tbl, str(e)[:80]))
        return 1
    landed = {r["store_uuid"] for r in (rows or []) if r.get("store_uuid")}
    log("[repair] %s stores have landed rows" % f"{len(landed):,}")
    if not landed:
        log("[repair] no landed rows at all — refusing to blank every checkpoint")
        return 1
    before = after = 0
    for sh in range(nshard):
        done = _ck_load(day, site, sh, nshard, log=lambda *_: None)
        if not done:
            continue
        keep = {u for u in done if getstore.url_id_to_uuid(u) in landed}
        before += len(done)
        after += len(keep)
        _ck_save(day, site, sh, nshard, keep, 0)
        log("[repair] shard %d/%d: %s -> %s (dropped %s phantom)"
            % (sh, nshard, f"{len(done):,}", f"{len(keep):,}", f"{len(done)-len(keep):,}"))
    log("[repair] TOTAL %s -> %s checkpointed (%s phantom stores returned to the work-list)"
        % (f"{before:,}", f"{after:,}", f"{before-after:,}"))
    return 0


def consolidate(site="ubereats", rebuild=False, log=print):
    """SINGLE-WRITER fold of the shards' append-only parts into the canonical catalog.

    Shards cannot merge (see _land): concurrent read-modify-write loses rows. So they append parts, and
    exactly one process — this one, run as a build after the fleet finishes — does the merge. Latest
    observation per (store_uuid, item_uuid) wins.

    Deliberately reads from the parts, not from the catalog it is about to write, so a partial or failed
    consolidation can be re-run without compounding. Idempotent: running it twice yields the identical
    table (verified live — 55,406 parts -> 151,748 rows on both passes).

    `rebuild=True` makes the catalog a PURE FUNCTION OF THE PARTS: the table is replaced, not merged, so
    every row is traceable to a part file and a from-scratch rebuild reproduces it exactly. That is the
    difference between consistent and CLEAN — the default merge is additive and therefore inherits
    whatever was in the table before, including ~98k rows this catalog accumulated through the
    write_accumulate race, whose provenance cannot be stated. Use rebuild once the parts cover the
    universe; use the default while parts are only a partial pass, or the rebuild would DISCARD the
    coverage the parts do not yet include.
    """
    tbl = "%s_products" % site
    rows = warehouse.query_parts(tbl + "_parts", "SELECT * FROM t") if hasattr(warehouse, "query_parts") \
        else warehouse.query(tbl + "_parts", "SELECT * FROM t")
    rows = list(rows or [])
    if not rows:
        log("[ue] consolidate: no parts to fold")
        return 0
    latest = {}
    for r in rows:                        # later parts overwrite earlier ones for the same identity
        latest[(r.get("store_uuid"), r.get("item_uuid"))] = r
    out = list(latest.values())
    fields = [k for k in PRODUCT_FIELDS if k != "raw_json"]
    log("[ue] consolidate: %s part rows -> %s distinct items (%s)"
        % (f"{len(rows):,}", f"{len(out):,}", "REBUILD — catalog replaced by parts" if rebuild else "merge"))
    if rebuild:
        # A rebuild REPLACES. Refuse to shrink the catalog without the caller having said so explicitly:
        # a rebuild from partial parts is exactly how a full catalog gets truncated to one pass's worth,
        # which is the failure this whole day has been about.
        try:
            have = warehouse.row_count(tbl)
        except Exception:
            have = 0
        if have and len(out) < have * 0.9:
            raise RuntimeError(
                "consolidate(rebuild=True) would cut %s to %s rows — parts do not cover the catalog. "
                "Run the fleet to completion first, or use the default merge."
                % (f"{have:,}", f"{len(out):,}"))
        warehouse.write_parquet(tbl, out, fields=fields)
    else:
        warehouse.write_accumulate(tbl, out, key=("store_uuid", "item_uuid"),
                                   fields=fields, coverage=False)
    return len(out)


def run(site="ubereats", shard=0, nshard=1, workers=None, log=print):
    """Sweep this shard of the universe. Returns a run record. NO cap: the only bound is the shard."""
    # GIVE THIS SHARD ITS OWN EXIT SLICE before anything else runs. Measured 2026-07-29 (§1e): with
    # nshard independent processes all drawing from the same pool, identity_router's per-process
    # concentration-avoidance can't see across processes — shards silently collide on the same exits.
    # set_shard makes every getstore._session() call in this process draw only from its own disjoint
    # slice, so the collision can't happen structurally. A no-op for nshard=1 (single-shard runs,
    # pool_health, costume_probe — none of which call this).
    try:
        import getstore
        getstore.set_shard(shard, nshard)
    except Exception:
        pass
    workers = workers or auto_workers(nshard, log=log)
    # RATE IS THE REAL BUDGET; workers just decide how many can be in flight while we wait for tokens.
    try:
        import blocks
        blocks.install()                  # per-(method, class) counts for this run
    except Exception:
        pass
    try:
        import pace
        _pacer = pace.install(pace.shard_rate(nshard))
        log("[ue] paced at %.2f req/s for this shard (fleet %.0f/s across %d shards) — AIMD from here"
            % (_pacer.rate, pace.FLEET_RATE, nshard))
    except Exception as e:
        log("[ue] pacing unavailable (%s) — running unpaced" % str(e)[:60])
    # INHERIT WHAT THE LAST RUN PAID TO LEARN. Exit reputation used to die with the process, so every
    # restart re-probed addresses the fleet already knew were burned — which does not merely waste the
    # request, it re-flags the address. Reading EVERY shard's file (not just our own) is deliberate:
    # what shard 2 spent requests learning is equally true for shard 5.
    try:
        import identity_router
        identity_router.load(site, nshard, log=log)
    except Exception as e:
        log("[ue] reputation carry-over unavailable (%s) — starting cold" % str(e)[:60])
    day = time.strftime("%Y-%m-%d")
    uni = universe(site, log=log)
    if not uni:
        return {"status": "failed", "error": "store universe empty — is ubereats_sitemap populated?"}
    mine = [(u, n) for (u, n) in uni] if nshard == 1 else \
           [(u, n) for (u, n) in uni if _shard_of(u, nshard) == shard]
    log("[ue] universe %s stores; shard %s/%s owns %s (the completeness denominator)"
        % (f"{len(uni):,}", shard, nshard, f"{len(mine):,}"))
    # The skip-list comes off the work-list, but the DENOMINATOR above already stated the full shard —
    # a skipped store is still part of the universe we are accountable for, and quietly shrinking the
    # denominator is how a pull starts grading itself against the easy half.
    _dead = dead_stores(site, log=log)
    if _dead:
        mine = [(u, n) for (u, n) in mine if u not in _dead]
        log("[ue] %s live stores to attempt this pass (%s skipped as dead)"
            % (f"{len(mine):,}", f"{len(_dead):,}"))

    global KNOWN
    KNOWN = known_items(site, log=log) if ENRICH else set()
    done = _ck_load(day, site, shard, nshard, log=log)
    todo = [(u, n) for (u, n) in mine if u not in done]
    log("[ue] %s remaining this pass (%s workers)" % (f"{len(todo):,}", workers))

    pending, batch_idx, n_items, n_ok, n_empty, n_fail = [], 0, 0, 0, 0, 0
    pend_bytes = [0]                                   # approx heap held by `pending`, for the size cap
    misses = []                                        # url_ids that returned no catalog this pass
    qa_rows = []                                       # sample of landed rows, for output validation
    import threading
    lock = threading.Lock()

    def _flush(force=False):
        nonlocal pending, batch_idx, n_items
        if not pending or (not force and len(pending) < BATCH_STORES * 20
                           and pend_bytes[0] < FLUSH_BYTES):
            return
        batch_idx += 1
        n_items += _land(site, day, batch_idx, shard, pending, log=log)
        pending = []
        pend_bytes[0] = 0
        _ck_save(day, site, shard, nshard, done, batch_idx)

    def _beat():
        """Liveness that does not require SUCCESS. The heartbeat used to live only in the branch where a
        store came back with items, so a shard failing every single request emitted nothing at all — and
        we read that silence twice as "no data yet" when it was the whole story. A run that is failing is
        still a run that is alive, and it must say so, with its failure count visible."""
        if (time.time() - _last_beat[0]) <= 60:
            return
        # THE LIVE PACING RATE, not just the one we started with. The startup log says what the pacer was
        # INITIALISED to; AIMD moves it continuously, and until now that trajectory was only visible in
        # the end-of-run record. That is the same blind spot that had me reporting a 60-second-stale
        # success rate as a headline: an instrument that only reports at the end cannot tell you what is
        # happening now. Convergence should be watchable while it converges.
        _pc = {}
        try:
            import pace
            _p = pace.get()
            if _p:
                _pc = _p.stats()
        except Exception:
            pass
        _bk = {}
        try:
            import blocks
            _t = blocks.get()
            if _t:
                _bk = _t.flat()           # {'isp.soft_block': 12, 'isp.success_pct': 93.1, ...}
                # TWO SCALARS, not the whole per-exit map — a heartbeat carrying 50 exits every beat is
                # noise nobody reads. These two are enough to tell, live, whether a falling success rate
                # is a handful of burned addresses or the whole pool, which are different emergencies.
                _ev = _t.exit_verdict()
                _bk["exit_pattern"] = _ev.get("pattern")
                _bk["exits_burned"] = len(_ev.get("below_median_25pp") or [])
        except Exception:
            pass
        # TOP-3 hot exits, not the whole map — same "two scalars, not 50" discipline as _bk above. This
        # is the field a fleet-level aggregator needs and no single shard has any use for on its own: two
        # shards' heartbeats naming the SAME exit at the SAME moment is the cross-shard concentration
        # collision (COLLECT-HANDOFF.md §1e) that neither shard's own metrics can see.
        _hot = []
        try:
            import identity_router
            _hot = [ip for ip, _n in identity_router.hot_exits(top=3)]
        except Exception:
            pass
        # WHICH RUNG THIS SHARD IS ACTUALLY ON — a shard silently escalated to `browser` (serialized,
        # unproven from a datacenter IP, see getstore._fetch_browser/ladder.py) looks identical to a
        # healthy shard on every OTHER field in this heartbeat except a collapsed pace_rate; nothing
        # said WHY. Missing this was the gap that made a burned-exit-slice theory and a bad-rung theory
        # indistinguishable from the outside.
        _rung = None
        try:
            import ladder
            _rung = ladder.current(site)
        except Exception:
            pass
        _progress(rows=n_items + len(pending),
                  stage="%s/%s stores" % (f"{len(done):,}", f"{len(mine):,}"),
                  pct=round(100.0 * len(done) / max(1, len(mine)), 1),
                  ok=n_ok, empty=n_empty, unreachable=n_fail,
                  rss_mb=_rss_mb(),
                  pace_rate=_pc.get("rate"), pace_backoffs=_pc.get("backoffs"),
                  pace_increases=_pc.get("increases"), pace_at_floor=_pc.get("at_floor"),
                  # IS THE BUDGET BEING SPENT? pace_rate alone is a ceiling; these say whether we are
                  # anywhere near it, and therefore whether concurrency is even the right dial.
                  pace_achieved=_pc.get("achieved"), pace_utilization=_pc.get("utilization"),
                  pace_wait_ms=_pc.get("wait_ms"),
                  hot_exits=_hot, ladder_rung=_rung,
                  **_bk)
        # Bank reputation on a SLOWER cadence than the beat. This is a warehouse round trip per shard,
        # and the thing it protects against is losing hours of learning to a kill -9 — not losing the
        # last thirty seconds of it.
        if (time.time() - _last_rep_save[0]) > REP_SAVE_S:
            _last_rep_save[0] = time.time()
            try:
                import identity_router
                identity_router.save(site, shard, nshard)
            except Exception:
                pass

    def _one(t):
        nonlocal n_ok, n_empty, n_fail
        url_id, name = t
        su = getstore.url_id_to_uuid(url_id)
        if not su:
            with lock:
                n_fail += 1
                _beat()
            return
        data = getstore.fetch_store(su, site=site)
        if not data:
            # DO NOT mark it done. Measured: above ~32 workers this endpoint stops answering and returns
            # EMPTY fast — no 429, no error, just nothing. Marking a store done on a null response would
            # let a throttled sweep race through 502k stores in minutes, land zero items, and report
            # complete. That is the exact "succeeded and lied" failure this whole workbench exists to
            # remove, and it would be invisible: fast, green, and empty.
            with lock:
                n_fail += 1
                _beat()
            return
        # STRUCTURAL COVERAGE CHECK — see has_catalog(). Without this a throttled 200 counts as a
        # covered store, and the checkpoint bakes the lie in so the NEXT run skips it too.
        if not has_catalog(data):
            with lock:
                n_fail += 1
                misses.append(url_id)
                _beat()
            return
        sname = data.get("title") or name
        try:
            items = ubereats._items_from_store([data], su, sname)
        except Exception:
            items = []
        # THE CATEGORY PATH IS FREE — stamp it on every item whether or not enrichment runs. The index is
        # built from the catalog we already have in hand, so the retailer's own hierarchy
        # ("Beer > Craft IPA") costs nothing extra and lands even when the detail call is skipped.
        idx = {}
        try:
            idx = ubereats._catalog_index([data])
            for it in items:
                c = idx.get(it.get("item_uuid")) or {}
                sn, bn = c.get("section_name") or "", c.get("subsection_name") or ""
                it["section_name"], it["subsection_name"] = sn, bn
                it["category_path"] = " > ".join([x for x in (sn, bn) if x])
        except Exception:
            pass
        if items and ENRICH:
            try:
                items = enrich_items(su, sname, items, idx, site=site, known=KNOWN)
            except Exception:
                pass                      # enrichment must never cost us the catalog we already have
        with lock:
            done.add(url_id)
            if items:
                n_ok += 1
                pending.extend(items)
                # `raw_json` dominates; measuring it is enough to track the buffer without walking
                # every field of every record on the hot path.
                pend_bytes[0] += sum(len(it.get("raw_json") or "") + 512 for it in items)
                # Keep a bounded sample for field-level QA. Bounded because this is about DETECTING a
                # parser break, which a few thousand rows settles as well as a million — and an
                # unbounded QA buffer would re-create the memory bug we just spent the day removing.
                if len(qa_rows) < 5000:
                    qa_rows.extend(items[:max(0, 5000 - len(qa_rows))])
            else:
                n_empty += 1
            n_seen = len(done)
            # HEARTBEAT ON A CLOCK, not only on a counter. Every-200-stores was the sole signal, so a
            # deliberately slow shard (7 workers is ~1/s) could go many minutes without a word — and the
            # journal marks a silent run `lost` after 900s. A healthy shard being reported dead is the
            # same misinformation as a dead one reported healthy, just in the other direction.
            if n_seen % 200 == 0:
                _last_beat[0] = 0            # force the beat below
            _beat()
            _flush()

    # THROTTLE TRIPWIRE. A sustained run of null responses means we are being rate-limited, not that
    # the stores are gone. Abort the pass rather than burn the universe against a closed door — the
    # checkpoint keeps what landed and the next run resumes.
    def _tripped():
        seen = n_ok + n_empty + n_fail
        return seen >= 300 and n_fail > seen * 0.9

    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in ex.map(_one, todo):
                if _tripped():
                    log("[ue] !! THROTTLED — %s of %s responses empty. Stopping this pass; lower "
                        "UE_WORKERS (measured: ~32 works, 64+ returns empty) or add shards."
                        % (f"{n_fail:,}", f"{n_ok + n_empty + n_fail:,}"))
                    break
    finally:
        with lock:
            _flush(force=True)

    dur = time.time() - t0
    remaining = len([1 for (u, _) in mine if u not in done])
    rate = (len(todo) - remaining) / dur if dur else 0
    log("[ue] COVERAGE %s/%s stores of this shard (%.1f%%); %s remaining | %s items | %.1f stores/s"
        % (f"{len(mine) - remaining:,}", f"{len(mine):,}",
           100.0 * (len(mine) - remaining) / max(1, len(mine)), f"{remaining:,}",
           f"{n_items:,}", rate))
    log("[ue] stores with a catalog: %s | empty: %s | unreachable: %s"
        % (f"{n_ok:,}", f"{n_empty:,}", f"{n_fail:,}"))
    # Projected full-universe wall-clock at the observed rate — the number that says whether the DAY
    # budget is met, stated every run rather than assumed.
    if rate:
        log("[ue] at this rate one shard of %s would take %.1fh; the full %s-store universe needs "
            "%.1f shard-hours (=> %d shards to finish inside a day)"
            % (f"{len(mine):,}", len(mine) / rate / 3600, f"{len(uni):,}",
               len(uni) / rate / 3600, max(1, int(len(uni) / rate / 86400) + 1)))
    _progress(rows=n_items, stage="shard complete" if not remaining else "partial", pct=100.0)

    # Append-only, like every other shard write. A miss is an observation with a date, not a mutable
    # attribute of a store, so it accumulates rather than being merged.
    if misses:
        try:
            warehouse.write_partition(
                MISS_TABLE % site, "%s_s%02d" % (day, shard),
                [{"day": day, "url_id": u, "shard": shard} for u in misses],
                fields=["day", "url_id", "shard"])
        except Exception as e:
            log("  [ue] miss log failed: %s" % str(e)[:90])
    # OUTPUT VALIDATION. Everything above proves we got the bytes; this asks whether the parser is still
    # extracting what it extracted yesterday. A target can redesign its payload, return 200 on every
    # request, and leave every counter green while the corpus fills with nulls — the failure that is
    # normally found weeks later by whoever consumes the data.
    qa = {}
    try:
        import extract_qa
        _lean = [k for k in PRODUCT_FIELDS if k != "raw_json"]
        st = extract_qa.field_stats(qa_rows, fields=_lean)
        if st:
            base = extract_qa.baseline_for(site, "%s_products" % site, log=log)
            vd = extract_qa.assess(st, base)
            bad = extract_qa.drifted(vd)
            qa = {"fields": len(st), "drifted": bad,
                  "fill": {f: st[f]["fill_pct"] for f in sorted(st)[:40]}}
            extract_qa.record(site, "%s_products" % site, st, day=day, log=log)
            if bad:
                # A parser break is not a successful run, however many rows it landed.
                log("[ue] !! FIELD DRIFT vs %d-day baseline: %s" % (7, extract_qa.summary(st, vd)))
            else:
                log("[ue] field QA: %d fields checked, no drift vs baseline" % len(st))
    except Exception as e:
        log("[ue] field QA skipped: %s" % str(e)[:100])

    # VALUE-LEVEL RULES. Fill rates can be perfect while every value is confident nonsense — a price
    # column that starts carrying the case price, an ABV of 900 from a moved decimal, a UPC missing its
    # last digit. Right type, wrong number, and nothing upstream notices. Findings are FLAGGED, never
    # corrected: landed data is not rewritten, and silently repairing a value would destroy the evidence
    # that the parser is broken.
    try:
        import value_rules
        vr = value_rules.batch_rates(qa_rows)
        if vr:
            vbase = extract_qa.baseline_for(site, "%s_valuerules" % site, log=lambda *_: None) \
                if "extract_qa" in dir() else {}
            vbad = value_rules.assess(vr, vbase, rows=len(qa_rows))
            qa["value_rules"] = vr
            if vbad:
                qa["value_violations"] = sorted(vbad)
                log("[ue] !! VALUE RULES firing above baseline: %s" % value_rules.summary(vbad))
            else:
                log("[ue] value rules: %d rule(s) seen, none above baseline" % len(vr))
            try:
                extract_qa.record(site, "%s_valuerules" % site,
                                  {r: {"rows": len(qa_rows),
                                       "filled": int(rate * len(qa_rows) / 1000.0),
                                       "fill_pct": rate} for r, rate in vr.items()},
                                  day=day, log=lambda *_: None)
            except Exception:
                pass
    except Exception as e:
        log("[ue] value rules skipped: %s" % str(e)[:100])

    # BANK REPUTATION AT THE END, unconditionally — this is the moment the process knows the most, and
    # a run that ends `degraded` (throttled, half-covered) is carrying the MOST valuable evidence of
    # all: precisely which exits went bad. Losing that is what made every restart start over.
    try:
        import identity_router
        n_rep = identity_router.save(site, shard, nshard, log=log)
        if n_rep:
            log("[ue] banked reputation for %d (exit, costume) pair(s) — the next run inherits it" % n_rep)
    except Exception:
        pass

    status = "success" if not remaining else "degraded"
    if qa.get("drifted") or qa.get("value_violations"):
        status = "degraded"
    rec = {"status": status, "site": site, "shard": "%s/%s" % (shard, nshard),
           "stores_total": len(mine), "stores_done": len(mine) - remaining, "remaining": remaining,
           "items": n_items, "with_catalog": n_ok, "empty": n_empty, "unreachable": n_fail,
           "stores_per_sec": round(rate, 2), "duration_s": round(dur, 1)}
    # REPORT THE RATE THE ENDPOINT ALLOWED. This is the number we kept guessing at and getting wrong;
    # once a run states where AIMD settled, the next run starts from evidence instead of a constant.
    try:
        import pace
        _pc = pace.get()
        if _pc:
            rec["pace"] = _pc.stats()
    except Exception:
        pass
    if qa:
        rec["field_qa"] = qa
    # WHY the failures happened, not just how many. `unreachable` alone cost a day of wrong models.
    try:
        import blocks
        _t = blocks.get()
        if _t:
            rec["blocks"] = _t.counts_json() if hasattr(_t, "counts_json") else _t.flat()
            log("[ue] fetch outcomes: %s" % _t.summary())
            # WHICH IDENTITIES, not just how many failures. The attribution has existed since the tally
            # was written and was never reported, so every run on file records a pool average — under
            # which "six addresses are burned" and "the fingerprint is blown" are the same number.
            rec["blocks_by_exit"] = _t.by_exit()
            rec["exit_verdict"] = _t.exit_verdict()
            _ev = rec["exit_verdict"]
            if _ev.get("pattern") == "burned_subset":
                log("[ue] exits: %d of %d rated far below a healthy median (%.0f%%) — retire %s, the "
                    "pool is not the problem" % (len(_ev["below_median_25pp"]), _ev["rated"],
                                                 _ev["median_pct"], ", ".join(_ev["below_median_25pp"])))
            elif _ev.get("pattern") == "uniform":
                log("[ue] exits: every rated exit within %.0f pp of the others — the addresses are NOT "
                    "the variable. More IPs buy nothing here." % _ev["spread_pp"])
            else:
                log("[ue] exits: %s (%s rated)" % (_ev.get("pattern"), _ev.get("rated")))
    except Exception:
        pass
    try:
        import identity_router
        rec["identity_router"] = identity_router.stats()
        log("[ue] identity_router: %d pairs tracked, %d quarantined, %d picks"
            % (rec["identity_router"]["pairs_tracked"], rec["identity_router"]["quarantined"],
               rec["identity_router"]["total_picks"]))
    except Exception:
        pass
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description="UberEats/Postmates catalog sweep (headless, shardable).")
    ap.add_argument("--site", default="ubereats", choices=("ubereats", "postmates"))
    ap.add_argument("--shard", default="0/1", help="i/N — this shard of the universe (default whole)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--no-enrich", action="store_true",
                    help="catalog only — skip the per-item getMenuItemV1 UPC/detail pass")
    a = ap.parse_args(argv)
    if a.no_enrich:
        globals()["ENRICH"] = False
    i, n = (a.shard.split("/") + ["1"])[:2]
    rec = run(site=a.site, shard=int(i), nshard=int(n), workers=a.workers)
    print(json.dumps(rec, indent=2))
    return 0 if rec.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
