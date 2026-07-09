"""walmart_scraper.py — Walmart.com bev-alc product/price pull (connId `walmart`).

Walmart.com is PerimeterX-protected, so a direct scrape 307-blocks. We pull structured product
records through Bright Data's managed `walmart_product` scraper (the `bdata` CLI, which handles the
anti-bot), then normalize to the bev-alc shape and land it in the warehouse as `walmart_products`,
plus a `walmart_runs` log for the tracking page. Self-reports `degraded` with warnings[] when the
pull is thin or fields drift — same contract as the other connectors.

Run (on a machine with `bdata` logged in + Tigris creds in the env / warehouse.env):
    python walmart_scraper.py                     # discover beverage URLs + pull + load
    python walmart_scraper.py --raw-glob '/tmp/wm3/*.json'   # normalize pre-pulled BD json (no spend)
    python walmart_scraper.py --queries "bourbon,vodka,tequila" --per 4
"""
import argparse, glob, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

DEFAULT_QUERIES = ["bourbon whiskey", "vodka 750ml", "cabernet sauvignon wine",
                   "tequila blanco", "gin 750ml", "spiced rum"]
NOISE = re.compile(r"framed|design-by|recipe|book|glass-set|shot-glass|decanter|"
                   r"gift-set-includes|sign|poster|shirt|barrel-head|extract", re.I)


def log(*a): print(*a, file=sys.stderr, flush=True)


# ---------------- Bright Data pull ----------------
def discover_urls(queries, per):
    urls, seen = [], set()
    for q in queries:
        try:
            out = subprocess.run(["bdata", "search", f"{q} site:walmart.com/ip", "--json"],
                                 capture_output=True, text=True, timeout=120).stdout
        except Exception as e:
            log(f"  search '{q}' failed: {e}"); continue
        found = re.findall(r"https://www\.walmart\.com/ip/[^\"'\s]+", out)
        n = 0
        for u in found:
            u = u.split("?")[0]
            if u in seen or NOISE.search(u):
                continue
            seen.add(u); urls.append(u); n += 1
            if n >= per: break
    return urls


def pull_product(url):
    try:
        r = subprocess.run(["bdata", "pipelines", "walmart_product", url, "--format", "json"],
                           capture_output=True, text=True, timeout=300)
        d = json.loads(r.stdout or "[]")
        return d if isinstance(d, list) else [d]
    except Exception as e:
        log(f"  pull failed {url}: {e}"); return []


# ---------------- normalize ----------------
def _spec(r, name):
    for s in (r.get("specifications") or []):
        if s.get("name", "").lower() == name.lower():
            return s.get("value")
    return None


def _size_ml(r):
    v = (_spec(r, "Size") or "") + " " + (r.get("product_name") or "")
    m = re.search(r"([\d.]+)\s*(ml|milliliter|l|liter|litre)\b", v.lower())
    if not m:
        return None
    n = float(m.group(1)); unit = m.group(2)
    return int(round(n * 1000)) if unit.startswith(("l", "lit")) else int(round(n))


def _abv(r):
    v = _spec(r, "Alcohol content by volume") or ""
    m = re.search(r"([\d.]+)", v)
    return float(m.group(1)) if m else None


def normalize(raw, run_id):
    seen, out = set(), []
    for r in raw:
        if not isinstance(r, dict) or not r.get("product_name"):
            continue
        pid = str(r.get("product_id") or r.get("sku") or r.get("url") or "")
        if pid in seen:
            continue
        seen.add(pid)
        cats = r.get("categories") or []
        price = r.get("final_price"); listp = r.get("initial_price")
        out.append(dict(
            run_id=run_id,
            product_id=pid, product_name=r.get("product_name"), brand=(r.get("brand") or ""),
            category=(r.get("category_name") or (cats[-1] if cats else "")),
            is_alcohol=bool({"Alcohol", "Spirits", "Wine", "Beer"} & set(cats)),
            size_ml=_size_ml(r), abv=_abv(r), upc=(r.get("upc") or r.get("gtin") or ""),
            price=price, list_price=listp,
            on_promo=bool(price and listp and price < listp),
            availability=(r.get("availability") or ""), in_stock=bool(r.get("is_available")),
            store=(r.get("store_name") or ""), store_loc=(r.get("store_location") or ""),
            rating=r.get("rating"), reviews=(r.get("review_count") or 0),
            url=r.get("url"), pulled_at=(r.get("timestamp") or "")))
    return out


