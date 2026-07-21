"""label_reader.py — read ONE product page / label into clean MDM fields, interactively.

This is the human-in-the-loop counterpart to the per-source catalog scrapers (total_wine.py's
`parse_product`, abc_facets.py). A steward pastes a single PDP URL (a Total Wine or ABC product page,
or any retail PDP); we fetch it — mobile-UA where the site is PerimeterX-walled, Bright-Data Unlocker
as a fallback for a walled page — and pull EVERY describing field the page structures into normalized
MDM fields, keeping the full attribute set as raw_json so nothing is lost. Optionally we also read the
label IMAGE with Claude vision (label_vision) to fill what the HTML doesn't structure (country of
ORIGIN, appellation, ABV printed on the label). Structured page data wins; vision only fills gaps; every
field carries its provenance ("structured" vs "vision"). Nothing is guessed — a field is present only if
the page or label states it.

Reads land in `label_reads` (audit trail, keyed by URL) the same way label_vision lands `label_extract`;
the master + Steward consume them like the scraped catalogs. Dispatch is by host, but the generic parser
(schema.org JSON-LD + OpenGraph + on-page spec tables) already covers most retail PDPs, so a brand-new
retailer works on day one and can be hardened into its own recipe later.

    python label_reader.py "https://www.totalwine.com/.../p/123456750"        # print the fields
    python label_reader.py "https://abcfws.com/some-product/" --vision         # + read the label image
"""
import argparse
import html as _html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# The canonical MDM field set a read fills — the union of what the catalog scrapers structure and what a
# label states. Order is presentation order (identity → attributes → appellation hierarchy → ratings →
# compliance). Anything the page carries that isn't one of these is preserved in raw_json.
CANON = ["brand", "product_name", "size", "size_options", "price", "category", "description", "image",
         "varietal", "wine_type", "style", "body", "abv", "proof", "vintage", "closure",
         "country", "state", "region", "sub_region", "appellation", "origin", "bottled_in",
         "upc", "finish", "taste", "food_pairing", "expert_rating", "customer_rating", "rating_count",
         "gov_warning"]

# Map a page/label attribute label (lower-cased, colon-stripped) onto a canonical MDM field. First match
# wins, so order the synonyms specific → general. This is the same "read the retailer's own taxonomy"
# idea as abc_facets, applied to a single PDP's spec table.
_MAP = [
    ("varietal", ("varietal", "varietal type", "grape varietal", "grape", "grape variety", "varietals",
                  "variety", "variety/style", "grape/varietal")),
    ("upc", ("upc", "barcode", "gtin", "gtin-12", "ean", "upc code")),
    ("wine_type", ("wine type", "type", "product type")),
    ("style", ("style", "wine style", "taste profile", "profile")),
    ("body", ("body",)),
    ("proof", ("proof",)),
    ("abv", ("abv", "alcohol by volume", "alcohol content", "alcohol/vol", "alcohol")),
    ("vintage", ("vintage", "vintage year")),
    ("closure", ("closure", "closure type", "cork")),
    ("sub_region", ("sub region", "sub-region", "subregion", "sub appellation", "sub-appellation")),
    ("appellation", ("appellation", "ava", "aoc", "docg", "doc", "denomination")),
    ("region", ("region", "appellation region", "wine region")),
    ("state", ("state", "province", "state/province")),
    ("country", ("country", "country of origin")),
    ("origin", ("origin", "country state", "originating country", "product of")),
    ("bottled_in", ("bottled in", "bottled by", "bottling")),
    ("size", ("size", "bottle size", "volume", "net contents", "format", "unit size")),
    ("food_pairing", ("food pairing", "food pairings", "pairings", "pairs with", "food match")),
    ("expert_rating", ("expert rating", "professional rating", "critic rating", "critic score",
                       "score", "rating (professional)", "points", "wine spectator", "james suckling",
                       "rating", "wine enthusiast", "robert parker")),
    ("finish", ("finish",)),
    ("taste", ("taste", "tasting notes", "tasting note", "flavor", "flavour", "notes", "nose", "palate")),
    ("brand", ("brand", "producer", "winery", "distillery", "brewery", "made by")),
    ("category", ("category", "department")),
]


