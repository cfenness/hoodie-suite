"""hoodie_ids.py — the canonical Hoodie ID spine: durable, legible identifiers for every master entity.

The master dims key on CONTENT hashes (sku_key / product_key / outlet_key) that change the moment we clean an
attribute — useless as a stable reference. Downstream systems (the fact tables, exports, the app, partner
feeds) need an id that survives rebuilds. This mints ONE Hoodie ID per master entity on first sighting and
PERSISTS it in the `hoodie_ids` registry, so every future rebuild reuses the same id.

    Format: HZ-<T>-<seq7>   e.g. HZ-P-0001234 (product), HZ-K-0057881 (sku), HZ-O-0000042 (outlet)
    T: B brand · P product · I item · K sku · O outlet · R retail store

Stability today = as stable as the master_key it maps: a key that changes still mints a NEW id. When the
matching layer learns old_key === new_key, we carry the id across (key migration — TODO). Starting simple and
durable is the point; the registry is the thing that must never be thrown away.

    python hoodie_ids.py           # assign / refresh IDs for every dimension
    python hoodie_ids.py --entity product
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

# entity_type -> (dim table, key column, id letter). Missing tables are skipped.
ENTITIES = [
    ("brand", "dim_brand", "brand_key", "B"),
    ("product", "dim_product", "product_key", "P"),
    ("item", "dim_item", "item_key", "I"),
    ("sku", "dim_sku", "sku_key", "K"),
    ("outlet", "dim_outlet_resolved", "outlet_key", "O"),
    ("store", "dim_store", "store_key", "R"),
]
REG = "hoodie_ids"
REG_FIELDS = ["entity_type", "master_key", "hoodie_id", "seq", "first_seen", "last_seen"]


def _load_registry():
    try:
        return warehouse.query(REG, "SELECT entity_type, master_key, hoodie_id, seq, first_seen, last_seen FROM t")
    except Exception:
        return []                                   # first run — no registry yet


def hoodie_id(letter, seq):
    return "HZ-%s-%07d" % (letter, seq)


_LETTER = {e: l for e, _, _, l in ENTITIES}


def ensure_many(keysets, now=None, log=print):
    """Get-or-mint Hoodie IDs for arbitrary keys, persisting once. `keysets` = {entity_type: iterable_of_keys}.
    Returns {entity_type: {key: hoodie_id}}. Lets a consumer (e.g. facts.py) stamp durable ids without needing
    a full assign() pass first — new keys mint, existing ones reuse. Same registry, so assign() stays in sync."""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = _load_registry()
    have = {(r["entity_type"], str(r["master_key"])): r for r in reg}
    maxseq = {}
    for r in reg:
        maxseq[r["entity_type"]] = max(maxseq.get(r["entity_type"], 0), int(r["seq"] or 0))
    records, out = list(reg), {}
    for etype, keys in keysets.items():
        letter = _LETTER.get(etype, etype[:1].upper())
        m = {}
        for k in keys:
            k = str(k)
            rec = have.get((etype, k))
            if rec:
                rec["last_seen"] = now
            else:
                maxseq[etype] = maxseq.get(etype, 0) + 1
                rec = {"entity_type": etype, "master_key": k, "hoodie_id": hoodie_id(letter, maxseq[etype]),
                       "seq": maxseq[etype], "first_seen": now, "last_seen": now}
                records.append(rec)
                have[(etype, k)] = rec
            m[k] = rec["hoodie_id"]
        out[etype] = m
    warehouse.write_parquet(REG, records, REG_FIELDS)
    log("[hz] ensure_many: %d total ids across %s" % (len(records), ", ".join(keysets)))
    return out


def assign(only=None, now=None, log=print):
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = _load_registry()
    have = {(r["entity_type"], r["master_key"]): r for r in reg}   # (etype,key) -> existing record
    maxseq = {}
    for r in reg:
        maxseq[r["entity_type"]] = max(maxseq.get(r["entity_type"], 0), int(r["seq"] or 0))
    records = list(reg)                             # start from the existing registry (durable)
    minted = {}
    for etype, table, keycol, letter in ENTITIES:
        if only and etype != only:
            continue
        try:
            keys = [r[keycol] for r in warehouse.query(
                table, "SELECT DISTINCT %s FROM t WHERE %s IS NOT NULL AND %s <> ''" % (keycol, keycol, keycol))]
        except Exception as e:
            log("  [hz] %-8s (%s): skipped — %s" % (etype, table, str(e)[:45]))
            continue
        new = 0
        for k in keys:
            k = str(k)
            rec = have.get((etype, k))
            if rec:
                rec["last_seen"] = now              # already has an id — reuse, refresh last_seen
            else:
                maxseq[etype] = maxseq.get(etype, 0) + 1
                nr = {"entity_type": etype, "master_key": k, "hoodie_id": hoodie_id(letter, maxseq[etype]),
                      "seq": maxseq[etype], "first_seen": now, "last_seen": now}
                records.append(nr)
                have[(etype, k)] = nr
                new += 1
        minted[etype] = new
        log("  [hz] %-8s %7d keys · +%-6d new · %d total" % (etype, len(keys), new, maxseq.get(etype, 0)))
    warehouse.write_parquet(REG, records, REG_FIELDS)   # complete registry (we loaded all + appended)
    log("[hz] %d Hoodie IDs (%d minted this run) -> %s" % (len(records), sum(minted.values()), REG))
    return records


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", default="", help="only this entity type (brand/product/item/sku/outlet/store)")
    a = ap.parse_args()
    assign(only=a.entity or None)
