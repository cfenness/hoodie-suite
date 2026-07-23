#!/usr/bin/env python3
"""specs_scraper.py — STORE-LEVEL price + INVENTORY-COUNT tracker for Spec's (specsonline.com).

Spec's serves bots (200) and embeds a per-store `variants` object right in the product
page — ~190 store variants, each with `inStock` (bool) and `unitPrice` (cents) keyed by a store code
in `code` ("<storeCode>-<sku>"). That page block gives per-store price + in/out for free in
ONE fetch. The actual UNIT COUNT lives one hop away: the PDP calls an inventory API
`GET /api/products/stock/{storeCode}-{upc}/` → {status:"ok", available:N, tracked:bool}.
So Spec's is a real COUNTS source (like Binny's/ABC), not just in/out — we read the number
per (store, product) from that endpoint. Snapshot keyed `sku|storeCode`, carrying `qty`.

connId: `specs`. Harvest product URLs from the sitemap (direct), poll a deterministic
sample, diff vs the prior snapshot. Self-reports `degraded` if the `variants` block can't
be parsed on most pages (markup drift).

Counts fan out one call per (in-stock store, product) — SPECS_QTY=1 default; set
SPECS_COUNT_STORES="0,5,35" to restrict counts to a focus set of stores (bounds request
volume on a full crawl), else all in-stock stores are counted.
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polite
from abc_fws_scraper import diff_snapshots   # generic per-key price/in-stock diff
SPECS_MIN_INT = float(os.environ.get("SPECS_MIN_INTERVAL", "0.6"))
SPECS_PROXY   = os.environ.get("SPECS_PROXY", "0") == "1"
SPECS_QTY     = os.environ.get("SPECS_QTY", "1") == "1"          # pull the numeric per-store unit count
_COUNT_STORES = {s.strip() for s in os.environ.get("SPECS_COUNT_STORES", "").split(",") if s.strip()}

BASE      = "https://specsonline.com"
SITEMAP   = BASE + "/sitemap.xml"
DELAY     = float(os.environ.get("SPECS_DELAY", "1"))
SNAP      = "specs_snapshot.json"
UA        = os.environ.get("SPECS_UA", "HoodieSuite-Research/1.0 (+market-intelligence; respects robots)")
LOC_RE    = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.I)
LD_RE     = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def _http(url, timeout=25):
    # via polite: per-host rate-limit + backoff + circuit breaker + optional BD residential proxy (Tier 2)
    body = polite.get(url, min_interval=SPECS_MIN_INT, jitter=SPECS_MIN_INT, timeout=timeout,
                      headers={"Accept-Encoding": "identity"}, breaker_after=4,
                      use_proxy=SPECS_PROXY, host="specsonline.com")
    return body.decode("utf-8", "replace")


def harvest_ids(max_products=None, log=print):
    # max_products=None -> WHOLE catalog (Spec's is ~41k across 21 sitemaps; the old default of 20000 silently
    # truncated it to half). Set SPECS_MAX_PRODUCTS to cap deliberately; a cap that bites is logged, not silent.
    if max_products is None:
        env = os.environ.get("SPECS_MAX_PRODUCTS")
        max_products = int(env) if env else 10 ** 9
    out, seen = [], set()
    try:
        idx = _http(SITEMAP)
    except Exception as e:
        log(f"sitemap index: {e}"); return out
    children = [u for u in LOC_RE.findall(idx) if "product" in u.lower()]
    for cs in children:
        try:
            body = _http(cs)
        except Exception as e:
            log(f"sitemap {cs}: {e}"); continue
        for u in LOC_RE.findall(body):
            slug = u.rstrip("/").rsplit("/", 1)[-1]
            if slug and slug not in seen:
                seen.add(slug); out.append((slug, u))
                if len(out) >= max_products:
                    log(f"[specs] hit SPECS_MAX_PRODUCTS={max_products} — TRUNCATING (catalog is larger)")
                    return out
        log(f"{cs.rsplit('/',1)[-1]}: {len(out)} products so far")
    return out


def pick_sample(catalog, n):
    if n <= 0 or n >= len(catalog):
        return list(catalog)
    cat = sorted(catalog, key=lambda t: t[0])
    stride = len(cat) / float(n)
    return [cat[int(i * stride)] for i in range(n)]


def _maybe_unescape(s):
    try: return s.encode("utf-8", "ignore").decode("unicode_escape", "ignore")
    except Exception: return s


def _variants_obj(html):
    """The page embeds an (escaped) JSON state with a "variants":{...} object keyed by
    store. Pull it whether the page is escaped or not, via brace-balancing."""
    for s in (html, _maybe_unescape(html)):
        i = s.find('"variants"')
        if i < 0:
            continue
        j = s.find("{", i)
        if j < 0:
            continue
        depth = 0
        for k in range(j, len(s)):
            ch = s[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[j:k + 1])
                    except Exception:
                        break
    return None


def _name(html):
    for blk in LD_RE.findall(html):
        try: data = json.loads(blk)
        except Exception: continue
        for d in (data if isinstance(data, list) else [data]):
            if isinstance(d, dict) and d.get("@type") == "Product" and d.get("name"):
                return d["name"]
    return None


def parse_stores(html):
    """Return per-store rows [{store, sku, instock, price}] from the variants block."""
    v = _variants_obj(html)
    if not isinstance(v, dict):
        return [], None
    name = _name(html)
    rows = []
    for d in v.values():
        if not isinstance(d, dict):
            continue
        code = str(d.get("code") or "")
        store, _, sku = code.partition("-")
        if not store:
            continue
        up = d.get("unitPrice")
        price = (up / 100.0) if isinstance(up, (int, float)) else None
        rows.append({"store": store, "sku": sku or "", "instock": bool(d.get("inStock")),
                     "price": price})
    return rows, name


def fetch_store_qty(store, upc, timeout=15):
    """Per-store on-hand UNITS via Spec's inventory API: /api/products/stock/{store}-{upc}/
    → {status:'ok', available:N, tracked:bool}. Returns the int count, or None when the
    product isn't inventory-tracked at that store or the call fails. This is the numeric
    count the embedded `variants` block omits (it carries only the inStock bool)."""
    if not (store and upc):
        return None
    try:
        d = json.loads(_http("%s/api/products/stock/%s-%s/" % (BASE, store, upc), timeout=timeout))
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("status") != "ok" or not d.get("tracked"):
        return None
    v = d.get("available")
    return int(v) if isinstance(v, (int, float)) else None


def store_quantities(rows, upc, log=print):
    """For a product's per-store `rows` (from parse_stores), fetch the unit count at each IN-STOCK store
    (out-of-stock ⇒ 0, no call needed). Restrict to SPECS_COUNT_STORES when set. Returns {store: qty}."""
    qmap = {}
    if not (SPECS_QTY and upc):
        return qmap
    for r in rows:
        st = r.get("store")
        if not r.get("instock"):
            continue
        if _COUNT_STORES and st not in _COUNT_STORES:
            continue
        q = fetch_store_qty(st, upc)
        if q is not None:
            qmap[st] = q
    return qmap


_UPC_RE = re.compile(r'/(\d{11,14})\.(?:jpg|jpeg|png|webp)', re.I)
# Every product-level field the PDP exposes → a clean column in specs_products (feeds the master).
SPECS_FLD = ["sku", "slug", "url", "name", "brand", "type", "varietal", "abv", "origin", "region", "state",
             "vintage", "tasting_notes", "pairs_with", "description", "price", "upc", "image",
             "in_stock_stores", "store_count", "units_total", "stores_tracked", "raw_json"]


def _brace_obj(s, anchor):
    """The brace-balanced JSON object that CONTAINS `anchor` — walk back to its opening '{', forward to close."""
    i = s.find(anchor)
    if i < 0:
        return None
    depth, start = 0, None
    for k in range(i, -1, -1):
        c = s[k]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                start = k; break
            depth -= 1
    if start is None:
        return None
    depth = 0
    for k in range(start, len(s)):
        c = s[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:k + 1])
                except Exception:
                    return None
    return None


def _attrs_obj(html):
    """The PDP's product-attribute object (type/brand/abv/region/origin/varietal/vintage/tastingNotes/pairsWith)."""
    for s in (html, _maybe_unescape(html)):
        o = _brace_obj(s, '"tastingNotes"')
        if isinstance(o, dict) and "brand" in o:
            return o
    return None


