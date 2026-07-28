#!/usr/bin/env python3
"""ubereats.py — UberEats alcohol/grocery + restaurant pull via our OWN headless Chromium (browser_warm).

NO Bright Data, NO manual cookie: a real Chromium on the Mac's residential IP loads the UberEats first-party BFF
(`www.ubereats.com/_p/api/*V*`) with no Forter block. A persistent profile keeps the delivery ZONE sticky, so
each run jumps straight to a set location and reads the merchant feed + per-store catalogs.

Why UberEats (Chris/Wes): the retailer/restaurant marks items UP on the aggregator to cover the platform fee, so
the UberEats price vs. the retailer's DIRECT price is the deliverable — the effective take-rate. So every record
carries channel="ubereats"; reconcile the merchant to the same hoodie_outlet as the direct feed and the delta
falls out. UberEats also hands us the UPC (GTIN) + promo + availability per item — rich enough for master matching.

Flow: prime the zone (once, persists) → getFeedV1 → /store/<slug>/<uuid> merchants → per-store
getCatalogPresentationV1 (item list) [+ getMenuItemV1 for the full UPC/promo detail]. parse_item() targets the
getMenuItemV1 schema (the richest); the catalog list is a lighter subset of the same fields.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_warm
try:
    from sku_match import norm_upc
except Exception:
    def norm_upc(u):
        d = re.sub(r"\D", "", str(u or ""))
        return d or None

API = "https://www.ubereats.com/_p/api/"
# A zone (delivery location) encoded in the `pl=` param — loads that market's feed even on a COLD profile (no
# sticky uev2.loc yet). This one = Metrowest Blvd, Orlando FL (28.5159561,-81.4512635). Swap per market.
ZONE_ORLANDO = ("https://www.ubereats.com/feed?diningMode=DELIVERY&pl=JTdCJTIyYWRkcmVzcyUyMiUzQSUyMk1ldHJvd2Vz"
                "dCUyMEJsdmQlMjIlMkMlMjJyZWZlcmVuY2UlMjIlM0ElMjJ1MDIlM0EwMSUzQTAxJTNBOTA3MDYzZmUtNjU1Ny1hNDQ4"
                "LTc5NDctZjBkNWYxNmNiZDRlJTIyJTJDJTIycmVmZXJlbmNlVHlwZSUyMiUzQSUyMnViZXJfcGxhY2VzJTIyJTJDJTIy"
                "bGF0aXR1ZGUlMjIlM0EyOC41MTU5NTYxJTJDJTIybG9uZ2l0dWRlJTIyJTNBLTgxLjQ1MTI2MzUlN0Q%3D")

# Postmates runs on the SAME Uber BFF (identical getStoreV1/getMenuItemV1) — only the domain differs. So the whole
# recipe is reused; `site` swaps the domain, feed base, and the `pl=` zone (same base64 location works on both).
_PL = ZONE_ORLANDO.split("pl=", 1)[1]
SITES = {
    "ubereats":  {"domain": "ubereats.com",  "base": "https://www.ubereats.com",
                  "zone": ZONE_ORLANDO},
    "postmates": {"domain": "postmates.com", "base": "https://postmates.com",
                  "zone": "https://postmates.com/feed?diningMode=DELIVERY&pl=" + _PL},
}
_CUR = {"base": "https://www.ubereats.com", "domain": "ubereats.com", "source": "ubereats"}


def _clear_challenge(p, log=print, tries=12):
    """Cloudflare/Uber interstitial ('Just a moment' / 'security check') auto-clears for a real headful render —
    but takes a few seconds. Poll until the real feed (store links) appears or the challenge text is gone."""
    for _ in range(tries):
        body = p.evaluate("() => document.body ? document.body.innerText.slice(0,80) : ''") or ""
        if "store/" in "".join(a.get_attribute("href") or "" for a in p.query_selector_all('a[href*="/store/"]')[:1]):
            return True
        if not re.search(r"just a moment|security (check|verification)|performing", body, re.I):
            return True
        p.wait_for_timeout(2500)
    log("  [ubereats] challenge did not clear (headless? datacenter IP scored down?)")
    return False
_ABV_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*%\s*ABV", re.I)
_PACK_RE = re.compile(r"(\d{1,3})\s*(?:ct|pk|pack|count)\b", re.I)
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|fl\s*oz|oz|liter)\b", re.I)
# getMenuItemV1 carries whole OTHER-item recommendation blocks (upsell/cross-sell/customizations of unrelated
# products). Those are noise for THIS item's record — prune before we keep the raw so raw_json is this item, lossless.
_RAW_PRUNE = ("itemsUpsell", "crossSellSection", "membershipUpsellPromotion", "customizationsList",
              "customizationDetailsItems", "tagSection", "adaptedDisclaimer", "boostPromotion")


def _dig(d, *path, default=None):
    """Safe nested get: _dig(d,'a','b',0,'c'). Ints index lists; anything else keys dicts. Missing → default."""
    cur = d
    for k in path:
        if isinstance(k, int):
            if isinstance(cur, list) and -len(cur) <= k < len(cur):
                cur = cur[k]
            else:
                return default
        elif isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur if cur is not None else default


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _cents(v):
    n = _num(v)
    return round(n / 100.0, 2) if n is not None else None


_PREV_PRICE_RE = re.compile(r"previous price was\s*\$?([\d,]+\.\d{2})", re.I)
_PRICE_TOK_RE = re.compile(r"^\$?([\d,]+\.\d{2})\s*$")


def _rich_texts(node):
    """Yield (text, decorations_list) for every richTextElements token in a catalog object. The getStoreV1 catalog
    puts price/list-price/size/ABV in itemThumbnailElements rich text (with STRIKE_THROUGH on the was-price)."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            t = _dig(node, "text", "text", "text") or _dig(node, "text", "text")
            dec = _dig(node, "text", "predefinedDecorations") or []
            if isinstance(t, str):
                yield t, (dec or [])
        for v in node.values():
            yield from _rich_texts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _rich_texts(v)


