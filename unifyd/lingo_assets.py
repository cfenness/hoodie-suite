#!/usr/bin/env python3
"""lingo_assets.py — pull a brand's SKU/product images from a Lingo digital-asset portal.

Lingo (lingoapp.com) is a DAM many CPG brands publish a *public* "portal" on for their
sales/retail partners — e.g. Curaleaf's New York Portal
(https://curaleaf.lingoapp.com/p/New-York-Portal-4PkjwR). Public portals expose a clean,
unauthenticated JSON API (api.lingoapp.com/v4) and every asset preview URL carries an
embedded `asset_token` that loads with no session — so a brand's product photography is
directly harvestable and durable.

The resolution chain (every hop public on a `privacy: public` portal, role=guest):
    /v4/portals/<shortId>            -> portal (space_id, kit counts)
    /v4/kits/<kitUuid>/outline       -> the kit's sections  (uuid, name, counts)
    /v4/sections/<uuid>?limit=200    -> items; type=='asset' carry the image
each asset -> { name, type, dimensions, thumbnails{24,292,480,1232}, permalink(full dl) }

Curaleaf NY portal's "Product Photography" kit-section is per-SKU, named on a structured key
`C.<FORMAT>.<SIZE>.<TYPE>.<STRAIN>` (e.g. `C.WHOLE FLOWER.3.5G.HYBRID`,
`C.CARTRIDGE.20-1.Sativa.NY`) — which is what makes it matchable to an inbound menu row.

Use:
    python lingo_assets.py --kit A4EAE236-742F-443B-BC4E-5A94D1A04B12 --out manifest.json
    python lingo_assets.py --portal 4PkjwR          # portal metadata + which kits it exposes

This is a brand-asset puller (image URLs), NOT a warehouse source — it emits a manifest JSON,
it does not write parquet. `norm_key()` / `match_products()` map an inbound menu's product
rows to the best product image (used by the Order Hub store-seed flow).
"""
import argparse, json, os, re, sys, urllib.request, urllib.error

API = "https://api.lingoapp.com/v4"
UA = os.environ.get("LINGO_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# The Curaleaf NY kit (matches the "Curaleaf NY Menu.xlsx" inbound format). Overridable via CLI.
CURALEAF_NY_KIT = "A4EAE236-742F-443B-BC4E-5A94D1A04B12"
CURALEAF_PORTAL = "4PkjwR"


def _get(path):
    """GET api.lingoapp.com/v4<path> -> parsed .result (raises on API error)."""
    url = path if path.startswith("http") else API + path
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode("utf-8", "replace"))
    if not body.get("success"):
        err = body.get("error", {})
        raise RuntimeError(f"lingo API {err.get('code')}: {err.get('message')} ({path})")
    return body.get("result", {})


def portal(short_id):
    """Public portal metadata: space, privacy, kit/asset counts."""
    p = _get(f"/portals/{short_id}").get("portal", {})
    sp = p.get("space", {})
    return {
        "short_id": p.get("short_id"), "uuid": p.get("uuid"), "name": p.get("name"),
        "privacy": p.get("privacy"), "space_id": p.get("space_id"),
        "space": sp.get("name"), "counts": p.get("counts", {}),
        "downloads_restricted": p.get("downloads_restricted"),
    }


def kit_sections(kit_uuid):
    """The kit's sections (uuid, name, asset/item counts) from the public outline."""
    kv = _get(f"/kits/{kit_uuid}/outline").get("kit_version", {})
    secs = kv.get("sections", []) or []
    return kv.get("counts", {}), [
        {"uuid": s.get("uuid"), "name": s.get("name"),
         "order": s.get("display_order"), "counts": s.get("counts", {})}
        for s in secs
    ]


def _asset_row(a):
    th = a.get("thumbnails", {}) or {}
    return {
        "name": a.get("name"), "type": a.get("type"), "category": a.get("category"),
        "dimensions": a.get("dimensions"), "size": a.get("size"),
        "img": th.get("1232") or th.get("480") or a.get("permalink"),  # display size
        "thumb": th.get("480") or th.get("292"),                       # grid size
        "full": a.get("permalink"),                                     # original download
        "text": ((a.get("meta") or {}).get("text_content") or "").strip()[:400] or None,
    }


def section_assets(section_uuid, limit=200):
    """All asset items in a section (skips inline notes / non-asset items)."""
    sec = _get(f"/sections/{section_uuid}?limit={limit}&page=1&v=0").get("section", {})
    return [_asset_row(i["asset"]) for i in (sec.get("items", []) or [])
            if i.get("type") == "asset" and i.get("asset")]


