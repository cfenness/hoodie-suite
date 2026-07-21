"""master_ttb.py — promote TTB COLA items into the canonical master ONLY when confirmed to exist elsewhere.

A TTB COLA filing is a label APPROVAL, not proof a product is on the market — millions are filed, many never
ship. So COLA seeds Tier 0 (quarantine); an item lands in the master only when an INDEPENDENT market source
(ABC, Total Wine, Binny's, on-premise menus, …) corroborates it. This assembles the market index from every
product source we hold, runs cola_tiering.tier() (cluster label-iterations -> corroborate -> promote, healing
the UPC on the way), and lands:
  · ttb_master   — Tier 1: confirmed-elsewhere items = the canonical master, each with PROVENANCE (the COLA
                   filings underneath + the corroborating source + confidence + healed UPC).
  · ttb_review   — low-confidence matches for the workbench review queue.
  · ttb_quarantine_summary — Tier 0 innovation radar (filed, unconfirmed) by supplier.

    python master_ttb.py --cola 25000        # scope the COLA slice (newest first); omit for a bigger run
"""
import argparse, json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import cola_tiering as ct
import upc as _upc


def _full_upc(raw):
    """Translation heal → a full valid code where provable: checkless-13 (the Kroger shape — this is where
    ttb_master's 72%-bad-check upc cohort came from) first (a broken '00'-13 would pass normalize's length
    gate as-is), then normalize (heals zero-stripped), else the raw digits unchanged."""
    return _upc.from_checkless_13(raw) or _upc.normalize(raw) or (raw or "")


def load_market(log=print):
    """Every product source -> normalized market rows {source, brand, name, size, upc}. DISTINCT collapses the
    store x sku explosion (abc_products is 1.9M rows but far fewer distinct products)."""
    rows = []

    def add(ds, sql, src, brand_k, name_k, size_k):
        try:
            for r in warehouse.query(ds, sql):
                nm = r.get(name_k)
                if not nm:
                    continue
                rows.append({"source": src, "brand": (r.get(brand_k) or nm), "name": nm,
                             "size": (r.get(size_k) or nm), "upc": _full_upc(r.get("upc"))})
        except Exception as e:
            log("  (skip %s: %s)" % (ds, str(e)[:40]))

    add("abc_catalog", "SELECT DISTINCT name, brand, size, upc FROM t WHERE name IS NOT NULL", "abc", "brand", "name", "size")
    add("binnys_products", "SELECT DISTINCT brand, name FROM t WHERE name IS NOT NULL", "binnys", "brand", "name", "name")
    add("total_wine_products", "SELECT DISTINCT name, size FROM t WHERE name IS NOT NULL", "totalwine", "name", "name", "size")
    add("specs_products", "SELECT DISTINCT name FROM t WHERE name IS NOT NULL", "specs", "name", "name", "name")
    add("kroger_products", "SELECT DISTINCT product_name, brand, size, upc FROM t WHERE product_name IS NOT NULL", "kroger", "brand", "product_name", "size")
    add("walmart_products", "SELECT DISTINCT product_name, brand, size_ml, upc FROM t WHERE product_name IS NOT NULL", "walmart", "brand", "product_name", "size_ml")
    add("target_products", "SELECT DISTINCT name, brand, upc FROM t WHERE name IS NOT NULL", "target", "brand", "name", "name")
    add("orlando_offprem_products", "SELECT DISTINCT name, brand, upc FROM t WHERE name IS NOT NULL", "independent", "brand", "name", "name")
    add("menu_beverages", "SELECT DISTINCT name FROM t WHERE name IS NOT NULL", "menu", "name", "name", "name")
    log("[master] market index built from %d distinct product listings" % len(rows))
    return rows


def _master_row(cl, matched_by):
    cor = cl.get("corroboration") or cl.get("candidate") or {}
    return dict(cluster_id=cl["cluster_id"], brand=cl.get("brand", ""), fanciful=cl.get("fanciful", ""),
                class_type=cl.get("class_type", ""), size_ml=cl.get("size_ml"), supplier=cl.get("supplier", ""),
                upc=cl.get("upc", "") or cor.get("upc", ""), corroborated_by=cor.get("source", ""),
                confidence=cor.get("confidence"), match_kind=cor.get("match", ""),
                size_matched=cor.get("size_matched"), candidate_name=cor.get("name", ""), matched_by=matched_by,
                member_count=cl.get("count", 0), members=json.dumps(cl.get("members", [])),
                first_day=cl.get("first_day"), last_day=cl.get("last_day"),
                tier=(0 if matched_by == "manual_pending" else 1))


