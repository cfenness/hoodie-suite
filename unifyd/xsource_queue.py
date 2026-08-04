#!/usr/bin/env python3
"""xsource_queue.py — the endless matching queue behind the Match Trainer.

WHY A POOL AND NOT A LIVE QUERY
  Generating candidates means joining every image-bearing retail catalog to `xwalk_source_sku` and
  `dim_sku` — measured at tens of seconds on a warm machine and enough to OOM the serving box when
  done carelessly. That is fine as a nightly build and completely unacceptable per keystroke. So the
  pool is BUILT once into `xsource_queue` and the API just reads the next unresolved slice.

  There is no shortage of work: 67k master rows across the retail catalogs produced pools of ~851
  merged / ~40k near-miss / ~1.8k control pairs from five sources alone, so the queue is effectively
  endless and the interesting question is ORDER, not supply.

ORDER IS THE PRODUCT
  A random pair teaches almost nothing; the same difference answered ten times teaches nothing after
  the second. So the pool is ranked by how much a human answer would move the model:

    1. pairs whose difference class is UNSEEN or still inconsistent — a new cause, or one where the
       answers so far disagree, is where a judgement changes a rule
    2. pairs the rule and the signature DISAGREE about — the boundary
    3. multi-source items — an answer that resolves an item seen by four retailers is worth more
       than one seen by two, because divergence and coverage both key on the collapse
    4. everything else

  `priority` is stored on the row, so the ordering is inspectable and reproducible rather than being
  a query the UI happens to run.

RESOLUTIONS ARE DICTIONARY ENTRIES
  A resolution lands twice: as a labelled pair in `xsource_gold`, and as value mappings in
  `xsource_dictionary` (canonical value + the source spelling that maps to it). The dictionary is
  what makes the queue get faster — the next pair carrying an already-resolved spelling arrives
  pre-filled, so the human is teaching a vocabulary rather than re-answering.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xsource_gold as xg  # noqa: E402
import xsource_match as xm  # noqa: E402

QUEUE_TABLE = "xsource_queue"
DICT_TABLE = "xsource_dictionary"
QUEUE_FIELDS = xg.FIELDS + ["priority", "resolved", "queued_at"]
DICT_FIELDS = ["dimension", "variant_key", "variant", "canonical", "times", "updated_at"]

# image table -> (crosswalk source name, that table's product key column)
SOURCES = {"binnys_products": ("binnys", "sku"), "abc_products": ("abc", "sku"),
           "total_wine_products": ("total-wine", "sku"), "haskells_products": ("haskells", "sku"),
           "specs_products": ("specs", "sku"), "cityhive_products": ("cityhive", "sku"),
           "offprem_products": ("offprem", "sku")}


def _s(v):
    """A warehouse value as text. None stays None so a missing value is never the string 'None';
    a float that is really an integer size loses the '.0' DuckDB gives it."""
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return v if isinstance(v, str) else str(v)


def _master_rows(limit_sources=None, log=print):
    """(resolved_id, brand, name, size, upc, source) across the retail catalogs, deduped IN DuckDB.
    Pulling raw joined rows OOM'd the serving box twice; only distinct item-level rows come back."""
    import warehouse
    con = warehouse.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET preserve_insertion_order=false")
    xw = warehouse.uri("xwalk_source_sku").strip("'")
    ds = warehouse.uri("dim_sku").strip("'")
    rows = []
    for t, (src, key) in SOURCES.items():
        if limit_sources and t not in limit_sources:
            continue
        try:
            u = warehouse.uri(t).strip("'")
            cols = [c[0] for c in con.execute("SELECT * FROM read_parquet('%s') LIMIT 0" % u).description]
            nc = next((c for c in ("name", "product_name", "title") if c in cols), None)
            zc = next((c for c in ("size", "item_size", "size_ml", "volume") if c in cols), None)
            bc = next((c for c in ("brand", "brand_name") if c in cols), None)
            if not nc or key not in cols:
                continue
            q = ("SELECT DISTINCT COALESCE(d.resolved_id, x.item_key) rid, %s br, p.%s nm, %s sz, "
                 "COALESCE(NULLIF(CAST(d.upc AS VARCHAR),''), CAST(d.gtin AS VARCHAR)) up "
                 "FROM read_parquet('%s') p JOIN read_parquet('%s') x "
                 "ON x.source='%s' AND CAST(x.product_id AS VARCHAR)=CAST(p.%s AS VARCHAR) "
                 "LEFT JOIN read_parquet('%s') d ON d.item_key=x.item_key"
                 % (("p." + bc) if bc else "NULL", nc, ("p." + zc) if zc else "NULL",
                    u, xw, src, key, ds))
            got = con.execute(q).fetchall()
            # EVERY value is coerced to text. `size` is a VARCHAR in binny's ("750ML") and a NUMBER
            # in offprem_products (750), and mixing them makes pyarrow infer int64 from the first
            # catalog and then refuse the second ("could not convert '750ML' ... to int64") — which
            # cost a whole build: 4,000 pairs generated, 0 landed, and the run reported success
            # because the land failure was caught and logged rather than raised. It also matters
            # upstream of the land: size_ml() and the name signature compare strings, so an int size
            # would silently never match its own text spelling.
            rows += [{"resolved_id": r[0], "brand": _s(r[1]), "name": _s(r[2]), "size": _s(r[3]),
                      "upc": _s(r[4]), "source": src} for r in got if r[0]]
            log("  %-22s %d distinct" % (t, len(got)))
        except Exception as e:
            log("  %-22s skipped: %s" % (t, str(e).split("\n")[0][:70]))
    return rows