def manifest(kit_uuid, only_sections=None):
    """Full manifest for a kit: every section with its assets + image URLs.

    only_sections: optional iterable of section names to keep (case-insensitive substring).
    """
    counts, secs = kit_sections(kit_uuid)
    want = [s.lower() for s in only_sections] if only_sections else None
    out = {"kit_uuid": kit_uuid, "counts": counts, "sections": []}
    for s in secs:
        if want and not any(w in (s["name"] or "").lower() for w in want):
            continue
        assets = section_assets(s["uuid"]) if s["counts"].get("assets") else []
        out["sections"].append({"name": s["name"], "order": s["order"],
                                "asset_count": len(assets), "assets": assets})
    out["asset_total"] = sum(len(s["assets"]) for s in out["sections"])
    return out


# ── menu-row matching ────────────────────────────────────────────────────────
_STOP = {"c", "ny", "the", "and", "w", "wo", "front", "menu", "angle", "l", "r",
         "gummy", "bg", "1x"}
_ALIAS = {  # normalize synonyms so menu wording lands on the asset key's vocabulary
    "20to1": "20-1", "20:1": "20-1", "1to1": "1-1", "1:1": "1-1", "1to20": "1-20",
    "1:20": "1-20", "preroll": "pre-roll", "prerolls": "pre-roll", "pre": "pre-roll",
    "vape": "cartridge", "cart": "cartridge", "carts": "cartridge", "tincture": "drops",
    "gummies": "chews", "gummy": "chews", "chew": "chews", "flwr": "flower",
    "indica": "indica", "sativa": "sativa", "hybrid": "hybrid",
}


def norm_key(s):
    """Tokenize a name (asset name or menu product) into a comparable token set."""
    s = (s or "").lower()
    # canonicalize potency ratios (20:1 / 20to1 / 20 to 1 -> 20-1) before splitting
    s = re.sub(r"(\d)\s*:\s*(\d)", r"\1-\2", s)
    s = re.sub(r"(\d)\s*to\s*(\d)", r"\1-\2", s)
    s = s.replace(".", " ").replace("_", " ").replace("/", " ")
    s = re.sub(r"[^a-z0-9\- ]", " ", s)
    toks = []
    for t in s.split():
        t = _ALIAS.get(t, t)
        if t in _STOP or len(t) < 1:
            continue
        toks.append(t)
    return set(toks)


def match_products(products, man, min_overlap=2):
    """For each product {name}, find the best-matching asset in the manifest.

    Returns [{name, matched, score, img, thumb, full}]; matched=None when no asset clears
    min_overlap shared tokens. Jaccard-weighted overlap, ties broken by raw overlap count.
    """
    assets = [(a, norm_key(a["name"])) for s in man.get("sections", []) for a in s["assets"]]
    res = []
    for p in products:
        pk = norm_key(p.get("name") or p.get("product") or "")
        best, best_score, best_ov = None, 0.0, 0
        for a, ak in assets:
            ov = len(pk & ak)
            if ov < min_overlap:
                continue
            score = ov / max(1, len(pk | ak))
            if score > best_score or (score == best_score and ov > best_ov):
                best, best_score, best_ov = a, score, ov
        res.append({"name": p.get("name"), "matched": best["name"] if best else None,
                    "score": round(best_score, 3), "overlap": best_ov,
                    "img": best["img"] if best else None,
                    "thumb": best["thumb"] if best else None,
                    "full": best["full"] if best else None})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pull SKU/product images from a public Lingo portal.")
    ap.add_argument("--kit", default=CURALEAF_NY_KIT, help="kit uuid (default: Curaleaf NY)")
    ap.add_argument("--portal", help="portal short id — print portal metadata and exit")
    ap.add_argument("--sections", help="comma-list of section-name filters (e.g. 'Product Photography')")
    ap.add_argument("--out", help="write manifest JSON to this path (else stdout summary)")
    a = ap.parse_args(argv)

    if a.portal:
        print(json.dumps(portal(a.portal), indent=2)); return 0

    only = [s.strip() for s in a.sections.split(",")] if a.sections else None
    man = manifest(a.kit, only_sections=only)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(man, f, indent=1)
        print(f"wrote {a.out}: {man['asset_total']} assets across "
              f"{len(man['sections'])} sections", file=sys.stderr)
    else:
        for s in man["sections"]:
            print(f"  {s['name']:<28} {s['asset_count']:>3} assets")
        print(f"  {'TOTAL':<28} {man['asset_total']:>3} assets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