def _ld_product(html):
    for blk in LD_RE.findall(html):
        try:
            data = json.loads(blk)
        except Exception:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if isinstance(d, dict) and d.get("@type") == "Product":
                return d
    return None


def _state_from_region(region):
    m = re.search(r"\(([A-Z]{2})\)\s*$", region or "")   # "Kentucky (KY)" -> "KY"
    return m.group(1) if m else ""


def parse_product(html, slug, url, stores=None):
    """FULL product-detail record from a Spec's PDP — everything on the page: the PRODUCT DETAILS block
    (type/brand/abv/region/origin/varietal/vintage/tasting notes/pairs-with) + ld+json (name/description/
    each-price/image) + the UPC parsed from the image filename (Spec's names product images by UPC) + a
    per-store availability roll-up. raw_json keeps the untouched source blocks (full-capture directive)."""
    a = _attrs_obj(html) or {}
    ld = _ld_product(html) or {}
    im = ld.get("image")
    img = (im[0] if isinstance(im, list) and im else im) if im else ""
    if not img:
        mi = re.search(r'(?:og:image|twitter:image)"[^>]*content="([^"]+)"', html or "", re.I)
        img = mi.group(1) if mi else ""
    um = _UPC_RE.search(img or "")
    off = ld.get("offers") or {}
    if isinstance(off, list):
        off = off[0] if off else {}
    try:
        price = float(off.get("price")) if off.get("price") else None
    except Exception:
        price = None
    stores = stores or []
    region = (a.get("region") or "").strip()
    return {
        "sku": (a.get("sku") or ld.get("sku") or "").strip(),
        "slug": slug, "url": url,
        "name": (ld.get("name") or _name(html) or "").strip(),
        "brand": (a.get("brand") or "").strip(),
        "type": (a.get("type") or "").strip(),
        "varietal": (a.get("varietal") or "").strip(),
        "abv": (a.get("abv") or "").strip(),
        "origin": (a.get("origin") or "").strip(),          # country, e.g. "United States"
        "region": region,                                   # e.g. "Kentucky (KY)"
        "state": _state_from_region(region),
        "vintage": (a.get("vintage") or "").strip(),
        "tasting_notes": (a.get("tastingNotes") or "").strip(),
        "pairs_with": (a.get("pairsWith") or "").strip(),
        "description": (ld.get("description") or "").strip(),
        "price": price,
        "upc": (um.group(1) if um else ""),
        "image": img,
        "in_stock_stores": sum(1 for s in stores if s.get("instock")),
        "store_count": len(stores),
        "raw_json": json.dumps({"attrs": a, "offers": off}, separators=(",", ":"))[:8000],
    }


