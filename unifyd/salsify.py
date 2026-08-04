#!/usr/bin/env python3
"""salsify.py — Salsify Sites: the PLATFORM recipe for every public catalog on sites.salsify.com.

WHY THIS IS A PLATFORM RECIPE (not a one-off scrape):
  Salsify is the PIM that a large slice of CPG brands and beverage-alcohol wholesalers publish their
  public catalog from, and every one of those catalogs is the SAME Next.js SSG app under
  `sites.salsify.com/<orgId>/<siteId>/`. Prove the shape once and every site on the platform is free:

      root HTML     -> __NEXT_DATA__ : buildId + catalogName + totalProducts/totalPages + facets
      list page 1   -> _next/data/<buildId>/index.json                       (page 1 is index, NOT products/1)
      list page N>1 -> _next/data/<buildId>/products/<N>.json?params=products&params=<N>
      product       -> _next/data/<buildId>/product/<id>/<slug>.json?id=<id>&title=<slug>
      sitemap       -> <site>/sitemap_1.xml   (when published: the whole id/slug universe in one fetch)

  AND THE URL LETS US LOOP IT. `https://sites.salsify.com/robots.txt` publishes
  `sitemap_index.xml` — a live directory of every PUBLIC catalog on the platform (519 sites across
  118 orgs as of 2026-08-03). `discover()` walks it, probes each root, and lands the directory to
  `salsify_catalogs`; `pull(catalog=...)` then crawls any one of them with the same code.

WHAT WE KEEP: everything the site exposes. The catalogs do NOT share a property namespace — BBG
  publishes SAP wholesaler attributes (Material ID, Supplier, Bottles Per Case, SAP Varietal…),
  Sazerac publishes a full GS1/GDSN item master (tradeItemgtin, percentageOfAlcoholByVolume,
  alcoholProof, net content + UOM, weights/dimensions, ingredients, nutrients, allergens, closure,
  country of origin, even the TTB COLA id). A fixed column set would drop most of that, so capture is
  two-grained ([[retailer-full-capture]] — grain governs where data is modelled, never whether it's kept):

    salsify_products    — one row per product: the canonical spine + the bev-alc fields we map across
                          namespaces. Accumulating catalog (write_accumulate), thin columns only.
    salsify_properties  — one row per (product, property, value): EVERY property of every property set,
                          every filter facet, every digital asset, every value of a multi-value field —
                          whatever the catalog happens to call them. Append-only + date-partitioned,
                          exactly like raw_payloads, because a property value is an EVENT (what the
                          source said today), not a mutable attribute ([[payloads-are-events]]). Only
                          products whose property fingerprint MOVED are re-written, so a daily cadence
                          costs the diff, not the catalog.
    salsify_catalogs    — the platform directory: every public site, its org/site ids, name, size.

BREAKTHRU (`bbg`): the seed and the reason for the split. `id` / `Material ID` IS Breakthru's
  distributor item code (their SAP material number) and `subtitle` / `Marketing Name` IS their own item
  description — both are landed under those names (`dist_item_code`, `item_description`) instead of being
  flattened into `id`/`brand` the way the superseded bbg_salsify.py did.

GRAIN: one row per product per catalog. Identity = catalog_id | product_id. Resumable (skips products
  already detailed), polite, stdlib-only, headless. Self-reports `degraded` — never bad data — when the
  site reports products but we enumerate none, when detail fetches collapse, or when identity/property
  fill drops off a cliff.

CLI:  python salsify.py --catalog bbg                 # full BBG catalog
      python salsify.py --catalog bbg --pages 5       # smoke (5 list pages)
      python salsify.py --discover                    # refresh the public-catalog directory
      python salsify.py --discover --probe-all        # …and probe every site (slower, richer)
      python salsify.py --list                        # seeded catalogs
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOST = "https://sites.salsify.com"
SITEMAP_INDEX = HOST + "/sitemap_index.xml"
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}

TABLE = "salsify_products"
PROPS_TABLE = "salsify_properties"
CAT_TABLE = "salsify_catalogs"
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "salsify")

# Seeded catalogs — the ones we deliberately pull. Everything else on the platform is DISCOVERED
# (see discover()); promote a discovered site here to put it on a cadence. `daily` marks the ones the
# platform pass runs; `bbg` has its own registry source so the pass skips it.
CATALOGS = {
    "bbg": dict(org="1cb19d26-0728-4fb1-afe0-3b8c309b6180",
                site="d28396be-a4b8-4891-9533-c40780422895",
                label="Breakthru Beverage Group", owner="BBG", tier="distributor", daily=True),
    "sazerac": dict(org="e9e3150b-a658-485d-981c-0fe8acbba350",
                    site="2e138396-e536-42bf-ac68-21d3ee08c95b",
                    label="Sazerac Active Consumer Units", owner="Sazerac", tier="supplier", daily=True),
    "heaven-hill": dict(org="bdf90657-7017-4b79-9030-f3fd0c5c46cc",
                        site="a533d9a1-7cc6-4bde-bc5b-e897d8b5da7d",
                        label="Heaven Hill All Brands", owner="Heaven Hill", tier="supplier", daily=True),
}

FIELDS = ["catalog_id", "catalog_name", "org_id", "site_id", "owner", "tier",
          "product_id", "dist_item_code", "system_id", "grouping_key", "sku_upc",
          "title", "item_description", "brand", "sort_value", "supplier", "brand_owner",
          "category", "sub_category", "size_text", "size_ml", "abv", "proof",
          "units_per_case", "country", "region", "varietal", "flavor", "market_region",
          "image", "image_count", "property_count", "properties_hash", "product_url", "pulled_at"]

PROP_FIELDS = ["catalog_id", "product_id", "group", "property", "label",
               "value_index", "value", "asset_name", "day", "captured_at"]

CAT_FIELDS = ["catalog_id", "org_id", "site_id", "catalog_name", "seeded", "status",
              "total_products", "total_pages", "page_size", "has_sitemap", "allow_export",
              "facet_properties", "publication_date", "build_id", "url", "checked_at"]

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SITE_RE = re.compile(r"%s/(%s)/(%s)/" % (re.escape(HOST), _UUID, _UUID))
_PROD_URL_RE = re.compile(r"/product/([^/]+)/([^/]+)/?\s*<")
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_GTIN = re.compile(r"(\d{12,14})")
_SIZE = re.compile(r"([\d.]+)\s*(ML|L|LT|OZ|G|GAL)\b", re.I)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")

# ── canonical mapping ────────────────────────────────────────────────────────────────────────────
# Catalogs do not agree on property names, so map by NORMALIZED name (lowercase, alphanumeric only)
# with an ordered alias list per canonical field: first alias that has a value wins. Unmapped
# properties are NOT dropped — they all land in salsify_properties.
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


ALIASES = {
    "dist_item_code": ["materialid", "heavenhillpartnumber", "itemcode", "distributoritemcode",
                       "supplieritemcode", "partnumber", "itemnumber"],
    "sku_upc": ["tradeitemgtin", "unitgtinupc", "01gtin12gs1tradeitemidentificationkeyvalue", "gtin",
                "upc", "gtinupc", "01alternateitemidentificationid", "gtin14", "gtin12"],
    "item_description": ["marketingname", "supplieritemdescription", "01entradeitemdescription",
                         "descriptionshort", "gtinname", "01enadditionaltradeitemdescription",
                         "itemdescription", "productdescription", "universalpropertyproductname",
                         "productname"],
    "brand": ["family", "brandname", "brand"],
    "sub_brand": ["subbrand"],
    "supplier": ["supplier", "suppliername", "vendorname"],
    "brand_owner": ["tradeitembrandownerpartyname", "brandowner", "manufacturer",
                    "manufacturerinternalreference"],
    "category": ["productcategory", "gpccategorycode", "functionalname", "productstyle",
                 "producttype", "category"],
    "sub_category": ["alcoholicproductbeveragetype", "alcoholicproductstypeoftaste", "subtype",
                     "productsubcategory"],
    "size_text": ["dimensions", "calvolumeofunitdisplay", "bottlevolume", "size", "netcontent",
                  "tradeitemmeasurementsmoduletradeitemmeasurementsnetcontent"],
    "size_uom": ["tradeitemmeasurementsnetcontentmeasurementunitcode", "netcontentuom", "sizeuom"],
    "abv": ["percentageofalcoholbyvolume", "abv", "alcoholbyvolume", "spiritproof"],
    "proof": ["alcoholproof", "proof"],
    "units_per_case": ["bottlespercase", "unitspercase", "casesize", "packsize"],
    "country": ["countryoforiginstatement", "countryname", "country", "countryofactivitycountrycode"],
    "region": ["sapregiondescription", "region", "appellation"],
    "varietal": ["sapvarietalgrapetypedescription", "varietal", "grapetype"],
    "flavor": ["sapflavorprofile", "flavor", "flavorprofile"],
    "market_region": ["usmarketregion", "marketregion"],
}
# BBG stores ABV under a property literally named "Spirit Proof" whose LABEL is "ABV / Spirit Proof"
# and whose value is an ABV (14.4 on a Chardonnay). Sazerac has a real `alcoholProof` (80) alongside
# `percentageOfAlcoholByVolume` (40). So `spiritproof` is the LAST abv alias and is only read as ABV
# when no true ABV property exists — and a value that looks like proof is converted, not mislabelled.

# Exact aliases are precise but finite, and every catalog invents its own prefixes ("Unit GTIN UPC",
# "Cal: Volume of Unit Display", "Supplier Item Description"). So a SECOND pass runs only where the
# exact pass found nothing: a distinctive-token pattern plus, where the field has a checkable shape,
# a value guard — so a loose name match still can't put a brand name in the GTIN column. Everything
# unmapped by both passes is still captured in full by salsify_properties; this only decides which
# values ALSO get a first-class column.
PATTERNS = [
    ("sku_upc", re.compile(r"(gtin|upc|ean)"), re.compile(r"^\d{8,14}$")),
    ("dist_item_code", re.compile(r"(itemcode|itemnumber|partnumber|materialnumber|skucode)"), None),
    ("item_description", re.compile(r"(itemdescription|productdescription|productname)"), None),
    ("abv", re.compile(r"(alcoholbyvolume|^abv$)"), re.compile(r"^\d{1,2}(\.\d+)?$")),
    # NO loose `proof` pattern on purpose: BBG's property is literally named "Spirit Proof" and holds
    # an ABV (11.0 on an 11% seltzer). A pattern that matched it would fill the proof column with ABVs
    # across 55k rows — a wrong number reads exactly like a right one. Proof comes from an exact alias
    # or stays null.
    ("size_text", re.compile(r"(volumeofunit|bottlevolume|netcontent|unitsize|containersize)"), None),
    ("units_per_case", re.compile(r"(percase|casepack|caseqty)"), None),
    ("country", re.compile(r"countryoforigin"), None),
]
_UOM_ML = {"MLT": 1.0, "ML": 1.0, "LTR": 1000.0, "L": 1000.0, "CLT": 10.0, "OZA": 29.5735, "OZ": 29.5735}


def _to_ml(text, uom=""):
    """Size in ml from a dimension string ('750ML', '1.75L') or a bare number + GS1 UOM code."""
    if not text:
        return None
    m = _SIZE.search(str(text))
    if m:
        v, u = float(m.group(1)), m.group(2).upper()
        if u in ("L", "LT"):
            return round(v * 1000)
        if u == "OZ":
            return round(v * 29.5735)
        if u == "GAL":
            return round(v * 3785.41)
        if u in ("ML", "G"):
            return round(v)
        return round(v)
    n = _NUM.search(str(text))
    if not n:
        return None
    f = _UOM_ML.get((uom or "").strip().upper())
    return round(float(n.group(0)) * f) if f else None


# ── fetch ────────────────────────────────────────────────────────────────────────────────────────
def _get(url, timeout=40, raw=False, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
            return b if raw else json.loads(b.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):          # deliberate on this platform (page 1, absent sitemap)
                raise
            last = e
        except Exception as e:
            last = e
        if i < tries - 1:
            time.sleep(0.6 * (i + 1))
    raise last


def base_url(org, site):
    return "%s/%s/%s" % (HOST, org, site)


def _data(base, bid, path):
    return _get("%s/_next/data/%s/%s" % (base, bid, path))


def catalog_meta(org, site, timeout=40):
    """One request: buildId + catalog name/size/facets, straight off the site root's __NEXT_DATA__.
    Also survives Salsify redeploys — the buildId is read live, never cached."""
    html = _get(base_url(org, site) + "/", timeout=timeout, raw=True).decode("utf-8", "replace")
    m = _NEXT_DATA.search(html)
    if not m:
        raise RuntimeError("no __NEXT_DATA__ (not a Salsify site page?)")
    d = json.loads(m.group(1))
    pp = (d.get("props") or {}).get("pageProps") or {}
    return {"build_id": d.get("buildId") or "",
            "catalog_name": pp.get("catalogName") or "",
            "total_products": int(pp.get("totalProducts") or 0),
            "total_pages": int(pp.get("totalPages") or 0),
            "page_size": int(pp.get("pageSize") or 16),
            "publication_date": pp.get("publicationDate") or "",
            "allow_export": bool(pp.get("allowProductsExport")),
            "facet_properties": [f.get("property") for f in (pp.get("filterProperties") or [])
                                 if isinstance(f, dict)],
            "page_one": pp}                   # page 1's products come free with this fetch


# ── discovery: every public catalog on the platform ──────────────────────────────────────────────
def sitemap_sites(timeout=60):
    """(org, site) for every catalog listed in the platform sitemap index — the public directory."""
    xml = _get(SITEMAP_INDEX, timeout=timeout, raw=True).decode("utf-8", "replace")
    out, seen = [], set()
    for m in _SITE_RE.finditer(xml):
        pair = (m.group(1), m.group(2))
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def site_product_urls(org, site, timeout=60):
    """(product_id, slug) for the whole catalog from its sitemap — one fetch instead of N list pages.
    Returns None when the site publishes no sitemap (BBG 403s here); callers fall back to paging."""
    try:
        xml = _get("%s/sitemap_1.xml" % base_url(org, site), timeout=timeout, raw=True)
    except Exception:
        return None
    text = xml.decode("utf-8", "replace")
    # UNQUOTE BOTH HALVES. A sitemap URL is percent-encoded, and this used to decode only the id — so a
    # slug carrying an encoded character survived as literal text (`%22` for a quote mark in
    # `E-H--Taylor-Amaranth-%22Grain-of-The-Gods%22-...`), and detail()'s own quote() then encoded the
    # `%` again to `%2522`. The route 403s on the double-encoded slug. One product in Sazerac had a
    # quote mark in its name and that asymmetry was the whole reason it could never be fetched.
    out = [(urllib.parse.unquote(pid), urllib.parse.unquote(slug))
           for pid, slug in _PROD_URL_RE.findall(text)]
    return out or None


def _seed_for(org, site):
    for cid, c in CATALOGS.items():
        if c["org"] == org and c["site"] == site:
            return cid
    return ""


def catalog_row(org, site, meta=None, now=None):
    """One `salsify_catalogs` row. Unprobed sites still land — a listing we haven't reached yet is a
    fact about the platform, and dropping it would quietly shrink the directory."""
    cid = _seed_for(org, site)
    row = {"catalog_id": cid or "%s:%s" % (org[:8], site[:8]), "org_id": org, "site_id": site,
           "seeded": bool(cid), "url": base_url(org, site) + "/",
           "checked_at": now if now is not None else int(time.time()),
           "catalog_name": "", "status": "listed", "total_products": None, "total_pages": None,
           "page_size": None, "has_sitemap": None, "allow_export": None,
           "facet_properties": "", "publication_date": "", "build_id": ""}
    if meta:
        row.update(status="public", catalog_name=meta["catalog_name"],
                   total_products=meta["total_products"], total_pages=meta["total_pages"],
                   page_size=meta["page_size"], build_id=meta["build_id"],
                   allow_export=meta["allow_export"], publication_date=meta["publication_date"],
                   facet_properties="; ".join([p for p in meta["facet_properties"] if p]))
    return row


def property_row(catalog_id, product_id, prop, day, now):
    """One `salsify_properties` row from a flatten() tuple."""
    group, name, label, vi, val, aname = prop
    return {"catalog_id": catalog_id, "product_id": product_id, "group": group, "property": name,
            "label": label, "value_index": vi, "value": val[:4000], "asset_name": aname,
            "day": day, "captured_at": now}


def discover(limit=None, workers=6, probe=True, land=True, log=print):
    """Walk the platform sitemap index → every public catalog, probe each for name/size, land the
    directory to `salsify_catalogs`. This is the loop: any row here can be pulled by org/site id."""
    pairs = sitemap_sites()
    if limit:
        pairs = pairs[:limit]
    for c in CATALOGS.values():               # seeds may not be in the public sitemap (BBG isn't) and
        if (c["org"], c["site"]) not in pairs:   # must survive --limit, or a probe run looks like a loss
            pairs.append((c["org"], c["site"]))
    log("[salsify] discover: %d public catalogs listed" % len(pairs))
    now = int(time.time())
    rows = []

    def probe_one(pair):
        org, site = pair
        row = catalog_row(org, site, now=now)
        if not probe:
            return row
        try:
            row = catalog_row(org, site, catalog_meta(org, site, timeout=25), now=now)
        except Exception as e:
            row["status"] = "unreachable:%s" % str(getattr(e, "code", e))[:40]
        return row

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(probe_one, pairs):
            rows.append(r)
    ok = [r for r in rows if r["status"] == "public"]
    log("[salsify] discover: %d reachable, %d products across them"
        % (len(ok), sum(r["total_products"] or 0 for r in ok)))
    if land and rows:
        try:
            import warehouse
            warehouse.write_accumulate(CAT_TABLE, rows,
                                       key=lambda r: "%s|%s" % (r["org_id"], r["site_id"]),
                                       fields=CAT_FIELDS)
            log("[salsify] landed %s: %d catalogs" % (CAT_TABLE, len(rows)))
        except Exception as e:
            log("[salsify] %s land skipped: %s" % (CAT_TABLE, str(e)[:110]))
    return rows


# ── parse ────────────────────────────────────────────────────────────────────────────────────────
def flatten(pr):
    """EVERY property the product exposes → [(group, property, label, value_index, value, asset_name)].
    Covers filter facets, every property set, digital assets, and the free-text blocks — with every
    value of a multi-value field kept (BBG's `US Market Region` is routinely ['Virginia','Nevada'],
    which a values[0] read silently halves)."""
    out = []

    def push(group, prop, label, vals):
        if not prop:
            return
        if not isinstance(vals, list):
            vals = [vals]
        for i, v in enumerate(vals):
            name = ""
            if isinstance(v, dict):
                name = v.get("name") or ""
                v = v.get("value", "")
            if isinstance(v, (list, dict)):
                v = json.dumps(v, separators=(",", ":"), default=str)
            v = "" if v is None else str(v).strip()
            if v == "":
                continue
            out.append((group, str(prop), str(label or prop), i, v, name))

    for f in (pr.get("filterProperties") or []):
        if isinstance(f, dict):
            push("filter", f.get("property"), f.get("label"), f.get("values") or [])
    for s in (pr.get("propertySets") or []):
        if not isinstance(s, dict):
            continue
        g = s.get("label") or "properties"
        for p in (s.get("properties") or []):
            if isinstance(p, dict):
                push(g, p.get("property"), p.get("label"), p.get("values") or [])
        for da in (s.get("digitalAssets") or []):
            if isinstance(da, dict):
                push(g, da.get("property"), da.get("label"), da.get("values") or [])
    da = pr.get("digitalAssets") or {}
    for p in (da.get("properties") or []):
        if isinstance(p, dict):
            push("assets", p.get("property"), p.get("label"), p.get("values") or [])
    for key in ("features", "qaContent", "useCaseContent", "variantAxisProperties"):
        blk = pr.get(key)
        if blk:
            push("content", key, key, [json.dumps(blk, separators=(",", ":"), default=str)])
    for i, im in enumerate(pr.get("images") or []):
        if isinstance(im, dict) and im.get("value"):
            out.append(("assets", "Image", "Image", i, im["value"], im.get("name") or ""))
    return out


# BBG publishes ONE field, "Spirit Proof", and it is MIXED UNITS — verified live across the catalog:
# beer/wine/sake rows carry an ABV (4.5 / 5 / 14.4 / 16 / 18) and spirits rows carry a PROOF (30 on a
# Potter's coffee liqueur, 92 / 97 / 118 on whiskeys). Reading it as one unit puts a 118% ABV whiskey
# and a 30% ABV liqueur in the master — numbers that look exactly as valid as the right ones. So the
# unit is resolved from the category (BBG prefixes every one: "Sp - Whiskey", "Beer - Craft",
# "Wine - Sake"), with a value threshold only as the fallback: nothing fermented reaches 25% ABV and no
# spirit is sold below 25 proof, so the boundary is unambiguous where the category isn't readable.
_TRUE_ABV = ("percentageofalcoholbyvolume", "abv", "alcoholbyvolume")
_SPIRIT_CAT = re.compile(r"(^sp[^a-z]|spirit|whisk|vodka|gin\b|rum|tequila|liqueur|cordial|brandy|"
                         r"cognac|agave|mezcal|schnapps|bourbon|scotch)")
_FERMENTED_CAT = re.compile(r"(beer|wine|sake|cider|seltzer|malt|fmb|cooler|mead|vermouth|champagne|"
                            r"sparkling|kombucha)")
_FERMENT_MAX_ABV = 25.0                      # fortified wine tops out ~24%; 25 proof spirits don't exist


def resolve_alcohol(index, category=""):
    """(abv, proof) — never both from one ambiguous number, and never a guess. Returns (None, None)
    rather than picking a unit it cannot justify: an absent value is recoverable from
    salsify_properties, a wrong one is not."""
    def num(s):
        m = _NUM.search(s or "")
        return float(m.group(0)) if m else None

    abv_v = proof_v = None
    for a in _TRUE_ABV:
        if index.get(a):
            abv_v = num("; ".join(index[a]))
            break
    if abv_v is None:
        abv_v = num(_pick_pattern_only(index, "abv"))
    for a in ALIASES["proof"]:
        if index.get(a):
            proof_v = num("; ".join(index[a]))
            break

    if abv_v is None and proof_v is None and index.get("spiritproof"):
        v = num("; ".join(index["spiritproof"]))
        if v is not None:
            cat = _norm(category)
            if _SPIRIT_CAT.search(cat):
                proof_v = v
            elif _FERMENTED_CAT.search(cat):
                abv_v = v
            elif v <= _FERMENT_MAX_ABV:       # no category to read — fall back to the value boundary
                abv_v = v
            else:
                proof_v = v

    if abv_v is not None and abv_v > 100:     # an out-of-range ABV is a proof that landed in the slot
        proof_v, abv_v = abv_v, None
    if abv_v is None and proof_v is not None:
        abv_v = round(proof_v / 2.0, 2)       # exact by US definition, not an estimate
    return abv_v, proof_v


def _pick_pattern_only(index, field):
    """The pattern pass alone — used where the exact-alias pass must be handled separately."""
    for f, pat, guard in PATTERNS:
        if f != field:
            continue
        for key in sorted(index):
            if not pat.search(key):
                continue
            hits = [v for v in index[key] if not guard or guard.match(v.strip())]
            if hits:
                return "; ".join(hits)
    return ""


def _pick(index, field):
    """First exact alias with a value (joined across multi-values); failing that, the pattern pass."""
    for alias in ALIASES.get(field, []):
        vals = index.get(alias)
        if vals:
            return "; ".join(vals)
    return _pick_pattern_only(index, field)


def parse_product(pr, cat=None, meta=None, props=None):
    """Flatten a product-detail payload into (row, property_rows). `cat` is a CATALOGS-shaped dict
    (catalog_id/org/site/owner/tier); `meta` supplies the catalog name."""
    cat = cat or {}
    meta = meta or {}
    props = props if props is not None else flatten(pr)

    index = {}
    for group, prop, label, _i, val, _n in props:
        if group == "assets":
            continue
        for k in (_norm(prop), _norm(label)):
            index.setdefault(k, [])
            if val not in index[k]:
                index[k].append(val)

    pid = str(pr.get("id") or pr.get("slugifiedId") or "")
    desc = _pick(index, "item_description") or (pr.get("subtitle") or pr.get("listSubtitle") or "").strip()
    images = [im.get("value") for im in (pr.get("images") or []) if isinstance(im, dict) and im.get("value")]
    asset_vals = [v for g, _p, _l, _i, v, _n in props if g == "assets"]

    upc = _pick(index, "sku_upc")
    if not upc:                                # BBG carries no GTIN property — it's in the asset filename
        names = [n for _g, _p, _l, _i, _v, n in props if n]
        for n in names + [pid]:
            m = _GTIN.search(n or "")
            if m:
                upc = m.group(1)
                break
    upc = (upc.split(";")[0] or "").strip()

    category = _pick(index, "category")
    abv_v, proof_v = resolve_alcohol(index, category)

    size_text = _pick(index, "size_text")
    fingerprint = hashlib.sha1(
        json.dumps(sorted((g, p_, i, v) for g, p_, _l, i, v, _n in props),
                   separators=(",", ":")).encode("utf-8")).hexdigest()[:16]

    row = {
        "catalog_id": cat.get("catalog_id") or "", "catalog_name": meta.get("catalog_name") or "",
        "org_id": cat.get("org") or "", "site_id": cat.get("site") or "",
        "owner": cat.get("owner") or "", "tier": cat.get("tier") or "",
        "product_id": pid,
        # Breakthru's distributor item code IS the site product id (their SAP Material ID); other
        # catalogs key on a GTIN, so fall back to the id only when no explicit item-code property.
        "dist_item_code": _pick(index, "dist_item_code") or pid,
        "system_id": pr.get("systemId") or "", "grouping_key": str(pr.get("groupingKey") or ""),
        "sku_upc": upc,
        "title": (pr.get("title") or pr.get("listTitle") or "").strip(),
        "item_description": desc,
        "brand": _pick(index, "brand") or (pr.get("sortValue") or "").strip(),
        "sort_value": (pr.get("sortValue") or "").strip(),
        "supplier": _pick(index, "supplier"), "brand_owner": _pick(index, "brand_owner"),
        "category": category, "sub_category": _pick(index, "sub_category"),
        "size_text": size_text, "size_ml": _to_ml(size_text, _pick(index, "size_uom")),
        "abv": abv_v, "proof": proof_v,
        "units_per_case": _pick(index, "units_per_case"),
        "country": _pick(index, "country"), "region": _pick(index, "region"),
        "varietal": _pick(index, "varietal"), "flavor": _pick(index, "flavor"),
        "market_region": _pick(index, "market_region"),
        "image": images[0] if images else (asset_vals[0] if asset_vals else ""),
        "image_count": len(set(images + asset_vals)),
        "property_count": len(props), "properties_hash": fingerprint,
        "product_url": "", "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return row, props


# ── crawl ────────────────────────────────────────────────────────────────────────────────────────
def list_page(base, bid, meta, n):
    """(product_id, slug) for one list page. Page 1 is `index.json` — `products/1.json` 403s on this
    platform, so the old products/1 loop silently dropped the first 16 products of every catalog."""
    try:
        pp = meta["page_one"] if n == 1 else _data(
            base, bid, "products/%d.json?params=products&params=%d" % (n, n))["pageProps"]
    except Exception:
        return []
    out = []
    for g in (pp.get("productGroups") or []):
        for p in (g.get("products") or []):
            pid = p.get("slugifiedId") or p.get("id")
            if pid:
                out.append((str(pid), p.get("slugifiedTitle") or str(pid)))
    return out


def enumerate_products(org, site, meta, pages=None, workers=8, log=print):
    """The whole id/slug universe: the sitemap in one fetch when published, else every list page."""
    if not pages:
        urls = site_product_urls(org, site)
        if urls:
            log("[salsify] %d products from sitemap" % len(urls))
            return urls, "sitemap"
    base, bid = base_url(org, site), meta["build_id"]
    total = meta["total_pages"] or 1
    n = min(pages or total, total)
    ids = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(lambda i: list_page(base, bid, meta, i), range(1, n + 1)):
            ids.extend(res)
    log("[salsify] %d products from %d/%d list pages" % (len(ids), n, total))
    return ids, "pages"


# THE SITEMAP AND THE DATA ROUTE DISAGREE ABOUT `&`. Salsify slugifies the HTML-ESCAPED title when it
# writes sitemap_1.xml, so "Lemonade & Lime" becomes `Lemonade-andamp-Lime` ("and" for &, plus a
# stray "amp") — but their data route resolves the slug built from the RAW title, `Lemonade-and-Lime`.
# The sitemap therefore publishes a slug their own route 403s on. Measured on Sazerac 2026-08-03: 106
# of 107 unfetchable products contained `andamp`, and 0 of the 7,573 that fetched did. Verified by
# replaying one id across variants — only `andamp` -> `and` resolves.
_SLUG_REPAIRS = [("andamp", "and")]


def _slug_variants(slug):
    """The slug as published, then the known sitemap-vs-route repairs. Order matters: never rewrite a
    slug that already works, only one that the route has actually rejected."""
    seen = [slug]
    for bad, good in _SLUG_REPAIRS:
        if bad in slug:
            cand = slug.replace(bad, good)
            if cand not in seen:
                seen.append(cand)
    return seen


def detail(base, bid, pid, slug):
    last = None
    for cand in _slug_variants(str(slug)):
        q = urllib.parse.urlencode({"id": pid, "title": cand})
        try:
            d = _data(base, bid, "product/%s/%s.json?%s" % (urllib.parse.quote(str(pid), safe=""),
                                                            urllib.parse.quote(cand, safe=""), q))
            return d["pageProps"]["product"]
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (403, 404):      # only a routing rejection is worth another slug
                raise
    raise last


def _snapshot(catalog_id, state_dir):
    p = os.path.join(state_dir, "%s_fingerprints.json" % catalog_id)
    try:
        return p, json.load(open(p))
    except Exception:
        return p, {}


def _run(catalog_id, n, new, changed, status, warnings, started):
    return {"id": "R-SAL" + hashlib.sha1((catalog_id + str(n)).encode()).hexdigest()[:3].upper(),
            "connId": "salsify", "startedAt": started, "finishedAt": int(time.time() * 1000),
            "durationMs": 0, "status": status, "trigger": "manual", "total": n,
            "degraded": status != "success", "warnings": warnings, "healed": [],
            "extracts": [{"id": TABLE, "rows": n, "delta": new, "changed": changed, "status": status}]}


def crawl_catalog(catalog_id=None, org=None, site=None, pages=None, workers=12, chunk=400,
                  land=True, resume=True, state_dir=None, log=print, repair_properties=False):
    """Full capture of one Salsify catalog. Returns (rows, warnings, stats)."""
    cat = dict(CATALOGS.get(catalog_id) or {})
    org = org or cat.get("org")
    site = site or cat.get("site")
    if not (org and site):
        raise ValueError("unknown catalog %r — pass org/site or add it to CATALOGS" % catalog_id)
    cat.update(catalog_id=catalog_id or "%s:%s" % (org[:8], site[:8]), org=org, site=site)
    state_dir = state_dir or _STATE
    os.makedirs(state_dir, exist_ok=True)

    meta = catalog_meta(org, site)
    base, bid = base_url(org, site), meta["build_id"]
    log("[salsify] %s (%s) · buildId=%s · %d products / %d pages"
        % (cat["catalog_id"], meta["catalog_name"], bid, meta["total_products"], meta["total_pages"]))

    ids, how = enumerate_products(org, site, meta, pages=pages, log=log)
    _progress(rows=0, stage="%s enumerated %d products (%s)" % (cat["catalog_id"], len(ids), how),
              pct=0.0, catalog=cat["catalog_id"], listed=len(ids), reported=meta["total_products"])
    warns = []
    if meta["total_products"] > 0 and not ids:
        warns.append("site reports %d products but 0 enumerated (%s) — routing drift"
                     % (meta["total_products"], how))
    if not pages and how == "pages" and meta["total_products"] and ids:
        got = len(ids) / float(meta["total_products"])
        if got < 0.9:
            warns.append("enumerated %d of %d reported products (%d%%)"
                         % (len(ids), meta["total_products"], round(got * 100)))

    # RESUME + the property fingerprints come from the WAREHOUSE, in one read. The fingerprints used to
    # live in agent_state/ — local disk on a machine Fly destroys at the end of every run, so the
    # "only re-emit properties that MOVED" rule never survived a single tick. Measured live: 1,574
    # sazerac products had landed 721,358 property rows, ~5x the ~96/product they actually expose,
    # because each retry re-emitted the whole catalog into an append-only table. `properties_hash` is
    # already a column on salsify_products, which IS shared state — read it from there.
    done, fps = set(), {}
    if resume and land:
        try:
            import warehouse
            for r in warehouse.query(TABLE, "SELECT product_id, properties_hash FROM t "
                                            "WHERE catalog_id = ?", [cat["catalog_id"]]):
                done.add(r["product_id"])
                if r.get("properties_hash"):
                    fps[r["product_id"]] = r["properties_hash"]
        except Exception:
            done, fps = set(), {}
    # A product in the wide table but with NO property rows is a HOLE, not a completed product — resume
    # must re-fetch it rather than skip it forever. (Holes exist because same-day reruns used to
    # overwrite each other's property partitions; the run-stamped part name stops new ones.) This costs
    # one extra read and is opt-in, because `count(DISTINCT product_id)` over every partition is minutes
    # of scan on a table this size — a daily tick should not pay for a repair it almost never needs.
    if repair_properties and land:
        try:
            import warehouse
            have_props = {r["product_id"] for r in warehouse.query_parts(
                PROPS_TABLE, "SELECT DISTINCT product_id FROM t WHERE catalog_id = ?",
                [cat["catalog_id"]])}
            holes = done - have_props
            if holes:
                log("[salsify] %d products have no property capture — re-fetching them" % len(holes))
                done = done - holes
                # AND FORGET THEIR FINGERPRINT. Re-fetching alone repairs nothing: the emit gate is
                # `fps.get(pid) != properties_hash`, and the hash comes from the wide row, which was
                # never lost — so a re-fetched hole matched its own fingerprint and emitted zero
                # property rows. Observed: a repair run re-fetched all 3,143 holes and left
                # salsify_properties byte-identical at 1,660,264. Dropping the fingerprint is what
                # makes the product look new again, which is what a hole IS.
                for pid in holes:
                    fps.pop(pid, None)
        except Exception as e:
            warns.append("property-hole check skipped: %s" % str(e)[:90])

    todo = [(pid, slug) for pid, slug in ids if pid not in done]
    log("[salsify] %d listed · %d already detailed · %d to fetch" % (len(ids), len(done), len(todo)))

    fp_path, local_fps = _snapshot(cat["catalog_id"], state_dir)
    if not fps:
        fps = local_fps                       # local/no-warehouse runs keep the on-disk snapshot
    day = time.strftime("%Y-%m-%d", time.gmtime())
    now = int(time.time())
    rows, failed, changed, prop_rows_total, part = [], 0, 0, 0, 0
    land_fails = []

    for i in range(0, len(todo), chunk):
        batch, prop_rows = [], []
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(lambda t: _safe(base, bid, t), todo[i:i + chunk]))
        for (pid, slug), pr in zip(todo[i:i + chunk], results):
            if pr is None:
                failed += 1
                continue
            row, props = parse_product(pr, cat, meta)
            row["product_url"] = "%s/product/%s/%s/" % (base, pid, slug)
            batch.append(row)
            # Only re-emit properties when they MOVED — a daily cadence then costs the diff, not the
            # 1.1M-row catalog. First sight of a product always writes.
            if fps.get(pid) != row["properties_hash"]:
                changed += 1
                fps[pid] = row["properties_hash"]
                prop_rows.extend(property_row(cat["catalog_id"], pid, p, day, now) for p in props)
        rows.extend(batch)
        prop_rows_total += len(prop_rows)
        if land and batch:
            part += 1
            land_fails.extend(_land(batch, prop_rows, cat["catalog_id"], day, part, log))
            try:
                json.dump(fps, open(fp_path, "w"))
            except Exception:
                pass
        seen = min(i + chunk, len(todo))
        log("[salsify] %d/%d products (+%d properties)" % (seen, len(todo), len(prop_rows)))
        _progress(rows=len(rows), stage="%s %d/%d products" % (cat["catalog_id"], seen, len(todo)),
                  pct=round(100.0 * seen / max(1, len(todo)), 1), catalog=cat["catalog_id"],
                  properties=prop_rows_total, failed=failed, listed=len(ids))

    if land:                                  # record how this catalog actually enumerates
        try:
            import warehouse
            crow = catalog_row(org, site, meta)
            crow["has_sitemap"] = (how == "sitemap")
            warehouse.write_accumulate(CAT_TABLE, [crow],
                                       key=lambda r: "%s|%s" % (r["org_id"], r["site_id"]),
                                       fields=CAT_FIELDS)
        except Exception as e:
            log("[salsify] %s update skipped: %s" % (CAT_TABLE, str(e)[:110]))

    # A landing failure is a DATA LOSS, not a log line — degrade the run so it is visible where it
    # happened rather than a week later in a row-count discrepancy nobody was looking for.
    warns.extend(land_fails[:8])
    if len(land_fails) > 8:
        warns.append("... and %d more landing failures" % (len(land_fails) - 8))

    # VERIFY WHAT WE FETCHED IS WHAT THE TABLE HOLDS. Row-count verify-landing cannot see this: a chunk
    # that vanishes mid-crawl is masked by later chunks growing the table past its previous size. Only
    # comparing the fetched set against the landed set catches a hole, which is exactly how 800 BBG
    # products stayed invisible through a run that reported `ok`.
    if land and rows:
        try:
            import warehouse
            got = warehouse.query(TABLE, "SELECT count(*) AS n FROM t WHERE catalog_id = ?",
                                  [cat["catalog_id"]])[0]["n"]
            expected = len(done) + len(rows)
            if got < expected:
                warns.append("landed %d of %d expected products for %s — %d rows did not survive the "
                             "merge (re-run to backfill; the crawl is resumable)"
                             % (got, expected, cat["catalog_id"], expected - got))
        except Exception as e:
            warns.append("could not verify landed row count: %s" % str(e)[:90])

    if todo and failed / float(len(todo)) > 0.2:
        warns.append("%d/%d detail fetches failed (%d%%)"
                     % (failed, len(todo), round(100.0 * failed / len(todo))))
    if rows:
        idf = sum(1 for r in rows if r["dist_item_code"]) / float(len(rows))
        if idf < 0.9:
            warns.append("dist_item_code fill %d%% (<90%%) — identity drift" % round(idf * 100))
        empty = sum(1 for r in rows if not r["property_count"])
        if empty > len(rows) * 0.5:
            warns.append("%d/%d products exposed 0 properties — detail-payload drift" % (empty, len(rows)))
    return rows, warns, {"listed": len(ids), "fetched": len(rows), "failed": failed,
                         "changed": changed, "properties": prop_rows_total, "how": how,
                         "reported": meta["total_products"], "catalog_name": meta["catalog_name"]}


def _progress(**kw):
    """Heartbeat into the run journal (`_collect/runs/<id>.json` in the shared bucket) so a crawl running
    on its own ephemeral Fly machine is watchable WHILE it runs. run_sources.run_one folds these lines in;
    printed anywhere else they're harmless noise. This is the whole reason the tracker needs no Mac-side
    poller — the scraper on Fly reports its own progress to the warehouse."""
    try:
        print("HOODIE_PROGRESS " + json.dumps(kw), flush=True)
    except Exception:
        pass


def _safe(base, bid, t):
    try:
        return detail(base, bid, t[0], t[1])
    except Exception:
        return None


# PART NAMES MUST BE UNIQUE PER RUN, NOT PER DAY. write_partition is idempotent per (table, part) — by
# design, so re-running the same slice doesn't duplicate. But the sequence number is positional: it
# counts chunks of THIS run's todo list, which differs between runs. So a second run on the same day
# reused p0001, p0002… and OVERWROTE the first run's parts with entirely different products.
#
# Measured live 2026-08-03: three runs in one day left salsify_properties at 1,660,264 rows, DOWN from
# 1,700,185 — an append-only table shrinking. The backfill's 800 products overwrote bbg p0001-p0002,
# and the slug repair's 106 + 1 overwrote sazerac/heaven-hill p0001, so ~1,500 products silently lost
# their property capture while the wide table stayed complete.
#
# The run stamp makes the part name unique per process, so a re-run APPENDS (which is what an
# append-only event log should do) instead of replacing a slice it never wrote.
_RUN_STAMP = time.strftime("%H%M%S", time.gmtime())


def _land(rows, prop_rows, catalog_id, day, part, log=print, run_stamp=None):
    run_stamp = run_stamp or _RUN_STAMP
    """Land one chunk. Returns the list of failures — the CALLER must surface them, because a swallowed
    landing failure is indistinguishable from success.

    This swallowed its exception and logged. On 2026-08-03 two of BBG's 139 chunks failed to merge
    (p0005, p0068) while their property partitions landed: 800 products were lost, the run reported
    `ok` with no warnings, and the only evidence was the tall table holding 55,580 products against the
    wide table's 54,780. A retry per chunk plus an honest degrade is the difference between a hole you
    find in a week and one you find in the run that made it."""
    import warehouse
    fails = []
    for attempt in range(3):
        try:
            warehouse.write_accumulate(TABLE, rows,
                                       key=lambda r: "%s|%s" % (r["catalog_id"], r["product_id"]),
                                       fields=FIELDS)
            break
        except Exception as e:
            if attempt == 2:
                fails.append("chunk p%04d: %s land failed after 3 tries: %s"
                             % (part, TABLE, str(e)[:110]))
                log("[salsify] %s land FAILED (chunk p%04d, %d rows): %s"
                    % (TABLE, part, len(rows), str(e)[:120]))
            else:
                time.sleep(2 * (attempt + 1))
    if not prop_rows:
        return fails
    try:                                      # append-only, date-partitioned: never merged, never re-read
        warehouse.write_partition(PROPS_TABLE, "%s_%s_%s_p%04d"
                                  % (day, catalog_id.replace(":", "-"), run_stamp, part),
                                  prop_rows, fields=PROP_FIELDS)
    except Exception as e:
        fails.append("chunk p%04d: %s capture failed: %s" % (part, PROPS_TABLE, str(e)[:110]))
        log("[salsify] %s capture failed: %s" % (PROPS_TABLE, str(e)[:120]))
    return fails


# ── entrypoints ──────────────────────────────────────────────────────────────────────────────────
def seeds(exclude=()):
    return [c for c in CATALOGS if c not in set(exclude)]


def pull(catalog="bbg", catalogs=None, org=None, site=None, pages=None, workers=12,
         land=True, state_dir=None, log=print, repair_properties=False):
    """Pull one or more catalogs. Registry entrypoint — `pull(catalog='bbg')` replaces bbg_salsify."""
    started = int(time.time() * 1000)
    targets = catalogs or [catalog]
    all_rows, runs, mv = [], [], {"sampled": 0, "changed": 0, "dropped": 0}
    for cid in targets:
        try:
            rows, warns, st = crawl_catalog(cid, org=org, site=site, pages=pages, workers=workers,
                                            land=land, state_dir=state_dir, log=log,
                                            repair_properties=repair_properties)
        except Exception as e:
            runs.append(_run(cid, 0, 0, 0, "failed", ["crawl failed: %s" % str(e)[:140]], started))
            log("[salsify] %s: crawl FAILED: %s" % (cid, str(e)[:140]))
            continue
        status = "degraded" if warns else "success"
        runs.append(_run(cid, len(rows), len(rows), st["changed"], status, warns, started))
        all_rows.extend(rows)
        mv["sampled"] += len(rows)
        mv["changed"] += st["changed"]
        log("[salsify] %s (%s): %d products of %d reported · %d property-changed · %d properties landed — %s"
            % (cid, st["catalog_name"], len(rows), st["reported"], st["changed"], st["properties"], status))
        for w in warns:
            log("   ! %s" % w)

    header = ["Catalog", "Item code", "Description", "Title", "Brand", "Supplier", "Size", "ABV", "UPC"]
    prev = [[r["catalog_id"], r["dist_item_code"], r["item_description"], r["title"], r["brand"],
             r["supplier"], r["size_text"], r["abv"], r["sku_upc"]] for r in all_rows]
    ds = {TABLE: {"header": header, "rows": prev[:1000], "total": len(all_rows), "_rows_full": prev}}
    return ds, runs, mv


def platform_pass(pages=None, land=True, log=print, repair_properties=False):
    """The daily platform sweep and THE ONLY registry entrypoint that writes salsify_products: refresh the
    public-catalog directory, then pull every seeded catalog — bbg included — sequentially in this one
    process.

    Sequential and single-process is the point, not an implementation detail. `write_accumulate` is
    read-modify-write, so two processes merging this table at once silently lose each other's rows; when
    `bbg` and `salsify` were separate registry sources the dispatcher gave them a machine each and a run
    that journalled 8,200 landed rows left 1,574 behind. One writer, always."""
    try:
        discover(land=land, log=log)
    except Exception as e:
        log("[salsify] discover skipped: %s" % str(e)[:140])
    return pull(catalogs=seeds(), pages=pages, land=land, log=log,
                repair_properties=repair_properties)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Salsify Sites — public catalog platform recipe.")
    ap.add_argument("--catalog", default=None, help="seeded catalog id (default: bbg)")
    ap.add_argument("--org", help="org uuid (for an unseeded catalog)")
    ap.add_argument("--site", help="site uuid (for an unseeded catalog)")
    ap.add_argument("--pages", type=int, help="limit list pages (smoke test)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--discover", action="store_true", help="refresh the public-catalog directory")
    ap.add_argument("--probe-all", action="store_true", help="with --discover: probe every listed site")
    ap.add_argument("--limit", type=int, help="with --discover: only the first N sites")
    ap.add_argument("--list", action="store_true", help="show seeded catalogs and exit")
    ap.add_argument("--no-land", action="store_true", help="parse only, don't write to the warehouse")
    ap.add_argument("--repair-properties", action="store_true",
                    help="re-fetch products that have no property capture (fills holes)")
    a = ap.parse_args(argv)

    if a.list:
        for cid, c in CATALOGS.items():
            print("  %-14s %-34s %s/%s" % (cid, c["label"], c["org"], c["site"]))
        return 0
    if a.discover:
        rows = discover(limit=a.limit, probe=True if a.probe_all or not a.limit else False,
                        land=not a.no_land)
        for r in sorted([r for r in rows if r["status"] == "public"],
                        key=lambda r: -(r["total_products"] or 0))[:25]:
            print("  %-46s %7s  %s" % (str(r["catalog_name"])[:46], r["total_products"], r["url"]))
        return 0

    cid = a.catalog or ("%s:%s" % (a.org[:8], a.site[:8]) if a.org and a.site else "bbg")
    ds, runs, mv = pull(catalog=cid, org=a.org, site=a.site, pages=a.pages, workers=a.workers,
                        land=not a.no_land, repair_properties=a.repair_properties)
    for row in ds[TABLE]["rows"][:15]:
        print("  •", row[1], "|", str(row[2])[:40], "|", str(row[4])[:18], "|", row[6], "|", row[8])
    print("runs:", [(r["extracts"][0]["id"], r["status"], r["total"]) for r in runs])
    print("movement:", mv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