# ---------------- data-quality concerns ----------------
def concerns(norm):
    n = len(norm) or 1
    def rate(pred): return round(100 * sum(1 for x in norm if pred(x)) / n, 1)
    c = []
    def add(sev, key, count, msg):
        if count: c.append(dict(severity=sev, key=key, count=count, message=msg))
    add("high", "not_alcohol", sum(1 for x in norm if not x["is_alcohol"]),
        "products not classified under Alcohol/Spirits/Wine/Beer — likely non-bev contamination")
    add("high", "missing_price", sum(1 for x in norm if not x.get("price")),
        "products with no price captured")
    add("med", "missing_abv", sum(1 for x in norm if x.get("abv") is None),
        "products missing ABV in Walmart specs")
    add("med", "missing_size", sum(1 for x in norm if not x.get("size_ml")),
        "products where size (mL) couldn't be parsed")
    add("med", "missing_upc", sum(1 for x in norm if not x.get("upc")),
        "products with no UPC/GTIN")
    oos = sum(1 for x in norm if not x["in_stock"])
    add("low" if oos < n * 0.5 else "med", "out_of_stock", oos,
        f"products out of stock ({rate(lambda x: not x['in_stock'])}% of the pull)")
    ups = [x["upc"] for x in norm if x.get("upc")]
    dupes = {u for u in ups if ups.count(u) > 1}
    add("med", "duplicate_upc", len(dupes), "UPCs appearing on more than one product")
    add("low", "on_promo", sum(1 for x in norm if x["on_promo"]),
        "products where the final price is below list (promo)")
    return c


# ---------------- run ----------------
def run(raw, note=""):
    run_id = "wm-" + time.strftime("%Y%m%d-%H%M%S")
    norm = normalize(raw, run_id)
    cc = concerns(norm)
    status = "degraded" if (not norm or any(x["severity"] == "high" for x in cc)) else "ok"
    warnings = [x["message"] for x in cc if x["severity"] == "high"]
    # land products (full snapshot for the tracker) + append a run record
    warehouse.write_parquet("walmart_products", norm)
    runs = []
    try:
        runs = warehouse.query("walmart_runs", "SELECT * FROM t")
    except Exception:
        pass
    runs.append(dict(run_id=run_id, at=int(time.time()), products=len(norm),
                     in_stock=sum(1 for x in norm if x["in_stock"]),
                     concerns=len(cc), high_concerns=sum(1 for x in cc if x["severity"] == "high"),
                     status=status, note=note, warnings=json.dumps(warnings)))
    warehouse.write_parquet("walmart_runs", runs[-50:])
    log(f"[{run_id}] {len(norm)} products · {len(cc)} concerns · status={status}")
    for x in cc:
        log(f"   {x['severity']:>4}  {x['key']}: {x['count']} — {x['message']}")
    return run_id, len(norm), cc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-glob", help="normalize pre-pulled Bright Data json files (no BD spend)")
    ap.add_argument("--queries", default=",".join(DEFAULT_QUERIES))
    ap.add_argument("--per", type=int, default=4, help="products per query")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    if a.raw_glob:
        raw = []
        for f in glob.glob(a.raw_glob):
            try:
                d = json.load(open(f)); raw += d if isinstance(d, list) else [d]
            except Exception:
                pass
        log(f"loaded {len(raw)} pre-pulled records from {a.raw_glob}")
    else:
        urls = discover_urls([q.strip() for q in a.queries.split(",") if q.strip()], a.per)
        log(f"discovered {len(urls)} beverage URLs; pulling…")
        raw = []
        for i, u in enumerate(urls, 1):
            raw += pull_product(u)
            log(f"  [{i}/{len(urls)}] {u.split('/ip/')[-1][:40]}")
    if not raw:
        log("no records pulled — Walmart may be blocking, or bdata is not logged in."); sys.exit(1)
    run(raw, note=a.note)


if __name__ == "__main__":
    main()
