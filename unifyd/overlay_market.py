"""overlay_market.py — S7/S8: what the join to canon just made visible about THEIR brands.

This is the half of the output that isn't about their file at all. Once rows carry a Hoodie ID they
join to the observation layer we collect ourselves, and the result is the only part of the returned
workbook they cannot produce from their own systems — which is exactly why it is the part that
sells. It is also the part most able to embarrass us, so the bar is high and it is enforced in code:

  EVERY claim carries n, geography, freshness and method.
  A BLOCK BELOW ITS BAR IS ABSENT, NOT PADDED. Silence over stretch, always. A rendered block with
  a shrug in it is worse than no block: the reader has no way to tell which of our claims are the
  careful ones, so they discount all of them.
  NEGATIVE CLAIMS ARE STRIPPED IN BRAND MODE. A widget embedded on a brand's own site must never
  badge that brand's accounts "high price" — the filtering is server-side (`mode="brand"`), not a
  front-end flag, because a front-end flag is a leak waiting to happen.

The observation side is injected the same way the master is, so the render bars are provable with
no DuckDB in the loop.
"""
import re
import time

# ── render bars. Deliberately conservative; every one of these is a reason a block stays silent.
MIN_MATCHED_ROWS = 5           # fewer matched rows than this and we know too little about the file
MIN_OBS_STORES = 12            # a breadth claim under this many observed stores is anecdote
MIN_MENU_N = 25                # a serve-pattern share under this many menus is noise
MIN_PRICE_REFS = 8             # a price percentile needs a distribution, not a handful of points
MAX_STALE_DAYS = 30            # an observation older than this doesn't support a present-tense claim
FRESH_TARGET_DAYS = 7          # what we'd like to be able to say


def _days_since(d):
    if not d:
        return None
    s = str(d)[:10]
    try:
        t = time.mktime(time.strptime(s, "%Y-%m-%d"))
    except (ValueError, OverflowError):
        return None
    return max(0, int((time.time() - t) / 86400))


def _fresh(days):
    if days is None:
        return "unknown"
    return "≤%dd" % max(1, days)


class Observations(object):
    """What the market read needs. Implemented over the warehouse in production, over lists in
    tests. Every method returns [] rather than raising — an unavailable layer must read as "no
    block", never as a broken run."""

    def shelf(self, canon_ids):
        """retail_observations rows for these identities: {canon_item_id, source, store_id, city,
        state, price, in_stock, date}."""
        return []

    def category_shelf(self, categories, geo):
        """The comparison pool: the same cut for every item in these categories in this geography."""
        return []

    def menus(self, brands):
        """menu_beverages rows mentioning these brands: {account, city, state, name, root, sub,
        base_spirit, date}."""
        return []

    def status(self):
        return {}


class MemoryObs(Observations):
    def __init__(self, shelf=None, menus=None, category=None):
        self._shelf, self._menus, self._cat = shelf or [], menus or [], category or []

    def shelf(self, canon_ids):
        want = set(canon_ids)
        return [r for r in self._shelf if r.get("canon_item_id") in want]

    def category_shelf(self, categories, geo):
        cats = {str(c).lower() for c in categories}
        return [r for r in self._cat
                if str(r.get("category", "")).lower() in cats
                and (not geo or str(r.get("city", "")).lower() == str(geo).lower())]

    def menus(self, brands):
        keys = [b for b in brands if b]
        out = []
        for r in self._menus:
            hay = ("%s %s" % (r.get("name", ""), r.get("description", ""))).lower()
            for b in keys:
                if b.lower() in hay:
                    out.append(dict(r, _brand=b))
                    break
        return out

    def status(self):
        return {"retail_observations": {"available": True, "rows": len(self._shelf)},
                "menu_beverages": {"available": True, "rows": len(self._menus)}}