def _overlay_tags(node):
    """Yield promo/badge tag texts from imageOverlayElements/tagSection (e.g. '9% off', 'Popular')."""
    for k in ("imageOverlayElements", "tagSection", "endorsementTags"):
        sub = node.get(k)
        if sub:
            for t, _dec in _tags_in(sub):
                yield t


def _tags_in(node):
    if isinstance(node, dict):
        if node.get("text") and (node.get("style") or node.get("hierarchy") or "tag" in str(node.get("type") or "")):
            yield node["text"], node.get("style")
        for v in node.values():
            yield from _tags_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from _tags_in(v)


def _stock_label(d):
    """Uber's qualitative stock bucket ('Many in stock' / 'Few left' / 'Out of stock') — the 'in stock' flag on the
    storefront tile. Lives in the catalog object's `endorsement.text` and/or a tag `identifier=item_availability_tag`
    (fed from the retailer's inventory feed, bucketed — not a raw count; pair with maxPermittedNumber for the qty)."""
    e = _dig(d, "endorsement", "text")
    if isinstance(e, str) and e.strip():
        return e.strip()
    hit = [""]

    def walk(n):
        if hit[0]:
            return
        if isinstance(n, dict):
            if n.get("identifier") == "item_availability_tag" and isinstance(n.get("text"), str):
                hit[0] = n["text"].strip(); return
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(d)
    return hit[0]


def _catalog_extras(d):
    """Mine the getStoreV1 CATALOG shape for fields the flat getMenuItemV1 keys don't carry here: the struck-through
    list price, the promo tag/uuid, the 'SIZE • X% ABV' descriptor, and the stock-bucket label. Best-effort dict."""
    out = {}
    out["stock_label"] = _stock_label(d)
    strike, cur, prev, descr = None, None, None, None
    for t, dec in _rich_texts(d):
        m = _PRICE_TOK_RE.match(t.strip())
        if m:
            val = float(m.group(1).replace(",", ""))
            if any("STRIKE" in str(x).upper() for x in dec):
                strike = strike or val
            else:
                cur = cur if cur is not None else val
        pm = _PREV_PRICE_RE.search(t)
        if pm:
            prev = float(pm.group(1).replace(",", ""))
        if "ABV" in t.upper() or ("•" in t and re.search(r"\d", t)):
            descr = descr or t
    out["list_price"] = strike or prev
    out["descriptor"] = descr or ""
    tags = [t for t in _overlay_tags(d)]
    promo_tag = next((t for t in tags if re.search(r"%\s*off|deal|save|bogo|\bfor\b", t, re.I)), "")
    out["promo_tag"] = promo_tag
    out["badges"] = tags
    out["promo_uuid"] = _dig(d, "promoInfo", "promotionUUID") or _dig(d, "promoInfo", "promotionUuid") or ""
    out["promo_type"] = _dig(d, "catalogItemAnalyticsData", "promoType") or ""
    out["avail_state"] = d.get("itemAvailabilityState") or ""
    out["analytics_label"] = d.get("analyticsLabel") or ""
    return out


