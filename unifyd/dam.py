#!/usr/bin/env python3
"""dam.py — the shared spine for harvesting supplier/brand DIGITAL ASSET LIBRARIES (media centres).

One recipe per PLATFORM, one rights record per SOURCE — the same shape as the distributor-catalog
recipes ([[distributor-catalog-recipes]]), with one addition that changes everything: a DAM's payload
is somebody else's copyrighted work, so the rights record is not documentation, it is a gate that
runs (see `rights.py`).

WHAT A DAM CONNECTOR PRODUCES (two outputs, both canon-keyed)
  1. `dam_assets`   — a POINTER row per asset: identity, folder path, filename, type/extension, size,
                      timestamps, and the URL. Never the bytes. Pointers and metadata are facts about
                      a file, ungated by design.
  2. `brand_events` — a dated product-event feed derived from press-release titles and asset metadata:
                      launches, limited editions, availability, partnerships, awards. Facts always
                      flow, so this lands regardless of what the image rights say.

  The CV reference gallery (phash + embedding) is the third output, and it is the one the gate can
  refuse. `dam_assets` carries `retention` / `phash` / `embedding_ref` columns that stay
  `pointer_only` / NULL when the source's scope doesn't cover derivative works — so the row itself
  shows you what was withheld and why (`rights_ref`), instead of the gallery quietly having a hole.

THE CHOKEPOINT
  `asset_bytes()` is the ONLY function in this package that may fetch an asset's bytes, and it calls
  `rights.require(rec, "fetch_asset")` first. A connector NEVER opens an asset URL itself. This is
  enforced mechanically by `dam_gate_test.py`, which scans this package's source for any fetch of an
  asset/download URL outside this function — the same ratchet shape as `browser_driver_test.py`,
  because "we would never do that" is not a control and a quiet degrade is indistinguishable from
  success ([[quiet-degrades]]).

THE HONESTY CONTRACT (dq.js spirit)
  Every derived event field is labelled DETERMINISTIC (read verbatim out of the source, or computed
  by an exact rule over it) or INFERENCE (a classifier's read of free text), per field, per row, in
  `field_provenance`. The distinction that matters most here is the DATE: a DAM's `created_on` is the
  UPLOAD timestamp, not the event date — Bacardi's entire 2018 folder carries `2018-04-11`, the day
  it was bulk-migrated. So the event date comes from the year FOLDER when the source organizes that
  way (precision `year`, deterministic) and is otherwise NULL with precision `unknown`. It is never
  back-filled from an upload stamp; a confident wrong date is worse than an absent one.
"""
import hashlib
import json
import os
import re
import time

import rights

ASSETS_TABLE = "dam_assets"
EVENTS_TABLE = "brand_events"

ASSET_FIELDS = [
    "source_id", "vendor", "drive_id", "drive_name", "folder_id", "folder_path", "asset_id",
    "asset_token", "name", "title", "description", "asset_type", "extension", "mime_type",
    "size_bytes", "asset_url", "thumb_url", "download_url", "created_on", "updated_on",
    # rights-governed columns — the visible record of what was and wasn't allowed
    "rights_ref", "image_use", "image_scope", "retention", "phash", "embedding_ref",
    "withheld_reason", "pulled_at",
]

EVENT_FIELDS = [
    "event_id", "hoodie_brand_id", "brand", "sku_id", "event_type", "event_date",
    "event_date_precision", "market", "price", "currency", "title", "asset_count",
    "source", "source_id", "source_asset_ids", "source_url", "rights_ref",
    "field_provenance", "fetched_at",
]

DETERMINISTIC = "DETERMINISTIC"
INFERENCE = "INFERENCE"

