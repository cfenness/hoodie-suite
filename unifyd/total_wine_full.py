"""total_wine_full.py — land Total Wine's FULL catalog (~143k) via the getProduct API, robustly.

The per-store puller (total_wine_inventory) warms ONE PerimeterX cookie per session — a single cookie won't
survive the whole catalog (~40h single-stream). This driver runs the SAME proven session-pull in BATCHES, each
batch a FRESH BD Browser session (re-warmed PX), and is:
  • resumable   — skips skuIds already in total_wine_products, so a killed run continues where it left off;
  • shardable   — SHARDS=N + SHARD=i process disjoint skuId slices (independent BD Browser IPs) to cut
                  wall-clock ~N×. Run N processes, i = 0..N-1;
  • board-aware — registers on Active Runs (connId total-wine-full[/shard]).

    # single stream
    TW_FETCH=bdbrowser python total_wine_full.py --store 920 --state FL
    # 5-way parallel: launch 5 procs, SHARD=0..4
    SHARDS=5 SHARD=0 TW_FETCH=bdbrowser python total_wine_full.py --store 920 --state FL
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import time
import warehouse
import observe
import runlog
import total_wine_inventory as twi

BATCH = int(os.environ.get("TW_BATCH", "3000"))       # skuIds per warmed session (re-warm between batches)


def _done_skus():
    """skuIds already landed — for resume (write_accumulate would overwrite anyway; this just skips re-fetch)."""
    try:
        return {str(r["sku"]) for r in warehouse.query("total_wine_products", "SELECT DISTINCT sku FROM t")}
    except Exception:
        return set()


def _land(rows, log, part=None):
    """Land a batch exactly like total_wine_inventory.pull_store: observations + enrich total_wine_products.
    `part` MUST be unique per batch/shard — else concurrent shards overwrite one shared observation file."""
    if not rows:
        return 0
    n = observe.record("total-wine", rows, log=lambda *a: None, part=part)

    def _s(v):
        return "" if v is None else str(v)
    enr = [{"sku": _s(r["sku"]), "name": _s(r["name"]), "brand": _s(r["brand"]), "size": _s(r["size"]),
            "price": r["price"], "abv": _s(r["abv"]), "varietal": _s(r["varietal"]), "origin": _s(r["origin"]),
            "region": _s(r["region"]), "style": _s(r["style"]), "category": _s(r["style"]), "url": "",
            "store_id": _s(r["store_id"])} for r in rows]
    try:
        warehouse.write_accumulate("total_wine_products", enr, key=lambda r: r["sku"])
    except Exception as e:
        log("  [tw-full] land failed: %s" % str(e)[:70])
    return n


def run(store, state=None, shards=1, shard=0, resume=True, log=print):
    allsk = twi._skuids_from_sitemap(None, log)                 # every product URL -> <productId>-1
    log("[tw-full] catalog: %d skuIds" % len(allsk))
    if shards > 1:
        allsk = [s for i, s in enumerate(allsk) if i % shards == shard]
        log("[tw-full] shard %d/%d -> %d skuIds" % (shard, shards, len(allsk)))
    if resume:
        done = _done_skus()
        allsk = [s for s in allsk if s not in done]
        log("[tw-full] %d remaining (after skipping %d already landed)" % (len(allsk), len(done)))
    state = (state or "").upper() or "US"
    total, got = len(allsk), 0
    conn = "total-wine-full" + ("/s%d" % shard if shards > 1 else "")
    with runlog.track(conn, total=total) as rl:
        day = time.strftime("%Y-%m-%d")
        for b in range(0, total, BATCH):
            batch = allsk[b:b + BATCH]
            rows = twi._pull_bdbrowser(store, state, batch, log)   # fresh session + PX warm per batch
            # unique observation partition per (shard, batch) — concurrent shards must NOT share one file
            got += _land(rows, log, part="%s_total-wine_s%d_b%d" % (day, shard, b))
            rl.progress(min(b + BATCH, total))
            log("[tw-full] %d/%d skus · +%d products this batch · %d landed cumulative"
                % (min(b + BATCH, total), total, len(rows), got))
    log("[tw-full] DONE store %s%s: %d products landed" % (store, (" shard %d" % shard) if shards > 1 else "", got))
    return got


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True, help="Total Wine storeId (920 = Orlando Millenia)")
    ap.add_argument("--state", default="", help="2-letter state (FL); the API needs state=US-XX")
    ap.add_argument("--shards", type=int, default=int(os.environ.get("SHARDS", "1")))
    ap.add_argument("--shard", type=int, default=int(os.environ.get("SHARD", "0")))
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args()
    run(a.store, state=a.state, shards=a.shards, shard=a.shard, resume=not a.no_resume)