def priority(pair, seen_causes, src_counts):
    """Lower sorts first. Encodes the ordering argued in the module docstring."""
    diff = pair.get("difference") or ""
    # DISTINCT SOURCES, not rows. Counting rows made this nearly meaningless: offprem_products alone
    # contributes 415k rows, so an item with four listings at one retailer scored the same as one
    # genuinely carried by four chains, and 94% of the pool landed in this bucket.
    n_src = len(src_counts.get(pair.get("a_id")) or ()) + len(src_counts.get(pair.get("b_id")) or ())
    if diff and seen_causes.get(diff, 0) < 3:
        return 0                                   # an unseen or barely-seen cause
    # The boundary — the rule and the surfaces disagree. Two shapes, and the near_miss half was
    # dead on its own: near_miss pairs differ in one signature component BY CONSTRUCTION, so
    # `rule_merges` is never true for them and this tier matched nothing at all.
    if pair.get("rule_merges") and diff not in ("", "identical"):
        return 1                                   # merged despite a visible difference
    if pair.get("stratum") == "near_miss" and pair.get("rule_merges"):
        return 1
    if n_src >= 4:
        return 2                                   # resolving a widely-carried item is worth more
    return 3


def difference(pair):
    """Which surface difference explains this pair — the same taxonomy the trainer shows, computed
    server-side so the ordering and the UI agree about what a pair IS."""
    a, b = pair.get("a_name") or "", pair.get("b_name") or ""
    az, bz = pair.get("a_size") or "", pair.get("b_size") or ""
    asz = xm.size_ml(az) or xm.size_ml(a)
    bsz = xm.size_ml(bz) or xm.size_ml(b)
    if asz and bsz and asz != bsz:
        return "size_value"
    import re
    stripped = lambda s: re.sub(r"\s+", " ", xm._SIZE_RE.sub(" ", s.lower())).strip()  # noqa: E731
    if stripped(a) == stripped(b):
        return "size_format" if str(az).strip().lower() != str(bz).strip().lower() else "identical"
    abk, bbk = xm.brand_key(pair.get("a_brand") or a), xm.brand_key(pair.get("b_brand") or b)
    if xm.name_sig(a, abk) == xm.name_sig(b, bbk):
        return "normalised_away"
    return "different_tokens"


