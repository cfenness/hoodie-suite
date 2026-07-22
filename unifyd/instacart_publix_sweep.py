#!/usr/bin/env python3
"""instacart_publix_sweep.py — sweep PUBLIX across its footprint on Instacart and land per-store INVENTORY.

FREE: a self-hosted headless Chromium (NO Bright Data, NO proxy) drives Instacart's own
SearchResultsPlacements GraphQL. For each delivery zip we enter the Publix storefront (skipping any zip that
isn't Publix territory) and probe the INVENTORY_QUERIES basket — every in-stock item lands in
retail_observations keyed by store_id (the per-store inventory signal) + the product catalog in
instacart_products. Lands to the SHARED warehouse when AWS_ENDPOINT_URL_S3/BUCKET_NAME are set (Tigris in
CI/app), else a local warehouse for a dry proof.

    python instacart_publix_sweep.py [--max-stores N] [--zips 33101,30303,...]

Publix operates in FL/GA/AL/SC/NC/TN/VA/KY. The default zip list tiles those metros; each zip resolves to
its nearest Publix, so distinct stores ≈ distinct zips (dupes dedup by shopId). Denser/complete coverage =
more zips (pass --zips or IC_ZIPS); this metro list is a strong first pass, not every one of the ~1,400 stores.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FETCH_POLICY", "free")            # belt-and-suspenders: never a paid seam
os.environ.setdefault("BROWSER_HEADFUL", "0")            # headless (proven to land from a datacenter IP)

# Publix-footprint metro zips (FL-dense, then GA/AL/SC/NC/TN/VA/KY). Each ≈ one nearest Publix store.
PUBLIX_ZIPS = [
    # FL — Publix's densest state
    "33101", "33139", "33301", "33401", "33409", "33418", "33432", "33470", "33064", "33028", "33027",
    "33174", "33186", "33033", "33990", "33901", "33912", "34102", "34110", "34142", "34236", "34285",
    "34741", "34747", "32801", "32828", "32835", "32712", "34761", "34711", "32168", "32114", "32117",
    "33602", "33607", "33629", "33701", "33710", "33607", "33511", "33544", "33549", "34638", "34655",
    "33801", "33813", "33063", "34470", "34471", "32601", "32605", "32226", "32202", "32204", "32256",
    "32301", "32308", "32501", "32526", "32571", "32908", "32901", "32955", "34952", "34953", "34990",
    # GA
    "30303", "30305", "30060", "30068", "30004", "30022", "30075", "30044", "30096", "31401", "31410",
    "31201", "31909", "30901", "30606", "30518", "30188",
    # AL
    "35203", "35205", "35242", "35801", "35806", "36104", "36602", "36695", "35401", "36830",
    # SC
    "29401", "29407", "29201", "29212", "29601", "29615", "29577", "29572", "29928", "29464", "29910",
    # NC
    "28202", "28277", "28105", "27601", "27609", "27519", "28401", "28403", "28801", "27401", "27701",
    # TN
    "37203", "37211", "37027", "37902", "37402", "38103",
    # VA
    "23219", "23451", "23233", "23113", "24011", "22902",
    # KY
    "40202", "40507",
]


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-stores", type=int, default=int(os.environ.get("IC_MAX_STORES", "0")),
                    help="stop after N distinct Publix stores (0 = all zips in the list)")
    ap.add_argument("--zips", default=os.environ.get("IC_ZIPS", ""),
                    help="comma-separated zips (overrides the built-in Publix footprint)")
    a = ap.parse_args()

    import json
    import warehouse
    from instacart import Instacart, INVENTORY_QUERIES

    # PROBE mode: replay an exact captured graphql URL from here and report what comes back (ground truth).
    probe = os.environ.get("IC_PROBE_URL", "").strip()
    if probe:
        print("[publix-sweep] PROBE of a captured graphql URL from this IP (no proxy, no bd)")
        Instacart().probe_url(probe, log=print)
        return 0

    # DIRECT-REPLAY path: if IC_ZONES is set (JSON list of {shopId,postalCode,zoneId,slug}) replay those
    # Publix stores directly — no homepage/address/geolocation (a datacenter IP won't surface Publix).
    zones_json = os.environ.get("IC_ZONES", "").strip()
    if zones_json:
        try:
            zones = json.loads(zones_json)
        except Exception as e:
            print("[publix-sweep] IC_ZONES is not valid JSON: %s" % str(e)[:120]); return 1
        where = "SHARED (Tigris)" if warehouse.remote() else "LOCAL (dry)"
        print("[publix-sweep] DIRECT REPLAY of %d Publix zone(s) -> %s (no proxy, no bd)" % (len(zones), where))
        rows = Instacart().pull_zones(zones, queries=INVENTORY_QUERIES, log=print)
        return _report(warehouse, rows, 0, 0, where)

    zips = [z.strip() for z in a.zips.split(",") if z.strip()] or PUBLIX_ZIPS
    if a.max_stores and a.max_stores > 0:
        zips = zips[: a.max_stores * 2]                  # ~1 store/zip; extra headroom for skips/dupes

    def obs():
        try:
            r = warehouse.query_parts("retail_observations",
                                      "SELECT count(*) c, count(distinct store_id) s FROM t WHERE source='instacart'")
            return (int(r[0]["c"]), int(r[0]["s"])) if r else (0, 0)
        except Exception:
            return (0, 0)

    where = "SHARED (Tigris)" if warehouse.remote() else "LOCAL (dry)"
    o0, s0 = obs()
    print("[publix-sweep] warehouse=%s  FETCH_POLICY=%s  headful=%s" % (
        where, os.environ.get("FETCH_POLICY"), os.environ.get("BROWSER_HEADFUL")))
    print("[publix-sweep] zips=%d  queries=%d  obs before: %d rows / %d stores" %
          (len(zips), len(INVENTORY_QUERIES), o0, s0))

    ic = Instacart(target_retailer="Publix")
    rows = ic.sweep(zips, retailer="Publix", queries=INVENTORY_QUERIES, log=print)

    return _report(warehouse, rows, o0, s0, where)


def _report(warehouse, rows, o0, s0, where):
    def obs():
        try:
            r = warehouse.query_parts("retail_observations",
                                      "SELECT count(*) c, count(distinct store_id) s FROM t WHERE source='instacart'")
            return (int(r[0]["c"]), int(r[0]["s"])) if r else (0, 0)
        except Exception:
            return (0, 0)
    o1, s1 = obs()
    try:
        prod = warehouse.row_count("instacart_products")
    except Exception:
        prod = 0
    levels = []                                          # stock-level distribution = did the availability signal land?
    try:
        levels = warehouse.query_parts(
            "retail_observations",
            "SELECT coalesce(nullif(stock_level,''),'(none)') lvl, count(*) n, sum(case when in_stock then 0 else 1 end) oos "
            "FROM t WHERE source='instacart' GROUP BY 1 ORDER BY 2 DESC")
    except Exception as e:
        print("  (stock-level breakdown skipped: %s)" % str(e)[:100])
    print("\n--- RESULT ---")
    print("  rows returned:            %d" % len(rows))
    print("  retail_observations(ic):  %d rows / %d stores  (+%d rows, +%d stores)" %
          (o1, s1, o1 - o0, s1 - s0))
    print("  instacart_products:       %d" % prod)
    print("  stock_level distribution (the inventory signal):")
    for r in (levels or []):
        print("      %-16s %6d rows  (%d out-of-stock)" % (r.get("lvl"), r.get("n"), r.get("oos") or 0))
    if levels and all((r.get("lvl") == "(none)") for r in levels):
        print("      NOTE: stock_level empty on all rows → the op didn't carry availability; presence=in-stock only.")
    print("\nVERDICT: %s" % (
        "INVENTORY LANDED — per-store in-stock rows written to %s, NO proxy, NO bd." % where
        if (o1 - o0) > 0 else
        "NO INVENTORY LANDED — check the log (no zone reached, or anti-bot at this IP)."))
    return 0 if (o1 - o0) > 0 else 1


if __name__ == "__main__":
    sys.exit(run())
