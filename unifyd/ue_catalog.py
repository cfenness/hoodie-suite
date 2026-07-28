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

WORKERS = int(os.environ.get("UE_WORKERS", "64"))     # aggregator_geo runs this endpoint at 64; proven
BATCH_STORES = int(os.environ.get("UE_BATCH", "400"))  # stores per landed batch + checkpoint
PRODUCT_FIELDS = ["store_uuid", "store_name", "source", "item_uuid", "name", "brand", "upc", "gtin",
                  "price", "list_price", "promo", "size", "abv", "in_stock", "stock_label", "category",
                  "raw_json"]
MENU_API = "https://www.ubereats.com/_p/api/getMenuItemV1"
ENRICH = os.environ.get("UE_ENRICH", "1") == "1"     # per-item UPC/GTIN detail — ON by default


def _menu_api(site):
    return MENU_API if site == "ubereats" else MENU_API.replace("www.ubereats.com", "postmates.com")


def enrich_items(su, store_name, items, idx, site="ubereats"):
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
    """
    s = getstore._session(site)
    H = dict(getstore._H)
    api = _menu_api(site)
    for rec in items:
        iu = rec.get("item_uuid")
        if not iu:
            continue
        ctx = idx.get(iu) or {}
        body = {"storeUuid": su, "menuItemUuid": iu, "sectionUuid": ctx.get("section", ""),
                "subsectionUuid": ctx.get("subsection", ""), "cbType": "EATER_ENDORSED"}
        try:
            r = s.post(api, json=body, headers=H, timeout=25)
            data = ubereats._menu_item_data(r.json()) if r.content else None
        except Exception:
            continue
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


def _progress(**kw):
    """Machine-readable progress for Hoodie Collect's live counters."""
    try:
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
    try:
        warehouse.write_accumulate(
            tbl, [{k: it.get(k) for k in PRODUCT_FIELDS} for it in items],
            key=("store_uuid", "item_uuid"), fields=PRODUCT_FIELDS)
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


def run(site="ubereats", shard=0, nshard=1, workers=None, log=print):
    """Sweep this shard of the universe. Returns a run record. NO cap: the only bound is the shard."""
    workers = workers or WORKERS
    day = time.strftime("%Y-%m-%d")
    uni = universe(site, log=log)
    if not uni:
        return {"status": "failed", "error": "store universe empty — is ubereats_sitemap populated?"}
    mine = [(u, n) for (u, n) in uni] if nshard == 1 else \
           [(u, n) for (u, n) in uni if _shard_of(u, nshard) == shard]
    log("[ue] universe %s stores; shard %s/%s owns %s (the completeness denominator)"
        % (f"{len(uni):,}", shard, nshard, f"{len(mine):,}"))

    done = _ck_load(day, site, shard, nshard, log=log)
    todo = [(u, n) for (u, n) in mine if u not in done]
    log("[ue] %s remaining this pass (%s workers)" % (f"{len(todo):,}", workers))

    pending, batch_idx, n_items, n_ok, n_empty, n_fail = [], 0, 0, 0, 0, 0
    import threading
    lock = threading.Lock()

    def _flush(force=False):
        nonlocal pending, batch_idx, n_items
        if not pending or (not force and len(pending) < BATCH_STORES * 20):
            return
        batch_idx += 1
        n_items += _land(site, day, batch_idx, shard, pending, log=log)
        pending = []
        _ck_save(day, site, shard, nshard, done, batch_idx)

    def _one(t):
        nonlocal n_ok, n_empty, n_fail
        url_id, name = t
        su = getstore.url_id_to_uuid(url_id)
        if not su:
            with lock:
                n_fail += 1
            return
        data = getstore.fetch_store(su, site=site)
        if not data:
            with lock:
                n_fail += 1
                done.add(url_id)          # a store that will not answer is DONE for today, not retried forever
            return
        sname = data.get("title") or name
        try:
            items = ubereats._items_from_store([data], su, sname)
        except Exception:
            items = []
        if items and ENRICH:
            try:
                items = enrich_items(su, sname, items, ubereats._catalog_index([data]), site=site)
            except Exception:
                pass                      # enrichment must never cost us the catalog we already have
        with lock:
            done.add(url_id)
            if items:
                n_ok += 1
                pending.extend(items)
            else:
                n_empty += 1
            n_seen = len(done)
            if n_seen % 200 == 0:
                _progress(rows=n_items + len(pending),
                          stage="%s/%s stores" % (f"{n_seen:,}", f"{len(mine):,}"),
                          pct=round(100.0 * n_seen / max(1, len(mine)), 1))
            _flush()

    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _ in ex.map(_one, todo):
                pass
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

    status = "success" if not remaining else "degraded"
    return {"status": status, "site": site, "shard": "%s/%s" % (shard, nshard),
            "stores_total": len(mine), "stores_done": len(mine) - remaining, "remaining": remaining,
            "items": n_items, "with_catalog": n_ok, "empty": n_empty, "unreachable": n_fail,
            "stores_per_sec": round(rate, 2), "duration_s": round(dur, 1)}


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