def claude_match(review_rows, model="claude-opus-4-8", log=print):
    """AI tier of the cascade: adjudicate each low-confidence candidate the automations couldn't confirm. Claude
    decides same-product / not for each pair; the ones it confirms promote (matched_by='claude'), the rest fall to
    the MANUAL queue. Opt-in on ANTHROPIC_API_KEY (no-op without -> everything goes to manual). Returns the set of
    confirmed cluster_ids."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not review_rows:
        return set()
    confirmed = set()
    for i in range(0, len(review_rows), 40):
        batch = review_rows[i:i + 40]
        lines = ['%d) TTB label: brand="%s" fanciful="%s" type="%s" size=%sml  vs  %s listing: "%s"'
                 % (j, r["brand"], r.get("fanciful", ""), r.get("class_type", ""), r.get("size_ml"),
                    r.get("corroborated_by", ""), r.get("candidate_name", "")) for j, r in enumerate(batch)]
        prompt = ("For each numbered pair, decide if the TTB label item and the retail listing are THE SAME "
                  "product — same brand + product + roughly the same size (ignore vintage, packaging words, minor "
                  "spelling). Return ONLY a JSON array of the integer indices that ARE the same product.\n\n"
                  + "\n".join(lines))
        body = json.dumps({"model": model, "max_tokens": 1500,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            rr = json.loads(urllib.request.urlopen(req, timeout=120).read())
            txt = "".join(b.get("text", "") for b in rr.get("content", []) if b.get("type") == "text")
            for k in json.loads(re.search(r"\[.*\]", txt, re.S).group(0)):
                if isinstance(k, int) and 0 <= k < len(batch):
                    confirmed.add(batch[k]["cluster_id"])
        except Exception:
            pass
    log("  [claude-match] adjudicated %d candidates -> %d confirmed" % (len(review_rows), len(confirmed)))
    return confirmed


def run(cola_limit=25000, threshold=0.65, log=print):
    # 0.65 promotes a genuine brand-in-product-name match WITH a size match (0.7) or an exact-brand match (0.65).
    # The library default (0.85) demands exact brand + size, which our current market coverage (TotalWine/Binny's/
    # Spec's/menus — abc_products landed EMPTY name/brand, a pull bug to fix) rarely satisfies. Tunable upward as
    # market coverage grows.
    log("[master] loading COLA (newest %d) + market sources…" % cola_limit)
    cola = warehouse.query("ttb_cola", 'SELECT * FROM t ORDER BY "Completed Date" DESC LIMIT %d' % cola_limit)
    market = load_market(log)
    res = ct.tier(cola, market, threshold=threshold)
    s = res["summary"]
    from collections import Counter

    # ── the matching cascade: automations -> Claude -> manual ──
    auto = [_master_row(cl, "auto") for cl in res["tier1"]]                       # 1) automations made the match
    cands = [_master_row(cl, "manual_pending") for cl in res["review"] if (cl.get("candidate") or {}).get("name")]
    log("[master] %d clusters · %d auto-confirmed · %d candidates the automations couldn't confirm -> Claude…"
        % (s["clusters"], len(auto), len(cands)))
    claude_ok = claude_match(cands, log=log)                                      # 2) Claude adjudicates the rest
    claude_rows, manual_rows = [], []
    for r in cands:
        if r["cluster_id"] in claude_ok:
            r["matched_by"], r["tier"] = "claude", 1
            claude_rows.append(r)
        else:
            manual_rows.append(r)                                                # 3) everything left -> manual queue

    master = auto + claude_rows
    if master:
        warehouse.write_parquet("ttb_master", master)
    warehouse.write_parquet("ttb_review", manual_rows)                           # the workbench's manual match queue
    warehouse.write_parquet("ttb_quarantine_summary",
                            [dict(r, run_id="master-" + time.strftime("%Y%m%d-%H%M%S")) for r in res["innovation"][:200]])
    log("[master] CONFIRMED %d = %d auto + %d Claude · %d -> manual queue · by-source %s"
        % (len(master), len(auto), len(claude_rows), len(manual_rows),
           dict(Counter(m["corroborated_by"] for m in master).most_common())))
    return {"master": len(master), "auto": len(auto), "claude": len(claude_rows),
            "manual": len(manual_rows), "summary": s}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cola", type=int, default=25000)
    a = ap.parse_args()
    print(json.dumps(run(cola_limit=a.cola)["summary"], indent=2))