# ── event classification (INFERENCE — a keyword read of a headline, labelled as such) ──────────────
# Ordered: the first matching type wins, most specific first. `launch` sits above `partnership`
# because "X LAUNCHES Y WITH PARTNER Z" is a launch story that mentions a partner.
#
# `leadership` and `corporate_legal` sit ABOVE `launch` for the same reason, learned from the live
# run: "BACARDI LIMITED ANNOUNCES NEW EUROPE REGIONAL PRESIDENT" matched `announces (the) new` and
# landed as a product launch. A personnel announcement is not a product event, and a feed that files
# it as one is worse than a feed that files it as `other`.
_EVENT_TYPES = [
    # The gap window is 60, not 40: "NAMES K.C. KAVANAGH CHIEF GLOBAL COMMUNICATIONS OFFICER" puts 42
    # characters of name and title between the verb and the role, so a 40-char window filed a
    # personnel announcement as `other`.
    ("leadership", r"\bnames?\b.{0,60}?\b(?:officer|president|ceo|cfo|coo|chairman|director|head of)\b"
                   r"|\bannounces?\b.{0,40}?\b(?:president|ceo|cfo|coo|chief|chairman)\b"
                   r"|\bappoint(?:s|ed|ment)\b|\bsuccession\b|\bsteps? down\b|\bretires?\b"),
    ("corporate_legal", r"\btrademark\b|\blawsuit\b|\bcourt\b|\btestifies\b|\blitigation\b"
                        r"|\bpetition\b|\bfreedom of information\b|\bfiles?\b.{0,30}\brequest\b"),
    ("limited_edition", r"\blimited[- ]edition|\bspecial edition|\bone[- ]off release|\bcollector'?s edition"),
    ("launch", r"\blaunch(?:es|ed|ing)?\b|\bintroduc(?:es|ing|ed)\b|\bunveil(?:s|ed|ing)?\b"
               r"|\bdebut(?:s|ed|ing)?\b|\bannounces? (?:the )?(?:new|arrival)|\bpresents\b|\breleases?\b"),
    ("availability", r"\bnow available\b|\bavailable (?:in|at|from|nationwide)|\brolls? out\b"
                     r"|\bexpand(?:s|ing|ed)? (?:to|into)\b|\barrives? in\b"),
    ("partnership", r"\bpartner(?:s|ship|ing)?\b|\bcollaborat(?:es|ion|ing)\b|\bteams? up\b"
                    r"|\bjoins? forces\b|\bx\b(?= [A-Z])"),
    ("award", r"\baward(?:s|ed)?\b|\bwins?\b|\bwinner\b|\bhonou?r(?:s|ed)\b|\btop honou?rs\b"
              r"|\bgold medal\b|\bnamed (?:best|the)\b"),
    ("sustainability", r"\bsustainab|\brecycl|\bcarbon\b|\bplastic\b|\bno-?straw\b|\bgood spirited\b"),
    # `cocktail_program` outranks `sponsorship`: "USBG LEGACY COCKTAIL COMPETITION, SPONSORED BY
    # BACARDÍ" is a cocktail programme whose funding mechanism happens to be a sponsorship, and the
    # programme is the part a bev-alc event feed is for. A pure "OFFICIAL SPIRIT OF <event>" headline
    # has no cocktail keyword and still lands as sponsorship.
    ("cocktail_program", r"\bcocktail (?:competition|coverage|program)|\blegacy cocktail\b|\brecipe\b"),
    ("sponsorship", r"\bsponsor(?:s|ed|ship)\b|\bofficial (?:partner|spirit|drink)\b|\bpresenting partner\b"),
]

# Market gazetteer — deliberately conservative. A headline saying "TORONTO" is evidence of a market;
# a headline saying "MARTINI" is not evidence of Italy. Word-boundary matched, longest first.
_MARKETS = [
    "United States", "United Kingdom", "Puerto Rico", "South Africa", "New Zealand", "Hong Kong",
    "Australia", "Argentina", "Singapore", "Germany", "Scotland", "Ireland", "Jamaica", "Mexico",
    "Canada", "France", "Brazil", "Poland", "Spain", "Italy", "India", "China", "Japan", "Cuba",
    "UK", "USA", "US",
    "New York", "Los Angeles", "San Francisco", "Las Vegas", "Miami", "Chicago", "Toronto",
    "London", "Paris", "Berlin", "Madrid", "Milan", "Sydney", "Dubai", "Amsterdam", "Edinburgh",
]
_MARKET_RE = [(m, re.compile(r"\b%s\b" % re.escape(m), re.I)) for m in _MARKETS]