def run_record(movement, n_products, status, warnings):
    rid = "R-SPX" + hashlib.sha1(str(movement.get("sampled", "")).encode()).hexdigest()[:3].upper()
    return {"id": rid, "connId": "specs", "startedAt": 0, "finishedAt": 0, "durationMs": 0,
            "status": status, "trigger": "manual", "total": n_products,
            "degraded": status == "degraded", "warnings": warnings, "healed": [],
            "extracts": [{"id": "specs_store_cells", "rows": movement["sampled"],
                          "delta": movement["changed"], "status": status}],
            "movement": movement}


def pull(sample=30, crawl_all=False, limit=None, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    catalog = harvest_ids(log=log)
    if not catalog:
        run = run_record(diff_snapshots({}, {}), 0, "failed", ["No products found in sitemap."])
        return {}, [run], run["movement"]
    targets = (catalog if limit is None else catalog[:limit]) if crawl_all else pick_sample(catalog, sample)
    log(f"catalog {len(catalog)} products; fetching {len(targets)} (per-store variants)")

    import runlog
    cur, ok_n, names, done, products = {}, 0, {}, [0], {}
    # FAST: concurrent per-page fetch (plain HTTP) instead of serial DELAY.
    import threading
    from concurrent.futures import ThreadPoolExecutor
    workers = int(os.environ.get("SPECS_WORKERS", "12"))
    jitter = float(os.environ.get("SPECS_JITTER", "0.2"))
    lock = threading.Lock()
    with runlog.track("specs", total=len(targets)) as _run:      # register on the Active Runs board w/ progress
        def _one(t):
            nonlocal ok_n
            slug, url = t
            try:
                html = _http(url)
                rows, name = parse_stores(html)
                prod = parse_product(html, slug, url, stores=rows)   # full product-detail record
                img = prod.get("image") or ""
                # numeric per-store units via the inventory API (the count the variants block omits)
                # store_quantities hits the per-store inventory API once PER STORE per product — fine for a
                # sample, ruinous for the 40k-product full crawl (it can't finish in the run timeout). Skip it
                # on the full catalog sweep (SPECS_UNITS=1 to force); the per-store in/out + price still land.
                qmap = (store_quantities(rows, prod.get("upc") or "", log=log)
                        if (os.environ.get("SPECS_UNITS", "0") == "1" or not crawl_all) else {})
                if qmap:
                    prod["units_total"] = sum(qmap.values())         # product headline: total on-hand across counted stores
                    prod["stores_tracked"] = len(qmap)
                with lock:
                    if rows:
                        ok_n += 1
                    for r in rows:
                        cur[f"{slug}|{r['store']}"] = {"price": r["price"], "instock": r["instock"],
                                                       "store": r["store"], "slug": slug, "sku": r["sku"],
                                                       "name": name, "image": img, "qty": qmap.get(r["store"]),
                                                       "upc": prod.get("upc") or "", "brand": prod.get("brand") or ""}
                    if name:
                        names[slug] = name
                    if prod.get("name") or prod.get("sku"):
                        products[slug] = prod
            except Exception as e:
                log(f"  {slug}: {e}")
            with lock:
                done[0] += 1
                if done[0] % 50 == 0:
                    _run.progress(done[0])
            if jitter:
                time.sleep(jitter)
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for _ in ex.map(_one, targets):
                    pass
        else:
            for t in targets:
                _one(t)
        _run.progress(len(targets))

    prev = {}
    snap_path = os.path.join(state_dir, SNAP)
    if os.path.exists(snap_path):
        try: prev = json.load(open(snap_path)).get("cells", {})
        except Exception: prev = {}
    movement = diff_snapshots(prev, cur)

    n_products = len({v["slug"] for v in cur.values()})
    warnings = []
    if not cur or ok_n / max(1, len(targets)) < 0.5:
        warnings.append(f"Per-store variants parsed on only {ok_n}/{len(targets)} pages — "
                        "the Spec's product-page variants block may have changed.")
    status = "failed" if not cur else ("degraded" if warnings else "success")

    json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(snap_path, "w"), indent=2)
    # Land the per-store observation time-series with the UNIT COUNT (makes Spec's a true Counts source, like
    # Binny's) — qty=exact on-hand, in_stock from the variants block. Dated partition per (date, source).
    try:
        import observe
        observe.record("specs", [dict(source="specs", store_id=v["store"], store="Spec's #%s" % v["store"],
                                      product_id=v.get("sku") or v["slug"], upc=v.get("upc", ""),
                                      brand=v.get("brand", ""), name=v.get("name") or "", price=v.get("price"),
                                      on_promo=False, in_stock=bool(v.get("instock")), qty=v.get("qty"),
                                      stock_level=("in" if v.get("instock") else "out"), is_hemp=False)
                                 for v in cur.values()], log=log)
    except Exception as e:
        log("  [specs] observe skipped: %s" % str(e)[:80])
    n_qty = sum(1 for v in cur.values() if v.get("qty") is not None)
    header = ["SKU", "Product", "Store", "Price", "In Stock", "Units"]
    rows = [[v["sku"] or v["slug"], v["name"], v["store"], v["price"], v["instock"], v.get("qty")]
            for k, v in sorted(cur.items())]
    datasets = {"specs_store_cells": {"header": header, "rows": rows[:800],
                                      "total": len(rows), "products": n_products, "movement": movement}}
    # Land the full product catalog (all detail fields + raw_json) — accumulates so a sample run updates the
    # products it touched and keeps the rest (persistent catalog). Feeds the master via specs_products.
    prod_list = list(products.values())
    if prod_list:
        try:
            import warehouse
            if crawl_all and limit is None:
                # a FULL crawl is the authoritative catalog → OVERWRITE — but only when it actually looks
                # like one. SHRINK GUARD (learned 2026-07-21: a full crawl died mid-run and its overwrite
                # clobbered 40,689 rows down to 163 — the empty-guard only stops 0-row writes): a "full"
                # result under 70% of the existing catalog is a failed/partial crawl, so ACCUMULATE it
                # instead — the touched products still update and the rest of the catalog survives.
                existing = warehouse.row_count("specs_products")
                if existing and len(prod_list) < 0.7 * existing:
                    warehouse.write_accumulate("specs_products", prod_list,
                                               key=lambda r: r.get("sku") or r.get("slug"), fields=SPECS_FLD)
                    log(f"landed specs_products: +{len(prod_list)} ACCUMULATED — full-crawl result is under "
                        f"70% of the existing {existing} rows (partial crawl?), refusing to overwrite")
                else:
                    warehouse.write_parquet("specs_products", prod_list, fields=SPECS_FLD)
                    log(f"landed specs_products: {len(prod_list)} products (full catalog, overwrite)")
            else:
                # a partial/sample run only updates the products it touched → accumulate, keep the rest.
                warehouse.write_accumulate("specs_products", prod_list,
                                           key=lambda r: r.get("sku") or r.get("slug"), fields=SPECS_FLD)
                log(f"landed specs_products: +{len(prod_list)} products (accumulated)")
        except Exception as e:
            log(f"specs_products land skipped: {str(e)[:80]}")
        p_header = ["SKU", "Name", "Brand", "Type", "ABV", "Region", "UPC", "Price"]
        p_rows = [[p["sku"], p["name"], p["brand"], p["type"], p["abv"], p["region"], p["upc"], p["price"]]
                  for p in prod_list]
        datasets["specs_products"] = {"header": p_header, "rows": p_rows[:800], "total": len(prod_list),
                                      "_rows_full": p_rows}
    json.dump({"specs_store_cells": datasets["specs_store_cells"]},
              open(os.path.join(out, "datasets.json"), "w"), indent=2)
    datasets["specs_store_cells"]["_rows_full"] = rows   # full set for export (in-memory return only)
    run = run_record(movement, n_products, status, warnings)
    log(f"done: {n_products} products × stores = {len(cur)} cells ({n_qty} with a unit count); "
        + (f"{movement['changed']} store-cells moved since last run" if prev else "baseline"))
    return datasets, [run], movement


def main(argv=None):
    ap = argparse.ArgumentParser(description="Store-level price/availability tracker for Spec's.")
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="./specs_out")
    a = ap.parse_args(argv)
    _, runs, _ = pull(sample=a.sample, crawl_all=a.all, limit=a.limit, out=a.out, state_dir=a.out)
    print(json.dumps(runs[0], indent=2))
    return 0 if runs[0]["status"] != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
