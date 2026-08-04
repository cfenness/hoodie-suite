#!/usr/bin/env python3
"""xsource_match.py — merge the master's cross-source over-splits, as an OVERLAY.

THE MEASUREMENT THIS EXISTS FOR
  Measured live 2026-08-04 across binnys / abc / total-wine / haskells / specs, restricted to
  fifteen brands that unquestionably sit on every one of those shelves:

      signatures spanning >=2 sources        77
      split across >1 master identity        56   (73%)

  "Tito's Handmade Vodka 750ml" has two identities. So does Bacardi Gold 750ml, and Hennessy VS at
  375ml, 1000ml and 1750ml separately. The consequence shows up everywhere downstream — 98.5% of
  items carrying a product image are seen by exactly ONE source, which reads as "no retailer overlap"
  when it is really "we did not merge them" ([[master-fanout-brand-resolution]]).

WHY AN OVERLAY AND NOT A FIX TO THE MASTER
  Landed data is never rewritten ([[normalization-scout]]); corrections are a translation layer. And
  the master is append-only and versioned ([[append-only-versioned-master]]). So this lands
  `xsource_identity` — a mapping from the master's `resolved_id` to a merged `xsource_id` — which a
  consumer COALESCEs on, exactly the way `canon_identity` overlays `item_key` today. Nothing is
  deleted, nothing is renumbered, and turning it off is a one-line change at the read site.

THE MERGE RULE, AND WHY IT IS THIS CONSERVATIVE
  Two identities merge only when they agree on every SHELF DISCRIMINATOR
  ([[discriminator-identity-model]]): brand key, product-name signature, and size. All three must be
  PRESENT — a missing size is not a wildcard, it is a refusal, because "Absolut Citron" without a
  size is not an item, it is a product, and merging across sizes would destroy the item grain the
  master is counted at ([[master-item-grain]]).

  And a UPC CONFLICT always wins. If two rows carry different explicit UPCs they are different
  items, whatever their names look like — the names are what is unreliable here, not the barcode.
  This is the guard that stops "Bogle Merlot" and "Bogle Cab" style look-alikes from collapsing.

  There is no fuzzy tier. Every merge is an exact match on a normalized signature, so a merge can
  always be explained by showing the two signatures.

MEASURED RESULT — THIS RULE DOES NOT CURRENTLY CLEAR ITS OWN BAR
  Run against the real master (binnys / abc / total-wine / haskells / specs, 67,099 distinct rows)
  on 2026-08-04:

      merges proposed   1,003 identities into 514 groups
      scored pairs         60      (the rest unscoreable — see below)
      PRECISION         0.233      true 14 / false 46
      recall            0.160
      -> refused to land (bar 0.98)

  So a brand + name-signature + size match is NOT sufficient on retail product names, and the honest
  status of this module is: it proposes merges, it measures itself, and it declines to ship them.
  It is registered DISABLED and lands nothing.

  Two things the measurement also exposed, both worth fixing before another attempt:
    • 59,455 of 67,099 rows are UNSCOREABLE. binnys / abc / total-wine carry no UPC at all — the
      very sources that need merging — so gold has to come from the master's own upc/gtin (it does
      now) and even then covers a thin slice. A human-labelled set is probably required.
    • a `resolved_id` can legitimately span several UPCs, so "two ids whose UPC sets do not
      intersect" is a harsher test than "these are different items". Some of the 46 false pairs are
      likely gold artefacts rather than real errors — which is itself a reason not to trust the
      0.233 as the final word, in either direction.

PRECISION IS MEASURED, NOT ASSUMED
  `score()` builds gold from the data itself, the same way `master_quality` does: two rows sharing a
  UPC SHOULD merge (recall), two rows with different UPCs must NOT (precision). The overlay ships
  with its measured score attached, and `build()` refuses to land a merge set whose precision falls
  below `MIN_PRECISION` — a matching layer that silently degrades identity is worse than none.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TABLE = "xsource_identity"
FIELDS = ["resolved_id", "xsource_id", "signature", "brand_key", "name_sig", "size_ml",
          "n_merged", "sources", "method", "precision", "built_at"]

# A merge set below this precision is not landed. 0.98 because an identity error is silent and
# permanent-feeling: it mis-attributes every downstream fact about the product, and nothing in the
# row looks wrong afterwards.
MIN_PRECISION = 0.98

_NOISE = {"the", "and", "of", "wine", "wines", "spirit", "spirits", "liqueur", "bottle", "btl",
          "nr", "pack", "case", "single", "each", "ea", "proof", "old", "yr", "year", "ml", "l",
          "liter", "litre", "bottled", "distilled"}

# CATEGORY NOUNS. TTB states the class in a FIELD; retail bakes it into the NAME
# ([[ttb-retail-class-bridge]]), and sources disagree about whether to — measured live, one source
# lists "Absolut Citron 750ml" and another "Absolut Citron Vodka 750 ml", which produced two
# signatures for one item. Dropping the category noun is safe here because the signature is already
# scoped by brand AND size, so the class carries no discriminating power that brand+variant does not
# already carry. `class_type.py` is the fuller treatment of this alignment; this is the narrow
# version the merge needs.
_CATEGORY = {"vodka", "rum", "gin", "whiskey", "whisky", "tequila", "bourbon", "scotch", "cognac",
             "brandy", "vermouth", "mezcal", "sake", "cordial", "schnapps", "aperitif"}
_NOISE |= _CATEGORY
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|liter|litre|oz)\b", re.I)


def _nbrand(s):
    """The SHARED brand normalization. Imported, never reimplemented — `precleanse.nbrand` drops
    generic tokens, and a local lookalike would silently produce a different match set than the rest
    of the platform (the mistake `dam_canon` documents)."""
    import precleanse
    return precleanse.nbrand(s or "")


def brand_key(brand):
    """`precleanse.nbrand` plus per-token singularization — the same key `overlay_match.brand_key`
    computes, so a signature built here compares equal to one built by the overlay."""
    toks = _nbrand(brand).split()
    return " ".join(t[:-1] if (len(t) > 3 and t.endswith("s")) else t for t in toks)


def size_ml(*texts):
    """Size in millilitres from any of the given strings, or None. None is a REFUSAL to merge, not a
    wildcard — see the module docstring."""
    for t in texts:
        m = _SIZE_RE.search(str(t or ""))
        if not m:
            continue
        v, unit = float(m.group(1)), m.group(2).lower()
        if unit.startswith("l") and v < 10:
            v *= 1000
        elif unit == "oz":
            v *= 29.5735
        v = round(v)
        if 10 <= v <= 20000:
            return v
    return None


def name_sig(name, bkey=""):
    """Order-independent token signature of the product name, with the brand tokens removed —
    whether a source repeats the brand inside the product name is a per-source convention, not a
    product difference."""
    import precleanse
    s = precleanse.deaccent(str(name or "")).lower()
    # Strip size expressions BEFORE tokenizing. "750ml" tokenizes as one token the size pattern
    # catches, but "750 ML" tokenizes as "750" + "ml" — the unit is noise-dropped and the bare
    # number SURVIVES, so the same product written two ways produced two signatures and never
    # merged. That was the live Tito's failure this whole module exists for.
    s = _SIZE_RE.sub(" ", s)
    btoks = set(bkey.split())
    toks = set()
    for w in re.split(r"[^a-z0-9]+", s):
        if not w or len(w) < 2 or w in _NOISE or _SIZE_RE.match(w):
            continue
        if w in btoks or (len(w) > 3 and w.endswith("s") and w[:-1] in btoks):
            continue
        toks.add(w)
    return " ".join(sorted(toks))


def signature(brand, name, size_text=""):
    """(brand_key, name_sig, size_ml) or None when any component is missing. PURE."""
    bk = brand_key(brand or name)
    if not bk:
        return None
    sz = size_ml(size_text, name)
    if not sz:
        return None
    ns = name_sig(name, bk)
    return (bk, ns, sz)


def merge(rows):
    """rows = [{resolved_id, brand, name, size, upc, source}] → {resolved_id: xsource_id}.

    Groups by exact signature, then SPLITS any group containing a UPC conflict — two different
    explicit UPCs inside one signature means the signature is not discriminating enough for that
    product, so the group is abandoned rather than merged on a rule we know is wrong there."""
    groups = {}
    for r in rows:
        sig = signature(r.get("brand"), r.get("name"), r.get("size"))
        if not sig:
            continue
        groups.setdefault(sig, []).append(r)

    out = {}
    for sig, members in groups.items():
        upcs = {(_norm_upc(m.get("upc"))) for m in members if _norm_upc(m.get("upc"))}
        if len(upcs) > 1:
            continue                      # UPC conflict — the barcode outranks the name, always
        ids = sorted({m["resolved_id"] for m in members if m.get("resolved_id")})
        if len(ids) < 2:
            continue                      # nothing to merge
        xid = ids[0]                      # deterministic: the lowest id becomes the merged identity
        for i in ids:
            out[i] = xid
    return out


def _norm_upc(u):
    d = re.sub(r"\D", "", str(u or ""))
    return d.lstrip("0") if len(d) >= 8 else ""


def score(rows):
    """Measure the merge rule against gold built from the data itself.

    PRECISION — of the pairs this rule merges, how many share a UPC (or have no UPC conflict)?
    RECALL    — of the pairs that share a UPC, how many does it merge?
    Only rows carrying a UPC can be scored; the rest are reported as unscoreable rather than
    silently counted as correct."""
    scoreable = [r for r in rows if _norm_upc(r.get("upc")) and r.get("resolved_id")]
    by_id_upc = {}
    for r in scoreable:
        by_id_upc.setdefault(r["resolved_id"], set()).add(_norm_upc(r["upc"]))

    m = merge(rows)
    merged_pairs, tp, fp = 0, 0, 0
    clusters = {}
    for src_id, dst in m.items():
        clusters.setdefault(dst, set()).add(src_id)
    for dst, members in clusters.items():
        ms = sorted(members | {dst})
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                a, b = by_id_upc.get(ms[i]), by_id_upc.get(ms[j])
                if not a or not b:
                    continue
                merged_pairs += 1
                if a & b:
                    tp += 1
                else:
                    fp += 1
    # Recall: UPC-identical identities that SHOULD have merged.
    upc_to_ids = {}
    for rid, upcs in by_id_upc.items():
        for u in upcs:
            upc_to_ids.setdefault(u, set()).add(rid)
    should, did = 0, 0
    for u, ids in upc_to_ids.items():
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                should += 1
                if m.get(ids[i], ids[i]) == m.get(ids[j], ids[j]):
                    did += 1
    return {"merges": len(m), "scored_pairs": merged_pairs,
            "precision": round(tp / merged_pairs, 4) if merged_pairs else None,
            "recall": round(did / should, 4) if should else None,
            "true_pairs": tp, "false_pairs": fp, "should_merge": should,
            "unscoreable_rows": len(rows) - len(scoreable),
            "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def build(land=True, min_precision=MIN_PRECISION, log=print):
    """Read the master, measure, and land the overlay only if precision clears the bar."""
    import warehouse
    try:
        rows = warehouse.query(
            "dim_sku",
            "SELECT COALESCE(resolved_id, item_key) resolved_id, upc, gtin FROM t "
            "WHERE COALESCE(resolved_id, item_key) IS NOT NULL")
        prod = warehouse.query(
            "_stage_product",
            "SELECT item_key, brand, product_name AS name, size_raw AS size, source FROM t")
    except Exception as e:
        log("xsource_match: master unreadable (%s)" % str(e)[:110])
        print('HOODIE_RESULT {"status": "degraded", "items_done": 0, "items_total": 0}')
        return [], {"status": "degraded", "reason": "master unavailable"}

    upc_by = {r["resolved_id"]: (r.get("upc") or r.get("gtin")) for r in rows}
    recs = [dict(p, resolved_id=p.get("item_key"), upc=upc_by.get(p.get("item_key"))) for p in prod]

    sc = score(recs)
    log("xsource_match: precision=%s recall=%s over %s scored pairs (%s merges)"
        % (sc["precision"], sc["recall"], sc["scored_pairs"], sc["merges"]))
    if sc["precision"] is not None and sc["precision"] < min_precision:
        log("xsource_match: precision %.3f below the %.2f bar — NOT LANDING. A matching layer that "
            "silently degrades identity is worse than none." % (sc["precision"], min_precision))
        print('HOODIE_RESULT {"status": "degraded", "items_done": 0, "items_total": 0}')
        return [], dict(sc, status="degraded", reason="precision below bar")

    m = merge(recs)
    by_id = {r["resolved_id"]: r for r in recs if r.get("resolved_id")}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    counts, srcs = {}, {}
    for a, b in m.items():
        counts[b] = counts.get(b, 0) + 1
        srcs.setdefault(b, set()).add((by_id.get(a) or {}).get("source"))
    out = []
    for rid, xid in sorted(m.items()):
        r = by_id.get(rid) or {}
        sig = signature(r.get("brand"), r.get("name"), r.get("size")) or ("", "", None)
        out.append({"resolved_id": rid, "xsource_id": xid, "signature": "|".join(str(x) for x in sig),
                    "brand_key": sig[0], "name_sig": sig[1], "size_ml": sig[2],
                    "n_merged": counts.get(xid, 0) + 1,
                    "sources": ",".join(sorted(s for s in srcs.get(xid, set()) if s)),
                    "method": "signature-exact", "precision": sc["precision"], "built_at": now})

    if land and out:
        try:
            warehouse.write_accumulate(TABLE, out, key="resolved_id", fields=FIELDS, coverage=False)
            log("landed %s: %d merged identities" % (TABLE, len(out)))
        except Exception as e:
            log("%s land skipped: %s" % (TABLE, str(e)[:100]))
    print("HOODIE_RESULT " + json.dumps(dict({"status": "success", "items_done": len(out),
                                              "items_total": len(out)}, **sc)))
    return out, sc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Merge the master's cross-source over-splits (overlay).")
    ap.add_argument("--no-land", action="store_true")
    ap.add_argument("--min-precision", type=float, default=MIN_PRECISION)
    a = ap.parse_args(argv)
    out, sc = build(land=not a.no_land, min_precision=a.min_precision)
    print(json.dumps(sc, indent=2))


if __name__ == "__main__":
    main()