def _all_gtins(idents):
    """Every GTIN string on the item (deduped, digits only) — retailers list the pack GTIN + unit GTIN + twins."""
    out, seen = [], set()
    for pi in (idents or []):
        if pi.get("type") == "GTIN" and pi.get("value"):
            v = re.sub(r"\D", "", str(pi["value"]))
            if v and v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _best_gtin(idents):
    """Pick the valid GTIN from productIdentifiers[] (payloads carry a good one + a shifted/malformed twin)."""
    cands = _all_gtins(idents)
    for v in cands:                       # prefer one that normalizes cleanly (valid check digit / sane length)
        if norm_upc(v):
            return v
    return cands[0] if cands else ""


def parse_item(d, store_uuid=None, store_name=None):
    """One getMenuItemV1 (or catalog item) `data` object → our channel record — FULL capture. Prices are in CENTS.

    We pull EVERYTHING the payload carries into columns (identity, all barcodes, price + promo mechanics, the
    availability/quantity layer, unit/measure, attributes, media, descriptors) and keep the pruned raw alongside,
    so nothing that could bound/build the master is left buried in a blob. Fields absent on the lighter getStoreV1
    catalog objects just come back empty."""
    price = d.get("price")
    pre = d.get("priceBeforeDiscount")
    desc = d.get("itemDescription") or ""
    title = (d.get("title") or "").strip()

    # the catalog (getStoreV1) shape differs from getMenuItemV1: list price / promo / ABV / size live in nested
    # rich-text + overlay tags, not the flat keys. Mine them so a catalog-only item is still fully captured.
    ex = _catalog_extras(d)
    nut = d.get("nutritionalInfo") or ex.get("descriptor") or ""
    abv = _ABV_RE.search(nut) or _ABV_RE.search(desc) or _ABV_RE.search(title)
    packm = _PACK_RE.search(nut) or _PACK_RE.search(title)
    sizem = _SIZE_RE.search(nut) or _SIZE_RE.search(title)

    # promo mechanics — flat getMenuItemV1 (v1/v2) with catalog rich-text/overlay as fallback
    promo = d.get("itemPromotion") or {}
    pv2 = d.get("itemPromotionV2") or {}
    offer = pv2.get("offerInfo") or {}
    promo_tag = (_dig(pv2, "promoTag", "text", default="") or ex.get("promo_tag") or "")
    promo_uuid = (_dig(pv2, "appliedPromotion", "uuid") or _dig(pv2, "availablePromotion", "uuid")
                  or _dig(pv2, "appliedPromotion", "promotionUuid") or ex.get("promo_uuid") or "")
    list_price = _cents(pre) if pre else ex.get("list_price")
    on_promo = bool((list_price and price and list_price > (price / 100.0))
                    or promo_tag or ex.get("promo_uuid") or ex.get("promo_type"))

    # the availability / quantity layer — the closest thing to real inventory Uber exposes. maxPermittedNumber is
    # min(on-hand, purchase-limit): for inventory-syncing retailers it TRACKS on-hand; a round default (100/250)
    # means the retailer doesn't feed qty. lowAvailabilityLabel populates near stockout ("Only N left").
    opt = _dig(d, "purchaseInfo", "purchaseOptions", 0, default={}) or {}
    qc = opt.get("quantityConstraintsV2") or opt.get("quantityConstraints") or {}
    max_qty = _num(qc.get("maxPermittedNumber"))
    min_qty = _num(qc.get("minPermittedNumber"))
    incr_qty = _num(qc.get("incrementNumber"))
    default_qty = _num(qc.get("defaultQuantityNumber"))
    # soldByUnit / pricedByUnit are {measurementType: "MEASUREMENT_TYPE_COUNT"} objects — pull the type string
    sold_by = _unit(opt.get("soldByUnit")) or _unit(_dig(d, "purchaseInfo", "pricingInfo", "soldByUnit"))
    priced_by = _unit(_dig(d, "purchaseInfo", "pricingInfo", "pricedByUnit"))
    is_sold_out = bool(d.get("isSoldOut")) or (d.get("itemAvailabilityState") in ("SOLD_OUT", "UNAVAILABLE"))
    suspend = d.get("suspendReason") or ""
    low_avail = d.get("lowAvailabilityLabel") or ""

    endorse = [t.get("text") for t in (d.get("endorsementTags") or []) if isinstance(t, dict) and t.get("text")]
    dietary = _dig(d, "itemAttributeInfo", "dietaryLabels", default=[]) or []
    classif = d.get("classifications") or []
    rules = d.get("rules") or ""
    is_alc = bool(d.get("numAlcoholicItems") or "21 or older" in rules
                  or ex.get("analytics_label") == "alcohol_item")

    raw = {k: v for k, v in d.items() if k not in _RAW_PRUNE}

    return dict(
        # identity
        item_uuid=d.get("uuid") or "", name=title,
        product_uuid=(_dig(d, "productInfo", "productUuid") or d.get("productUuid") or ""),
        section=d.get("sectionUuid") or "", subsection=d.get("subsectionUuid") or "",
        # the retailer's OWN category labels — a maintained product hierarchy, free with the crawl
        section_name=d.get("sectionName") or "", subsection_name=d.get("subsectionName") or "",
        category_path=" > ".join([x for x in (d.get("sectionName"), d.get("subsectionName")) if x]),
        # barcodes (best valid + everything)
        upc=_best_gtin(d.get("productIdentifiers")),
        gtins="|".join(_all_gtins(d.get("productIdentifiers"))),
        # price + promo mechanics
        price=_cents(price), list_price=list_price, on_promo=on_promo,
        discount=round(list_price - (price / 100.0), 2) if (on_promo and list_price and price) else 0.0,
        promo_text=(promo.get("description") or promo.get("title") or promo_tag or "")[:200],
        promo_tag=promo_tag[:80], promo_type=(promo.get("type") or ex.get("promo_type") or ""),
        promo_pct=_num(offer.get("discountPercentage")),
        promo_flat=_cents(offer.get("discountFlatAmount")),
        promo_uuid=promo_uuid,
        # availability / quantity layer
        in_stock=(not is_sold_out) and not suspend,
        is_sold_out=is_sold_out, suspend_reason=suspend[:80],
        suspend_until=str(d.get("suspendUntil") or ""), low_availability=low_avail[:80],
        avail_state=ex.get("avail_state", ""), stock_label=ex.get("stock_label", ""),
        max_qty=max_qty, min_qty=min_qty, increment_qty=incr_qty, default_qty=default_qty,
        sold_by=sold_by[:40], priced_by=priced_by[:40],
        # attributes
        is_alcohol=is_alc, num_alcoholic=_num(d.get("numAlcoholicItems")),
        age_rule=rules[:120],
        abv=(float(abv.group(1)) if abv else None),
        pack=(int(packm.group(1)) if packm else None),
        item_size=(sizem.group(0) if sizem else ""),
        nutritional_info=nut[:160],
        classifications="|".join(str(c) for c in classif)[:160],
        dietary_labels="|".join(str(x) for x in dietary)[:160],
        endorsements="|".join(endorse + ex.get("badges", []))[:200],
        description=desc[:400],
        # media
        image=d.get("imageUrl") or "",
        image_count=len(d.get("images") or ([d["imageUrl"]] if d.get("imageUrl") else [])),
        # provenance
        store_uuid=store_uuid or "", store_name=store_name or "",
        raw_json=json.dumps(raw, separators=(",", ":"))[:16000])


