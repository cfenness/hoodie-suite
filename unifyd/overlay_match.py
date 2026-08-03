"""overlay_match.py — S3 of the Overlay pipeline: resolve an UPLOADED file against the master.

This is the core pass and the genuinely new one. Everything else in the pipeline reuses an engine
that already exists; nothing in the repo previously resolved an arbitrary customer file against
Hoodie identity. Five tiers, and the tier is reported — never a single blended number, because the
DETERMINISTIC share is the number that matters and it's the honest one to lead with:

  1  exact normalized UPC/GTIN  ↔ item_identity          DETERMINISTIC
  2  zero-strip heal → match    ↔ item_identity          DETERMINISTIC   (the heal is itself a finding)
  3  distributor item code      ↔ dist_item_xwalk        DETERMINISTIC where the distributor is landed
  4  signature cluster (brand + canonical name + size)   INFERENCE, confidence-scored
  5  unmatched                                            never hidden — reported with a WHY histogram

Two properties the implementation is built around:

  BULK, NOT PER-ROW. A 50k-row file must resolve inside the wall-clock budget, so every tier does
  ONE bulk lookup over the file's DISTINCT keys (chunked), never a query per row. A file with 50k
  rows and 4k distinct UPCs costs 4k keys, not 50k round trips.

  HONEST DEGRADATION. Any table can be absent (a fresh environment, a warehouse blip). A tier whose
  table can't be read reports itself `unavailable` with the reason and the run continues at the
  tiers that CAN run — a missing table must never look like a low match rate, because those two
  things mean opposite things to the person reading the output.

The master side is injected (`MasterIndex`), so the resolver is testable with no DuckDB and no
network — `MemoryMaster` in the tests is the same interface the warehouse-backed one implements.
"""
import re

import precleanse
import upc as upc_mod

TIER_LABELS = {
    1: ("DETERMINISTIC", "upc_exact"),
    2: ("DETERMINISTIC", "upc_zero_strip_heal"),
    3: ("DETERMINISTIC", "dist_item_code"),
    4: ("INFERENCE", "signature_cluster"),
}

_CHUNK = 4000                 # distinct keys per bulk lookup — DuckDB IN-lists degrade beyond this


# ── keys ───────────────────────────────────────────────────────────────────────────────────────

def canon_id(code):
    """Digits-only UPC as a bigint — the identity `build_item_identity` writes, so a match here and
    a row in `item_identity` are the same number by construction. None when it isn't one."""
    d = re.sub(r"\D", "", str(code or ""))
    if len(d) < 11:
        return None
    try:
        return int(d)
    except ValueError:
        return None


def _healed(code):
    """The zero-strip heal, and ONLY when it is proven: a 10/11-digit code re-padded to 12 that then
    clears the GS1 mod-10 check. Returns the healed code or ''. A random pad clears mod-10 ~10% of
    the time, which is exactly why the check digit is treated as the proof and not as a hint."""
    d = re.sub(r"\D", "", str(code or ""))
    if len(d) in (10, 11):
        p = d.zfill(12)
        if upc_mod.check_digit_ok(p):
            return p
    return ""


_NOISE = {"glass", "bottle", "bottles", "btl", "can", "cans", "pet", "plastic", "gift", "box",
          "boxed", "bag", "each", "ea", "nr", "pack", "packs", "the", "a", "an", "and", "of", "with"}


def brand_key(brand):
    """The brand match key. `precleanse.nbrand` is the shared normalization, but it only
    singularizes the LAST token — so "TITOS HANDMADE" and "Tito's Handmade" produce different keys
    and the same brand fails to corroborate. Uploaded files carry exactly that variance (an export
    writes the possessive out, the next one doesn't), so the overlay singularizes EVERY token.

    Applied to both sides of the comparison through `signature()`, so the file and the master are
    always keyed the same way. Deliberately local: `nbrand` is load-bearing for the master build's
    brand blocks, and widening it there is a change to a different system's clustering.
    """
    toks = precleanse.nbrand(brand).split()
    return " ".join(t[:-1] if (len(t) > 3 and t.endswith("s")) else t for t in toks)


