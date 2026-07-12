"""kroger_api.py — Kroger bev-alc price + inventory via the OFFICIAL Kroger Developer API (connId kroger).

Unlike the retailers we scrape, Kroger publishes a real API: OAuth2 client-credentials → Products
(brand, size, UPC, regular/promo price, and STORE-LEVEL stock level when a locationId is passed) +
Locations. That gives genuine per-store inventory — the store-level "what's in stock" the plan calls for.
Lands `kroger_products` + `kroger_runs` in the warehouse. Runs anywhere (Mac or the cloud runner).

Setup: create an app at https://developer.kroger.com (scope: product.compact), then provide the creds
either as env vars (KROGER_CLIENT_ID / KROGER_CLIENT_SECRET — the cloud runner passes these from repo
secrets) or in warehouse.env. Cred-gated: no-op with a note when they're absent.

    python kroger_api.py                      # default bev-alc terms across a few store zips
    python kroger_api.py --zips 30303,10001 --terms "bourbon,vodka,cabernet"
"""
import argparse, base64, json, os, random, sys, time, urllib.parse, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe


def _front_image(p):
    """Best bottle-shot URL from a Kroger product's images[] (front perspective, largest size)."""
    best, best_area = "", -1
    rank = {"xlarge": 4, "large": 3, "medium": 2, "small": 1, "thumbnail": 0}
    for im in (p.get("images") or []):
        front = (im.get("perspective") == "front") or im.get("featured")
        for s in (im.get("sizes") or []):
            score = rank.get(s.get("size", ""), 0) + (10 if front else 0)
            if s.get("url") and score > best_area:
                best, best_area = s["url"], score
    return best

TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
API = "https://api.kroger.com/v1"
# The API is search-based (term × store), so coverage = terms × zips. These are the comprehensive
# defaults (used when the UI/CLI leaves them blank): the full bev-alc taxonomy across every Kroger banner
# region. Widen either list for more coverage; narrow for a quick check.
DEFAULT_TERMS = [
    # spirits
    "bourbon", "rye whiskey", "scotch", "irish whiskey", "tennessee whiskey", "vodka", "gin",
    "tequila", "mezcal", "rum", "cognac", "brandy", "liqueur", "cordial",
    # wine
    "cabernet sauvignon", "merlot", "pinot noir", "red blend", "chardonnay", "sauvignon blanc",
    "pinot grigio", "rose wine", "riesling", "moscato", "champagne", "prosecco", "sparkling wine",
    "port wine", "sangria",
    # beer
    "ipa", "lager", "pilsner", "stout", "pale ale", "wheat beer", "light beer", "mexican beer",
    # rtd / adjacent
    "hard seltzer", "canned cocktail", "hard cider", "sake", "vermouth", "non alcoholic beer",
    # hemp/thc — grabbed + set aside
    "hemp", "cbd", "thc seltzer", "thc drink"]
DEFAULT_ZIPS = [
    "45202", "43215", "30303", "77002", "75201", "37203", "46204", "40202", "38103", "72201",   # Kroger
    "80202", "80903",                                                                            # King Soopers
    "85004", "85701",                                                                            # Fry's
    "90012", "92101",                                                                            # Ralphs
    "89101", "84101", "87102",                                                                   # Smith's
    "98101", "97204",                                                                            # QFC / Fred Meyer
    "67202", "28202", "27601", "20001",                                                          # Dillons / Harris Teeter
    "60601", "53202"]                                                                            # Food 4 Less / Metro Market


def log(*a): print(*a, file=sys.stderr, flush=True)


def _load_creds():
    for p in [os.environ.get("WH_ENV_FILE", ""),
              os.path.expanduser("~/Desktop/Desktop - Chris’s MacBook Pro/Projects/hoodie-backend/warehouse.env"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "warehouse.env")]:
        if p and os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1); k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k and v and not os.environ.get(k):
                        os.environ[k] = v
            break
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        try:
            import configparser
            cp = configparser.ConfigParser(); cp.read(os.path.expanduser("~/.aws/credentials"))
            prof = os.environ.get("AWS_PROFILE", "default")
            if cp.has_section(prof):
                os.environ.setdefault("AWS_ACCESS_KEY_ID", cp.get(prof, "aws_access_key_id", fallback=""))
                os.environ.setdefault("AWS_SECRET_ACCESS_KEY", cp.get(prof, "aws_secret_access_key", fallback=""))
        except Exception:
            pass


# Kroger throttles with 429/503 when we push too fast (the 503s in the logs). Back off + retry, and
# trip a circuit breaker after too many in a row so we STOP rather than grind into a hard block.
_KR_STRIKES = [0]                      # consecutive throttles across the run
_KR_BREAKER = int(os.environ.get("KROGER_BREAKER", "25"))   # tolerate a longer 503 spell before giving up
# POLITE PACING — the previous version fired calls back-to-back with no gap, so Kroger 503'd fast and the
# breaker killed the run after ~2 stores. Keep a min gap between calls and ADAPT it up on every throttle
# (halve back toward the floor on sustained success), so a run slows into Kroger's limits instead of dying.
_KR_PACE_MIN = float(os.environ.get("KROGER_PACE", "0.4"))  # floor gap between calls (s)
_KR_PACE_MAX = 8.0
_KR_PACE = [_KR_PACE_MIN]              # current adaptive gap
_KR_LAST = [0.0]                       # ts of last request
_KR_OK = [0]                           # consecutive successes (for decay)


