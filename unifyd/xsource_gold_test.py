"""Exercise the gold-set builder.

A gold set that agrees with the matcher by construction measures nothing, so the properties asserted
here are mostly about keeping the human's judgement independent of the machine's. Pure stdlib."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xsource_gold as xg  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def r(rid, brand, name, size, source, upc=""):
    return {"resolved_id": rid, "brand": brand, "name": name, "size": size,
            "source": source, "upc": upc}


ROWS = [
    r("A", "Tito's", "Tito's Handmade Vodka 750ml", "750ml", "binnys"),
    r("B", "Titos", "Titos Handmade Vodka 750 ML", "750 ML", "total-wine"),
    r("C", "Absolut", "Absolut Citron 750ml", "750ml", "binnys"),
    r("D", "Absolut", "Absolut Citron Vodka 750 ml", "750 ml", "abc"),
    r("E", "Absolut", "Absolut Mandrin 750ml", "750ml", "binnys"),
    r("F", "Absolut", "Absolut Citron 1750ml", "1750ml", "abc"),
    r("G", "Jameson", "Jameson Irish Whiskey 750ml", "750ml", "binnys"),
    r("H", "Jameson", "Jameson Black Barrel 750ml", "750ml", "abc"),
    r("I", "Jameson", "Jameson Irish Whiskey 1750ml", "1750ml", "haskells"),
]

print("candidates:")
c = xg.candidates(ROWS, n=30, seed=7, log=lambda *a: None)
check(c, "candidates are produced (%d)" % len(c))
strata = {p["stratum"] for p in c}
check("merged" in strata, "a MERGED stratum exists (measures precision)")
check("near_miss" in strata, "a NEAR_MISS stratum exists (measures recall — what the rule declined)")
check("control" in strata, "a CONTROL stratum exists (audits the labeller)")

print("\n  the label column must ship EMPTY:")
check(all(p["label"] == "" for p in c),
      "no pair arrives pre-labelled — a pre-filled answer produces rubber-stamping")
check(all(p["suggested"] == "" for p in c),
      "and with the LLM off there is no suggestion either, rather than a fabricated one")
check("label" in xg.SHEET_COLS and xg.SHEET_COLS[-1] == "label",
      "the answer column is LAST in the sheet, after the evidence")
check(xg.SHEET_COLS.index("suggested") < xg.SHEET_COLS.index("label"),
      "the machine's opinion sits in its OWN column, never the answer column")

print("\n  controls have a known answer:")
ctrl = [p for p in c if p["stratum"] == "control"]
check(ctrl, "controls were sampled (%d)" % len(ctrl))
check(all(str(p["a_size"]) != str(p["b_size"]) for p in ctrl),
      "every control pair is DIFFERENT-size, so the answer is always 'n'")
# The audit only works if the control is SUBTLE. Same brand + different product is obviously "n"
# and a labeller gets it right without reading — it tests nothing.
import xsource_match as _xm
for p in ctrl:
    sa = _xm.signature(p["a_brand"], p["a_name"], p["a_size"])
    sb = _xm.signature(p["b_brand"], p["b_name"], p["b_size"])
    check(sa and sb and sa[0] == sb[0] and sa[1] == sb[1] and sa[2] != sb[2],
          "control is the SAME PRODUCT at a different size (the subtle case), not two obviously "
          "different products")

print("\n  reproducibility:")
c2 = xg.candidates(ROWS, n=30, seed=7, log=lambda *a: None)
check([p["pair_id"] for p in c] == [p["pair_id"] for p in c2],
      "the same seed produces the same sheet — a half-finished sheet is never invalidated")
c3 = xg.candidates(ROWS, n=30, seed=99, log=lambda *a: None)
check([p["pair_id"] for p in c] != [p["pair_id"] for p in c3] or len(c) < 3,
      "a different seed produces a different sample")

print("\nround-trip:")
tmp = os.path.join(tempfile.mkdtemp(), "gold.csv")
xg.export(c, tmp, log=lambda *a: None)
check(os.path.exists(tmp), "the sheet writes")
lab = xg.read_labels(tmp)
check(len(lab) == len(c), "every pair reads back (%d)" % len(lab))
check(all(v is None for v in lab.values()), "an unfilled sheet reads back as UNLABELLED, not as 'no'")

# fill it in, including one deliberately wrong control
import csv as _csv
with open(tmp, encoding="utf-8") as f:
    rows = list(_csv.DictReader(f))
for i, row in enumerate(rows):
    row["label"] = "y" if row["stratum"] == "merged" else ("y" if row["stratum"] == "control" and i == len(rows) - 1 else "n")
with open(tmp, "w", newline="", encoding="utf-8") as f:
    w = _csv.DictWriter(f, fieldnames=xg.SHEET_COLS)
    w.writeheader()
    for row in rows:
        w.writerow({k: row.get(k, "") for k in xg.SHEET_COLS})

pairs, rep = xg.ingest(tmp, pairs=[dict(p) for p in c], land=False, log=lambda *a: None)
check(rep["labelled"] > 0, "labels attach (%d)" % rep["labelled"])
check(rep["controls_wrong"] >= 1,
      "a control answered wrongly is DETECTED (%d) — the sheet audits its own labeller"
      % rep["controls_wrong"])
check(rep["control_accuracy"] is not None and rep["control_accuracy"] < 1.0,
      "and control accuracy is reported (%s), not folded into the score" % rep["control_accuracy"])

print("\nscoring is PER STRATUM:")
sc = xg.score(pairs, log=lambda *a: None)
check(isinstance(sc, dict) and "merged" in sc, "the merged stratum is scored on its own")
check("ALL" in sc, "an overall figure exists alongside, never instead of, the strata")
check(all(("precision" in v and "recall" in v) for k, v in sc.items()),
      "each stratum reports precision AND recall")
check(xg.score([], log=lambda *a: None)["status"] == "no_labels",
      "an unlabelled set scores NOTHING rather than 100%")

print("\nvalue parsing:")
check(xg.VALID.get("y") is True and xg.VALID.get("n") is False, "y/n parse")
check(xg.VALID.get("?") is None and xg.VALID.get("") is None,
      "'?' and blank are UNKNOWN — never coerced into a judgement")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
