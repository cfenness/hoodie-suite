#!/usr/bin/env python3
"""hemp_finder.py — hemp/THC beverage retailers in EVERY state, via the VTInfo brand finder (vtinfo.py).

A hemp brand's VIP "where to buy" returns every account carrying it near a ZIP. Run a hemp brand across a
national ZIP grid (a metro per state) → all its retailers nationwide: which CHAINS carry hemp (Total Wine /
Kwik Trip / Cub / Coborn's / Target …) AND the independents, address-level, tagged with state + a chain guess.
Cann alone spans the hemp-legal states (it returned 200 accounts in one MN zip); add more brands to widen.
Lands `hemp_retailers`. This is the "all states I can find" engine.

    python hemp_finder.py --brands cann
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import vtinfo

# one major-metro ZIP per state + DC — the finder returns accounts only where the brand actually sells, so this
# lands exactly the states that have hemp retail ("all states I can find").
STATE_ZIPS = {
    "AL": "35203", "AK": "99501", "AZ": "85004", "AR": "72201", "CA": "90012", "CO": "80202", "CT": "06103",
    "DE": "19801", "DC": "20001", "FL": "33130", "GA": "30303", "HI": "96813", "ID": "83702", "IL": "60601",
    "IN": "46204", "IA": "50309", "KS": "66101", "KY": "40202", "LA": "70112", "ME": "04101", "MD": "21201",
    "MA": "02108", "MI": "48226", "MN": "55401", "MS": "39201", "MO": "63101", "MT": "59601", "NE": "68102",
    "NV": "89101", "NH": "03301", "NJ": "07102", "NM": "87501", "NY": "10001", "NC": "27601", "ND": "58501",
    "OH": "43215", "OK": "73102", "OR": "97204", "PA": "19102", "RI": "02903", "SC": "29201", "SD": "57501",
    "TN": "37201", "TX": "78701", "UT": "84101", "VT": "05602", "VA": "23219", "WA": "98104", "WV": "25301",
    "WI": "53703", "WY": "82001",
}
FLD = ["brand", "account", "chain", "street", "city", "state", "zip", "phone", "lat", "lng",
       "store_type", "source", "zip_searched"]
# known chains → normalize an account name to a chain (else 'independent')
_CHAINS = [("Total Wine", r"total wine"), ("Kwik Trip", r"kwik trip"), ("Cub", r"\bcub\b"),
           ("Coborn's", r"coborn"), ("Target", r"\btarget\b"), ("Casey's", r"casey"), ("Hy-Vee", r"hy-?vee"),
           ("Circle K", r"circle k"), ("Holiday", r"holiday stationstore|\bholiday\b"), ("Binny's", r"binny"),
           ("Spec's", r"spec'?s"), ("ABC Fine Wine", r"abc fine wine"), ("Meijer", r"meijer"),
           ("Lunds & Byerlys", r"lunds|byerly"), ("7-Eleven", r"7-?eleven|7 eleven"), ("BevMo", r"bevmo"),
           ("Walgreens", r"walgreen"), ("CVS", r"\bcvs\b"), ("Whole Foods", r"whole foods"), ("Kroger", r"kroger")]


def _chain(name):
    n = (name or "").lower()
    for label, rx in _CHAINS:
        if re.search(rx, n):
            return label
    return "independent"


def run(brands=("cann",), delay=1.0, land=True, log=print):
    zips = list(STATE_ZIPS.values())
    all_rows, by_state = {}, {}
    for brand in brands:
        ds, runs, mv = vtinfo.pull(brand=brand, zips=zips, delay=delay, out="/tmp/vt_hemp",
                                   state_dir="/tmp/vt_hemp", log=log)
        rows = next(iter(ds.values()), {}).get("_rows_full", []) if ds else []
        for r in rows:
            rec = dict(zip(vtinfo.HEADER, r))              # Brand,Account,Street,City,State,Zip,Phone,Miles,Lat,Lng,StoreType,Source,Zip_Searched
            key = (rec["Brand"], rec["Account"], rec["Street"])
            all_rows[key] = {"brand": rec["Brand"], "account": rec["Account"], "chain": _chain(rec["Account"]),
                             "street": rec["Street"], "city": rec["City"], "state": rec["State"],
                             "zip": rec["Zip"], "phone": rec["Phone"], "lat": rec.get("Lat", ""),
                             "lng": rec.get("Lng", ""), "store_type": rec.get("StoreType", ""),
                             "source": "vtinfo", "zip_searched": rec.get("Zip_Searched", "")}
            by_state[rec["State"]] = by_state.get(rec["State"], 0) + 1
    recs = list(all_rows.values())
    if land and recs:
        warehouse.write_accumulate("hemp_retailers", recs,
                                   key=lambda r: (r["brand"], r["account"], r["street"]), fields=FLD)
    from collections import Counter
    ch = Counter(r["chain"] for r in recs)
    log("[hemp_finder] %d distinct hemp retailers across %d states -> hemp_retailers" % (len(recs), len(by_state)))
    log("[hemp_finder] states: %s" % dict(sorted(by_state.items())))
    log("[hemp_finder] top chains: %s" % dict(ch.most_common(12)))
    return recs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hemp retailers in every state via the VTInfo brand finder.")
    ap.add_argument("--brands", default="cann", help="comma-separated brand keys in vtinfo.BRANDS")
    ap.add_argument("--delay", type=float, default=1.0)
    a = ap.parse_args(argv)
    run(brands=[b.strip() for b in a.brands.split(",") if b.strip()], delay=a.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