def build(n=4000, land=True, log=print):
    """Generate the candidate pool. A registered build, not something the UI triggers."""
    rows = _master_rows(log=log)
    if not rows:
        log("xsource_queue: no master rows — nothing to queue")
        print('HOODIE_RESULT {"status": "degraded", "items_done": 0, "items_total": 0}')
        return [], {"status": "degraded"}
    log("xsource_queue: %d master rows" % len(rows))

    src_counts = {}
    for r in rows:
        src_counts.setdefault(r["resolved_id"], set()).add(r["source"])

    pairs = xg.candidates(rows, n=n, seed=7, log=log)
    for p in pairs:
        p["difference"] = difference(p)
    seen = {}
    for p in pairs:
        seen[p["difference"]] = seen.get(p["difference"], 0) + 1
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    running = {}
    for p in pairs:
        p["priority"] = priority(p, running, src_counts)
        running[p["difference"]] = running.get(p["difference"], 0) + 1
        p["resolved"] = ""
        p["queued_at"] = now
    pairs.sort(key=lambda p: (p["priority"], p["pair_id"]))

    landed, land_error = None, None
    if land:
        try:
            import warehouse
            # A REBUILD MUST NEVER ERASE AN ANSWER. candidates() is seeded, so a weekly rebuild
            # regenerates the same pair_ids — and write_accumulate lets the newer row win, which
            # would overwrite `resolved`/`label` with the blanks a fresh candidate carries. Pairs
            # already answered are dropped from the land entirely.
            done = set()
            try:
                for r in warehouse.query(QUEUE_TABLE,
                                         "SELECT pair_id FROM t WHERE COALESCE(resolved,'') <> ''"):
                    done.add(r["pair_id"] if isinstance(r, dict) else r[0])
            except Exception:
                pass                                  # no table yet — nothing to protect
            fresh = [p for p in pairs if p["pair_id"] not in done]
            if len(fresh) != len(pairs):
                log("%s: %d of %d pairs already answered — left untouched"
                    % (QUEUE_TABLE, len(pairs) - len(fresh), len(pairs)))
            warehouse.write_accumulate(QUEUE_TABLE, fresh, key="pair_id",
                                       fields=QUEUE_FIELDS + ["difference"], coverage=False)
            landed = len(fresh)
            log("landed %s: %d pairs" % (QUEUE_TABLE, len(fresh)))
        except Exception as e:
            landed, land_error = 0, str(e)[:160]
            log("%s LAND FAILED: %s" % (QUEUE_TABLE, land_error))
    cov = {"pairs": len(pairs), "causes": seen,
           "by_priority": {str(k): sum(1 for p in pairs if p["priority"] == k) for k in (0, 1, 2, 3)}}
    log("xsource_queue: %d pairs | priority mix %s" % (len(pairs), cov["by_priority"]))
    # A build that GENERATED 4,000 pairs and landed none is a failure, and it reported `success`
    # once because the land was in a try/except and the count came from `pairs`. items_done is the
    # LANDED count, and a failed land degrades the run — the queue the UI reads is the table.
    # A land of 0 because every pair was ALREADY ANSWERED is not a failure, which is why the
    # degrade keys on the error and not on the count.
    print("HOODIE_RESULT " + json.dumps(dict(
        {"status": "degraded" if land_error else "success",
         "items_done": len(pairs) if landed is None else landed,
         "items_total": len(pairs),
         "warnings": ["generated %d pairs but landed 0 — the queue is unchanged (%s)"
                      % (len(pairs), land_error)] if land_error else []}, **cov)))
    return pairs, cov


# ── what the API calls ────────────────────────────────────────────────────────────────────────────
def next_batch(n=25, log=print):
    """The next unresolved slice, best-first. Returns [] rather than raising when the pool is not
    built — the trainer then says so instead of showing an empty queue that looks like completion."""
    import warehouse
    try:
        rows = warehouse.query(QUEUE_TABLE,
                               "SELECT * FROM t WHERE resolved IS NULL OR resolved = '' "
                               "ORDER BY priority, pair_id LIMIT %d" % int(n))
    except Exception as e:
        log("xsource_queue: pool unavailable (%s)" % str(e)[:90])
        return []
    return rows