class WarehouseObs(Observations):
    """The production observation layer. Each table is probed independently; a table that can't be
    read silences ONLY the blocks that need it."""

    def __init__(self, warehouse_mod=None, days=90):
        import warehouse as _w
        self.w = warehouse_mod or _w
        self.days = days
        self._status = {}

    def _q(self, table, sql, params=None, parts=False):
        try:
            fn = self.w.query_parts if parts else self.w.query
            rows = fn(table, sql, params)
            self._status.setdefault(table, {"available": True})
            return rows or []
        except Exception as e:
            self._status[table] = {"available": False, "why": str(e)[:140]}
            return []

    def shelf(self, canon_ids):
        ids = [int(c) for c in canon_ids if c is not None]
        if not ids:
            return []
        out = []
        for i in range(0, len(ids), 4000):
            chunk = ids[i:i + 4000]
            ph = ", ".join(["?"] * len(chunk))
            out += self._q("retail_observations",
                           "SELECT CAST(regexp_replace(CAST(upc AS VARCHAR),'[^0-9]','','g') AS BIGINT) "
                           "         AS canon_item_id, "
                           "       source, store_id, chain, price, in_stock, date "
                           "FROM t WHERE upc IS NOT NULL "
                           "  AND length(regexp_replace(CAST(upc AS VARCHAR),'[^0-9]','','g')) >= 11 "
                           "  AND CAST(regexp_replace(CAST(upc AS VARCHAR),'[^0-9]','','g') AS BIGINT) "
                           "      IN (%s) "
                           "  AND date >= CAST(current_date - INTERVAL %d DAY AS VARCHAR)" % (ph, self.days),
                           chunk, parts=True)
        return self._geo(out)

    def _geo(self, rows):
        """Attach city/state from `src_outlets` on (source, store_id) — the same join the locator
        uses. Rows that don't resolve keep a blank geography and are excluded from any geo-scoped
        claim rather than silently pooled into one."""
        keys = sorted({(str(r.get("source") or ""), str(r.get("store_id") or ""))
                       for r in rows if r.get("store_id")})
        if not keys:
            return rows
        idx = {}
        for i in range(0, len(keys), 2000):
            chunk = keys[i:i + 2000]
            ph = ", ".join(["(?, ?)"] * len(chunk))
            flat = [v for pair in chunk for v in pair]
            for g in self._q("src_outlets",
                             "SELECT source, CAST(store_id AS VARCHAR) AS store_id, city, state "
                             "FROM t WHERE (source, CAST(store_id AS VARCHAR)) IN (%s)" % ph, flat):
                idx[(str(g.get("source") or ""), str(g.get("store_id") or ""))] = g
        for r in rows:
            g = idx.get((str(r.get("source") or ""), str(r.get("store_id") or ""))) or {}
            r["city"], r["state"] = g.get("city") or "", g.get("state") or ""
        return rows

    def category_shelf(self, categories, geo):
        cats = [str(c) for c in categories if c]
        if not cats:
            return []
        ph = ", ".join(["?"] * len(cats))
        sql = ("SELECT o.price AS price, o.store_id AS store_id, o.source AS source, o.date AS date "
               "FROM t o WHERE o.price IS NOT NULL "
               "  AND o.date >= CAST(current_date - INTERVAL %d DAY AS VARCHAR)" % self.days)
        return self._q("retail_observations", sql, None, parts=True)

    def menus(self, brands):
        keys = [str(b) for b in brands if b]
        if not keys:
            return []
        where = " OR ".join(["lower(name) LIKE ?"] * len(keys))
        return self._q("menu_beverages",
                       "SELECT account, name, description, root, sub, base_spirit, category "
                       "FROM t WHERE %s" % where, ["%%%s%%" % k.lower() for k in keys])

    def status(self):
        for t in ("retail_observations", "menu_beverages", "src_outlets"):
            self._status.setdefault(t, {"available": None, "why": "not queried this run"})
        return self._status


# ── blocks ─────────────────────────────────────────────────────────────────────────────────────

def _block(id_, title, claim, n, geo, fresh, method, detail=None, tone="neutral"):
    return {"id": id_, "title": title, "claim": claim, "n": n, "geography": geo,
            "freshness": fresh, "method": method, "detail": detail or [], "tone": tone}


