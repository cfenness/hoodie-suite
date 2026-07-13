"""hoodie_ids.py — the Hoodie ID: a memorable, hierarchical master code (Bacardi-item-number style).

A durable code per master entity where the CHILD embeds its PARENT, so you can read the hierarchy off the code
and roll up by truncation — and, like a Bacardi item number, you can actually remember it. Fixed-width segments:

    BRAND (6, mnemonic)  PRODUCT (+3)  CONTAINER (+1)  SIZE (+3, centilitres)  PACK (+2)
    BACARD               SUP           G               175                     ST   -> BACARDSUPG175ST
                                                                                VP   -> ...G175VP  (value-added pack)

  brand   = code[:6]   BACARD                (6)   — 6-char mnemonic from the name (BUDWEI, BUDLIG, BACSUP…)
  product = code[:9]   BACARDSUP             (+3)  — mnemonic of the distinguishing words, disambiguated per brand
  item    = code[:13]  BACARDSUPG175         (+4)  — CONTAINER + SIZE (attribute-encoded; the size IS the identity)
  sku     = code[:15]  BACARDSUPG175ST       (+2)  — PACK (ST single / VP value-added pack / 06 six-pack …)

Codes are DERIVED from attributes (so they're readable + stable) and PERSISTED in `hoodie_ids` keyed by the
master key, with per-level collision disambiguation — a real entity keeps its code across rebuilds. VAPs are
tagged both in the code (VP) and as an `is_vap` flag on dim_sku. assign() stamps hoodie_id onto every dim.

    python hoodie_ids.py            # mint/refresh codes + stamp the dims
"""
import argparse
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

REG = "hoodie_ids"
REG_FIELDS = ["entity_type", "master_key", "parent_key", "hoodie_id", "first_seen", "last_seen"]
_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# filler words dropped before abbreviating a name (so "Bacardi Rum Co" -> BACARD, not BACRUM)
_STOP = {"THE", "AND", "OF", "CO", "COMPANY", "INC", "LLC", "LTD", "CORP", "BRAND", "BRANDS", "IMPORTS",
         "WINERY", "WINERIES", "WINES", "WINE", "DISTILLERY", "DISTILLING", "BREWING", "BREWERY", "BREWERIES",
         "SPIRITS", "SPIRIT", "VINEYARD", "VINEYARDS", "ESTATE", "ESTATES", "CELLARS", "FAMILY", "BODEGA"}
_VAP = re.compile(r"gift|value[\s-]?add|\bvap\b|bonus|with (a )?glass|w/ ?glass|\+ ?glass|combo|gift set|"
                  r"gift pack|holiday pack|tasting set|\bset\b|twin pack.*glass", re.I)


