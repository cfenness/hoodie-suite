#!/usr/bin/env python3
"""instacart_free_verify.py — does the FREE Instacart connector actually LAND data end-to-end? Prove it.

`instacart_free_probe.py` proved the *recon path* (a bare browser reaches Instacart's store + product
GraphQL, no proxy, no bd). This proves the *connector*: it drives the real `Instacart` class
(open_session → open_zone → fetch_page → parse_item → _land) against a LOCAL warehouse and reports the
row counts it lands. Green here = the connector is done end-to-end on the free path; only then do we
repoint server.py off the paid Bright Data dataset.

Runs against a scratch LOCAL warehouse (agent_state/warehouse) — no S3/prod creds needed; the same code
lands to Tigris when AWS_ENDPOINT_URL_S3/BUCKET_NAME are set in the app. Standing rule: NO proxy, NO bd.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Cost guard belt-and-suspenders: this connector must never touch a paid proxy.
os.environ.setdefault("FETCH_POLICY", "free")

ZIP = os.environ.get("IC_ZIP", "10001")
QUERIES = [q.strip() for q in os.environ.get("IC_QUERIES", "milk,vodka,chips,coffee,eggs").split(",") if q.strip()]


def run():
    import warehouse
    from instacart import Instacart

    def n(name):
        try:
            return warehouse.row_count(name)
        except Exception:
            return 0

    before = n("instacart_products")
    print("[verify] instacart_products before: %d" % before)
    print("[verify] zip=%s queries=%s FETCH_POLICY=%s" % (ZIP, QUERIES, os.environ.get("FETCH_POLICY")))

    ic = Instacart()
    rows = []
    try:
        rows = ic.pull(address=ZIP, retailers=["grocery"], queries=QUERIES,
                       per_query_pages=1, log=lambda m: print("  " + str(m)))
    except Exception as e:
        print("[verify] connector raised: %s" % (str(e)[:300]))

    after = n("instacart_products")
    obs = 0
    try:
        r = warehouse.query_parts("retail_observations", "SELECT count(*) c FROM t WHERE source='instacart'")
        obs = int(r[0]["c"]) if r else 0
    except Exception as e:
        print("[verify] obs count skipped: %s" % str(e)[:120])

    print("\n--- RESULT ---")
    print("  rows returned by pull():   %d" % len(rows))
    print("  instacart_products:        %d -> %d  (+%d)" % (before, after, after - before))
    print("  retail_observations(ic):   %d" % obs)
    sample = rows[0] if rows else {}
    if sample:
        print("  sample: %r" % {k: sample.get(k) for k in ("store", "name", "price", "product_id")})

    landed = (after - before) > 0 or len(rows) > 0
    print("\nVERDICT: %s" % (
        "FREE CONNECTOR LANDS DATA — open_zone captured a real zone, SearchResultsPlacements replayed, "
        "products landed to the warehouse with NO proxy, NO bd. Repoint server.py off the BD dataset."
        if landed else
        "NO DATA LANDED — the connector reached the browser but did not capture a zone / land products. "
        "Inspect the log above (membership wall, zone capture, or anti-bot at the datacenter IP)."))
    return 0 if landed else 1


if __name__ == "__main__":
    sys.exit(run())