# ── URL guard ───────────────────────────────────────────────────────────────────────────────────────
_PRIVATE = re.compile(r"^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.0\.0\.0|::1|"
                      r"172\.(1[6-9]|2\d|3[01])\.)", re.I)


def ok_url(url):
    """Only http(s) to a public host — a light SSRF guard so this authenticated endpoint can't be aimed at
    the container's own metadata/localhost. Returns (ok, reason)."""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False, "unparseable URL"
    if u.scheme not in ("http", "https"):
        return False, "only http/https URLs are supported"
    host = (u.hostname or "").strip()
    if not host or "." not in host:
        return False, "a full product-page URL is required"
    if _PRIVATE.match(host) or host.endswith(".internal") or host.endswith(".local"):
        return False, "internal hosts are not allowed"
    return True, ""


# ── fetch ───────────────────────────────────────────────────────────────────────────────────────────
def _looks_blocked(html):
    h = (html or "")[:4000].lower()
    return ("px-captcha" in h or "perimeterx" in h or "/_px/" in h or "captcha-delivery" in h or
            "access to this page has been denied" in h or "are you a human" in h or len(html or "") < 600)


def _fetch(url, mobile=False, log=print):
    """Fetch a PDP as text. Try direct first (mobile-UA for the PerimeterX chains); if that's blocked or
    thin, fall back to the Bright Data Unlocker when a key is configured (human-triggered single fetch, so
    the proxy cost is negligible — unlike a bulk crawl, which stays opt-in). Returns (html, method)."""
    from total_wine import MOBILE_UA
    ua = MOBILE_UA if mobile else DESKTOP_UA
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "text/html,*/*",
                                                   "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=35) as r:
            t = r.read().decode("utf-8", "replace")
        if t and not _looks_blocked(t):
            return t, ("direct-mobile" if mobile else "direct")
        log("[label_reader] direct fetch looked blocked/thin — trying Unlocker")
    except Exception as e:
        log("[label_reader] direct fetch failed (%s) — trying Unlocker" % str(e)[:80])
    # Bright Data Unlocker fallback (renders JS + defeats PerimeterX/Akamai). Needs a BD key.
    try:
        import menu_site as ms
        return ms._unlock(url, ms._bd_key()), "unlocker"
    except Exception as e:
        raise RuntimeError("could not fetch the page (direct blocked and no Bright Data key for the "
                           "Unlocker fallback): %s" % str(e)[:120])


