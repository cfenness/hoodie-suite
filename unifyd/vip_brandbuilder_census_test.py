#!/usr/bin/env python3
"""vip_brandbuilder_census_test.py — classification + soft-hit confirmation, no network.

Pins the real response shapes measured live against products.vtinfo.com/bbs on 2026-08-02:
  200 + success + distributorName  -> a real VIP distributor record ("hit" on /info)
  400 "Distributor ID is invalid"  -> a confirmed miss
  500 "SSO Service Error"          -> inconclusive (reproduced 3/3 identical on retry live —
                                       not request noise, but not the clean "invalid" signal either)

The core contract this file exists to prove: a hit on /info is NOT enough to call a distributor
found. Real live behavior for Columbia (01191) and Florida Distributing (00177) shows /info
succeeding is just a VIP record existing — this only becomes a genuine find ("confirmed") once
/products independently proves the catalog is populated. A distributor with a VIP record but zero
products must land as "info_only", never silently promoted to "confirmed".

    python3 unifyd/vip_brandbuilder_census_test.py
"""
import os
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


# Real payload shapes captured live 2026-08-02.
HIT_200 = (200, {"code": 200, "errorMessage": None, "success": True,
                 "data": {"vipSourceId": 420007425, "vipCustomerId": 10007962, "sourceCode": "00177",
                          "distributorName": "Florida Distributing Company"}})
MISS_400 = (400, {"code": 400, "errorMessage": "Distributor ID is invalid\n", "data": None, "success": False})
INCONCLUSIVE_500 = (500, {"code": 400, "errorMessage": "SSO Service Error\n", "data": None, "success": False})
MALFORMED_200 = (200, {"code": 200, "success": True, "data": {}})              # 200 but no distributorName
PRODUCTS_NONEMPTY = (200, {"code": 200, "success": True,
                           "data": [{"product_id": "1", "product_packages": [{"dist_item_code": "69885"}]}]})
PRODUCTS_EMPTY = (200, {"code": 200, "success": True, "data": []})


def main():
    import warehouse
    warehouse._LOCAL_DIR = tempfile.mkdtemp(prefix="bbs_census_")
    import vip_brandbuilder_census as m

    print("classify_info() reads the real live response shapes correctly")
    outcome, data = m.classify_info(*HIT_200)
    check("a real distributor record classifies as a hit", outcome == "hit", outcome)
    check("the distributor name survives", data.get("distributorName") == "Florida Distributing Company", data)

    outcome, data = m.classify_info(*MISS_400)
    check("'Distributor ID is invalid' classifies as a confirmed miss", outcome == "miss", outcome)

    outcome, data = m.classify_info(*INCONCLUSIVE_500)
    check("'SSO Service Error' classifies as inconclusive, NOT a miss", outcome == "inconclusive", outcome)

    outcome, data = m.classify_info(*MALFORMED_200)
    check("HTTP 200 with no distributorName is inconclusive, not a silent hit", outcome == "inconclusive", outcome)

    print("\nSTAY SOFT: a hit on /info alone never reaches 'confirmed' without a real products check")
    real_fetch = m._fetch_json
    m._fetch_json = lambda url, timeout=15: (
        PRODUCTS_NONEMPTY if url.endswith("/products") else HIT_200)
    try:
        state = m._blank()
        lock = __import__("threading").Lock()
        m._probe_one("00177", m._Counters(), state, lock, log=lambda *a: None)
        rec = state["hits"]["00177"]
        check("a hit WITH real products lands as 'confirmed'", rec["status"] == "confirmed", rec)
        check("n_products reflects the real confirming check", rec["n_products"] == 1, rec)
    finally:
        m._fetch_json = real_fetch

    m._fetch_json = lambda url, timeout=15: (
        PRODUCTS_EMPTY if url.endswith("/products") else HIT_200)
    try:
        state = m._blank()
        lock = __import__("threading").Lock()
        m._probe_one("00177", m._Counters(), state, lock, log=lambda *a: None)
        rec = state["hits"]["00177"]
        check("a hit with ZERO products lands as 'info_only', never silently promoted", rec["status"] == "info_only",
              rec)
        check("n_products is honestly 0, not omitted", rec["n_products"] == 0, rec)
    finally:
        m._fetch_json = real_fetch

    print("\na miss never reaches state['hits'] at all")
    m._fetch_json = lambda url, timeout=15: MISS_400
    try:
        state = m._blank()
        lock = __import__("threading").Lock()
        c = m._Counters()
        m._probe_one("99999", c, state, lock, log=lambda *a: None)
        check("a confirmed miss is NOT in hits", "99999" not in state["hits"], state)
        check("a confirmed miss IS recorded in done (won't be re-probed)", state["done"].get("99999") == "miss",
              state)
        check("miss counter incremented", c.n_miss == 1, c.n_miss)
    finally:
        m._fetch_json = real_fetch

    print("\nan inconclusive result is recorded distinctly — not folded into miss or hit")
    m._fetch_json = lambda url, timeout=15: INCONCLUSIVE_500
    try:
        state = m._blank()
        lock = __import__("threading").Lock()
        c = m._Counters()
        m._probe_one("54321", c, state, lock, log=lambda *a: None)
        check("inconclusive is not in hits", "54321" not in state["hits"], state)
        check("inconclusive is tagged distinctly in done (re-probeable later)",
              state["done"].get("54321") == "inconclusive", state)
        check("inconclusive counter incremented, not the miss counter", c.n_inconclusive == 1 and c.n_miss == 0,
              (c.n_inconclusive, c.n_miss))
    finally:
        m._fetch_json = real_fetch

    print("\nkeyspace covers the full 100k range and shards cleanly")
    full = m.keyspace()
    check("full keyspace is 100,000 ids", len(full) == 100000, len(full))
    check("keyspace is zero-padded 5-digit strings", full[0] == "00000" and full[-1] == "99999",
          (full[0], full[-1]))
    shard0 = m.keyspace(shard=0, nshard=4)
    shard1 = m.keyspace(shard=1, nshard=4)
    check("shards partition disjointly", not (set(shard0) & set(shard1)), None)
    check("shards reunite to the full keyspace",
          sum(len(m.keyspace(shard=s, nshard=4)) for s in range(4)) == 100000, None)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