def _who_you_are(matched, rows, fields):
    """Block 1 — 'we know exactly who this is'. Pure arithmetic over their own file plus our
    resolved identities; no observation layer needed, so it renders whenever anything matched."""
    ids = {m["hoodie_id"] for m in matched if m.get("hoodie_id") is not None}
    brands = {str(r.get("brand") or m.get("matched_brand") or "").strip().lower()
              for r, m in zip(rows, matched)}
    brands.discard("")
    if not ids and not brands:
        return None
    return _block("who_you_are", "Who you are, resolved",
                  "%d of your %d rows resolve to %d distinct items across %d brands."
                  % (sum(1 for m in matched if m.get("hoodie_id") is not None), len(rows),
                     len(ids), len(brands)),
                  n=len(rows), geo="your file", fresh="this run",
                  method="deterministic count over the resolved identities",
                  tone="neutral")


def _breadth(matched, obs, mode):
    """Block 3 — tracked-shelf presence. Counted, never modelled: 'N of M tracked shelves' is a
    count of stores WE observed, and the phrase 'tracked' is doing real work."""
    ids = sorted({m["hoodie_id"] for m in matched if m.get("hoodie_id") is not None})
    if len(ids) < 1:
        return None
    rows = obs.shelf(ids)
    if not rows:
        return None
    stores = {(r.get("source"), r.get("store_id")) for r in rows if r.get("store_id")}
    if len(stores) < MIN_OBS_STORES:
        return None
    days = min([d for d in (_days_since(r.get("date")) for r in rows) if d is not None] or [None])
    if days is None or days > MAX_STALE_DAYS:
        return None
    cities = {}
    for r in rows:
        c = (r.get("city") or "").strip()
        if c:
            cities.setdefault(c, set()).add((r.get("source"), r.get("store_id")))
    top = sorted(cities.items(), key=lambda kv: -len(kv[1]))[:5]
    geo = ", ".join(c for c, _ in top) if top else "tracked stores"
    return _block("breadth", "Where your items are on shelf",
                  "Your matched items appear on %d tracked shelves%s."
                  % (len(stores), (" across %d cities" % len(cities)) if cities else ""),
                  n=len(stores), geo=geo, fresh=_fresh(days),
                  method="distinct (source, store) pairs in our own retail observations, "
                         "joined on the resolved identity",
                  detail=[{"city": c, "tracked_shelves": len(s)} for c, s in top],
                  tone="positive")


_TOKEN = re.compile(r"[a-z0-9]+")


def _serve(matched, rows, obs, mode):
    """Block 4 — the serve. "X% of <brand> drinks on tracked menus are <sub-family>". The share is
    a deterministic aggregate over menu observations; the only judgement is the brand-name match,
    and that is exact substring containment, stated as the method.

    The headline names the brand the AGGREGATE IS ABOUT — not the file's most common brand. Those
    are routinely different (a file is mostly one brand, the menus mostly mention another), and
    naming the wrong one would attach a real number to the wrong subject, which is the exact class
    of error this whole surface cannot afford.
    """
    # key = their spelling lowercased (that is what the menu text is matched against);
    # value = the DISPLAY spelling, preferring the master's properly-cased name over an
    # ALL-CAPS export, because the claim is a sentence someone will read out loud.
    brands = {}
    for r, m in zip(rows, matched):
        b = str(r.get("brand") or m.get("matched_brand") or "").strip()
        if len(b) < 4:
            continue
        disp = str(m.get("matched_brand") or "").strip() or (b.title() if b.isupper() else b)
        k = b.lower()
        if k not in brands or m.get("matched_brand"):
            brands[k] = disp
    if not brands:
        return None
    top = list(brands)[:12]
    hits = obs.menus(top)
    if len(hits) < MIN_MENU_N:
        return None

    # attribute every hit to the brand it actually mentions, then aggregate WITHIN that brand
    per_brand = {}
    for h in hits:
        hay = ("%s %s" % (h.get("name", ""), h.get("description", ""))).lower()
        b = h.get("_brand") or next((k for k in top if k in hay), None)
        if b:
            per_brand.setdefault(b, []).append(h)
    if not per_brand:
        return None
    lead_brand, brand_hits = max(per_brand.items(), key=lambda kv: len(kv[1]))
    if len(brand_hits) < MIN_MENU_N:
        return None

    fam = {}
    for h in brand_hits:
        k = (h.get("sub") or h.get("root") or h.get("category") or "").strip()
        if k:
            fam[k] = fam.get(k, 0) + 1
    if not fam:
        return None
    total = sum(fam.values())
    lead, lead_n = max(fam.items(), key=lambda kv: kv[1])
    share = round(100.0 * lead_n / total)
    accounts = {h.get("account") for h in brand_hits if h.get("account")}
    days = min([d for d in (_days_since(h.get("date")) for h in brand_hits) if d is not None] or [None])
    geo = ", ".join(sorted({h.get("city") for h in brand_hits if h.get("city")})[:4]) or "tracked menus"
    return _block("serve", "How your brands are served",
                  "%d%% of the %s drinks we see on tracked menus are served as a %s."
                  % (share, brands[lead_brand], lead),
                  n=len(accounts) or len(brand_hits), geo=geo, fresh=_fresh(days),
                  method="share of classified menu items whose name contains the brand, over menus "
                         "we collected ourselves (n = distinct accounts carrying %s)" % brands[lead_brand],
                  detail=[{"serve": k, "share_pct": round(100.0 * v / total), "items": v}
                          for k, v in sorted(fam.items(), key=lambda kv: -kv[1])[:6]],
                  tone="neutral")