class KrogerBlocked(RuntimeError):
    pass


def _req(url, headers=None, data=None, retries=5):
    for attempt in range(retries + 1):
        gap = _KR_PACE[0] - (time.time() - _KR_LAST[0])   # honor the adaptive pace
        if gap > 0:
            time.sleep(gap)
        r = urllib.request.Request(url, data=data, headers=headers or {})
        _KR_LAST[0] = time.time()
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                _KR_STRIKES[0] = 0
                _KR_OK[0] += 1
                if _KR_OK[0] >= 20 and _KR_PACE[0] > _KR_PACE_MIN:   # sustained success → speed back up
                    _KR_PACE[0] = max(_KR_PACE_MIN, _KR_PACE[0] / 2); _KR_OK[0] = 0
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                _KR_STRIKES[0] += 1; _KR_OK[0] = 0
                _KR_PACE[0] = min(_KR_PACE_MAX, max(_KR_PACE[0] * 1.5, _KR_PACE_MIN * 2))  # back off the pace
                if _KR_STRIKES[0] >= _KR_BREAKER:
                    raise KrogerBlocked("Kroger throttling us (%d %d× in a row) — stopping this run to avoid a block"
                                        % (e.code, _KR_STRIKES[0]))
                ra = e.headers.get("Retry-After") if e.headers else None
                backoff = float(ra) if (ra and str(ra).isdigit()) else min(60.0, 2.0 ** attempt)
                time.sleep(backoff + random.uniform(0, 1.5))
                continue
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise RuntimeError("HTTP %s — %s" % (e.code, body or e.reason))
    raise RuntimeError("Kroger: exhausted retries after repeated throttling")


def token():
    cid = os.environ.get("KROGER_CLIENT_ID", ""); sec = os.environ.get("KROGER_CLIENT_SECRET", "")
    if not (cid and sec):
        return None
    basic = base64.b64encode(("%s:%s" % (cid, sec)).encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "product.compact"}).encode()
    d = _req(TOKEN_URL, {"Authorization": "Basic " + basic,
                         "Content-Type": "application/x-www-form-urlencoded"}, body)
    return d.get("access_token")


# store-level SCALE knobs (env-tunable so a run can be dialed small for a check or full for the whole banner):
#   KROGER_STORES_PER_ZIP (default 50, Kroger caps filter.limit at 200) — how many stores to enumerate per ZIP
#   KROGER_RADIUS         (miles, optional) — widen each ZIP's store search to reach the full metro
#   KROGER_MAX_PER_TERM   (default 250, Kroger caps filter.start at 250) — paginate the per-store catalog per term
def locations(tok, zip_code, limit=None, radius=None):
    limit = int(limit or os.environ.get("KROGER_STORES_PER_ZIP", "50"))
    params = {"filter.zipCode.near": zip_code, "filter.limit": min(max(limit, 1), 200)}
    radius = radius or os.environ.get("KROGER_RADIUS")
    if radius:
        params["filter.radiusInMiles"] = radius
    d = _req("%s/locations?%s" % (API, urllib.parse.urlencode(params)), {"Authorization": "Bearer " + tok})
    return [(loc["locationId"], loc.get("name", ""),
             (loc.get("address", {}) or {}).get("city", ""), (loc.get("address", {}) or {}).get("state", ""))
            for loc in d.get("data", [])]


def _parse_product(p, location_id, term):
    it = (p.get("items") or [{}])[0]
    price = it.get("price") or {}
    stock = (it.get("inventory") or {}).get("stockLevel", "")
    cats = p.get("categories") or []
    name = p.get("description", "")
    return dict(
        product_id=p.get("productId", ""), upc=p.get("upc", ""), brand=(p.get("brand") or ""),
        product_name=name, category=", ".join(cats),
        size=it.get("size", ""), price=price.get("regular"), promo=price.get("promo"),
        on_promo=bool(price.get("promo") and price.get("regular") and price["promo"] < price["regular"]),
        stock_level=stock, in_stock=(stock not in ("", "TEMPORARILY_OUT_OF_STOCK")),
        image_url=_front_image(p), is_hemp=observe.is_hemp(p.get("brand"), name, " ".join(cats)),
        raw_json=json.dumps(p), location_id=location_id, term=term)


def products(tok, term, location_id, limit=50, max_items=None):
    """PAGINATE the per-store catalog for a term (was a single 25-item page). Kroger returns up to 50/page and
    accepts filter.start up to 250, so this walks the pages to ~250 products/term/store."""
    max_items = int(max_items or os.environ.get("KROGER_MAX_PER_TERM", "250"))
    out, start = [], 0
    while start < max_items:
        take = min(limit, max_items - start, 50)
        params = {"filter.term": term, "filter.locationId": location_id, "filter.limit": take}
        if start:
            params["filter.start"] = start
        d = _req("%s/products?%s" % (API, urllib.parse.urlencode(params)), {"Authorization": "Bearer " + tok})
        batch = d.get("data", []) or []
        out.extend(_parse_product(p, location_id, term) for p in batch)
        if len(batch) < take:                     # short page → last page
            break
        start += take
    return out


