#!/usr/bin/env python3
"""hemp_chains.py — the CHAINS that sell hemp/THC BEVERAGES, each assessed for whether we can get per-store
INVENTORY COUNTS (the goal) and how. This is the hemp cut of the bev-alc chains registry, extended with the
grocery/convenience chains that carry THC beverages (esp. Minnesota + the Midwest, where low-dose THC drinks
are sold in mainstream retail) but aren't primarily bev-alc.

`hemp` status:  confirmed = we already found hemp products in our data · likely = sells THC bev, needs a pull to
confirm · verify = plausible, unconfirmed.
`inventory`:    count = exact per-store units · instock = in/out only · none = no public inventory.
`count_method`: how we'd get the count — binnys_qty (native per-store qty), total_wine_getproduct (exact units),
                shopify_cartadd (the cart-add trick), api:kroger (store availability), abc_fws (store in/out),
                unknown (needs recon).
Lands `hemp_chains` so the tracker/Sources read it and we can drive the inventory pull priority. Confirmed
counts land first (Binny's, Total Wine); the "likely" grocery/convenience are the expansion to verify.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

FLD = ["chain", "type", "states", "hemp", "inventory", "count_method", "our_source", "est_locations", "note"]
# (chain, type, states, hemp, inventory, count_method, our_source, est_locations, note)
_H = [
    # ── confirmed in our data — pull inventory FIRST ──
    ("Binny's Beverage Depot", "liquor", "IL", "confirmed", "count", "binnys_qty", "binnys_products", 45,
     "367 hemp products, native per-store qty — the model"),
    ("Total Wine & More", "liquor", "MN,national", "confirmed", "count", "total_wine_getproduct", "total_wine_products", 270,
     "exact per-store units via getProduct; THC bev in MN — thin hemp so far, push MN stores"),
    ("Spec's", "liquor", "TX", "confirmed", "instock", "specs_cart", "specs_products", 200, "256 hemp; per-store in/out"),
    ("Kroger", "grocery", "national", "confirmed", "instock", "api:kroger", "kroger_products", 2700,
     "CBD-leaning; store availability via API"),
    ("ABC Fine Wine & Spirits", "liquor", "FL", "confirmed", "instock", "abc_fws", "abc_products", 170,
     "FL hemp bev; per-store in/out inventory (abc_fws)"),
    ("Nothing But Hemp", "hemp", "MN", "confirmed", "count", "shopify_cartadd", "orlando_hemp_products", 6,
     "MN hemp chain + e-comm; cart-add counts (THC Tinctures=98, Active=78)"),
    ("Your CBD Store / SUNMED", "hemp", "national(franchise)", "confirmed", "count", "shopify_cartadd", "orlando_hemp_products", 500,
     "national CBD franchise, Shopify — cart-add"),
    # ── Minnesota / Midwest grocery — THC bev in mainstream retail, high-probability, VERIFY + pull ──
    ("Hy-Vee", "grocery", "MN,IA,Midwest", "likely", "unknown", "unknown", "", 285, "carries THC bev in MN/IA — recon the e-comm inventory"),
    ("Cub Foods", "grocery", "MN", "likely", "unknown", "unknown", "", 75, "MN THC bev in-store; check e-comm"),
    ("Lunds & Byerlys", "grocery", "MN", "likely", "unknown", "unknown", "", 27, "premium MN grocer, THC bev"),
    ("Coborn's", "grocery", "MN,Midwest", "likely", "unknown", "unknown", "", 55, "MN THC bev"),
    ("Kwik Trip", "convenience", "MN,WI,IA", "likely", "unknown", "unknown", "", 850, "MN/WI convenience, THC bev"),
    ("Casey's", "convenience", "Midwest", "likely", "unknown", "unknown", "", 2600, "Midwest THC bev in hemp states"),
    ("Holiday Stationstores", "convenience", "MN,Upper-Midwest", "likely", "unknown", "unknown", "", 500, "MN convenience (Circle K-owned), THC bev"),
    ("Meijer", "grocery", "Midwest", "likely", "unknown", "unknown", "", 500, "Midwest supercenter, hemp/CBD"),
    ("Schnucks", "grocery", "MO,Midwest", "verify", "unknown", "unknown", "", 110, "Midwest grocer"),
    # ── national convenience / mass — THC bev in the loophole states (Grepsr had some on-hold) ──
    ("Circle K", "convenience", "national", "likely", "unknown", "unknown", "", 7000, "THC bev in hemp states; Grepsr on-hold"),
    ("Target", "mass", "national", "likely", "unknown", "bd:target", "", 1950, "CBD + some THC bev; Grepsr on-hold"),
    ("7-Eleven", "convenience", "national", "likely", "count", "sevennow", "sevennow_products", 9000,
     "7NOW (first-party delivery) exposes EXACT per-store on-hand counts + UPC + price; Beer/Wine confirmed "
     "(Austin store: 125 beer + 97 wine w/ counts), hemp bev surfaces the same way in stores that carry it"),
    ("Sheetz", "convenience", "Mid-Atlantic", "verify", "unknown", "unknown", "", 700, "carries Delta-8/THC bev in some markets"),
    # ── liquor superstores — carry hemp bev where legal ──
    ("BevMo!", "liquor", "CA,AZ", "verify", "unknown", "unknown", "", 160, "check hemp bev assortment"),
    ("Twin Liquors", "liquor", "TX", "verify", "unknown", "unknown", "", 100, "TX hemp bev"),
    # ── drug — CBD, generally not THC bev ──
    ("CVS", "drug", "national", "verify", "none", "none", "", 9000, "CBD topicals; not THC bev"),
    ("Walgreens", "drug", "national", "verify", "none", "none", "", 8000, "CBD; not THC bev"),
]


def build(log=print):
    rows = [dict(zip(FLD, r)) for r in _H]
    warehouse.write_parquet("hemp_chains", rows, fields=FLD)
    from collections import Counter
    st = Counter(r["hemp"] for r in rows); iv = Counter(r["inventory"] for r in rows)
    log("[hemp_chains] %d chains -> hemp_chains | status %s | inventory %s" % (len(rows), dict(st), dict(iv)))
    return rows


if __name__ == "__main__":
    for r in build():
        print("  %-26s %-11s %-9s %-9s %s" % (r["chain"][:26], r["hemp"], r["inventory"], r["count_method"], r["states"]))