def _unit(v):
    """soldByUnit/pricedByUnit are {measurementType: 'MEASUREMENT_TYPE_COUNT'} — return the short type string."""
    if isinstance(v, dict):
        return str(v.get("measurementType") or "").replace("MEASUREMENT_TYPE_", "")
    return str(v or "").replace("MEASUREMENT_TYPE_", "")


# ── merchant name → is this an ALCOHOL retailer? (dedicated liquor/wine + convenience/drug that carry beer/wine)
# Deliberately NOT grocery front pages (Winn-Dixie/Seabra) — their alcohol is behind a category, and the front
# catalog is food. Dedicated alcohol merchants' getStoreV1 IS the alcohol shelf.
_ALCOHOL_RE = re.compile(r"liquor|wine|spirit|beer|bottle|package|abc fine|total wine|bevmo|binny|"
                         r"7.?eleven|wawa|circle k|cvs|walgreens|discount bev|brew|cellar|vine", re.I)
_RETAIL_RE = _ALCOHOL_RE      # crawl targets alcohol merchants by default
# DEDICATED liquor store → its whole getStoreV1 catalog IS alcohol. Convenience/drug (CVS/Wawa/Walgreens) return
# a MIXED catalog, so there we flag alcohol per-item by a bev-alc name match (drops the lemonade/snacks).
_LIQUOR_STORE_RE = re.compile(r"liquor|wine|spirit|abc fine|total wine|bevmo|binny|cellar|package|bottle shop|"
                              r"discount bev|vineyard|\bvine\b|winery|distiller", re.I)