# ── generic PDP parsing (JSON-LD + OpenGraph + spec tables) ──────────────────────────────────────────
class _SpecTables(HTMLParser):
    """Harvest label→value pairs from the two shapes retail PDPs use: <dt>/<dd> definition lists and
    two-cell table rows (<th>/<td> or <td>/<td>). Robust to nested markup — we accumulate text per cell."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pairs = []
        self._tag = None            # 'key' | 'val' | None
        self._buf = []
        self._row = []              # cells collected in the current <tr>
        self._dt = None

    def handle_starttag(self, tag, attrs):
        if tag == "dt":
            self._flush(); self._tag = "key"
        elif tag == "dd":
            self._flush(); self._tag = "val"
        elif tag in ("td", "th"):        # a table CELL — <th>Label</th><td>Value</td> is the common spec row
            self._flush(); self._tag = "cell"
        elif tag == "tr":
            self._row = []

    def handle_endtag(self, tag):
        if tag == "dt":
            self._dt = self._text(); self._tag = None; self._buf = []
        elif tag == "dd":
            if self._dt:
                self.pairs.append((self._dt, self._text()))
            self._dt = None; self._tag = None; self._buf = []
        elif tag in ("td", "th"):
            self._row.append(self._text()); self._tag = None; self._buf = []
        elif tag == "tr":
            if len(self._row) == 2 and self._row[0] and self._row[1]:
                self.pairs.append((self._row[0], self._row[1]))
            self._row = []
        elif tag == "dl" or tag == "table":
            self._dt = None

    def handle_data(self, data):
        if self._tag:
            self._buf.append(data)

    def _text(self):
        return re.sub(r"\s+", " ", "".join(self._buf)).strip()

    def _flush(self):
        self._buf = []


def _strip(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _html.unescape(s or ""))).strip()


def _abs(u, base):
    try:
        return urllib.parse.urljoin(base, u) if u else ""
    except Exception:
        return u or ""


def _jsonld_products(html):
    """Every schema.org Product object found in <script type=application/ld+json> (handles arrays + @graph)."""
    out = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            n = stack.pop()
            if isinstance(n, list):
                stack.extend(n)
            elif isinstance(n, dict):
                if "@graph" in n:
                    stack.extend(n["@graph"] if isinstance(n["@graph"], list) else [n["@graph"]])
                t = n.get("@type") or ""
                t = " ".join(t) if isinstance(t, list) else str(t)
                if "Product" in t or ("name" in n and "offers" in n):
                    out.append(n)
    return out


def _og(html, prop):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(prop),
                  html, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(prop),
                  html, re.I)
    return _html.unescape(m.group(1).strip()) if m else ""


def _map_attr(fields, attrs, provenance):
    """Fold harvested label→value pairs into canonical MDM fields (first synonym match wins, don't clobber)."""
    norm = {}
    for k, v in attrs.items():
        kk = re.sub(r"\s+", " ", (k or "").strip().rstrip(":").lower())
        vv = _strip(v)
        if kk and vv and kk not in norm:
            norm[kk] = vv
    for canon, syns in _MAP:
        if fields.get(canon):
            continue
        for s in syns:
            if norm.get(s):
                fields[canon] = norm[s]
                provenance[canon] = "structured"
                break


def parse_generic(html, url):
    """Parse any retail PDP into (fields, attrs, images). Structured signals in priority order:
    JSON-LD Product → OpenGraph/meta → on-page spec tables. `attrs` keeps every raw label→value pair."""
    fields, provenance, images = {}, {}, []
    attrs = {}

    # 1) JSON-LD Product (the richest, most reliable signal when present)
    for p in _jsonld_products(html):
        def g(*keys):
            for k in keys:
                if p.get(k):
                    return p[k]
            return None
        if not fields.get("product_name") and g("name"):
            fields["product_name"] = _strip(str(g("name")))
        br = g("brand")
        if not fields.get("brand") and br:
            fields["brand"] = _strip(br.get("name") if isinstance(br, dict) else str(br))
        if not fields.get("description") and g("description"):
            fields["description"] = _strip(str(g("description")))
        if not fields.get("category") and g("category"):
            fields["category"] = _strip(str(g("category")))
        img = g("image")
        for i in (img if isinstance(img, list) else [img]):
            if isinstance(i, dict):
                i = i.get("url") or i.get("contentUrl")
            if i:
                images.append(_abs(str(i), url))
        gtin = g("gtin12", "gtin13", "gtin14", "gtin", "gtin8")
        if not fields.get("upc") and gtin:
            fields["upc"] = re.sub(r"\D", "", str(gtin))
        offers = g("offers")
        off = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(off, dict) and not fields.get("price") and off.get("price"):
            fields["price"] = str(off["price"])
        rating = g("aggregateRating")
        if isinstance(rating, dict):
            if not fields.get("customer_rating") and rating.get("ratingValue"):
                fields["customer_rating"] = str(rating["ratingValue"])
            if not fields.get("rating_count") and (rating.get("reviewCount") or rating.get("ratingCount")):
                fields["rating_count"] = str(rating.get("reviewCount") or rating.get("ratingCount"))
        # schema.org additionalProperty is a clean attribute bag some retailers populate
        for ap in (p.get("additionalProperty") or []):
            if isinstance(ap, dict) and ap.get("name") and ap.get("value") is not None:
                attrs.setdefault(str(ap["name"]), str(ap["value"]))
    for k in ("product_name", "brand", "description", "category", "upc", "price",
              "customer_rating", "rating_count"):
        if fields.get(k):
            provenance[k] = "structured"

    # 2) OpenGraph / meta fallbacks
    if not fields.get("product_name"):
        t = _og(html, "og:title") or _strip(_search(r"<title[^>]*>(.*?)</title>", html))
        if t:
            fields["product_name"] = t; provenance["product_name"] = "structured"
    if not fields.get("description"):
        d = _og(html, "og:description") or _og(html, "description")
        if d:
            fields["description"] = d; provenance["description"] = "structured"
    if not fields.get("brand"):
        b = _og(html, "og:brand") or _og(html, "product:brand")
        if b:
            fields["brand"] = b; provenance["brand"] = "structured"
    if not fields.get("price"):
        pr = _og(html, "product:price:amount") or _og(html, "og:price:amount")
        if pr:
            fields["price"] = pr; provenance["price"] = "structured"
    og_img = _og(html, "og:image") or _og(html, "og:image:secure_url")
    if og_img:
        images.append(_abs(og_img, url))

    # 3) Total-Wine-style embedded attribute JSON ({"attributeName":"Region","value":"Napa"}), if present
    for an, av in re.findall(r'"attributeName":"([^"]+)","value":"([^"]+)"', html):
        attrs.setdefault(an, av)

    # 4) On-page spec tables / definition lists
    try:
        sp = _SpecTables(); sp.feed(html)
        for k, v in sp.pairs:
            if k and v and len(k) < 60 and k.lower() != v.lower():
                attrs.setdefault(k, v)
    except Exception:
        pass

    _map_attr(fields, attrs, provenance)

    # dedupe images, keep absolute http(s) only
    seen, imgs = set(), []
    for i in images:
        if i and i.startswith("http") and i not in seen:
            seen.add(i); imgs.append(i)
    if imgs and not fields.get("image"):
        fields["image"] = imgs[0]; provenance["image"] = "structured"
    return fields, attrs, imgs, provenance


def _search(pat, text, flags=re.S | re.I):
    m = re.search(pat, text, flags)
    return m.group(1) if m else ""


# ── source-specific: Total Wine (reuse the proven microdata parser) ──────────────────────────────────
def parse_total_wine(html, url):
    import total_wine
    p = total_wine.parse_product(html, url) or {}
    fields = {"brand": p.get("brand", ""), "product_name": p.get("name", ""), "size": p.get("size", ""),
              "price": str(p.get("price", "") or ""), "category": p.get("category", ""),
              "description": p.get("desc", ""), "image": p.get("img", ""), "varietal": p.get("varietal", ""),
              "wine_type": p.get("wine_type", ""), "style": p.get("style", ""), "body": p.get("body", ""),
              "abv": p.get("abv", ""), "country": p.get("country", ""), "state": p.get("state", ""),
              "region": p.get("region", ""), "sub_region": p.get("sub_region", ""),
              "appellation": p.get("appellation", ""), "origin": p.get("origin", ""),
              "finish": p.get("finish", ""), "taste": p.get("taste", ""),
              "food_pairing": p.get("food_pairing", ""), "expert_rating": p.get("expert_rating", "")}
    fields = {k: v for k, v in fields.items() if v}
    provenance = {k: "structured" for k in fields}
    try:
        attrs = json.loads(p.get("raw_json") or "{}")
    except Exception:
        attrs = {}
    imgs = [p["img"]] if p.get("img") else []
    return fields, attrs, imgs, provenance


# ── vision merge (fill gaps the HTML doesn't structure) ──────────────────────────────────────────────
# label_vision.extract → CANON name. HTML wins where present; vision only fills empties.
_VISION_MAP = {"product_name": "product_name", "brand": "brand", "origin": "origin",
               "bottled_in": "bottled_in", "region": "region", "sub_region": "sub_region",
               "appellation": "appellation", "varietal": "varietal", "vintage": "vintage",
               "abv": "abv", "upc": "upc", "net_contents": "size", "gov_warning": "gov_warning"}


def _merge_vision(fields, provenance, image_url, log=print):
    try:
        import label_vision
        v = label_vision.extract(image_url)
    except Exception as e:
        log("[label_reader] vision skipped: %s" % str(e)[:120])
        return {"ok": False, "error": str(e)[:160]}
    filled = []
    for vk, canon in _VISION_MAP.items():
        val = v.get(vk)
        if val in (None, "", False):
            continue
        val = str(val) if not isinstance(val, bool) else ("yes" if val else "")
        if val and not fields.get(canon):
            fields[canon] = val
            provenance[canon] = "vision"
            filled.append(canon)
    return {"ok": True, "filled": filled, "raw": v}


# ── the read ─────────────────────────────────────────────────────────────────────────────────────────
def read(url, vision=False, land=True, log=print):
    """Read a single PDP/label URL into MDM fields. Returns the full result dict (also the /api/label/read
    body). Raises ValueError on a bad URL, RuntimeError if the page can't be fetched."""
    url = (url or "").strip()
    ok, why = ok_url(url)
    if not ok:
        raise ValueError(why)
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    is_tw = "totalwine.com" in host
    html, method = _fetch(url, mobile=is_tw, log=log)
    if is_tw:
        fields, attrs, imgs, provenance = parse_total_wine(html, url)
        if not fields.get("product_name"):                          # microdata missed — fall back to generic
            fields, attrs, imgs, provenance = parse_generic(html, url)
        source = "total-wine"
    else:
        fields, attrs, imgs, provenance = parse_generic(html, url)
        source = re.sub(r"^www\.", "", host)

    vision_info = None
    if vision and (fields.get("image") or imgs):
        vision_info = _merge_vision(fields, provenance, fields.get("image") or imgs[0], log=log)

    # normalize to CANON order, drop empties
    fields = {k: fields[k] for k in CANON if fields.get(k)}
    warnings = []
    if not fields.get("product_name") and not fields.get("brand"):
        warnings.append("No product identity parsed — the page may be JS-only or blocked; try the vision "
                        "read, or this host may need its own recipe.")

    result = {"ok": True, "url": url, "source": source, "host": host, "method": method,
              "fields": fields, "provenance": provenance, "attrs": attrs, "images": imgs,
              "vision": vision_info, "warnings": warnings, "ts": int(time.time())}
    if land:
        try:
            _land(result)
        except Exception as e:
            log("[label_reader] land failed (non-fatal): %s" % str(e)[:120])
    return result


def _land(result):
    """Persist the read to `label_reads` (audit trail, keyed by URL → latest wins), mirroring how
    label_vision lands label_extract. Flat columns for the canonical fields + raw_json/provenance."""
    f = result["fields"]
    rec = {"url": result["url"], "source": result["source"], "host": result["host"],
           "method": result["method"], "vision": bool(result.get("vision", {}) and result["vision"].get("ok")),
           "raw_json": json.dumps(result["attrs"], separators=(",", ":"))[:60000],
           "provenance_json": json.dumps(result["provenance"], separators=(",", ":")),
           "ts": result["ts"]}
    for k in CANON:
        rec[k] = f.get(k, "")
    warehouse.write_accumulate("label_reads", [rec], key=lambda r: r["url"])


def recent(limit=50):
    """Recent reads for the history strip. Empty list if the table doesn't exist yet."""
    try:
        rows = warehouse.query("label_reads",
                               "SELECT url, source, brand, product_name, image, vision, ts FROM t "
                               "ORDER BY ts DESC LIMIT %d" % int(limit))
        return rows
    except Exception:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read one PDP/label URL into MDM fields.")
    ap.add_argument("url")
    ap.add_argument("--vision", action="store_true", help="also read the label image with Claude vision")
    ap.add_argument("--no-land", action="store_true", help="don't persist to label_reads")
    a = ap.parse_args(argv)
    r = read(a.url, vision=a.vision, land=not a.no_land)
    print("source: %s  ·  method: %s" % (r["source"], r["method"]))
    for k, v in r["fields"].items():
        print("  %-14s %s  [%s]" % (k, str(v)[:70], r["provenance"].get(k, "?")))
    if r["warnings"]:
        print("warnings:", "; ".join(r["warnings"]))
    print("(%d raw attributes, %d images)" % (len(r["attrs"]), len(r["images"])))


if __name__ == "__main__":
    main()
