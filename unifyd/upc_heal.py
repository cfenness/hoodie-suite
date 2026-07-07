"""upc_heal.py — run the UPC engine over a dataset's UPC column and report health.

Auto-driven when a source field is mapped to master.upc: the base data is never touched, but every
distinct UPC is classified (upc.classify: empty/malformed/placeholder/bad_check/restricted/coupon/
valid) and, when an applicant/owner column is also mapped, checked against the GS1-prefix→owner
crosswalk ([[upc-resolution-engine]]) so a borrowed/mismatched UPC surfaces. Returns a per-dataset
report the master can gate on. Reads distinct values via DuckDB (distinct ≪ rows), classifies in
Python — fast even for 1M-row sources.
"""
import os, sys, time
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upc


def heal(dataset, upc_col, applicant_col=None, cap=500000, log=print):
    import warehouse
    sel = 'CAST("%s" AS VARCHAR) u' % upc_col.replace('"', '')
    grp = "1"
    if applicant_col:
        sel += ', CAST("%s" AS VARCHAR) a' % applicant_col.replace('"', '')
        grp = "1,2"
    q = ("SELECT %s, count(*) n FROM t WHERE CAST(\"%s\" AS VARCHAR)<>'' GROUP BY %s LIMIT %d"
         % (sel, upc_col.replace('"', ''), grp, int(cap)))
    rows = warehouse.query(dataset, q)
    pairs = [(r["u"], r.get("a")) for r in rows]
    xwalk = upc.build_crosswalk(pairs) if applicant_col else None
    status, total, mismatch, healable = Counter(), 0, 0, 0
    for r in rows:
        st = upc.classify(r["u"]); status[st] += r["n"]; total += r["n"]
        if st != "valid":
            healable += r["n"]                         # placeholder/bad_check/etc. → needs healing/QC
        elif xwalk is not None:
            a = upc.assess(r["u"], r.get("a"), xwalk)
            if a.get("owner_agrees") is False:
                mismatch += r["n"]
    report = {"dataset": dataset, "upc_col": upc_col, "applicant_col": applicant_col,
              "rows_with_upc": total, "distinct": len(rows), "by_status": dict(status),
              "valid": status.get("valid", 0), "valid_pct": round(100 * status.get("valid", 0) / max(1, total), 1),
              "needs_qc": healable, "owner_mismatch": mismatch,
              "crosswalk_prefixes": (len(xwalk) if xwalk else 0), "at": int(time.time())}
    log("upc_heal %s.%s: %d rows, %d distinct, %.1f%% valid, %d need QC, %d owner-mismatch"
        % (dataset, upc_col, total, len(rows), report["valid_pct"], healable, mismatch))
    return report


if __name__ == "__main__":
    import sys as _s
    ds = _s.argv[1] if len(_s.argv) > 1 else "bc_liquor"
    col = _s.argv[2] if len(_s.argv) > 2 else "PRODUCT_BASE_UPC_NO"
    print(heal(ds, col))