# Price: the literal currency amount as written. Extraction is DETERMINISTIC (the string is verbatim
# in the source); whether that price refers to THIS product is not, which is why `price` is only
# emitted when it co-occurs with a launch/availability event and the raw match is kept in the title.
_PRICE_RE = re.compile(r"(?P<cur>[$£€]|USD|GBP|EUR)\s?(?P<amt>\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.I)
_CURRENCY = {"$": "USD", "£": "GBP", "€": "EUR", "USD": "USD", "GBP": "GBP", "EUR": "EUR"}

_YEAR_FOLDER = re.compile(r"^(19|20)\d{2}$")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── the chokepoint ────────────────────────────────────────────────────────────────────────────────
def asset_bytes(rec, asset, timeout=60, max_bytes=25_000_000):
    """THE ONLY place an asset's bytes may be fetched. Gated on `fetch_asset`, so a source whose terms
    prohibit reuse (or say nothing) raises RightsViolation here rather than quietly downloading.

    Deliberately not called anywhere in the P1 path: with the first vendor's terms classified
    `prohibited`, this function exists to be refused, and the test suite asserts exactly that."""
    url = asset.get("asset_url") or asset.get("url")
    rights.require(rec, "fetch_asset", url)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": rights.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes)


def derive_cv_reference(rec, asset, data=None, log=print):
    """Perceptual hash + embedding for the CV reference gallery, gated on `derive_hash` /
    `derive_embedding`. Returns (phash, embedding_ref, withheld_reason).

    A derivative work of a copyrighted image is still governed by the licence on that image, so this
    is gated at the SAME level as retention — not treated as "just a number we computed". When the
    gate refuses, the reason is returned (and lands on the asset row) instead of being swallowed."""
    ok, why = rights.may(rec, "derive_hash")
    if not ok:
        return None, None, why
    if data is None:
        data = asset_bytes(rec, asset)
    phash = hashlib.sha256(data).hexdigest()[:32]     # placeholder digest; true pHash lands with P3
    emb_ref = None
    ok_emb, why_emb = rights.may(rec, "derive_embedding")
    if ok_emb:
        emb_ref = "pending-p3"                        # the CV gallery build is P3, not P1
    else:
        log("embedding withheld: %s" % why_emb)
    return phash, emb_ref, None


# ── asset rows ────────────────────────────────────────────────────────────────────────────────────
def asset_row(rec, vendor, drive, folder_path, f, pulled_at):
    """Normalize one platform file record → a `dam_assets` pointer row, with the rights columns
    resolved from the record. `retention` is the honest statement of what we kept: `pointer_only`
    whenever the gate refuses the bytes."""
    perms = rec.get("permissions") or {}
    may_retain, why = rights.may(rec, "retain_asset")
    return {
        "source_id": rec.get("source_id"), "vendor": vendor,
        "drive_id": drive.get("id"), "drive_name": drive.get("name"),
        "folder_id": f.get("parent_folder_id"), "folder_path": folder_path,
        "asset_id": f.get("id"), "asset_token": f.get("file_token"),
        "name": f.get("name"), "title": f.get("title"),
        "description": (f.get("description") or "").strip(),
        "asset_type": f.get("type"), "extension": (f.get("extension") or "").lower(),
        "mime_type": f.get("mime_type"), "size_bytes": f.get("size"),
        "asset_url": f.get("url"), "thumb_url": f.get("thumb_url"),
        "download_url": f.get("download_url"),
        "created_on": f.get("created_on"), "updated_on": f.get("updated_on"),
        "rights_ref": rights.rights_ref(rec),
        "image_use": perms.get("image_use"), "image_scope": perms.get("scope"),
        "retention": "full" if may_retain else "pointer_only",
        "phash": None, "embedding_ref": None,
        "withheld_reason": None if may_retain else why,
        "pulled_at": pulled_at,
    }


# ── brand events ──────────────────────────────────────────────────────────────────────────────────
_UUID_PREFIX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-", re.I)
_EXT = re.compile(r"\.(pdf|docx?|jpe?g|png|mp4|mov|zip|tif+|svg|webp)\s*$", re.I)
_VARIANT = re.compile(r"[\s,]*\(\s*\d+\s*\)\s*$")


def _norm_title(t):
    """Collapse a headline to a comparison key, so the assets of ONE story become ONE event.

    Three normalizations, each earned from the live drive rather than guessed:
      • a leading upload UUID — DNA prefixes re-uploaded files with a fresh uuid, so five copies of
        one press kit differ ONLY by that prefix and fragmented into five events;
      • repeated/trailing extensions — the drive really does contain "… Hero Image.jpg.jpg";
      • a trailing "(n)" variant counter — "Fleuriste St-Germain Aug 12 (7)" is the seventh frame of
        one shoot, not the seventh launch.
    Applied in a loop because they stack (a UUID-prefixed ".jpg.jpg" needs two extension passes)."""
    t = _UUID_PREFIX.sub("", (t or "").strip())
    for _ in range(3):
        prev = t
        t = _VARIANT.sub("", _EXT.sub("", t)).strip()
        if t == prev:
            break
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


# Brand aliases that are ALSO ordinary words. A headline about a martini (the drink), a patron (the
# customer) or Bombay (the city) is not evidence of the brand — but the string match is identical, so
# the match cannot be labelled DETERMINISTIC. These are matched and kept, and their provenance is
# downgraded to INFERENCE, which is the honest description of what that match actually is.
AMBIGUOUS_ALIASES = {"martini", "patron", "bombay", "banks", "legacy"}


def _mnorm(s):
    """One normalization for BOTH sides of a brand match. Applying it to the text but not the alias
    was a live defect: "DEWAR'S®" normalized to "dewar s" while the alias stayed "dewar's", so every
    Dewar's headline fell through to whatever brand appeared earlier in the line."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", s or "", flags=re.U).lower()).strip()


def match_brand(title, brands):
    """Match a headline against the vendor's declared brand roster: an exact, word-boundary match on
    a literal the vendor publishes, with the matched literal recorded — not a fuzzy guess. Longest
    alias first so "JOHN DEWAR & SONS" beats a bare vendor name earlier in the headline.

    Returns (brand, alias, ambiguous). `ambiguous` is True when the alias that matched is also a
    common word, and it flows into `field_provenance` as INFERENCE rather than DETERMINISTIC."""
    t = " %s " % _mnorm(title)
    cands = []
    for brand, aliases in brands.items():
        for a in aliases:
            na = _mnorm(a)
            if na:
                cands.append((len(na), brand, a, na))
    for _ln, brand, alias, na in sorted(cands, reverse=True):
        if re.search(r"(?<!\w)%s(?!\w)" % re.escape(na), t):
            return brand, alias, na in AMBIGUOUS_ALIASES
    return None, None, False


def classify_event(title):
    for etype, pat in _EVENT_TYPES:
        if re.search(pat, title or "", re.I):
            return etype
    return "other"


def find_market(title):
    for name, rx in _MARKET_RE:
        if rx.search(title or ""):
            return name
    return None


def find_price(title):
    m = _PRICE_RE.search(title or "")
    if not m:
        return None, None
    cur = _CURRENCY.get(m.group("cur").upper(), m.group("cur"))
    try:
        return float(m.group("amt").replace(",", "")), cur
    except ValueError:
        return None, None


def derive_events(rec, assets, brands, folder_years=None, log=print):
    """Derive `brand_events` rows from asset titles + folder metadata.

    Grain: one row per (brand, story, event year) — assets of the same story collapse into one event
    carrying `asset_count` and the contributing `source_asset_ids`.

    Provenance, per field:
      brand / hoodie_brand_id  DETERMINISTIC — exact match on a vendor-published brand literal,
                               downgraded to INFERENCE when the alias that matched is also a common
                               word (see AMBIGUOUS_ALIASES): "MARTINI" in a headline may be the brand
                               or the drink, and a string match cannot tell you which
      event_date               DETERMINISTIC — the folder's own year label, when the source uses one
      title / source_*         DETERMINISTIC — verbatim from the platform record
      price / currency         DETERMINISTIC — the currency literal as written in the headline
      event_type / market      INFERENCE     — a keyword classifier's read of free text
    """
    folder_years = folder_years or {}
    by_key = {}
    for a in assets:
        title = (a.get("title") or a.get("name") or "").strip()
        if not title:
            continue
        brand, alias, ambiguous = match_brand(title, brands)
        if not brand:
            continue
        year = folder_years.get(a.get("folder_id"))
        key = (brand, _norm_title(title), year)
        slot = by_key.setdefault(key, {"brand": brand, "alias": alias, "ambiguous": ambiguous,
                                       "title": title, "year": year, "ids": [], "urls": []})
        slot["ids"].append(a.get("asset_id"))
        if a.get("asset_url") and not slot["urls"]:
            slot["urls"].append(a["asset_url"])

    rows, fetched = [], _now()
    for (brand, nkey, year), s in by_key.items():
        etype = classify_event(s["title"])
        market = find_market(s["title"])
        price, currency = find_price(s["title"])
        # Price is only meaningful on a story about buying the thing; a "$1M donation" headline is
        # not a price point, so it is dropped rather than landed as one.
        if price is not None and etype not in ("launch", "availability", "limited_edition"):
            price, currency = None, None
        eid = "E-" + hashlib.sha1(
            ("%s|%s|%s|%s" % (rec.get("source_id"), brand, nkey, year)).encode("utf-8")).hexdigest()[:16]
        brand_prov = INFERENCE if s.get("ambiguous") else DETERMINISTIC
        prov = {"brand": brand_prov, "hoodie_brand_id": brand_prov, "brand_alias": s.get("alias"),
                "title": DETERMINISTIC,
                "source_asset_ids": DETERMINISTIC, "source_url": DETERMINISTIC,
                "event_date": DETERMINISTIC if year else None,
                "event_date_precision": DETERMINISTIC,
                "event_type": INFERENCE, "market": INFERENCE if market else None,
                "price": DETERMINISTIC if price is not None else None,
                "sku_id": None}
        rows.append({
            "event_id": eid,
            # hoodie_brand_id is the vendor-scoped brand slug until DAM brands are resolved against
            # dim_brand (P2). Naming it now and resolving later beats inventing a fake canon id.
            "hoodie_brand_id": "%s:%s" % (rec.get("source_id"), slug(brand)),
            "brand": brand, "sku_id": None,
            "event_type": etype,
            "event_date": ("%s-01-01" % year) if year else None,
            "event_date_precision": "year" if year else "unknown",
            "market": market, "price": price, "currency": currency,
            "title": s["title"][:400], "asset_count": len(s["ids"]),
            "source": rec.get("source_id"), "source_id": rec.get("source_id"),
            "source_asset_ids": ",".join(str(i) for i in sorted(s["ids"])[:50]),
            "source_url": (s["urls"] or [None])[0],
            "rights_ref": rights.rights_ref(rec),
            "field_provenance": json.dumps({k: v for k, v in prov.items() if v}),
            "fetched_at": fetched,
        })
    return rows


def year_map(folders):
    """folder_id → 4-digit year, for sources that file assets under year folders. Only an exact
    4-digit folder name counts — that is what makes the derived date deterministic rather than a
    guess pulled out of a folder called 'Summer campaign'."""
    return {f["id"]: f["name"] for f in folders if _YEAR_FOLDER.match((f.get("name") or "").strip())}


def folder_paths(folders, root_id):
    """folder_id → 'Home/Images/2018' — the human-readable location, computed from parent links."""
    by_id = {f["id"]: f for f in folders}
    out = {}
    for fid in by_id:
        parts, cur, guard = [], fid, 0
        while cur in by_id and guard < 32:
            parts.append(by_id[cur].get("name") or str(cur))
            if cur == root_id:
                break
            cur = by_id[cur].get("parent_folder_id")
            guard += 1
        out[fid] = "/".join(reversed(parts))
    return out


# ── landing ───────────────────────────────────────────────────────────────────────────────────────
def land(rec, assets, events, log=print):
    """Land both outputs + the rights ledger. Persistent catalogs, so `write_accumulate`
    ([[scraper-write-accumulate]]) — a re-pull of one drive must grow the book, never clobber it."""
    n_a = n_e = 0
    try:
        import warehouse
        if assets:
            warehouse.write_accumulate(ASSETS_TABLE, assets,
                                       key=lambda r: "%s|%s" % (r["source_id"], r["asset_id"]),
                                       fields=ASSET_FIELDS, coverage=False)
            n_a = len(assets)
            log("landed %s: %d asset pointers" % (ASSETS_TABLE, n_a))
        if events:
            warehouse.write_accumulate(EVENTS_TABLE, events, key="event_id",
                                       fields=EVENT_FIELDS, coverage=False)
            n_e = len(events)
            log("landed %s: %d events" % (EVENTS_TABLE, n_e))
    except Exception as e:
        log("dam land skipped: %s" % str(e)[:140])
    rights.land_record(rec, log=log)
    return n_a, n_e


def run_record(source_id, connid, started, total, new, status, warnings, extracts):
    return {"id": "R-DAM" + hashlib.sha1(("%s%s" % (source_id, total)).encode()).hexdigest()[:3].upper(),
            "connId": connid, "startedAt": started, "finishedAt": int(time.time() * 1000),
            "durationMs": 0, "status": status, "trigger": "manual", "total": total,
            "degraded": status != "success", "warnings": warnings, "healed": [],
            "extracts": extracts}


def snapshot_diff(keys, path):
    """new / dropped since the last run, from a JSON snapshot (same shape as the other connectors)."""
    cur = {str(k): 1 for k in keys}
    prev = {}
    if os.path.exists(path):
        try:
            prev = json.load(open(path)).get("cells", {})
        except Exception:
            prev = {}
    new = sum(1 for k in cur if k not in prev)
    dropped = sum(1 for k in prev if k not in cur)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(path, "w"))
    except Exception:
        pass
    return new, dropped