def _price_position(matched, rows, fields, obs, mode):
    """Block 5 — price position. A PERCENTILE against the observed local distribution, never a
    '% off' — a deep cut on an inflated everyday can still sit above the area median (the
    locator's load-bearing rule, applied here)."""
    if "price" not in fields or mode == "brand":
        # In brand mode this block can only produce a negative claim about the brand's own
        # accounts, so it is not rendered at all — the filtering is here, server-side.
        return None
    pairs = []
    for r, m in zip(rows, matched):
        if m.get("hoodie_id") is None:
            continue
        try:
            p = float(re.sub(r"[^0-9.]", "", str(r.get("price") or "")))
        except ValueError:
            continue
        if p > 0:
            pairs.append((m["hoodie_id"], p))
    if len(pairs) < MIN_PRICE_REFS:
        return None
    obs_rows = obs.shelf([pid for pid, _ in pairs])
    ref = {}
    for o in obs_rows:
        p = o.get("price")
        if p is None:
            continue
        try:
            ref.setdefault(o.get("canon_item_id"), []).append(float(p))
        except (TypeError, ValueError):
            continue
    scored = []
    for pid, p in pairs:
        pool = sorted(ref.get(pid) or [])
        if len(pool) < MIN_PRICE_REFS:
            continue
        below = sum(1 for x in pool if x < p)
        scored.append((pid, p, round(100.0 * below / len(pool)), len(pool)))
    if len(scored) < MIN_PRICE_REFS:
        return None
    med = sorted(s[2] for s in scored)[len(scored) // 2]
    days = min([d for d in (_days_since(o.get("date")) for o in obs_rows) if d is not None] or [None])
    return _block("price_position", "Where your prices sit",
                  "Your listed prices sit at the %dth percentile of what we observe on shelf for "
                  "the same items." % med,
                  n=len(scored), geo="tracked stores", fresh=_fresh(days),
                  method="per-item percentile of your price within the observed shelf-price "
                         "distribution for the SAME resolved identity — a percentile, not a % off",
                  detail=[{"hoodie_id": pid, "your_price": p, "percentile": pc, "reference_points": n}
                          for pid, p, pc, n in sorted(scored, key=lambda s: -s[2])[:8]],
                  tone="neutral")


def _coverage_note(matched, obs):
    """Not a claim — the honest counterweight. What we could NOT say, and why."""
    ids = {m["hoodie_id"] for m in matched if m.get("hoodie_id") is not None}
    return {"resolved_identities": len(ids),
            "observation_layers": obs.status(),
            "bars": {"min_matched_rows": MIN_MATCHED_ROWS, "min_observed_stores": MIN_OBS_STORES,
                     "min_menus": MIN_MENU_N, "min_price_reference_points": MIN_PRICE_REFS,
                     "max_stale_days": MAX_STALE_DAYS}}


def report(rows, fields, matched, obs, mode="consumer", max_blocks=6, log=None):
    """Assemble the join report. Blocks that clear their bar, in order; nothing else.

    `mode`: "consumer" (the full verdict) | "brand" (every negative claim stripped, server-side).
    """
    fields = set(fields)
    n_matched = sum(1 for m in matched if m.get("hoodie_id") is not None)
    blocks, withheld = [], []

    if n_matched < MIN_MATCHED_ROWS:
        withheld.append({"id": "*", "why": "only %d rows resolved to an identity (bar is %d) — "
                                           "there isn't enough here to say anything about the market"
                                           % (n_matched, MIN_MATCHED_ROWS)})
        return {"blocks": [], "withheld": withheld, "mode": mode, "coverage": _coverage_note(matched, obs)}

    for name, fn in (("who_you_are", lambda: _who_you_are(matched, rows, fields)),
                     ("breadth", lambda: _breadth(matched, obs, mode)),
                     ("serve", lambda: _serve(matched, rows, obs, mode)),
                     ("price_position", lambda: _price_position(matched, rows, fields, obs, mode))):
        try:
            b = fn()
        except Exception as e:                       # a block that errors is a block that's absent
            withheld.append({"id": name, "why": "could not be computed: %s" % str(e)[:100]})
            continue
        if b:
            blocks.append(b)
        else:
            withheld.append({"id": name, "why": _why_absent(name, mode)})

    if mode == "brand":
        blocks = [b for b in blocks if b.get("tone") != "negative"]

    blocks = blocks[:max_blocks]
    if log:
        log("[overlay_market] %d blocks rendered, %d withheld (mode=%s)"
            % (len(blocks), len(withheld), mode))
    return {"blocks": blocks, "withheld": withheld, "mode": mode,
            "coverage": _coverage_note(matched, obs)}


_ABSENT = {
    "who_you_are": "nothing in the file resolved to a brand or an identity",
    "breadth": "fewer than %d tracked shelves carry these items, or the observations are older "
               "than %d days — either way the claim wouldn't be supportable" % (MIN_OBS_STORES, MAX_STALE_DAYS),
    "serve": "fewer than %d tracked menus mention these brands" % MIN_MENU_N,
    "price_position": "no price column, or fewer than %d observed reference prices per item"
                      % MIN_PRICE_REFS,
}


def _why_absent(name, mode):
    if name == "price_position" and mode == "brand":
        return "price position is not shown in brand mode — it can only produce a negative claim " \
               "about the brand's own accounts"
    return _ABSENT.get(name, "below its render bar")


def _selftest():
    rows = [{"brand": "Jim Beam", "product_name": "Bourbon", "price": "24.99"} for _ in range(20)]
    matched = [{"hoodie_id": 80686001409 + (i % 3), "matched_brand": "Jim Beam"} for i in range(20)]
    fields = ["brand", "product_name", "price"]
    today = time.strftime("%Y-%m-%d")

    # ── empty observation layer: blocks are ABSENT with a reason, never padded ──
    r = report(rows, fields, matched, MemoryObs())
    ids = {b["id"] for b in r["blocks"]}
    assert ids == {"who_you_are"}, ids
    why = {w["id"]: w["why"] for w in r["withheld"]}
    assert "tracked shelves" in why["breadth"] and "tracked menus" in why["serve"], why

    # ── breadth renders once enough distinct stores clear the bar, with n/geo/freshness ──
    shelf = [{"canon_item_id": 80686001409 + (i % 3), "source": "kroger", "store_id": "S%d" % i,
              "city": "Miami" if i % 2 else "Tampa", "state": "FL", "price": 25.0 + (i % 7),
              "date": today} for i in range(30)]
    r2 = report(rows, fields, matched, MemoryObs(shelf=shelf))
    b = next(x for x in r2["blocks"] if x["id"] == "breadth")
    assert b["n"] == 30 and "Miami" in b["geography"] and b["freshness"].startswith("≤"), b
    assert "tracked" in b["claim"] and "method" in b and b["method"], b

    # a stale layer supports no present-tense claim
    old = [dict(s, date="2020-01-01") for s in shelf]
    assert not any(x["id"] == "breadth" for x in report(rows, fields, matched, MemoryObs(shelf=old))["blocks"])
    # …and neither does one below the store bar
    thin = shelf[:MIN_OBS_STORES - 1]
    assert not any(x["id"] == "breadth" for x in report(rows, fields, matched, MemoryObs(shelf=thin))["blocks"])

    # ── the serve stat reproduces exactly from the raw observations ──
    menus = ([{"account": "Bar %d" % i, "city": "Miami", "name": "Jim Beam Highball",
               "sub": "Highball", "date": today} for i in range(12)]
             + [{"account": "Bar %d" % (100 + i), "city": "Miami", "name": "Jim Beam Sour",
                 "sub": "Sour", "date": today} for i in range(18)])
    r3 = report(rows, fields, matched, MemoryObs(shelf=shelf, menus=menus))
    sb = next(x for x in r3["blocks"] if x["id"] == "serve")
    assert sb["n"] == 30, sb                       # n = distinct accounts, and it says so
    # the headline is the LEADING serve (18/30 Sour = 60%); Highball is 12/30 = 40% in the detail
    assert "60%" in sb["claim"] and "Sour" in sb["claim"] and "Jim Beam" in sb["claim"], sb["claim"]
    assert sb["detail"][0]["serve"] == "Sour" and sb["detail"][0]["share_pct"] == 60, sb["detail"]
    assert sb["detail"][1]["serve"] == "Highball" and sb["detail"][1]["share_pct"] == 40, sb["detail"]
    # the headline names the brand the AGGREGATE is about, not the file's most common brand:
    # a file dominated by a brand nobody pours must not borrow another brand's number
    mixed_rows = [{"brand": "Nonesuch Vodka", "product_name": "X"} for _ in range(40)] + rows
    mixed_m = [{"hoodie_id": 1, "matched_brand": "Nonesuch Vodka"} for _ in range(40)] + matched
    mb = next(x for x in report(mixed_rows, fields, mixed_m,
                                MemoryObs(shelf=shelf, menus=menus))["blocks"] if x["id"] == "serve")
    assert "Jim Beam" in mb["claim"] and "Nonesuch" not in mb["claim"], mb["claim"]

    # a brand not on any menu produces no block at all
    assert not any(x["id"] == "serve" for x in
                   report([dict(r, brand="Nonesuch") for r in rows], fields,
                          [dict(m, matched_brand="Nonesuch") for m in matched],
                          MemoryObs(shelf=shelf, menus=menus))["blocks"])

    # ── price position is a PERCENTILE, and it is stripped in brand mode ──
    r4 = report(rows, fields, matched, MemoryObs(shelf=shelf))
    pp = next((x for x in r4["blocks"] if x["id"] == "price_position"), None)
    assert pp and "percentile" in pp["claim"], pp
    assert "not a % off" in pp["method"], pp["method"]
    r5 = report(rows, fields, matched, MemoryObs(shelf=shelf), mode="brand")
    assert not any(x["id"] == "price_position" for x in r5["blocks"])
    assert any("brand mode" in w["why"] for w in r5["withheld"]), r5["withheld"]

    # ── too little matched: the whole report withholds, and says why in one line ──
    r6 = report(rows[:2], fields, matched[:2], MemoryObs(shelf=shelf))
    assert r6["blocks"] == [] and "isn't enough here" in r6["withheld"][0]["why"], r6

    # ── a block that raises is absent, not fatal ──
    class Boom(MemoryObs):
        def shelf(self, ids):
            raise RuntimeError("observation layer down")

    r7 = report(rows, fields, matched, Boom())
    assert any(w["id"] == "breadth" and "could not be computed" in w["why"] for w in r7["withheld"]), r7
    assert any(b["id"] == "who_you_are" for b in r7["blocks"])      # the rest still renders

    # every rendered block carries the four required stamps, always
    for rep in (r2, r3, r4):
        for b in rep["blocks"]:
            assert b["n"] and b["geography"] and b["freshness"] and b["method"], b

    print("overlay_market.py self-test: OK")


if __name__ == "__main__":
    _selftest()
