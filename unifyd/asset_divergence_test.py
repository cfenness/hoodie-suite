"""Exercise the divergence detector.

The load-bearing property is a NEGATIVE one: this thing must not tell a brand team their retail
execution is broken on the strength of an unmeasured threshold. So most of these assert what it
refuses to claim. Pure stdlib — the clustering core takes plain lists of floats."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import asset_divergence as ad  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def vec(*xs):
    return list(xs)


# ── cosine ────────────────────────────────────────────────────────────────────────────────────────
print("cosine:")
check(abs(ad.cosine([1, 0], [1, 0]) - 1.0) < 1e-9, "identical vectors -> 1.0")
check(abs(ad.cosine([1, 0], [0, 1])) < 1e-9, "orthogonal vectors -> 0.0")
check(ad.cosine([1, 0], [0, 0]) is None,
      "a ZERO vector yields None, not 0.0 — an undefined similarity must not read as disagreement")
check(ad.cosine([], [1, 0]) is None, "an empty vector yields None")
check(ad.cosine([1, 0], [1, 0, 0]) is None, "mismatched lengths yield None")

# ── clustering ────────────────────────────────────────────────────────────────────────────────────
print("\nclustering:")
A1, A2, B1 = vec(1.0, 0.0, 0.0), vec(0.98, 0.20, 0.0), vec(0.0, 0.0, 1.0)
a = ad.cluster([("a1", A1), ("a2", A2), ("b1", B1)], threshold=0.76)
check(a["a1"] == a["a2"], "two near-identical looks cluster together")
check(a["b1"] != a["a1"], "a genuinely different look splits off")
check(len(set(a.values())) == 2, "two clusters in total")
check(ad.cluster([("a1", A1), ("a2", A2), ("b1", B1)], threshold=0.76) == a,
      "clustering is deterministic — the same input always gives the same partition")
check(len(set(ad.cluster([("x", A1), ("y", A2)], threshold=0.999).values())) == 2,
      "a stricter threshold splits what a generous one keeps together")

# ── the refusals ──────────────────────────────────────────────────────────────────────────────────
print("\nwhat it refuses to claim (no measured precision):")
IMGS = [{"source": s, "image": "i%d" % n, "vec": v, "first_seen": f, "last_seen": l}
        for n, (s, v, f, l) in enumerate([
            ("kroger", A1, "2024-01-01", "2026-08-01"),
            ("totalwine", A1, "2024-02-01", "2026-08-01"),
            ("binnys", A2, "2024-03-01", "2026-08-01"),
            ("abc", A1, "2024-01-15", "2026-08-01"),
            ("haskells", B1, "2019-05-01", "2022-03-01")])]
rows = ad.analyze_item("012345678905", IMGS, precision=None)
check(rows, "an item with enough sources produces rows")
check(all(r["stale_candidate"] is None for r in rows),
      "stale_candidate is None on EVERY row without measured precision")
check(all(r["withheld_reason"] for r in rows), "every row says WHY the verdict was withheld")
check(all(r["precision_measured"] is False for r in rows), "and records that precision is unmeasured")
check({r["verdict"] for r in rows} == {"divergent"}, "the divergence itself IS reported")

print("\n  ...but the deterministic evidence is all there:")
check(rows[0]["n_clusters"] == 2, "the number of distinct looks is counted (%s)" % rows[0]["n_clusters"])
check(rows[0]["n_sources_in_cluster"] == 4 and rows[0]["cluster_rank"] == 0,
      "the majority look is ranked first, by source BREADTH (%s sources)" % rows[0]["n_sources_in_cluster"])
check(rows[1]["n_sources_in_cluster"] == 1, "the minority look carries its own source count")
check(rows[1]["sources"] == "haskells", "and names which retailer shows it")
check(rows[1]["last_seen"] == "2022-03-01",
      "recency evidence travels with the cluster (%s)" % rows[1]["last_seen"])
check(abs(rows[0]["cluster_share"] - 0.8) < 1e-9, "cluster share is computed (%s)" % rows[0]["cluster_share"])
check(all(r["threshold"] == ad.DEFAULT_THRESHOLD and r["method"] for r in rows),
      "every row states the threshold and method that produced it")

# ── thin items ────────────────────────────────────────────────────────────────────────────────────
print("\nthin items are not 'aligned':")
thin = ad.analyze_item("0999", [{"source": "kroger", "image": "x", "vec": A1},
                                {"source": "abc", "image": "y", "vec": B1}], precision=None)
check(len(thin) == 1 and thin[0]["verdict"] == "insufficient_data",
      "two sources disagreeing is insufficient_data, NOT a divergence finding")
check(thin[0]["stale_candidate"] is None and thin[0]["withheld_reason"], "...with the reason stated")
check(ad.analyze_item("0", [], precision=None) == [], "no usable images yields no rows at all")
check(ad.analyze_item("0", [{"source": "a", "image": "i", "vec": None}], precision=None) == [],
      "an unembedded image is not a finding")

# ── with precision measured, the verdict turns on — carefully ─────────────────────────────────────
print("\nwith precision measured:")
P = {"precision": 0.9, "n": 50}
prows = ad.analyze_item("012345678905", IMGS, precision=P)
check(prows[0]["stale_candidate"] is False, "the majority look is never the stale candidate")
check(prows[1]["stale_candidate"] is True,
      "a minority look on a minority of chains becomes a stale CANDIDATE")
check(all(r["withheld_reason"] is None for r in prows), "nothing is withheld once precision exists")

# A near-even split is a real two-pack situation (regional SKU, transition period), not staleness.
EVEN = [{"source": s, "image": "e%d" % n, "vec": v} for n, (s, v) in enumerate([
    ("kroger", A1), ("totalwine", A1), ("abc", A1), ("binnys", B1), ("haskells", B1), ("meijer", B1)])]
erows = ad.analyze_item("0111", EVEN, precision=P)
check(erows[0]["n_clusters"] == 2, "an even split is still divergent")
check(all(r["stale_candidate"] is False for r in erows),
      "but NEITHER side is called stale — a 50/50 split is two live packs, not an error")

# ── backtest is the only thing that turns it on ───────────────────────────────────────────────────
print("\nbacktest:")
LAB = [{"upc": "1", "images": [{"image": "a", "vec": A1}, {"image": "b", "vec": A2}], "same_pack": True},
       {"upc": "2", "images": [{"image": "c", "vec": A1}, {"image": "d", "vec": B1}], "same_pack": False},
       {"upc": "3", "images": [{"image": "e", "vec": A1}, {"image": "f", "vec": B1}], "same_pack": False}]
bt = ad.backtest(LAB)
check(bt["precision"] == 1.0, "a clean labelled set measures precision 1.0 (%s)" % bt["precision"])
check(bt["true_split"] == 2 and bt["false_split"] == 0, "true/false splits are counted separately")
check(ad.backtest([])["precision"] is None, "an empty labelled set measures NOTHING, not 100%")
check(ad.load_precision() is None or isinstance(ad.load_precision(), dict),
      "precision is loaded from a file a human writes, never asserted in code")
check(not os.path.exists(ad.PRECISION_FILE),
      "no precision file is committed — the verdict ships OFF")

print("\nthe HASH tier (runs on pillow; img_vec needs torch and has never been populated):")
import img_hash
H_A, H_A2, H_B = "ffff0000ffff0000", "ffff0000ffff0002", "00ff00ff00ff00ff"
check(img_hash.hamming(H_A, H_A2) == 1, "a re-encode moves a bit or two (%s)" % img_hash.hamming(H_A, H_A2))
check(img_hash.hamming(H_A, H_B) > ad.HASH_MAX_BITS, "a different file is far apart (%s)" % img_hash.hamming(H_A, H_B))
check(img_hash.hamming(None, H_A) is None, "a MISSING hash is not distance 0")
check(img_hash.dhash(b"not an image") is None, "undecodable bytes yield no hash, never a fake one")

hc = ad.cluster_hashes([("a", H_A), ("b", H_A2), ("c", H_B)])
check(hc["a"] == hc["b"] and hc["c"] != hc["a"], "hash clustering groups the same file, splits a different one")
check(ad.cluster_hashes([("a", H_A), ("b", H_A2), ("c", H_B)]) == hc, "hash clustering is deterministic")

HIMG = [{"source": s, "image": "h%d" % n, "dhash": d} for n, (s, d) in enumerate([
    ("kroger", H_A), ("totalwine", H_A2), ("abc", H_A), ("binnys", H_B)])]
hrows = ad.analyze_item("0555", HIMG, precision=None)
check(hrows and hrows[0]["method"] == "dhash-hamming-greedy", "the hash tier is used when no vectors exist")
check(hrows[0]["threshold"] == ad.HASH_MAX_BITS, "and the row reports the BIT threshold, not a cosine")
check({r["verdict"] for r in hrows} == {"divergent_unconfirmed"},
      "a hash split is divergent_UNCONFIRMED — same pack, different photo also hashes apart")

# The asymmetry that matters: even WITH precision measured, a hash split never becomes a verdict.
phrows = ad.analyze_item("0555", HIMG, precision={"precision": 0.95, "n": 100})
check(all(r["stale_candidate"] is None for r in phrows),
      "a hash-tier split NEVER yields a stale verdict, even with precision measured")
check(all(r["withheld_reason"] and "hash tier" in r["withheld_reason"] for r in phrows),
      "...and says the hash tier is why")

print("\n  tier preference:")
MIXED = [{"source": "kroger", "image": "m1", "vec": A1, "dhash": H_A},
         {"source": "abc", "image": "m2", "vec": A1, "dhash": H_B},
         {"source": "binnys", "image": "m3", "vec": A2, "dhash": H_B}]
mrows = ad.analyze_item("0777", MIXED, precision=None)
check(mrows[0]["method"] == "clip-cosine-greedy", "CLIP wins when vectors are present")
check(mrows[0]["n_clusters"] == 1,
      "and it collapses images the hash tier would have split — the whole reason CLIP is the upgrade")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
