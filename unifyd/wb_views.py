#!/usr/bin/env python3
"""wb_views.py — pre-compute the Matching workbench's list views as SMALL, denormalized parquet tables at
master-REBUILD time, so the workbench cold-loads by reading a tiny file instead of scanning dim_sku (~874k)
and rebuilding the full name maps (dim_product ~830k + dim_item ~871k) on the first request.

The join (sku -> item -> product -> brand) runs ONCE here, capped, with names already resolved — so the
server endpoints read wb_master / wb_queue / wb_summary directly, no joins or maps at request time.

  wb_master  — corroborated SKUs (sources>=2), the trustworthy master list
  wb_queue   — single-source SKUs (sources=1), the analyst review queue, most-seen first
  wb_summary — rebuild-stable funnel numbers (total / multi / source_rows / by_source)

Called at the tail of build_product_master.build(); the server falls back to the live (cached) computation
when a view table isn't present yet. Standalone: `python wb_views.py`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

CAP = int(os.environ.get("WB_VIEW_CAP", "4000"))       # list views are paginated/scannable in the UI; cap generously
FIELDS = ["sku_key", "item_key", "product_key", "brand", "product_name", "size_ml", "container",
          "pack", "upc", "sources", "source_list", "source_rows",
          # detail-modal fields, pre-joined so the item-detail modal serves from the warmed view (no dim scans)
          "gtin", "image", "varietal", "region", "sub_region", "appellation", "origin", "bottled_in",
          "abv", "category", "style"]
_JOIN = ("SELECT s.sku_key, s.item_key, i.product_key, b.brand, p.product_name, i.size_ml, i.container, "
         "s.pack, s.upc, s.sources, s.source_list, s.source_rows, "
         "s.gtin, p.image, p.varietal, p.region, p.sub_region, p.appellation, p.origin, p.bottled_in, "
         "p.abv, p.category, p.style "
         "FROM dim_sku s LEFT JOIN dim_item i ON s.item_key = i.item_key "
         "LEFT JOIN dim_product p ON i.product_key = p.product_key "
         "LEFT JOIN dim_brand b ON p.brand_key = b.brand_key ")


def _rows(con, sql):
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _size_label(v):
    if not v:
        return ""
    try:
        v = float(v)
    except Exception:
        return ""
    return ("%gL" % (v / 1000.0)) if v >= 1000 else ("%gml" % v)


def build_merges(con, log=print):
    """Ambiguous MERGE groups for the cluster-review page: SKUs that are the SAME product + size + pack but
    split across DIFFERENT/missing UPCs — i.e. very likely one sellable unit fractured by UPC noise. Grouping
    by (item_key, pack) isolates exactly that (same item = same product+size; same pack = same unit; the only
    thing left differing is the UPC). Distinct-UPC count drives confidence + whether it's a clear merge or a
    genuine judgement call. Prioritized by total source rows (impact). -> wb_merges."""
    # denormalize the members AND the shared product attributes here so the review modal reads only this cached
    # row (no per-open dim_sku/item/product/vintage scans). Members share one product, so geo/abv/etc. are group-
    # level; only upc/gtin/sources differ per member (+ the per-UPC image, looked up live).
    MERGE = ("SELECT s.item_key, s.pack, b.brand, p.product_name, i.size_ml, i.container, "
             "count(*) n_skus, sum(s.source_rows) total_rows, list(s.sku_key) sku_keys, list(s.upc) upcs, "
             "list(s.gtin) gtins, list(s.sources) src_counts, list(s.source_list) src_lists, "
             "list(s.source_rows) row_counts, any_value(p.abv) abv, any_value(p.varietal) varietal, "
             "any_value(p.origin) origin, any_value(p.bottled_in) bottled_in, any_value(p.region) region, "
             "any_value(p.sub_region) sub_region, any_value(p.appellation) appellation, any_value(p.flavor) flavor, "
             "any_value(p.style) style, any_value(p.category) category, any_value(p.image) image "
             "FROM dim_sku s LEFT JOIN dim_item i ON s.item_key = i.item_key "
             "LEFT JOIN dim_product p ON i.product_key = p.product_key "
             "LEFT JOIN dim_brand b ON p.brand_key = b.brand_key "
             "GROUP BY s.item_key, s.pack, b.brand, p.product_name, i.size_ml, i.container "
             "HAVING count(*) > 1 ORDER BY total_rows DESC LIMIT %d" % CAP)
    out = []
    for g in _rows(con, MERGE):
        skus, upcs, gtins = g["sku_keys"], g["upcs"], (g.get("gtins") or [])
        srcc, srcl, rowc = g["src_counts"], g["src_lists"], g["row_counts"]
        members = []
        for k in range(len(skus)):
            members.append({"sku_key": skus[k], "upc": (upcs[k] or ""),
                            "gtin": ((gtins[k] if k < len(gtins) else "") or ""), "sources": srcc[k],
                            "source_list": list(srcl[k]) if srcl[k] is not None else [],
                            "source_rows": rowc[k]})
        distinct_upc = len({m["upc"] for m in members if m["upc"]})
        conf = 0.92 if distinct_upc <= 1 else (0.6 if distinct_upc == 2 else 0.42)
        reason = ("Same product / size / pack; UPC missing on some — almost certainly one SKU."
                  if distinct_upc <= 1 else
                  "Same product / size / pack but %d different UPCs — a real merge call." % distinct_upc)
        pk = ("×%s" % g["pack"]) if (g["pack"] and str(g["pack"]) not in ("1", "1.0", "")) else ""
        name = " · ".join(x for x in [(g["product_name"] or "").strip(), _size_label(g["size_ml"]),
                                      (g["container"] or ""), pk] if x) or "(unnamed)"
        attrs = {"size": _size_label(g["size_ml"]), "container": g.get("container") or "",
                 "pack": str(g["pack"] or ""), "abv": g.get("abv") or "", "varietal": g.get("varietal") or "",
                 "origin": g.get("origin") or "", "bottled_in": g.get("bottled_in") or "",
                 "region": g.get("region") or "", "sub_region": g.get("sub_region") or "",
                 "appellation": g.get("appellation") or "", "flavor": g.get("flavor") or "",
                 "style": g.get("style") or "", "category": g.get("category") or "", "image": g.get("image") or ""}
        out.append({"merge_id": "%s|%s" % (g["item_key"], g["pack"] or ""), "item_key": g["item_key"],
                    "pack": str(g["pack"] or ""), "brand": g["brand"] or "", "name": name,
                    "size_ml": g["size_ml"], "n_skus": g["n_skus"], "total_rows": g["total_rows"],
                    "distinct_upc": distinct_upc, "confidence": conf, "reason": reason,
                    "members": json.dumps(members), "attrs": json.dumps(attrs)})
    warehouse.write_parquet("wb_merges", out,
                            fields=["merge_id", "item_key", "pack", "brand", "name", "size_ml", "n_skus",
                                    "total_rows", "distinct_upc", "confidence", "reason", "members", "attrs"])
    log("[wb_views] wb_merges=%d ambiguous merge groups" % len(out))
    return len(out)


def _grain_merges(grain, rows, name_of, block_of, thresh=0.82):
    """Generic mnemonic-blocked dedup for one grain: block, then keep in-block members matching the anchor
    (SequenceMatcher >= thresh). Returns candidate groups. `rows` carry key/name/source_rows/sources/hoodie_id."""
    import collections
    import difflib
    import re

    def _n(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())
    blocks = collections.defaultdict(list)
    for r in rows:
        b = block_of(r)
        if b:
            blocks[b].append(r)
    out = []
    for code, mem in blocks.items():
        if len(mem) < 2:
            continue
        mem.sort(key=lambda r: -(r["source_rows"] or 0))                 # anchor = most-corroborated spelling
        anchor = mem[0]
        an = _n(name_of(anchor))
        grp, sims = [anchor], []
        for m in mem[1:]:
            s = difflib.SequenceMatcher(None, an, _n(name_of(m))).ratio()
            if s >= thresh:
                grp.append(m); sims.append(s)
        if len(grp) < 2:
            continue
        members = [{"key": g["key"], "name": name_of(g), "hoodie_id": g.get("hoodie_id") or "",
                    "source_rows": g["source_rows"] or 0, "sources": g["sources"] or 0} for g in grp]
        out.append({"grain": grain, "merge_id": "%s|%s" % (grain, code), "block": code,
                    "canonical": name_of(anchor), "n": len(grp),
                    "total_rows": sum(m["source_rows"] for m in members), "confidence": round(min(sims), 2),
                    "reason": "%d %s spellings share mnemonic %s and match the canonical" % (len(grp), grain, code),
                    "members": json.dumps(members)})
    out.sort(key=lambda x: -x["total_rows"])
    return out[:CAP]


def build_brand_merges(con, log=print):
    """Dedup candidates at EVERY grain — brand, product, item, supplier — via the Hoodie ID mnemonic as a
    BLOCKING key. Corroboration lives above the SKU: two sources often agree on the brand/product/item without
    an exact SKU match, and near-duplicate names (New Amsterdam / New Amsteram) never sku-match at all. Blocking
    by the nested ID prefix (brand [:6], product [:9], item [:13]) + a similarity filter surfaces those merges
    at each level. -> wb_matches (grain-tagged, impact-ranked). (Name kept for the build() caller.)"""
    import hoodie_ids
    q = warehouse.query
    out = []
    # BRAND
    out += _grain_merges("brand",
                         [{"key": r["brand_key"], "brand": r["brand"], **r} for r in
                          q("dim_brand", "SELECT brand_key, brand, source_rows, sources, hoodie_id FROM t "
                                         "WHERE brand IS NOT NULL AND brand <> ''")],
                         lambda r: r["brand"], lambda r: hoodie_ids.brand_code(r["brand"]))
    # PRODUCT — block by the RAW brand+product mnemonic (recomputed; the stamped id is disambiguated-unique so
    # it never collides). Near-name products of the same brand block together, then similarity-filtered.
    dp = warehouse.uri("dim_product"); db = warehouse.uri("dim_brand"); di = warehouse.uri("dim_item")
    out += _grain_merges("product",
                         [{"key": r["product_key"], **r} for r in
                          q("dim_product", "SELECT p.product_key, p.product_name, p.source_rows, p.sources, "
                            "p.hoodie_id, b.brand FROM read_parquet('%s') p LEFT JOIN read_parquet('%s') b "
                            "ON p.brand_key=b.brand_key WHERE p.product_name IS NOT NULL AND p.product_name <> ''"
                            % (dp, db))],
                         lambda r: r["product_name"],
                         lambda r: hoodie_ids.brand_code(r.get("brand") or "") + hoodie_ids.product_code(r["product_name"], r.get("brand") or ""))
    # ITEM — block by brand+product+container+size mnemonic; name = product_name + size
    out += _grain_merges("item",
                         [{"key": r["item_key"], **r} for r in
                          q("dim_item", "SELECT i.item_key, i.size_ml, i.container, i.hoodie_id, i.source_rows, "
                            "i.sources, p.product_name, b.brand FROM read_parquet('%s') i "
                            "LEFT JOIN read_parquet('%s') p ON i.product_key=p.product_key "
                            "LEFT JOIN read_parquet('%s') b ON p.brand_key=b.brand_key "
                            "WHERE p.product_name IS NOT NULL AND p.product_name <> ''" % (di, dp, db))],
                         lambda r: "%s %s" % (r.get("product_name") or "", r.get("size_ml") or ""),
                         lambda r: (hoodie_ids.brand_code(r.get("brand") or "") + hoodie_ids.product_code(r.get("product_name") or "", r.get("brand") or "")
                                    + hoodie_ids.container_code(r.get("container")) + hoodie_ids.size_code(r.get("size_ml"))))
    # SUPPLIER — empty until supplier data lands; the path is ready
    try:
        out += _grain_merges("supplier",
                             [{"key": r["supplier_key"], **r} for r in
                              q("dim_supplier", "SELECT supplier_key, supplier_name, source_rows, sources, "
                                                "hoodie_id FROM t WHERE supplier_name IS NOT NULL")],
                             lambda r: r["supplier_name"], lambda r: hoodie_ids.brand_code(r["supplier_name"]))
    except Exception:
        pass
    warehouse.write_parquet("wb_matches", out,
                            fields=["grain", "merge_id", "block", "canonical", "n", "total_rows",
                                    "confidence", "reason", "members"])
    from collections import Counter
    bg = Counter(r["grain"] for r in out)
    log("[wb_views] wb_matches=%d dedup candidate groups %s (mnemonic-blocked, all grains)" % (len(out), dict(bg)))
    return len(out)


def apply_decisions(rows, log=print):
    """RECLUSTER — respect the human decisions in master_decisions so the master reflects the corrections:
    - merge: the listed sku_keys are ONE SKU → collapse into one canonical (source_list/rows accumulate).
    - split (from the match workbench): the listed SOURCES don't belong to that cluster → prune them.
    A merged cluster can cross the sources>=2 line (moves to master); a pruned one can drop below it (→ queue).
    No decisions = identity (fast path). vpid is never a merge/split anchor — decisions key on our sku_key."""
    import json as _json
    try:                                    # SELECT * — the split rows carry `removed`, merge rows carry
        decs = warehouse.query("master_decisions", "SELECT * FROM t")   # `members`; schema varies, so read all
    except Exception:
        return rows
    if not decs:
        return rows
    parent = {}

    def find(x):
        r = x
        while parent.get(r, r) != r:
            r = parent[r]
        while parent.get(x, x) != r:
            parent[x], x = r, parent[x]
        return r
    split_src, nmerge = {}, 0
    for d in decs:
        if d.get("action") == "merge":
            try:
                mem = _json.loads(d.get("members") or "[]")
            except Exception:
                mem = []
            for k in mem[1:]:
                parent.setdefault(mem[0], mem[0]); parent[k] = mem[0]; nmerge += 1
        elif d.get("action") == "split":
            try:
                rem = _json.loads(d.get("removed") or "[]")
            except Exception:
                rem = []
            if rem:
                split_src.setdefault(d.get("cluster_id"), set()).update(rem)
    canon = {}
    for r in rows:
        sk = r["sku_key"]
        ck = find(sk)
        sl = r.get("source_list")
        if isinstance(sl, str):
            try:
                sl = _json.loads(sl)
            except Exception:
                sl = [sl] if sl else []
        sl = list(sl or [])
        rem = split_src.get(sk)
        if rem:
            sl = [s for s in sl if s not in rem]
        if ck in canon:
            c = canon[ck]
            c["source_list"] = sorted(set((c["source_list"] or []) + sl))
            c["source_rows"] = (c.get("source_rows") or 0) + (r.get("source_rows") or 0)
        else:
            nr = dict(r); nr["sku_key"] = ck; nr["source_list"] = sorted(set(sl)); canon[ck] = nr
    out = []
    for c in canon.values():
        c["sources"] = len(c["source_list"] or [])
        if c["sources"] >= 1:
            out.append(c)
    log("[wb_views] recluster: %d merges + %d splits applied → %d clusters (was %d)"
        % (nmerge, len(split_src), len(out), len(rows)))
    return out


def build_catalog_flat(con, log=print):
    """Flat, PRE-SORTED SKU catalog (`catalog_skus`): dim_sku joined to item/product/brand ONCE and written
    sorted by brand/product. The /api/master/skus browser then pages it with a cheap LIMIT/OFFSET over a flat
    file (row-group order == the sort) instead of a 1M-row 4-table join + full sort on every request (~38s).
    Lowercased brand/product/upc columns let search filter without a per-row CAST/lower. COPY writes straight to
    parquet (no Python materialization of 1M rows)."""
    uri = warehouse.uri("catalog_skus").strip("'")
    con.execute(
        "COPY (SELECT s.sku_key, s.upc, s.pack, s.vintage, s.edition, s.sources, s.source_list, "
        "p.product_name, p.category, p.origin, p.abv, it.size_ml, b.brand, b.brand_group, "
        "lower(CAST(b.brand AS VARCHAR)) brand_lc, lower(CAST(p.product_name AS VARCHAR)) product_lc, "
        "lower(CAST(s.upc AS VARCHAR)) upc_lc "
        "FROM dim_sku s LEFT JOIN dim_item it ON s.item_key=it.item_key "
        "LEFT JOIN dim_product p ON it.product_key=p.product_key "
        "LEFT JOIN dim_brand b ON p.brand_key=b.brand_key "
        "ORDER BY (b.brand IS NULL OR CAST(b.brand AS VARCHAR)=''), b.brand, p.product_name) "
        "TO '%s' (FORMAT PARQUET)" % uri)
    n = _rows(con, "SELECT count(*) c FROM read_parquet('%s')" % uri)[0]["c"]
    log("[wb_views] catalog_skus=%d (flat, pre-sorted)" % n)
    return n


def build(log=print):
    con = warehouse.connect()
    for t in ("dim_sku", "dim_item", "dim_product", "dim_brand", "_stage_product"):
        try:
            con.execute("CREATE OR REPLACE VIEW %s AS SELECT * FROM read_parquet('%s')"
                        % (t, warehouse.uri(t).strip("'")))
        except Exception as e:
            log("[wb_views] view %s: %s" % (t, str(e)[:60]))
    master = _rows(con, _JOIN + "WHERE s.sources >= 2 ORDER BY s.sources DESC, s.source_rows DESC LIMIT %d" % CAP)
    queue = _rows(con, _JOIN + "WHERE s.sources = 1 ORDER BY s.source_rows DESC LIMIT %d" % CAP)
    # RECLUSTER — apply human merge/split decisions, then re-partition (a merge can push a SKU into the master;
    # a split can drop it back to the queue). Identity when there are no decisions.
    combined = apply_decisions(master + queue, log=log)
    master = sorted([r for r in combined if (r.get("sources") or 0) >= 2],
                    key=lambda r: (-(r.get("sources") or 0), -(r.get("source_rows") or 0)))[:CAP]
    queue = sorted([r for r in combined if (r.get("sources") or 0) == 1],
                   key=lambda r: -(r.get("source_rows") or 0))[:CAP]
    warehouse.write_parquet("wb_master", master, fields=FIELDS)
    warehouse.write_parquet("wb_queue", queue, fields=FIELDS)
    agg = _rows(con, "SELECT count(*) total, sum(CASE WHEN sources >= 2 THEN 1 ELSE 0 END) multi FROM dim_sku")[0]
    by = _rows(con, 'SELECT s AS "source", count(*) n FROM (SELECT unnest(source_list) s FROM dim_sku) '
                    "GROUP BY 1 ORDER BY 2 DESC")
    try:
        src_rows = _rows(con, "SELECT count(*) n FROM _stage_product")[0]["n"]
    except Exception:
        src_rows = agg["total"]
    # PRODUCT-level tiering — TTB has no size/UPC so it can only corroborate at the product grain (a registered
    # product also seen in retail = Tier-1). This is TTB's real contribution, invisible at the SKU grain.
    pc = _rows(con, "SELECT count(*) ptot, sum(CASE WHEN sources>=2 THEN 1 ELSE 0 END) pcorr, "
                    "sum(CASE WHEN list_contains(source_list,'ttb_products') "
                    "AND len(list_filter(source_list, x -> x <> 'ttb_products'))>0 THEN 1 ELSE 0 END) ttb_corr, "
                    "sum(CASE WHEN list_contains(source_list,'ttb_products') THEN 1 ELSE 0 END) ttb_tot "
                    "FROM dim_product")[0]
    warehouse.write_parquet("wb_summary", [{"total": agg["total"], "multi": agg["multi"] or 0,
                                            "source_rows": src_rows, "by_source": json.dumps(by),
                                            "prod_total": pc["ptot"], "prod_corr": pc["pcorr"] or 0,
                                            "ttb_corr": pc["ttb_corr"] or 0, "ttb_total": pc["ttb_tot"] or 0}],
                            fields=["total", "multi", "source_rows", "by_source", "prod_total", "prod_corr",
                                    "ttb_corr", "ttb_total"])
    merges = build_merges(con, log=log)
    try:
        matches = build_brand_merges(con, log=log)         # mnemonic-blocked dedup at brand/product/item/supplier
    except Exception as e:
        matches = 0; log("[wb_views] wb_matches skipped: %s" % str(e)[:80])
    try:
        build_catalog_flat(con, log=log)                   # flat pre-sorted SKU catalog for fast paging
    except Exception as e:
        log("[wb_views] catalog_skus skipped: %s" % str(e)[:80])
    try:
        import img_embed                                    # refresh the image-similarity candidate signal from
        img_embed.cross_match(threshold=0.88, log=log)     # whatever's in img_vec (cheap; embedding is separate)
    except Exception as e:
        log("[wb_views] img_matches skipped: %s" % str(e)[:80])
    log("[wb_views] wb_master=%d · wb_queue=%d (cap %d) · total=%d multi=%d · wb_merges=%d · wb_matches=%d"
        % (len(master), len(queue), CAP, agg["total"], agg["multi"] or 0, merges, matches))
    return {"wb_master": len(master), "wb_queue": len(queue), "wb_merges": merges,
            "wb_matches": matches, "total": agg["total"]}


if __name__ == "__main__":
    build()