def _land(uniq, n_stores, run_id):
    """Land the snapshot + dated observations + a run row. Called after EACH zip so progress is saved
    and visible (restart-safe) — not just once at the end. ACCUMULATES: merges this run's rows into the
    existing catalog (dedup by store×product, newest wins) so a small run GROWS the catalog instead of
    overwriting a bigger prior run (the dated time-series lives in retail_observations)."""
    try:
        existing = warehouse.query("kroger_products", "SELECT * FROM t")
    except Exception:
        existing = []
    idx = {(r.get("location_id"), r.get("product_id")): r for r in existing}
    for r in uniq:
        idx[(r.get("location_id"), r.get("product_id"))] = r
    catalog = list(idx.values())
    warehouse.write_parquet("kroger_products", catalog)
    observe.record("kroger", [dict(store=r["store"], store_id=r["location_id"],
                                   product_id=r["product_id"], upc=r["upc"], brand=r["brand"],
                                   name=r["product_name"], price=r["price"], promo=r["promo"],
                                   on_promo=r["on_promo"], in_stock=r["in_stock"], qty=None,
                                   stock_level=r["stock_level"], is_hemp=r.get("is_hemp")) for r in uniq])
    oos = sum(1 for r in uniq if not r["in_stock"])
    runs = []
    try:
        runs = [x for x in warehouse.query("kroger_runs", "SELECT * FROM t") if x.get("run_id") != run_id]
    except Exception:
        pass
    runs.append(dict(run_id=run_id, at=int(time.time()), products=len(uniq), stores=n_stores,
                     in_stock=len(uniq) - oos, on_promo=sum(1 for r in uniq if r["on_promo"]),
                     status=("ok" if uniq else "degraded"), note="kroger api"))
    warehouse.write_parquet("kroger_runs", runs[-50:])


def run(zips, terms):
    _load_creds()
    tok = None
    try:
        tok = token()
    except Exception as e:
        log("  Kroger token failed: %s" % str(e)[:150])
    if not tok:
        log("  [kroger] OFF — set KROGER_CLIENT_ID / KROGER_CLIENT_SECRET (developer.kroger.com) to enable")
        return None
    run_id = "kr-" + time.strftime("%Y%m%d-%H%M%S")
    pace = float(os.environ.get("KROGER_PACE", "0.5"))   # base gap between calls (backoff handles bursts)
    seen, uniq, stores = set(), [], set()
    import runlog
    _rl = runlog.start("kroger", total=len(zips))        # register on the Active Runs board (was invisible)
    try:
        for zi, z in enumerate(zips, 1):
            try:
                locs = locations(tok, z)
            except KrogerBlocked:
                raise
            except Exception as e:
                log("  locations(%s) failed: %s" % (z, str(e)[:80])); continue
            for (lid, name, city, state) in locs:
                stores.add(lid)
                for t in terms:
                    try:
                        for r in products(tok, t, lid):
                            k = (r["product_id"], lid)
                            if k in seen:
                                continue
                            seen.add(k)
                            r["run_id"] = run_id; r["store"] = name; r["city"] = city; r["state"] = state
                            uniq.append(r)
                    except KrogerBlocked:
                        raise                         # stop the whole run — don't grind into a ban
                    except Exception as e:
                        log("  products(%s@%s) failed: %s" % (t, lid, str(e)[:60]))
                    time.sleep(pace + random.uniform(0, pace))
            _land(uniq, len(stores), run_id)          # incremental land after each zip
            _rl.progress(zi)
            log("  [%s] zip %d/%d (%s) — %d products, %d stores so far -> warehouse"
                % (run_id, zi, len(zips), z, len(uniq), len(stores)))
    except KrogerBlocked as e:
        log("  [%s] STOPPED EARLY: %s" % (run_id, e))
        _land(uniq, len(stores), run_id)              # keep what we got
        _rl.finish("degraded", note="throttle breaker after %d stores; %d products" % (len(stores), len(uniq)))
        log("[%s] STOPPED %d products across %d stores (throttle breaker) -> warehouse" % (run_id, len(uniq), len(stores)))
        return run_id, len(uniq)
    _rl.finish("done", note="%d products across %d stores" % (len(uniq), len(stores)))
    log("[%s] DONE %d products across %d stores -> warehouse" % (run_id, len(uniq), len(stores)))
    return run_id, len(uniq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zips", default=",".join(DEFAULT_ZIPS))
    ap.add_argument("--terms", default=",".join(DEFAULT_TERMS))
    a = ap.parse_args()
    run([z.strip() for z in a.zips.split(",") if z.strip()],
        [t.strip() for t in a.terms.split(",") if t.strip()])


if __name__ == "__main__":
    main()
