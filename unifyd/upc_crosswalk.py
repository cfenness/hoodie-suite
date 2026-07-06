"""upc_crosswalk.py — build the OWNED UPC prefix -> brand-owner crosswalk from enriched COLA
data, and (optionally) score every row for owner-agreement + confidence.

The moat: COLA gives a UPC and its applicant together, so real UPCs teach us which GS1 company
prefix belongs to which brand owner — a bev-alc UPC↔owner registry built from ground-truth
filings (generic UPC DBs don't know who filed the label). Once built, any UPC can be checked:
does its company prefix agree with the applicant who filed it? Mismatches -> review queue.

Run after ttb_enrich.py has produced the enriched CSV:
    python unifyd/upc_crosswalk.py --enriched ~/hoodie-cola/cola_enriched.csv \
        --out-crosswalk ~/hoodie-cola/upc_crosswalk.json \
        --score-out ~/hoodie-cola/cola_scored.csv
"""
import argparse, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upc


def owner_of(row, col):
    # detail "Contact Information" = applicant name + address; take the leading words as the name.
    v = row.get(col) or row.get("Contact Information") or row.get("Applicant") or ""
    return " ".join(str(v).split()[:6])


def main():
    ap = argparse.ArgumentParser(description="Build the UPC prefix->owner crosswalk from enriched COLA.")
    ap.add_argument("--enriched", required=True, help="cola_enriched.csv from ttb_enrich.py")
    ap.add_argument("--owner-col", default="Contact Information")
    ap.add_argument("--out-crosswalk", default="")
    ap.add_argument("--score-out", default="")
    a = ap.parse_args()

    with open(a.enriched, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    def upc_of(r):
        return r.get("UPC") or r.get("upc_raw") or ""
    pairs = [(upc_of(r), owner_of(r, a.owner_col)) for r in rows]
    xw = upc.build_crosswalk(pairs)
    real = sum(1 for u, _ in pairs if upc.is_real(u))
    print("rows: %d | real UPCs: %d | crosswalk prefixes: %d" % (len(rows), real, len(xw)))

    if a.out_crosswalk:
        json.dump(xw, open(a.out_crosswalk, "w"), indent=0)
        print("wrote crosswalk ->", a.out_crosswalk)

    if a.score_out and rows:
        cols = list(rows[0].keys()) + ["prefix_owner", "owner_agrees", "upc_confidence"]
        f = open(a.score_out, "w", newline="", encoding="utf-8")
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        agree = disagree = 0
        for r in rows:
            res = upc.assess(upc_of(r), applicant=owner_of(r, a.owner_col), crosswalk=xw)
            r["prefix_owner"] = res["prefix_owner"] or ""
            r["owner_agrees"] = "" if res["owner_agrees"] is None else str(res["owner_agrees"])
            r["upc_confidence"] = "%.2f" % res["confidence"]
            agree += res["owner_agrees"] is True
            disagree += res["owner_agrees"] is False
            w.writerow(r)
        f.close()
        print("scored -> %s | owner agree=%d, MISMATCH=%d (flag for review)" % (a.score_out, agree, disagree))


if __name__ == "__main__":
    main()