_BEVALC_RE = re.compile(r"\b(beer|wine|vodka|whiskey|whisky|bourbon|scotch|tequila|rum|gin|brandy|cognac|"
                        r"liqueur|seltzer|lager|ipa|ale|stout|pilsner|champagne|prosecco|rose|rosé|merlot|"
                        r"cabernet|chardonnay|pinot|sauvignon|malbec|riesling|moscato|sake|cider|mead|"
                        r"vermouth|aperitif|schnapps|mezcal|hard\s+(?:seltzer|tea|cider)|malt\s+beverage)\b", re.I)


def _feed_stores(page):
    """Merchant list from the rendered feed: [(slug, uuid, name, href)] via the /store/<slug>/<uuid> links."""
    rows = page.eval_on_selector_all(
        'a[href*="/store/"]',
        """els => Array.from(new Map(els.map(e => [e.getAttribute('href').split('?')[0],
              (e.innerText||'').split('\\n')[0]])).entries())""")
    out = []
    for href, name in rows:
        m = re.search(r"/store/([^/]+)/([A-Za-z0-9_\-]+)$", href)
        if m:
            out.append((m.group(1), m.group(2), (name or "").strip(), _CUR["base"] + href))
    return out


def crawl(zone_url=None, max_stores=8, retail_only=True, enrich=True, max_items_enrich=250, site="ubereats", log=print):
    """Prime the sticky zone, read the feed, pull each (retail) store's catalog → parsed items with channel price.
    Returns (stores, items). Uses the PROVEN no-BD recipe: real Chrome + headful + a human CLICK-THROUGH from the
    feed (deep-linking a store URL trips reCAPTCHA's 'security check'; a trusted click from the feed clears it).
    Catalog list = getStoreV1; per-item detail (UPC/promo) = getMenuItemV1 (fires on item click — sampled)."""
    cfg = SITES.get(site, SITES["ubereats"])
    _CUR.update(base=cfg["base"], domain=cfg["domain"], source=site)
    zone_url = zone_url or cfg["zone"]
    captured = {"store": [], "items": [], "mi_req": None}
    # Off the Mac (Fly headful runner) the datacenter IP fails UberEats botdefense — route the browser
    # through the residential proxy. resi.browser() is None when unconfigured (Mac uses its own IP), so
    # this is a no-op locally. Validated 2026-07-23: headful Chrome + Webshare residential → getFeedV1 200.
    try:
        import resi
        _proxy = resi.browser()
    except Exception:
        _proxy = None
    with browser_warm.Warmer(cfg["domain"], channel="chrome", headful=True, proxy=_proxy) as w:
        ctx = w._ctx
        p = w._page()

        def on_resp(r):
            u = r.url
            if "/_p/api/getStoreV1" in u:
                try: captured["store"].append(r.json())
                except Exception: pass
            elif "/_p/api/getMenuItemV1" in u:
                try: captured["items"].append(r.json())
                except Exception: pass

        def on_req(r):
            # learn the getMenuItemV1 request the app itself makes (url + JSON body + the x-uber-* headers Uber's
            # bot-defense keys on). Replaying those headers is what lets our POST clear the reCAPTCHA challenge.
            if "/_p/api/getMenuItemV1" in r.url and captured["mi_req"] is None:
                try:
                    hdrs = {k: v for k, v in dict(r.headers).items()
                            if k.lower().startswith("x-uber-") or k.lower() == "x-csrf-token"}
                    captured["mi_req"] = {"url": r.url, "body": json.loads(r.post_data or "{}"), "headers": hdrs}
                except Exception:
                    captured["mi_req"] = {"url": r.url, "body": {}, "headers": {}}
        ctx.on("response", on_resp)
        ctx.on("request", on_req)

        p.goto(zone_url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(5000)
        _clear_challenge(p, log)          # ride out Cloudflare's "just a moment" (headful clears it)
        w.human()
        # scroll the feed to load EVERY merchant (infinite scroll) — not just the initial render
        prev = 0
        for i in range(60):
            p.mouse.wheel(0, 9000); p.wait_for_timeout(1100)
            n = len(_feed_stores(p))
            if n == prev and i > 4:
                break
            prev = n
        stores = _feed_stores(p)
        if retail_only:
            stores = [s for s in stores if _RETAIL_RE.search(s[2])] or stores
        log("[ubereats] zone feed: %d merchants (retail-filtered)" % len(stores))

        items = []
        for slug, uuid, name, href in stores[:max_stores]:
            captured["store"].clear(); captured["items"].clear()
            try:
                # be on the feed, then TRUSTED-CLICK the store card (never goto the store URL directly). A slow
                # Cloudflare challenge can hold navigation past the timeout — tolerate it and use whatever loaded.
                if "/feed" not in p.url:
                    try:
                        p.goto(zone_url, wait_until="commit", timeout=45000)
                    except Exception:
                        pass
                    p.wait_for_timeout(3000); _clear_challenge(p, log)
                if not _feed_stores(p):                    # feed didn't render (scored down) — give it another beat
                    p.wait_for_timeout(4000); _clear_challenge(p, log)
                w.human(3)
                if not w.click_through(uuid):             # match the card by its uuid in the href
                    log("  %-32s card not found" % name[:32]); continue
                got = _items_from_store(captured["store"], uuid, name)
                # ENRICH: getStoreV1 is light (title+price, no UPC/qty). Click one item to learn the app's real
                # getMenuItemV1 request, then replay it per item for the FULL detail (UPC + qty + everything).
                enriched = {}
                if got and (enrich or enrich is None):
                    idx = _catalog_index(captured["store"])
                    _click_first_item(p, log)             # fires one real getMenuItemV1 → template + first detail
                    p.wait_for_timeout(2500)
                    req = captured["mi_req"]
                    if req and req.get("body"):
                        first = _menu_item_data(captured["items"][0]) if captured["items"] else None
                        known = {"url": req["url"], "index": idx, "headers": req.get("headers", {}),
                                 "item": (first or {}).get("uuid"),
                                 "section": (first or {}).get("sectionUuid"),
                                 "subsection": (first or {}).get("subsectionUuid"),
                                 "store": uuid}
                        enriched = enrich_store(w, uuid, name, got, req["body"], known,
                                                max_items=max_items_enrich, log=log)
                    else:
                        log("  %-32s no getMenuItemV1 template (item click didn't fire) — catalog only" % name[:24])
                    # also fold in any getMenuItemV1 detail that fired from CLICKS (richest — has the UPC)
                    for det in captured["items"]:
                        data = _menu_item_data(det)
                        if data and data.get("uuid"):
                            enriched[data["uuid"]] = parse_item(data, uuid, name)
                merged = [enriched.get(r["item_uuid"], r) for r in got]   # prefer full detail, fall back to catalog
                liquor = bool(_LIQUOR_STORE_RE.search(name))   # dedicated liquor store → whole catalog is alcohol
                for it in merged:
                    it["is_alcohol"] = it["is_alcohol"] or liquor or bool(_BEVALC_RE.search(it.get("name", "")))
                items.extend(merged)
                log("  %-32s %d items (%d enriched)%s" % (name[:32], len(merged), len(enriched),
                    " (STORE WALLED)" if not merged and not captured["store"] else ""))
                p.go_back(); p.wait_for_timeout(2500)
            except Exception as e:
                log("  %-32s ERR %s" % (name[:32], str(e)[:60]))
        return stores, items


def _click_first_item(p, log=print):
    """Click the first product tile on a rendered store page so the app fires one real getMenuItemV1 (our template).
    Uber's store grid tiles are role=button / product links; try a few stable selectors."""
    for sel in ('a[href*="/store/"][href*="/item/"]', '[data-testid*="store-item"]',
                'div[role="button"] img', 'li div[role="button"]', 'div[role="button"]'):
        try:
            el = p.query_selector(sel)
            if el:
                el.scroll_into_view_if_needed(); p.wait_for_timeout(400); el.click(); return True
        except Exception:
            continue
    log("    [enrich] no product tile matched to seed the template")
    return False


def _items_from_store(payloads, store_uuid, store_name):
    """Walk captured getStoreV1 (or getMenuItemV1) payloads → item objects → parse_item. Items nest under
    catalog sections; we take every object with a title + numeric price."""
    out, seen = [], set()
    for pl in payloads:
        for obj in _iter_item_objs(pl):
            u = obj.get("uuid")
            if not u or u in seen:
                continue
            seen.add(u)
            rec = parse_item(obj, store_uuid, store_name)
            if rec["name"]:
                out.append(rec)
    return out


def _iter_item_objs(node):
    """Recursively yield dicts that look like catalog items (have a title and a numeric price)."""
    if isinstance(node, dict):
        if node.get("title") and isinstance(node.get("price"), (int, float)):
            yield node
        for v in node.values():
            yield from _iter_item_objs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_item_objs(v)


def _catalog_index(payloads):
    """From getStoreV1 payloads build item_uuid → {section, subsection, section_name, subsection_name}.

    The uuids are what getMenuItemV1 needs in its request body. The NAMES are the free product hierarchy:
    this walk already descends the retailer's own category tree ("Beer / Craft IPA"), and until now it
    kept the opaque uuids and threw the labels away — paying the whole traversal cost to discard the one
    part a human or a model can use. A breadcrumb is a taxonomy the retailer maintains for us; dropping
    it is the same discard-nothing violation as dropping a field.
    """
    idx = {}
    names = {}                                             # uuid → title, for resolving section refs

    def collect_names(node):
        if isinstance(node, dict):
            u, t = node.get("uuid"), node.get("title")
            if u and t and not isinstance(node.get("price"), (int, float)):
                names.setdefault(u, t)                     # a titled node that isn't a priced item = a section
            for v in node.values():
                collect_names(v)
        elif isinstance(node, list):
            for v in node:
                collect_names(v)

    def walk(node, section="", subsection=""):
        if isinstance(node, dict):
            sec = node.get("sectionUuid") or section        # inherit the nearest enclosing section/subsection
            sub = node.get("subsectionUuid") or subsection
            if node.get("uuid") and node.get("title") and isinstance(node.get("price"), (int, float)):
                idx.setdefault(node["uuid"], {"section": sec, "subsection": sub,
                                              "section_name": names.get(sec, ""),
                                              "subsection_name": names.get(sub, "")})
            for v in node.values():
                walk(v, sec, sub)
        elif isinstance(node, list):
            for v in node:
                walk(v, section, subsection)

    for pl in payloads:
        collect_names(pl)                                  # names first — a section may appear after its items
    for pl in payloads:
        walk(pl)
    return idx


def _fill_template(template_body, store_uuid, item_uuid, section, subsection, known):
    """Take the getMenuItemV1 request body captured from a real click (`known` = that click's own
    uuids) and produce the body for another item by swapping the uuid-valued fields. Robust to key renames:
    we replace by VALUE (the known item/section/subsection uuids) rather than by hard-coded key names."""
    b = json.loads(json.dumps(template_body))  # deep copy
    swaps = {known.get("item"): item_uuid, known.get("section"): section,
             known.get("subsection"): subsection, known.get("store"): store_uuid}
    swaps = {k: v for k, v in swaps.items() if k and v is not None}

    def sub(node):
        if isinstance(node, dict):
            return {k: sub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [sub(v) for v in node]
        return swaps.get(node, node)
    return sub(b)


def _menu_item_data(resp):
    """getMenuItemV1 response → the item `data` object (schema nests it under data/menuItem/item)."""
    for path in (("data",), ("data", "menuItem"), ("data", "item"), ("menuItem",), ("item",)):
        obj = _dig(resp, *path)
        if isinstance(obj, dict) and obj.get("uuid") and obj.get("title"):
            return obj
    # fall back: first title+price dict anywhere
    return next(iter(_iter_item_objs(resp)), None)


def enrich_store(w, store_uuid, store_name, catalog_recs, template, known, max_items=250, log=print):
    """Upgrade catalog items (title+price, no UPC) to FULL getMenuItemV1 detail (UPC + qty + everything) by
    replaying the app's own getMenuItemV1 request per item via in-page POST. Needs a captured request `template`
    (body) + `known` (the template click's own uuids) to swap by value. Returns enriched records keyed by uuid."""
    if not template or not known.get("item"):
        return {}
    import uuid as _uuid
    url = known.get("url") or (API + "getMenuItemV1")
    idx = known.get("index") or {}
    base_hdrs = dict(known.get("headers") or {})     # the app's x-uber-* set — clears Uber's bot-defense
    out = {}
    n = 0
    stat = {}                    # status-code histogram — diagnoses why replays miss (auth? shape? rate?)
    sample_miss = None
    for rec in catalog_recs:
        iu = rec.get("item_uuid")
        if not iu or n >= max_items:
            continue
        ctx = idx.get(iu, {})
        body = _fill_template(template, store_uuid, iu, ctx.get("section", ""), ctx.get("subsection", ""), known)
        hdrs = dict(base_hdrs)
        hdrs["x-uber-request-id"] = str(_uuid.uuid4())    # unique per request, like the app
        try:
            status, resp = w.post_json(url, body, headers=hdrs)
        except Exception as e:
            log("    enrich POST err %s" % str(e)[:50]); break
        stat[status] = stat.get(status, 0) + 1
        if status == 200 and resp:
            data = _menu_item_data(resp)
            if data:
                out[iu] = parse_item(data, store_uuid, store_name)
            elif sample_miss is None:
                sample_miss = ("200-no-item", list(resp.keys()) if isinstance(resp, dict) else str(resp)[:120])
        elif sample_miss is None:
            sample_miss = (status, str(resp)[:120])
        n += 1
        if n % 25 == 0:
            log("    enriched %d/%d (status %s) …" % (n, min(len(catalog_recs), max_items), stat))
        w._page().wait_for_timeout(350)   # polite: ~3/s, spread the detail fetches
    log("  [enrich] %s: %d/%d → full detail | status %s%s" % (
        store_name[:24], len(out), n, stat, (" | miss=%s" % (sample_miss,)) if sample_miss else ""))
    return out


UE_FIELDS = ["item_uuid", "product_uuid", "store_uuid", "store_name", "name", "section", "subsection",
             "section_name", "subsection_name", "category_path",
             "upc", "gtins", "price", "list_price", "on_promo", "discount", "promo_text", "promo_tag",
             "promo_type", "promo_pct", "promo_flat", "promo_uuid", "in_stock", "is_sold_out", "suspend_reason",
             "suspend_until", "low_availability", "avail_state", "stock_label", "max_qty", "min_qty",
             "increment_qty", "default_qty",
             "sold_by", "priced_by", "is_alcohol", "num_alcoholic", "age_rule", "abv", "pack", "item_size",
             "nutritional_info", "classifications", "dietary_labels", "endorsements", "description",
             "image", "image_count", "zone", "raw_json"]


def land(items, zone="orlando", site="ubereats", log=print):
    """Persist the pull: <site>_products catalog + the dated price time-series. source=site IS the CHANNEL —
    paired with the direct feed's source it yields the aggregator markup. (UberEats + Postmates share the Uber
    BFF but land to separate channel tables.)"""
    import warehouse
    import observe
    for it in items:
        it["zone"] = zone
        # coerce any nested field (list/dict) to a JSON string so Parquet has a stable scalar column — full
        # capture (gtins/classifications/dietary_labels/nutritional_info/endorsements) is kept, not dropped.
        for k in UE_FIELDS:
            v = it.get(k)
            if isinstance(v, (list, dict)):
                it[k] = json.dumps(v, separators=(",", ":"), default=str)
    warehouse.write_accumulate("%s_products" % site, items,
                               key=lambda r: (r.get("store_uuid"), r.get("item_uuid")), fields=UE_FIELDS)
    observe.record(site, [dict(source=site, store_id=i.get("store_uuid", ""),
                                     store=i.get("store_name", ""), product_id=i.get("item_uuid", ""),
                                     upc=i.get("upc", ""), brand="", name=i.get("name", ""),
                                     price=i.get("price"), on_promo=bool(i.get("on_promo")),
                                     in_stock=bool(i.get("in_stock")),
                                     qty=i.get("max_qty"),            # bounded on-hand proxy = min(on-hand, buy-limit)
                                     stock_level=(i.get("stock_label") or i.get("low_availability") or ""),
                                     is_hemp=False)
                                for i in items if i.get("price") is not None], log=log)
    log("[ubereats] landed %d items -> ubereats_products + retail_observations (channel=ubereats)" % len(items))


def main(argv=None):
    ap = argparse.ArgumentParser(description="UberEats channel pull (no Bright Data — our own real Chrome).")
    ap.add_argument("--site", default="ubereats", choices=["ubereats", "postmates"], help="Uber-BFF channel")
    ap.add_argument("--zone", default=None, help="override the feed zone URL (pl=)")
    ap.add_argument("--max-stores", type=int, default=6)
    ap.add_argument("--all-merchants", action="store_true", help="don't filter to retail/grocery")
    ap.add_argument("--no-enrich", action="store_true", help="catalog only; skip per-item getMenuItemV1 detail")
    ap.add_argument("--max-items-enrich", type=int, default=250, help="per-store cap on detail fetches (politeness)")
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    stores, items = crawl(zone_url=a.zone, max_stores=a.max_stores, retail_only=not a.all_merchants,
                          enrich=not a.no_enrich, max_items_enrich=a.max_items_enrich, site=a.site)
    if items and not a.no_land:
        try:
            land(items, site=a.site)
        except Exception as e:
            print("land skipped:", str(e)[:90])
    alc = [i for i in items if i["is_alcohol"]]
    print(json.dumps({"merchants": len(stores), "items": len(items), "alcohol_items": len(alc),
                      "with_upc": sum(1 for i in items if i["upc"]),
                      "with_qty": sum(1 for i in items if i.get("max_qty") is not None),
                      "with_low_avail": sum(1 for i in items if i.get("low_availability")),
                      "on_promo": sum(1 for i in items if i["on_promo"]),
                      "sample": items[:3]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