def dictionary(log=print):
    """{dimension: {variant_key: canonical}} — the accumulated vocabulary, so a NEW session inherits
    everything already taught rather than starting blank."""
    import warehouse
    out = {}
    try:
        for r in warehouse.query(DICT_TABLE, "SELECT * FROM t"):
            out.setdefault(r["dimension"], {})[r["variant_key"]] = {
                "value": r["canonical"], "n": r.get("times") or 1, "seen": r.get("variant") or ""}
    except Exception:
        pass
    return out


def resolve(payload, log=print):
    """Land one resolution: the labelled pair, plus every value mapping it teaches.

    Blank dimensions are omitted, never written empty — an unresolved dimension is silence."""
    import warehouse
    pid = (payload or {}).get("pair_id")
    if not pid:
        return {"error": "pair_id required"}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    label = (payload.get("label") or "").lower()
    row = {"pair_id": pid, "label": label if label in ("y", "n") else "",
           "labelled_by": payload.get("by") or "", "labelled_at": now}
    for f in ("canon_brand", "canon_product", "canon_size", "canon_category",
              "canon_type", "canon_class", "canon_subclass", "canon_varietal"):
        v = (payload.get(f) or "").strip()
        if v:
            row[f] = v

    landed = {"gold": 0, "dictionary": 0}
    try:
        cur = warehouse.query(QUEUE_TABLE, "SELECT * FROM t WHERE pair_id = ?", [pid])
        base = dict(cur[0]) if cur else {"pair_id": pid}
    except Exception:
        base = {"pair_id": pid}
    base.update(row)
    base["resolved"] = now
    try:
        warehouse.write_accumulate(QUEUE_TABLE, [base], key="pair_id",
                                   fields=QUEUE_FIELDS + ["difference"], coverage=False)
        warehouse.write_accumulate(xg.TABLE, [{k: v for k, v in base.items() if k in xg.FIELDS}],
                                   key="pair_id", fields=xg.FIELDS, coverage=False)
        landed["gold"] = 1
    except Exception as e:
        log("resolve: gold land failed: %s" % str(e)[:90])

    # Dictionary entries: the canonical value plus the source spellings it replaces, and the value
    # mapped to ITSELF so a typed term with no source spelling is still learned.
    drows, src = [], {"canon_brand": ("a_brand", "b_brand"), "canon_product": ("a_name", "b_name"),
                      "canon_size": ("a_size", "b_size")}
    for f, v in row.items():
        if not f.startswith("canon_") or not v:
            continue
        variants = [base.get(k) for k in src.get(f, ())] + [v]
        for var in variants:
            var = (var or "").strip()
            if not var:
                continue
            key = " ".join(var.lower().split())
            drows.append({"dimension": f, "variant_key": key, "variant": var,
                          "canonical": v, "times": 1, "updated_at": now})
    if drows:
        try:
            warehouse.write_accumulate(DICT_TABLE, drows,
                                       key=lambda r: "%s|%s" % (r["dimension"], r["variant_key"]),
                                       fields=DICT_FIELDS, coverage=False)
            landed["dictionary"] = len(drows)
        except Exception as e:
            log("resolve: dictionary land failed: %s" % str(e)[:90])
    return {"ok": True, "pair_id": pid, "landed": landed}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build/serve the continuous matching queue.")
    ap.add_argument("cmd", choices=["build", "next", "dict"], nargs="?", default="build")
    ap.add_argument("--n", type=int, default=4000)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(n=a.n)
    elif a.cmd == "next":
        print(json.dumps(next_batch(a.n if a.n < 200 else 25), indent=2, default=str))
    else:
        print(json.dumps(dictionary(), indent=2, default=str))


if __name__ == "__main__":
    main()
