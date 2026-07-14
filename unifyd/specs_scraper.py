#!/usr/bin/env python3
"""specs_scraper.py — STORE-LEVEL price + availability tracker for Spec's (specsonline.com).

Spec's serves bots (200) and embeds a per-store `variants` object right in the product
page — 114 stores, each with `inStock` (bool) and `unitPrice` (cents) keyed by a store code
in `code` ("<storeCode>-<sku>"). So we fetch the product page directly (no Bright Data) and
read per-store price + availability. Snapshot is keyed `sku|storeCode`; day-over-day the
per-store in/out and price moves are the directional signal (Spec's exposes binary in/out,
not a unit count like Binny's).

connId: `specs`. Harvest product URLs from the sitemap (direct), poll a deterministic
sample, diff vs the prior snapshot. Self-reports `degraded` if the `variants` block can't
be parsed on most pages (markup drift).
"""
import argparse, hashlib, json, os, re, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import polite
from abc_fws_scraper import diff_snapshots   # generic per-key price/in-stock diff
SPECS_MIN_INT = float(os.environ.get("SPECS_MIN_INTERVAL", "0.6"))
SPECS_PROXY   = os.environ.get("SPECS_PROXY", "0") == "1"

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


_UPC_RE = re.compile(r'/(\d{11,14})\.(?:jpg|jpeg|png|webp)', re.I)
# Every product-level field the PDP exposes → a clean column in specs_products (feeds the master).
SPECS_FLD = ["sku", "slug", "url", "name", "brand", "type", "varietal", "abv", "origin", "region", "state",
             "vintage", "tasting_notes", "pairs_with", "description", "price", "upc", "image",
             "in_stock_stores", "store_count", "raw_json"]


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
                with lock:
                    if rows:
                        ok_n += 1
                    for r in rows:
                        cur[f"{slug}|{r['store']}"] = {"price": r["price"], "instock": r["instock"],
                                                       "store": r["store"], "slug": slug, "sku": r["sku"],
                                                       "name": name, "image": img}
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
    header = ["SKU", "Product", "Store", "Price", "In Stock"]
    rows = [[v["sku"] or v["slug"], v["name"], v["store"], v["price"], v["instock"]]
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
                # a FULL crawl is the authoritative catalog → OVERWRITE (replaces any older/duplicated table).
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
    log(f"done: {n_products} products × stores = {len(cur)} cells; "
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