def name_sig(name, brand=None):
    """Order-independent token signature of a product name — lowercase, de-accented, punctuation and
    container/format noise dropped. Two spellings of one product produce one signature; two genuinely
    different products do not (the tokens differ). Mirrors build_product_master's canonicalization
    intent without importing it (that module pulls the whole warehouse in).

    The brand is REMOVED from the name tokens when supplied, because whether the brand is repeated
    inside the product name is a per-source convention, not a product difference — one source lands
    'Tito's Handmade' + 'Texas Vodka', the next lands 'Tito's Handmade' + 'Titos Handmade Texas
    Vodka', and those are the same SKU. Singular/plural variants of each brand token are stripped
    together, so 'Titos' in the name is removed by the brand key 'tito'.
    """
    s = precleanse.deaccent(str(name or "")).lower()
    toks = [t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _NOISE and len(t) > 1]
    if brand:
        drop = set()
        for t in brand_key(brand).split():
            drop.update({t, t + "s", t.rstrip("s")})
        kept = [t for t in toks if t not in drop]
        # A product named exactly like its brand ('Hennessy' / 'Hennessy') would strip to nothing;
        # keep the original tokens rather than emit an empty — an empty signature never matches.
        toks = kept or toks
    return " ".join(sorted(set(toks)))


def _ml(size_raw, size_ml):
    """Size in ml from whichever of the two the file carries."""
    for v in (size_ml, size_raw):
        if v in (None, ""):
            continue
        m = re.search(r"([\d.]+)\s*(ml|l|lt|ltr|liter|litre|oz)\b", str(v), re.I)
        if m:
            try:
                n = float(m.group(1))
            except ValueError:
                continue
            u = m.group(2).lower()
            n = n * 1000 if u.startswith("l") and u != "ltr"[:0] and u[0] == "l" else (
                n * 29.57 if u == "oz" else n)
            return round(n) if 1 <= n <= 20000 else None
        try:
            n = float(str(v).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 20000:
            return round(n)
    return None


def signature(brand, product_name, size_raw, size_ml, container=None):
    """The Tier-4 cluster key: precleansed brand + canonical name + size in ml. Returns '' when the
    row can't produce one (no brand, or no name) — an empty signature must never match.

    Container is deliberately NOT in the key. It belongs to the SKU grain conceptually, but the
    vocabulary is not shared across sources ('glass' / 'bottle' / 'GLS' / blank), so keying on it
    would fail matches that are genuinely the same SKU. It is scored instead (see `_sig_conf`):
    an agreeing container raises confidence, a blank one costs nothing, and a DISAGREEING one is
    what actually matters — that gets scored down.
    """
    b = brand_key(brand)
    n = name_sig(product_name, brand)
    if not b or not n:
        return ""
    ml = _ml(size_raw, size_ml)
    return "%s|%s|%s" % (b, n, ml if ml else "")


# ── the master side ────────────────────────────────────────────────────────────────────────────

class MasterIndex(object):
    """What the resolver needs from the master. Implemented over the warehouse in production and
    over dicts in tests, so the tier logic is proven without DuckDB in the loop."""

    def upc_lookup(self, canon_ids):
        raise NotImplementedError

    def dist_lookup(self, item_keys):
        raise NotImplementedError

    def signature_lookup(self, brand_keys):
        raise NotImplementedError

    def status(self):
        return {}


class MemoryMaster(MasterIndex):
    """Dict-backed master. `items` is {canon_id: record}; `dist` is {item_key: [record, …]};
    `catalog` is a list of master records used to build the signature index."""

    def __init__(self, items=None, dist=None, catalog=None):
        self.items = items or {}
        self.dist = dist or {}
        self.catalog = catalog or []
        self._sig = None

    def upc_lookup(self, canon_ids):
        return {c: self.items[c] for c in canon_ids if c in self.items}

    def dist_lookup(self, item_keys):
        return {k: self.dist[k] for k in item_keys if k in self.dist}

    def _build_sig(self):
        idx = {}
        for r in self.catalog:
            sig = signature(r.get("brand"), r.get("product_name"), r.get("size_raw"),
                            r.get("size_ml"), r.get("container"))
            if sig:
                idx.setdefault(sig, []).append(r)
        self._sig = idx

    def signature_lookup(self, brand_keys):
        if self._sig is None:
            self._build_sig()
        want = set(brand_keys)
        return {s: rs for s, rs in self._sig.items() if s.split("|", 1)[0] in want}

    def status(self):
        return {"item_identity": {"available": True, "rows": len(self.items)},
                "dist_item_xwalk": {"available": True, "rows": len(self.dist)},
                "product_master": {"available": True, "rows": len(self.catalog)}}


class WarehouseMaster(MasterIndex):
    """The production master: `item_identity` (Tier 1/2), `dist_item_xwalk` (Tier 3), and
    `_stage_product` (Tier 4's candidate pool). Each is probed independently and a table that
    can't be read disables ONLY its tier, with the reason recorded."""

    def __init__(self, warehouse_mod=None):
        import warehouse as _w
        self.w = warehouse_mod or _w
        self._status = {}

    def _query(self, table, sql, params=None, tier=None):
        try:
            rows = self.w.query(table, sql, params)
            self._status.setdefault(table, {"available": True})
            return rows
        except Exception as e:
            self._status[table] = {"available": False, "why": str(e)[:140], "tier": tier}
            return None

    def _chunks(self, keys):
        keys = list(keys)
        for i in range(0, len(keys), _CHUNK):
            yield keys[i:i + _CHUNK]

    def upc_lookup(self, canon_ids):
        out = {}
        for chunk in self._chunks(canon_ids):
            ph = ", ".join(["?"] * len(chunk))
            rows = self._query(
                "item_identity",
                "SELECT canon_item_id, any_value(brand) AS brand, any_value(product_name) AS product_name, "
                "any_value(category) AS category, any_value(size_ml) AS size_ml, "
                "count(DISTINCT source) AS sources "
                "FROM t WHERE canon_item_id IN (%s) GROUP BY 1" % ph, list(chunk), tier=1)
            if rows is None:
                return {}
            for r in rows:
                out[int(r["canon_item_id"])] = r
        return out

    def dist_lookup(self, item_keys):
        out = {}
        for chunk in self._chunks(item_keys):
            ph = ", ".join(["?"] * len(chunk))
            rows = self._query(
                "dist_item_xwalk",
                "SELECT dist_item_key, distributor_id, canon_item_id, "
                "       any_value(distributor_name) AS distributor_name, "
                "       any_value(brand) AS brand, any_value(product_name) AS product_name, "
                "       any_value(size_raw) AS size_raw "
                "FROM t WHERE dist_item_key IN (%s) GROUP BY 1, 2, 3" % ph, list(chunk), tier=3)
            if rows is None:
                return {}
            for r in rows:
                out.setdefault(r["dist_item_key"], []).append(r)
        return out

    def signature_lookup(self, brand_keys):
        """Tier 4 pulls CANDIDATES by brand, then signatures them in Python. Pulling by brand keeps
        the query bounded by the file's brand count (hundreds), not by the master's size."""
        # nbrand is a Python normalization, so the SQL side filters on a coarse prefix and the exact
        # brand-key match happens here. Coarse-but-cheap in SQL, exact-but-local in Python.
        prefixes = sorted({k.split(" ")[0] for k in brand_keys if k})
        idx = {}
        for chunk in self._chunks(prefixes):
            ph = ", ".join(["?"] * len(chunk))
            rows = self._query(
                "_stage_product",
                "SELECT brand, product_name, size_ml, container, category, upc "
                "FROM t WHERE lower(regexp_replace(CAST(brand AS VARCHAR), '[^A-Za-z0-9 ]', '', 'g')) "
                "       SIMILAR TO '(%s)( .*)?'" % "|".join("%s" % "?" for _ in chunk),
                list(chunk), tier=4)
            if rows is None:
                # Fall back to an exact-prefix IN list; some builds don't have SIMILAR TO on the view.
                rows = self._query(
                    "_stage_product",
                    "SELECT brand, product_name, size_ml, container, category, upc FROM t "
                    "WHERE lower(CAST(brand AS VARCHAR)) IN (%s)" % ph, list(chunk), tier=4)
            if rows is None:
                return {}
            want = set(brand_keys)
            for r in rows:
                sig = signature(r.get("brand"), r.get("product_name"), r.get("size_ml"),
                                r.get("size_ml"), r.get("container"))
                if sig and sig.split("|", 1)[0] in want:
                    idx.setdefault(sig, []).append(r)
        return idx

    def status(self):
        for t in ("item_identity", "dist_item_xwalk", "_stage_product"):
            self._status.setdefault(t, {"available": None, "why": "not queried this run"})
        return self._status


# ── the resolver ───────────────────────────────────────────────────────────────────────────────

def _display(rec):
    parts = [str(rec.get("brand") or "").strip(), str(rec.get("product_name") or "").strip()]
    nm = " ".join(p for p in parts if p).strip()
    sz = rec.get("size_ml") or rec.get("size_raw")
    return ("%s %s" % (nm, ("%gml" % float(sz)) if _isnum(sz) else str(sz or ""))).strip() if sz else nm


def _isnum(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def resolve(rows, fields, master, log=None, distributor=None):
    """Resolve every row. `rows` are the MAPPED dicts (master field → their raw value).

    Returns {matches, aggregate, why, tiers, master_status} where `matches[i]` is:
        {hoodie_id, match_tier, match_method, match_confidence, matched_display, matched_category}
    and an unmatched row carries hoodie_id=None plus a `why` reason string.
    """
    fields = set(fields)
    n = len(rows)
    matches = [None] * n

    has_code = ("upc" in fields) or ("gtin" in fields)
    has_dist = "dist_item_code" in fields
    has_sig = ("brand" in fields) and ("product_name" in fields)

    # ── Tier 1 + 2: one pass to collect distinct identity keys, one bulk lookup ──
    exact, healed = {}, {}                    # row index → canon id
    bad_code = {}                             # row index → why the code can't be an identity
    if has_code:
        for i, r in enumerate(rows):
            raw = r.get("upc") or r.get("gtin") or ""
            if not str(raw).strip():
                continue
            st = upc_mod.classify(raw)
            if st in ("placeholder", "bad_check", "restricted", "coupon"):
                bad_code[i] = st              # a code that isn't an identity must not become one
                continue
            d = re.sub(r"\D", "", str(raw))
            # Tier 1 is a code that arrived COMPLETE (a real GS1 length). Tier 2 is one that only
            # became a code after the heal — that distinction is the whole reason the tiers are
            # reported separately, since Tier 2 is also a finding about their export tool.
            if len(d) in (12, 13, 14):
                cid = canon_id(d)
                if cid is not None:
                    exact[i] = cid
                    continue
            h = _healed(raw)
            if h:
                healed[i] = canon_id(h)
            elif len(d) == 8:
                bad_code[i] = "ean8"
            elif d:
                bad_code[i] = "malformed"

    hits = {}
    if exact or healed:
        hits = master.upc_lookup(sorted(set(list(exact.values()) + list(healed.values()))))

    for i, cid in exact.items():
        rec = hits.get(cid)
        if rec:
            matches[i] = _match(1, cid, rec, 1.0)
    for i, cid in healed.items():
        rec = hits.get(cid)
        if rec:
            matches[i] = _match(2, cid, rec, 1.0)

    # ── Tier 3: their own item numbers ──
    dist_note = None
    if has_dist:
        import dist_xwalk
        need = {}
        for i, r in enumerate(rows):
            if matches[i] is not None:
                continue
            k = dist_xwalk.item_key(r.get("dist_item_code"))
            if k:
                need.setdefault(k, []).append(i)
        if need:
            found = master.dist_lookup(sorted(need))
            scope, dist_note = _scope_distributor(found, distributor)
            for k, idxs in need.items():
                cands = found.get(k) or []
                if scope:
                    cands = [c for c in cands if str(c.get("distributor_id")) == scope]
                if not cands:
                    continue
                for i in idxs:
                    if matches[i] is not None:
                        continue
                    pick = _disambiguate(cands, rows[i])
                    if pick is None:
                        continue            # still several identities → we do not pick one
                    cid = pick.get("canon_item_id")
                    matches[i] = _match(3, cid, pick, 1.0,
                                        note=None if cid is not None else
                                        "matched to the distributor's own item, whose retail UPC "
                                        "we don't yet hold")

    # ── Tier 4: signature cluster ──
    sig_of = {}
    if has_sig:
        for i, r in enumerate(rows):
            if matches[i] is not None:
                continue
            s = signature(r.get("brand"), r.get("product_name"), r.get("size_raw"),
                          r.get("size_ml"), r.get("container"))
            if s:
                sig_of[i] = s
        if sig_of:
            brands = {s.split("|", 1)[0] for s in sig_of.values()}
            idx = master.signature_lookup(sorted(brands))
            for i, s in sig_of.items():
                cands = idx.get(s) or []
                ids = {canon_id(c.get("upc")) for c in cands}
                ids.discard(None)
                cont = rows[i].get("container")
                if len(ids) == 1:
                    matches[i] = _match(4, next(iter(ids)), cands[0], _sig_conf(s, cands[0], cont))
                elif len(cands) == 1 and not ids:
                    # the master knows the product but has no UPC for it — a real, partial answer
                    matches[i] = _match(4, None, cands[0], _sig_conf(s, cands[0], cont) * 0.9,
                                        note="the master carries this product without a UPC")

    # ── Tier 5: unmatched, with the WHY histogram ──
    why = {}
    for i in range(n):
        if matches[i] is not None:
            continue
        reason = _why(i, rows[i], fields, bad_code, sig_of, has_code, has_dist, has_sig)
        matches[i] = {"hoodie_id": None, "match_tier": 5, "match_method": None,
                      "match_confidence": 0.0, "matched_display": "", "matched_category": "",
                      "why": reason}
        why[reason] = why.get(reason, 0) + 1

    agg = aggregate(matches, n)
    agg["why"] = sorted(({"reason": k, "rows": v} for k, v in why.items()),
                        key=lambda d: -d["rows"])
    agg["master_status"] = master.status()
    if dist_note:
        agg["distributor"] = dist_note
    agg["capped_at_inference"] = not has_code and not has_dist
    if agg["capped_at_inference"]:
        agg["capped_note"] = ("no UPC and no item-code column found → identity resolved by "
                              "brand + size + name only, so match confidence is capped at INFERENCE")
    if log:
        log("[overlay_match] %d rows → %s%% deterministic (T1 %d / T2 %d / T3 %d), %d inference, "
            "%d unmatched" % (n, agg["deterministic_pct"], agg["tiers"]["1"], agg["tiers"]["2"],
                              agg["tiers"]["3"], agg["tiers"]["4"], agg["tiers"]["5"]))
    return {"matches": matches, "aggregate": agg}


# What a matched master record contributes downstream (S5 enrichment reads these by name).
# Carried on the match record itself so `enrich` never has to go back to the master — and so an
# enrichment column can't silently resolve empty because the field wasn't carried across.
MASTER_FIELDS = ("brand", "product_name", "category", "class_type", "varietal", "origin",
                 "supplier", "container", "size_ml", "pack", "abv", "image")


# A `dist_item_code` is only an identity WITH its distributor. Measured on the live crosswalk:
# 36% of item keys are ambiguous across distributors, and the common short numeric codes collide
# 16–26 ways — so a bare code resolves to nothing at all unless the distributor is pinned first.
# Two ways to pin it, in order of strength:
#   1. the caller says so (a rep flow, or an engagement, knows whose book this is)
#   2. INFER it from the file as a whole — with 339 books, a file of real item codes overwhelmingly
#      lands in exactly one. The inference is over the WHOLE FILE, not per row, which is what makes
#      it safe: one decision backed by hundreds of codes, reported so it can be overridden.
_INFER_MIN_KEYS = 20          # below this the file doesn't carry enough codes to identify a book
_INFER_MIN_SHARE = 0.60       # the winner must explain most of the codes that hit anything
_INFER_MIN_MARGIN = 3.0       # …and beat the runner-up decisively


def _scope_distributor(found, given=None):
    """Return (distributor_id or None, note). `found` is {item_key: [candidate, …]}."""
    if given:
        return str(given), {"distributor_id": str(given), "basis": "supplied by the caller"}
    per = {}
    for k, cands in found.items():
        for d in {str(c.get("distributor_id")) for c in cands if c.get("distributor_id")}:
            per.setdefault(d, set()).add(k)
    if len(per) <= 1:
        d = next(iter(per), None)
        return (d, {"distributor_id": d, "basis": "every matching item code belongs to one book"}) \
            if d else (None, None)
    ranked = sorted(per.items(), key=lambda kv: -len(kv[1]))
    (top, top_keys), (_, second_keys) = ranked[0], ranked[1]
    hit_keys = len({k for ks in per.values() for k in ks})
    share = len(top_keys) / max(1, hit_keys)
    margin = len(top_keys) / max(1, len(second_keys))
    if len(top_keys) >= _INFER_MIN_KEYS and share >= _INFER_MIN_SHARE and margin >= _INFER_MIN_MARGIN:
        return top, {"distributor_id": top, "basis": "inferred from the file",
                     "codes_explained": len(top_keys), "of_codes_matched": hit_keys,
                     "share": round(share, 3), "margin_over_runner_up": round(margin, 2)}
    return None, {"distributor_id": None,
                  "basis": "could not identify one distributor from these item codes — "
                           "matching only where the brand corroborates the code",
                  "candidates": len(per)}


def _disambiguate(cands, row):
    """One candidate, or None. Never a guess: several candidates collapse only when they agree on
    ONE identity, or when the row's brand leaves exactly one identity standing."""
    ids = {c.get("canon_item_id") for c in cands}
    if len(ids) == 1:
        return cands[0]
    rb = brand_key(row.get("brand"))
    if rb:
        agree = [c for c in cands if brand_key(c.get("brand")) == rb]
        if agree and len({c.get("canon_item_id") for c in agree}) == 1:
            return agree[0]
    return None


def _match(tier, cid, rec, conf, note=None):
    label, method = TIER_LABELS[tier]
    return {"hoodie_id": cid, "match_tier": tier, "match_method": method,
            "match_label": label, "match_confidence": round(conf, 3),
            "matched_display": _display(rec), "matched_category": rec.get("category") or "",
            "matched_brand": rec.get("brand") or "", "note": note,
            "master": {k: rec.get(k) for k in MASTER_FIELDS if rec.get(k) not in (None, "")}}


def _sig_conf(sig, rec, row_container=None):
    """Tier-4 confidence. Base 0.60 for a brand+name agreement; a stated, agreeing size is what
    separates a real SKU match from a product-level one, so that is what earns most of the rest."""
    conf = 0.60
    parts = sig.split("|")
    if len(parts) >= 3 and parts[2]:
        conf += 0.18                       # the file stated a size and it agreed
    if rec.get("upc"):
        conf += 0.05                       # the candidate carries an identity we can hand back
    a = re.sub(r"[^a-z]", "", str(row_container or "").lower())
    b = re.sub(r"[^a-z]", "", str(rec.get("container") or "").lower())
    if a and b:
        conf += 0.07 if a == b else -0.20   # a DISAGREEING container is the signal that matters
    return max(0.30, min(conf, 0.85))       # Tier 4 never reaches deterministic confidence


def _why(i, row, fields, bad_code, sig_of, has_code, has_dist, has_sig):
    """The unmatched reason. Every one of these is a coverage work item for us, and saying so is
    the honest version of a low match rate."""
    if i in bad_code:
        return {"placeholder": "the code is a placeholder barcode, not an identity",
                "bad_check": "the code fails its own GS1 check digit",
                "restricted": "the code is an in-store number-system code, not a global identity",
                "coupon": "the code is a coupon number-system code, not a product identity",
                "ean8": "the code is an EAN-8, shorter than the identity our master indexes",
                "malformed": "the code isn't a GS1-shaped identifier"}[bad_code[i]]
    if not has_code and not has_dist and not has_sig:
        return "no identifier, brand or product name in the file to resolve on"
    if not has_code and not has_dist:
        if i not in sig_of:
            return "no identifier, and the brand or product name is blank on this row"
        return "no identifier, and brand + size + name didn't resolve to a single item"
    if has_code and not str(row.get("upc") or row.get("gtin") or "").strip():
        return "no identifier on this row"
    if i in sig_of:
        return "not in our master yet — this is how it grows"
    if has_sig:
        return "identifier not in our master, and brand + size + name didn't form a signature"
    return "identifier not in our master yet — this is how it grows"


def aggregate(matches, n=None):
    """Match rate BY TIER — never a single blended number."""
    n = n if n is not None else len(matches)
    tiers = {str(t): 0 for t in range(1, 6)}
    for m in matches:
        tiers[str(m["match_tier"])] += 1
    det = tiers["1"] + tiers["2"] + tiers["3"]
    return {
        "rows": n, "tiers": tiers,
        "deterministic": det,
        "deterministic_pct": round(100.0 * det / n, 1) if n else 0.0,
        "inference": tiers["4"],
        "inference_pct": round(100.0 * tiers["4"] / n, 1) if n else 0.0,
        "unmatched": tiers["5"],
        "unmatched_pct": round(100.0 * tiers["5"] / n, 1) if n else 0.0,
        "identified": sum(1 for m in matches if m.get("hoodie_id") is not None),
    }


def _selftest():
    master = MemoryMaster(
        items={
            36000291452: {"brand": "Acme", "product_name": "Acme Gin", "category": "Gin",
                          "size_ml": 750, "sources": 4},
            82184090565: {"brand": "Jack Daniel's", "product_name": "Old No. 7", "category": "Whiskey",
                          "size_ml": 750, "sources": 9},
        },
        dist={"A1234": [{"dist_item_key": "A1234", "canon_item_id": 82184090565,
                         "distributor_name": "Columbia", "brand": "Jack Daniel's",
                         "product_name": "Old No. 7", "size_raw": "750 ML"}],
              "AMBIG": [{"dist_item_key": "AMBIG", "canon_item_id": 1, "brand": "X", "product_name": "X"},
                        {"dist_item_key": "AMBIG", "canon_item_id": 2, "brand": "Y", "product_name": "Y"}],
              "NOUPC": [{"dist_item_key": "NOUPC", "canon_item_id": None, "distributor_name": "Columbia",
                         "brand": "Regional", "product_name": "House Red", "size_raw": "750 ML"}]},
        catalog=[{"brand": "Tito's Handmade", "product_name": "Texas Vodka", "size_ml": 750,
                  "container": "glass", "category": "Vodka", "upc": "619947000020"},
                 {"brand": "Kendall Jackson", "product_name": "Vintner's Reserve Chardonnay",
                  "size_ml": 750, "container": "", "category": "Table Wine", "upc": ""}])

    rows = [
        {"upc": "036000291452"},                                       # T1 exact
        {"upc": "36000291452"},                                        # T2 zero-strip heal (proven)
        {"upc": "", "dist_item_code": "A-1234 "},                      # T3 their own item number
        {"upc": "", "dist_item_code": "AMBIG"},                        # T3 ambiguous → not matched
        {"upc": "", "dist_item_code": "NOUPC"},                        # T3 half-match, honestly labelled
        {"brand": "TITO HANDMADE", "product_name": "TITOS HANDMADE TEXAS VODKA",
         "size_raw": "750 ML", "container": "Glass"},                  # T4 signature
        {"upc": "000000000000"},                                       # T5 placeholder
        {"upc": "036000291453"},                                       # T5 bad check digit
        {"upc": "619947000020"},                                       # T5 a good code we don't hold yet
    ]
    fields = ["upc", "dist_item_code", "brand", "product_name", "size_raw", "container"]
    res = resolve(rows, fields, master)
    m, agg = res["matches"], res["aggregate"]

    assert m[0]["match_tier"] == 1 and m[0]["hoodie_id"] == 36000291452, m[0]
    assert m[0]["matched_display"] == "Acme Acme Gin 750ml", m[0]
    # the matched record's fields ride along, so S5 can fill a column without a second lookup
    assert m[0]["master"]["category"] == "Gin" and m[0]["master"]["size_ml"] == 750, m[0]["master"]
    assert m[1]["match_tier"] == 2 and m[1]["hoodie_id"] == 36000291452, m[1]
    assert m[2]["match_tier"] == 3 and m[2]["hoodie_id"] == 82184090565, m[2]
    # one item code pointing at two identities is ambiguous — we don't pick one
    assert m[3]["match_tier"] == 5, m[3]
    # a distributor item we know without a retail UPC is a REAL partial answer, labelled as one
    assert m[4]["match_tier"] == 3 and m[4]["hoodie_id"] is None and "don't yet hold" in m[4]["note"], m[4]
    assert m[5]["match_tier"] == 4 and m[5]["match_label"] == "INFERENCE", m[5]
    assert 0.6 <= m[5]["match_confidence"] <= 0.85, m[5]
    # the three unmatched reasons are DIFFERENT reasons, and each is stated
    assert m[6]["why"].startswith("the code is a placeholder"), m[6]
    assert "check digit" in m[7]["why"], m[7]
    assert "master" in m[8]["why"], m[8]

    assert agg["tiers"] == {"1": 1, "2": 1, "3": 2, "4": 1, "5": 4}, agg["tiers"]
    assert agg["deterministic"] == 4 and agg["deterministic_pct"] == 44.4, agg
    assert agg["unmatched"] == 4, agg
    assert len(agg["why"]) == 4 and agg["why"][0]["rows"] >= 1, agg["why"]

    # a placeholder/restricted code must NEVER become an identity, even though it parses as digits
    assert m[6]["hoodie_id"] is None
    ns2 = resolve([{"upc": "212345678909"}], ["upc"], master)["matches"][0]
    assert ns2["match_tier"] == 5 and "in-store" in ns2["why"], ns2

    # ── Tier 3 and the distributor, measured against the real failure this fixes ──
    # On the live crosswalk 36% of item keys collide across distributors (the short numeric ones
    # 16-26 ways), so a bare code resolved to NOTHING until the distributor was pinned.
    _BR = ["Tito's Handmade", "Jack Daniel's", "Herradura", "Kendall Jackson", "Veuve Clicquot"]
    collide = {}
    for d in range(1, 6):                       # code 00214 in five different books, five products
        collide.setdefault("00214", []).append(
            {"dist_item_key": "00214", "distributor_id": "D%d" % d, "canon_item_id": 1000 + d,
             "brand": _BR[d - 1], "product_name": "P%d" % d, "distributor_name": "Dist %d" % d})
    # D3 also carries 30 codes nobody else does — that is what identifies the book
    for c in ["77%03d" % i for i in range(30)]:
        collide[c] = [{"dist_item_key": c, "distributor_id": "D3", "canon_item_id": 2000 + int(c[2:]),
                       "brand": _BR[2], "product_name": "P", "distributor_name": "Dist 3"}]
    cm = MemoryMaster(dist=collide)

    # 1. a colliding code ALONE is not resolvable, and we do not pick one
    one = resolve([{"dist_item_code": "00214"}], ["dist_item_code"], cm)
    assert one["matches"][0]["match_tier"] == 5, one["matches"][0]
    assert one["aggregate"]["distributor"]["distributor_id"] is None
    assert "could not identify one distributor" in one["aggregate"]["distributor"]["basis"]

    # 2. the SAME code inside a file that identifies its book resolves — this is the fix
    book = [{"dist_item_code": c} for c in ["77%03d" % i for i in range(30)]] + \
           [{"dist_item_code": "00214"}]
    many = resolve(book, ["dist_item_code"], cm)
    note = many["aggregate"]["distributor"]
    assert note["distributor_id"] == "D3" and note["basis"] == "inferred from the file", note
    # 31 = its 30 unique codes plus the colliding one it also carries
    assert note["codes_explained"] == 31 and note["share"] >= 0.6, note
    assert many["matches"][-1]["match_tier"] == 3, many["matches"][-1]
    assert many["matches"][-1]["hoodie_id"] == 1003, many["matches"][-1]   # D3's product, not D1's
    assert many["aggregate"]["deterministic"] == 31, many["aggregate"]

    # 3. an explicit distributor pins it with no inference at all
    told = resolve([{"dist_item_code": "00214"}], ["dist_item_code"], cm, distributor="D5")
    assert told["matches"][0]["hoodie_id"] == 1005, told["matches"][0]
    assert told["aggregate"]["distributor"]["basis"] == "supplied by the caller"

    # 4. unscoped, the row's BRAND can leave exactly one identity standing
    corr = resolve([{"dist_item_code": "00214", "brand": "KENDALL JACKSON"}],
                   ["dist_item_code", "brand"], cm)
    assert corr["matches"][0]["match_tier"] == 3 and corr["matches"][0]["hoodie_id"] == 1004, corr["matches"][0]
    # …but a brand that matches none of them still does not pick one
    no = resolve([{"dist_item_code": "00214", "brand": "Unrelated"}], ["dist_item_code", "brand"], cm)
    assert no["matches"][0]["match_tier"] == 5, no["matches"][0]

    # 5. too few codes to identify a book ⇒ no inference, even if one distributor leads
    thin = resolve([{"dist_item_code": c} for c in ["77000", "77001", "00214"]], ["dist_item_code"], cm)
    assert thin["aggregate"]["distributor"]["distributor_id"] is None, thin["aggregate"]["distributor"]
    assert thin["matches"][-1]["match_tier"] == 5      # the colliding code stays unmatched

    # ── honest degradation: no identifier columns at all ⇒ capped at INFERENCE, and it says so ──
    cap = resolve([{"brand": "Tito's Handmade", "product_name": "Texas Vodka", "size_raw": "750 ml"}],
                  ["brand", "product_name", "size_raw"], master)
    assert cap["aggregate"]["capped_at_inference"] is True
    assert "capped at INFERENCE" in cap["aggregate"]["capped_note"]
    assert cap["matches"][0]["match_tier"] == 4

    # the brand key singularizes every token, so possessive/plural variants agree
    assert brand_key("TITOS HANDMADE") == brand_key("Tito's Handmade") == "tito handmade"
    assert signature("TITOS HANDMADE", "Texas Vodka", "750ml", None) == \
           signature("Tito's Handmade", "Texas Vodka", "750ml", None)

    # ── signature: the SAME product written three ways collapses to one key ──
    a = signature("TITO HANDMADE", "TITOS HANDMADE TEXAS VODKA", "750 ML", None, "Glass")
    b = signature("Tito's Handmade", "Texas Vodka Titos Handmade", None, 750, "")
    assert a == b and a, (a, b)
    # …but a different size is a different SKU, which is the entire point of matching at SKU grain
    assert signature("Tito's", "Texas Vodka", "1.75 L", None) != signature("Tito's", "Texas Vodka", "750ml", None)
    assert signature("", "Texas Vodka", "750ml", None) == ""      # no brand ⇒ no signature, no match

    # a DISAGREEING container costs confidence (a can and a bottle are not one SKU)
    agree = _sig_conf("b|n|750", {"upc": "1", "container": "glass"}, "glass")
    clash = _sig_conf("b|n|750", {"upc": "1", "container": "can"}, "glass")
    assert clash < agree - 0.2, (agree, clash)

    # ── a tier whose table is unreadable disables ONLY that tier, and says why ──
    class Broken(MemoryMaster):
        def upc_lookup(self, cids):
            self.broke = True
            return {}

        def status(self):
            s = MemoryMaster.status(self)
            s["item_identity"] = {"available": False, "why": "transient read failure", "tier": 1}
            return s

    br = Broken(items={}, dist=master.dist, catalog=master.catalog)
    r2 = resolve([{"upc": "036000291452", "dist_item_code": "A-1234"}], fields, br)
    assert r2["matches"][0]["match_tier"] == 3, r2["matches"][0]      # Tier 3 still runs
    assert r2["aggregate"]["master_status"]["item_identity"]["available"] is False

    print("overlay_match.py self-test: OK")


if __name__ == "__main__":
    _selftest()
