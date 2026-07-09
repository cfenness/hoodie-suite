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
import argparse, base64, json, os, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

TOKEN_URL = "https://api.kroger.com/v1/connect/oauth2/token"
API = "https://api.kroger.com/v1"
DEFAULT_TERMS = ["bourbon", "whiskey", "vodka", "tequila", "cabernet sauvignon", "chardonnay", "ipa", "lager"]
DEFAULT_ZIPS = ["45202", "30303", "77002", "80202"]      # Cincinnati, Atlanta, Houston, Denver


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


def _req(url, headers=None, data=None):
    r = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def token():
    cid = os.environ.get("KROGER_CLIENT_ID", ""); sec = os.environ.get("KROGER_CLIENT_SECRET", "")
    if not (cid and sec):
        return None
    basic = base64.b64encode(("%s:%s" % (cid, sec)).encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "product.compact"}).encode()
    d = _req(TOKEN_URL, {"Authorization": "Basic " + basic,
                         "Content-Type": "application/x-www-form-urlencoded"}, body)
    return d.get("access_token")


def locations(tok, zip_code, limit=2):
    q = urllib.parse.urlencode({"filter.zipCode.near": zip_code, "filter.limit": limit})
    d = _req("%s/locations?%s" % (API, q), {"Authorization": "Bearer " + tok})
    return [(loc["locationId"], loc.get("name", ""),
             (loc.get("address", {}) or {}).get("city", ""), (loc.get("address", {}) or {}).get("state", ""))
            for loc in d.get("data", [])]


def products(tok, term, location_id, limit=25):
    q = urllib.parse.urlencode({"filter.term": term, "filter.locationId": location_id, "filter.limit": limit})
    d = _req("%s/products?%s" % (API, q), {"Authorization": "Bearer " + tok})
    out = []
    for p in d.get("data", []):
        it = (p.get("items") or [{}])[0]
        price = it.get("price") or {}
        inv = it.get("inventory") or {}
        stock = inv.get("stockLevel", "")
        out.append(dict(
            product_id=p.get("productId", ""), upc=p.get("upc", ""), brand=(p.get("brand") or ""),
            product_name=p.get("description", ""),
            category=", ".join(p.get("categories") or []),
            size=it.get("size", ""), price=price.get("regular"), promo=price.get("promo"),
            on_promo=bool(price.get("promo") and price.get("regular") and price["promo"] < price["regular"]),
            stock_level=stock, in_stock=(stock not in ("", "TEMPORARILY_OUT_OF_STOCK")),
            location_id=location_id, term=term))
    return out


def run(zips, terms):
    _load_creds()
    tok = None
    try:
        tok = token()
    except Exception as e:
        log("  Kroger token failed: %s" % str(e)[:120])
    if not tok:
        log("  [kroger] OFF — set KROGER_CLIENT_ID / KROGER_CLIENT_SECRET (developer.kroger.com) to enable")
        return None
    run_id = "kr-" + time.strftime("%Y%m%d-%H%M%S")
    rows, stores = [], []
    for z in zips:
        try:
            locs = locations(tok, z)
        except Exception as e:
            log("  locations(%s) failed: %s" % (z, str(e)[:80])); continue
        for (lid, name, city, state) in locs:
            stores.append((lid, name, city, state))
            for t in terms:
                try:
                    for r in products(tok, t, lid):
                        r["run_id"] = run_id; r["store"] = name; r["city"] = city; r["state"] = state
                        rows.append(r)
                except Exception as e:
                    log("  products(%s@%s) failed: %s" % (t, lid, str(e)[:60]))
                time.sleep(0.2)
    # de-dupe by product×location
    seen, uniq = set(), []
    for r in rows:
        k = (r["product_id"], r["location_id"])
        if k not in seen:
            seen.add(k); uniq.append(r)
    warehouse.write_parquet("kroger_products", uniq)
    oos = sum(1 for r in uniq if not r["in_stock"])
    runs = []
    try:
        runs = warehouse.query("kroger_runs", "SELECT * FROM t")
    except Exception:
        pass
    runs.append(dict(run_id=run_id, at=int(time.time()), products=len(uniq), stores=len(stores),
                     in_stock=len(uniq) - oos, on_promo=sum(1 for r in uniq if r["on_promo"]),
                     status=("ok" if uniq else "degraded"), note="kroger api"))
    warehouse.write_parquet("kroger_runs", runs[-50:])
    log("[%s] %d products across %d stores (%d OOS, %d promo) -> warehouse"
        % (run_id, len(uniq), len(stores), oos, sum(1 for r in uniq if r["on_promo"])))
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