def _alnum(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _words(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    s = s.replace("'", "").replace("’", "")            # Tito's -> Titos
    return re.findall(r"[A-Za-z0-9]+", s.upper())


def brand_code(name):
    """6-char memorable mnemonic. Budweiser->BUDWEI, Bud Light->BUDLIG, Bacardi Superior->BACSUP."""
    ws = [w for w in _words(name) if w not in _STOP] or _words(name)
    if not ws:
        return "XXXXXX"
    if len(ws) == 1:
        return _alnum(ws[0])[:6].ljust(6, "X")
    per = [3, 3] if len(ws) == 2 else [2, 2, 2]
    return ("".join(w[:n] for w, n in zip(ws, per))[:6]).ljust(6, "X")


def product_code(product_name, brand_name):
    """3-char mnemonic of the words that DISTINGUISH the product from its brand (flavor / variant)."""
    bset = set(_words(brand_name))
    ws = [w for w in _words(product_name) if w not in bset and w not in _STOP]
    if not ws:
        ws = [w for w in _words(product_name) if w not in bset] or ["STD"]
    if len(ws) == 1:
        return _alnum(ws[0])[:3].ljust(3, "X")
    return ("".join(w[:1] for w in ws[:3])).ljust(3, "X")   # initials of up to 3 words


def container_code(c):
    c = (c or "").lower()
    return ("C" if "can" in c else "B" if ("box" in c or "bag" in c or "carton" in c or "tetra" in c)
            else "P" if ("plas" in c or "pet" in c) else "K" if "keg" in c else "G")   # G = glass/bottle default


def size_code(ml):
    try:
        cl = round(float(ml or 0) / 10.0)                   # centilitres: 750ml->075, 1750->175, 50->005
    except Exception:
        cl = 0
    return str(min(int(cl), 999)).zfill(3)


def is_vap(name):
    return bool(_VAP.search(name or ""))


def pack_code(pack, name):
    if is_vap(name):
        return "VP"
    try:
        p = int(float(pack))
        if p > 1:
            return str(min(p, 99)).zfill(2)
    except Exception:
        pass
    return "ST"


def _load_registry():
    try:
        return warehouse.query(REG, "SELECT entity_type, master_key, parent_key, hoodie_id, first_seen, last_seen FROM t")
    except Exception:
        return []


def _uniq(cand, prefix_len, used):
    """Ensure `cand` is unique among `used` by rewriting the chars right after `prefix_len` (the parent stays
    intact) with a base36 counter — widening from 1 char to as many as the segment allows until it's free."""
    if cand not in used:
        used.add(cand)
        return cand
    avail = len(cand) - prefix_len
    for width in range(1, avail + 1):                  # 1 char (36), then 2 (1.3k), then 3 (47k)…
        for i in range(36 ** width):
            body = "".join(_B36[(i // (36 ** k)) % 36] for k in range(width - 1, -1, -1))
            c = cand[:prefix_len] + body + cand[prefix_len + width:]
            if len(c) == len(cand) and c not in used:
                used.add(c)
                return c
    used.add(cand)
    return cand


def assign(now=None, stamp=True, log=print):
    now = now or time.strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = _load_registry()
    have = {(r["entity_type"], str(r["master_key"])): r for r in reg}
    idof = {k: r["hoodie_id"] for k, r in have.items()}         # (etype,key) -> code, for parent lookup
    used = {e: set() for e in ("brand", "product", "item", "sku")}
    for r in reg:
        used.get(r["entity_type"], set()).add(r["hoodie_id"])
    records = list(reg)
    minted = {"brand": 0, "product": 0, "item": 0, "sku": 0}

    def get(etype, key, parent_key, cand, prefix_len):
        k = (etype, str(key))
        r = have.get(k)
        if r:
            r["last_seen"] = now
            return r["hoodie_id"]
        code = _uniq(cand, prefix_len, used[etype])
        r = {"entity_type": etype, "master_key": str(key), "parent_key": str(parent_key or ""),
             "hoodie_id": code, "first_seen": now, "last_seen": now}
        records.append(r); have[k] = r; idof[k] = code; minted[etype] += 1
        return code

    uri = warehouse.uri
    # BRAND
    bname = {}
    for r in warehouse.query("dim_brand", "SELECT brand_key, brand FROM t WHERE brand_key IS NOT NULL"):
        bname[str(r["brand_key"])] = r["brand"]
        get("brand", r["brand_key"], None, brand_code(r["brand"]), 0)
    log("  [hz] brand   %d codes (+%d)" % (len(used["brand"]), minted["brand"]))
    # PRODUCT (brand code + mnemonic of the words that distinguish the product FROM its brand)
    for r in warehouse.query("dim_product",
                             "SELECT product_key, brand_key, product_name FROM t WHERE product_key IS NOT NULL"):
        bc = idof.get(("brand", str(r["brand_key"])), "XXXXXX")
        pc = product_code(r["product_name"], bname.get(str(r["brand_key"]), ""))
        get("product", r["product_key"], r["brand_key"], bc + pc, 6)
    log("  [hz] product %d codes (+%d)" % (len(used["product"]), minted["product"]))
    # ITEM (product code + container + size)
    for r in warehouse.query("dim_item",
                             "SELECT item_key, product_key, size_ml, container FROM t WHERE item_key IS NOT NULL"):
        pc = idof.get(("product", str(r["product_key"])), "XXXXXXXXX")
        get("item", r["item_key"], r["product_key"], pc + container_code(r["container"]) + size_code(r["size_ml"]), 9)
    log("  [hz] item    %d codes (+%d)" % (len(used["item"]), minted["item"]))
    # SKU (item code + pack); also compute is_vap for the flag
    skus = warehouse.query("dim_sku", "SELECT s.sku_key, s.item_key, s.pack, s.upc, p.product_name "
                           "FROM read_parquet('%s') s JOIN read_parquet('%s') i ON s.item_key=i.item_key "
                           "JOIN read_parquet('%s') p ON i.product_key=p.product_key WHERE s.sku_key IS NOT NULL"
                           % (uri("dim_sku"), uri("dim_item"), uri("dim_product")))
    vap = {}
    for r in skus:
        ic = idof.get(("item", str(r["item_key"])), "XXXXXXXXXXXXX")
        get("sku", r["sku_key"], r["item_key"], ic + pack_code(r["pack"], r["product_name"]), 13)
        vap[str(r["sku_key"])] = is_vap(r["product_name"])
    log("  [hz] sku     %d codes (+%d)" % (len(used["sku"]), minted["sku"]))

    warehouse.write_parquet(REG, records, REG_FIELDS)
    log("[hz] registry: %d codes (%d minted) -> %s" % (len(records), sum(minted.values()), REG))
    if stamp:
        _stamp_dims(idof, vap, log)
    return records


def _stamp_dims(idof, vap, log):
    """Add hoodie_id onto every dim (and is_vap onto dim_sku) so the code rides with the data."""
    for etype, table, keycol in [("brand", "dim_brand", "brand_key"), ("product", "dim_product", "product_key"),
                                 ("item", "dim_item", "item_key"), ("sku", "dim_sku", "sku_key")]:
        try:
            rows = warehouse.query(table, "SELECT * FROM t")
        except Exception as e:
            log("  [hz] stamp %s skipped: %s" % (table, str(e)[:50])); continue
        for r in rows:
            r["hoodie_id"] = idof.get((etype, str(r.get(keycol))))
            if etype == "sku":
                r["is_vap"] = bool(vap.get(str(r.get(keycol)), False))
        warehouse.write_parquet(table, rows)
        log("  [hz] stamped hoodie_id onto %s (%d rows)" % (table, len(rows)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-stamp", action="store_true", help="mint the registry but don't rewrite the dims")
    a = ap.parse_args()
    assign(stamp=not a.no_stamp)
