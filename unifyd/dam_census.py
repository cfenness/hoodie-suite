#!/usr/bin/env python3
"""dam_census.py — map SUPPLIER → media centre → DAM VENDOR → public? → provisional permission class.

This is the multiplier's input. `dam_dna.py` proved that one connector covers every supplier on one
platform; the census is what tells you WHICH platform each supplier is on, so connector work is
spent on the vendors that cover the most suppliers instead of on whoever came up first.

HOW A MEDIA CENTRE IS FOUND — AND THE GUARDRAIL THAT SHAPED IT
  The capability's method rule is: public share drives + documented public APIs + robots-permitted
  paths only, and explicitly **no subdomain enumeration**. The design's own discovery sketch lists
  `media.<co>.com` / `press.<co>.com` / `assets.<co>.com` as patterns to try, which is in tension
  with that rule — guessing hostnames is a mild form of the thing the rule forbids.

  Resolved in favour of the rule, and the result is better discovery anyway:

    PRIMARY (default, always on) — LINK DISCOVERY. Fetch the supplier's own corporate site and read
    the media-centre link THEY PUBLISH. Nothing is guessed; we follow what the company chose to make
    public, which is also how a human would find it. One request per supplier, robots-checked.

    SECONDARY (opt-in, `DAM_CENSUS_PROBE=1`, off by default) — the conventional hostnames. Every row
    records `discovery_method`, so a link-discovered centre and a probed one are never confused in
    the output, and the census can be run entirely within the strict reading of the rule.

  Nothing here touches cert-transparency logs, wordlists, or non-public drives.

WHAT A CENSUS ROW CARRIES
  supplier, corporate domain, media-centre URL, DAM VENDOR (fingerprinted), public-vs-gated, robots
  posture, and a PROVISIONAL permission class from `rights.classify` over the terms we could reach.
  Provisional is the operative word: it is a machine read of a ToS page, it is never a rights record,
  and `dam_census` is a research table — no connector may run off it. Promoting a supplier means
  authoring a reviewed `rights_records/<id>.json` and a TENANTS row, deliberately.

THE TWO PLANS (design §4)
  `plan(url)` emits the extraction plan (vendor, folder API, asset types, public?) AND the rights
  plan (robots, ToS snapshot + classification, needs_counsel) for one media-centre URL. That is what
  `source_analyzer.analyze()` attaches when a page fingerprints as a DAM, so pointing the analyzer at
  a media centre answers "how would we pull it" and "may we" in the same pass.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rights  # noqa: E402

TABLE = "dam_census"
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "dam_census")

CENSUS_FIELDS = [
    "supplier", "corporate_domain", "media_url", "media_host", "dam_vendor", "vendor_signals",
    "vendor_confidence", "kind", "public", "drive_id", "company_id", "reachable", "http_status",
    "robots_allows", "tos_url", "tos_chars", "tos_capture", "image_use", "scope", "confidence",
    "needs_counsel", "provisional", "discovery_method", "connector", "notes", "checked_at",
]

# ── DAM vendor signatures ─────────────────────────────────────────────────────────────────────────
# (vendor, [markers], connector-module-or-None). `connector` is the payoff column: a supplier on a
# vendor we already have a connector for is a TENANTS row away from being a live source.
VENDOR_SIGS = [
    ("DNA", ["window.driveviewstate", "dna-footer-logo", "dnaposthogconfig",
             "/drives/view-new/", "/company-files/"], "dam_dna"),
    ("Bynder", ["bynder.com", "getbynder.com", "window.bynder", "bynder-widget",
                "/api/v4/media", "bynder-cdn"], None),
    ("Brandfolder", ["brandfolder.com", "cdn.brandfolder.io", "brandfolder-assets",
                     "window.brandfolder"], None),
    ("Widen/Acquia DAM", ["widencdn.net", "widencollective.com", "embed.widencdn",
                          "acquia-dam"], None),
    ("Aprimo", ["aprimo.com", "dam.aprimo", "aprimo-dam"], None),
    ("MediaValet", ["mediavalet.com", "mediavalet.net", "mvassets"], None),
    ("Canto", ["canto.com", "canto.global", "cantoflight", "/api_binary/v1/"], None),
    ("Frontify", ["frontify.com", "frontify-cdn", ".frontify.com/d/"], None),
    ("Wedia", ["wedia-group.com", "wedia.fr", "/wedia/"], None),
    ("IntelligenceBank", ["intelligencebank.com"], None),
    ("Third Light", ["thirdlight.com", "/apiv2/"], None),
    # NOT listed: Cloudinary. `res.cloudinary.com` is a CDN host on a large share of the web, so it
    # fingerprinted Heaven Hill as running a DAM on the strength of one image URL. A delivery CDN is
    # not an asset library, and a vendor column that says otherwise sends connector work nowhere.
    ("PhotoShelter", ["photoshelter.com", "libris.photoshelter"], None),
    # Press-room platforms are NOT DAMs, but they publish press releases — which is the `brand_events`
    # half of this capability. Classified separately so the census answers both questions at once
    # rather than reporting "no DAM" for a supplier whose facts are perfectly reachable.
    ("Prezly (press room)", ["prezly.com", ".prezly.com"], None),
    ("Cision MediaRoom (press room)", ["mediaroom.com", "cision.com"], None),
    ("Business Wire (press room)", ["businesswire.com"], None),
]

_PRESS_ONLY = {"Prezly (press room)", "Cision MediaRoom (press room)", "Business Wire (press room)"}

# A page that is a media centre at all — used to avoid classifying a corporate homepage as a DAM
# just because it links to one.
_DAM_WORDS = ("media centre", "media center", "media library", "press kit", "brand assets",
              "asset library", "digital asset", "media assets", "brand portal", "press room",
              "newsroom", "press releases", "media resources", "content hub")

# PRIMARY discovery, in two tiers — because the first cut only matched the tidy phrasing and found
# ONE media centre across 24 suppliers. What these companies actually publish is `/news`,
# `/media`, `news-and-media`, `/our-company/news-and-media.html` — a bare section, not "Media
# Centre". STRONG hints are unambiguous asset libraries; WEAK hints are news/press sections that are
# usually a press room (facts) and sometimes the door to the DAM. Both are followed, strong first,
# and the row records which tier found it.
_LINK_STRONG = re.compile(
    r"(media[-_ ]?(centre|center|library|kit|assets|resources|bank)"
    r"|press[-_ ]?(room|kit|centre|center|office|area)"
    r"|brand[-_ ]?(assets|portal|centre|center|hub|toolkit)"
    r"|newsroom|content[-_ ]?hub|asset[-_ ]?(library|portal)|digital[-_ ]?asset)", re.I)
_LINK_WEAK = re.compile(
    r"(^|[/\-_. ])(news|media|press)([/\-_. ?#]|$)"
    r"|news[-_ &]*(and|&|amp;)?[-_ ]*media|media[-_ &]*(and|&|amp;)?[-_ ]*news", re.I)

# A bare-fetch failure mode that is EVERYWHERE in bev-alc and must be named, not counted as "no
# media centre": the age gate. Reporting it as absence would understate reachable coverage and send
# someone hunting for a link that a human browser would have been shown immediately.
_AGE_GATE = re.compile(r"age[-_ ]?gate|are you (of )?legal drinking age|verify your age"
                       r"|enter your (date of )?birth|drinking age|18\+|21\+", re.I)

# A portal that declares itself gated. Checked BEFORE any other public/private heuristic, because a
# vendor-hosted portal saying "PRIVATE" is the most reliable signal in the whole census.
_PRIVATE_BADGE = re.compile(
    r">\s*PRIVATE\s*<|\bprivate\s+(?:brandfolder|portal|workspace)\b"
    r"|request access to (?:view|this)|sign in to (?:view|access)"
    r"|you (?:do not|don't) have permission|access is restricted|invitation only", re.I)

# Third-party services that reliably match the WEAK hints and are never a media centre. Each of
# these cost a wasted fetch and a junk row before it was listed.
_NOT_MEDIA = re.compile(
    r"(ethicspoint|whistleblower|speakup|sec\.gov|q4inc|q4cdn|investis|nasdaq\.com|londonstockexchange"
    r"|/investors?/|investors?\.|/careers?/|linkedin\.com|twitter\.com|x\.com/|facebook\.com"
    r"|instagram\.com|youtube\.com|tiktok\.com|/privacy|/cookie|/sitemap)", re.I)

# SECONDARY, opt-in only (see the module docstring).
_CONVENTIONAL = ("media.%s", "press.%s", "assets.%s", "newsroom.%s", "brand.%s")

# ── the supplier seed ─────────────────────────────────────────────────────────────────────────────
# The top bev-alc suppliers by global volume/value, with their public corporate domains. A SEED, not
# the universe: `suppliers_from_warehouse()` widens it from the master (dim_supplier / ttb_cola) when
# the warehouse is reachable, and the census reports which source each row came from.
SUPPLIER_SEED = [
    ("Diageo", "diageo.com"),
    ("Pernod Ricard", "pernod-ricard.com"),
    ("Bacardi Limited", "bacardilimited.com"),
    ("Brown-Forman", "brown-forman.com"),
    ("Suntory Global Spirits", "suntoryglobalspirits.com"),
    ("Campari Group", "camparigroup.com"),
    ("Constellation Brands", "cbrands.com"),
    ("E. & J. Gallo Winery", "ejgallo.com"),
    ("Moët Hennessy", "moethennessy.com"),
    ("William Grant & Sons", "williamgrant.com"),
    ("Edrington", "edrington.com"),
    ("Sazerac", "sazerac.com"),
    ("Heaven Hill Brands", "heavenhill.com"),
    ("Rémy Cointreau", "remy-cointreau.com"),
    ("Mast-Jägermeister", "jagermeister.com"),
    ("Proximo Spirits", "proximospirits.com"),
    ("Anheuser-Busch InBev", "ab-inbev.com"),
    ("Molson Coors", "molsoncoors.com"),
    ("Heineken", "theheinekencompany.com"),
    ("Boston Beer Company", "bostonbeer.com"),
    ("Treasury Wine Estates", "tweglobal.com"),
    ("The Wine Group", "thewinegroup.com"),
    ("Ste. Michelle Wine Estates", "smwe.com"),
    ("Duckhorn Portfolio", "duckhorn.com"),
]

# TIER 2 — mid-market and craft. Added because the first census over the top 24 found exactly ONE
# identified DAM vendor: the giants build bespoke media centres (or publish only a press room), while
# companies this size are the ones who BUY Bynder / Brandfolder / Frontify off the shelf. If the
# vendor-recipe multiplier exists anywhere in this trade, it is here — so the census has to look here
# before "build the second connector" can be answered with evidence instead of a guess.
SUPPLIER_SEED_TIER2 = [
    ("Sierra Nevada Brewing", "sierranevada.com"),
    ("New Belgium Brewing", "newbelgium.com"),
    ("Dogfish Head", "dogfish.com"),
    ("Firestone Walker", "firestonebeer.com"),
    ("Lagunitas", "lagunitas.com"),
    ("Deschutes Brewery", "deschutesbrewery.com"),
    ("Founders Brewing", "foundersbrewing.com"),
    ("Stone Brewing", "stonebrewing.com"),
    ("Odell Brewing", "odellbrewing.com"),
    ("Tito's Handmade Vodka", "titosvodka.com"),
    ("Buffalo Trace Distillery", "buffalotracedistillery.com"),
    ("Four Roses Bourbon", "fourrosesbourbon.com"),
    ("Michter's Distillery", "michters.com"),
    ("Uncle Nearest", "unclenearest.com"),
    ("High West Distillery", "highwest.com"),
    ("Westward Whiskey", "westwardwhiskey.com"),
    ("Ole Smoky Distillery", "olesmoky.com"),
    ("Stoli Group", "stoli.com"),
    ("Mark Anthony Brands", "markanthony.com"),
    ("Jackson Family Wines", "jacksonfamilywines.com"),
    ("Delicato Family Wines", "delicato.com"),
    ("Trinchero Family Estates", "tfewines.com"),
    ("Terlato Wines", "terlatowines.com"),
    ("Banfi Vintners", "banfiwines.com"),
    ("Palm Bay International", "palmbay.com"),
    ("Vintage Wine Estates", "vintagewineestates.com"),
]


def _get(url, timeout=20):
    """(html, status, error). Never raises — a census must survive every dead host it meets."""
    req = urllib.request.Request(url, headers={
        "User-Agent": rights.UA,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(3_000_000).decode("utf-8", "replace"), r.getcode(), None
    except urllib.error.HTTPError as e:
        return "", e.code, "HTTP %s" % e.code
    except Exception as e:
        return "", None, "%s: %s" % (type(e).__name__, str(e)[:90])


def _robots_ok(url):
    """(allowed, robots_text). Fails OPEN on an absent robots.txt — absence is not a prohibition —
    but a real Disallow stops the census the same way it stops a connector."""
    p = urllib.parse.urlsplit(url)
    text, _code, err = _get("%s://%s/robots.txt" % (p.scheme or "https", p.netloc), timeout=12)
    if err or not text.strip():
        return True, ""
    return rights.robots_allows(text, url), text


# ── vendor fingerprinting ─────────────────────────────────────────────────────────────────────────
# A DAM vendor's domain appearing in a page proves nothing on its own — it may be a browsable
# PORTAL or it may be a CDN serving two files linked from an ordinary marketing page. Tito's
# `/brand-assets` is the latter: a hand-written page with exactly two `titosvodka.bynder.com/m/…`
# links, no folder tree, no catalogue, nothing to enumerate. Counting it as a DAM would put a
# supplier in the denominator that no vendor connector could ever cover, which is the same mistake
# as the Cloudinary one, a level more precise.
_ASSET_ONLY_PATH = re.compile(r"/m/[0-9a-f]{8,}/|/transform/|/original/|/download/", re.I)


def portal_or_cdn(html, vendor):
    """('portal' | 'cdn_only' | None) for a fingerprinted vendor. `cdn_only` means the vendor host
    appears solely as asset URLs — assets to link, not a library to walk."""
    if not vendor or not html:
        return None
    hosts = [m for _v, markers, _c in VENDOR_SIGS for m in markers if "." in m and "/" not in m]
    refs = []
    for h in hosts:
        refs += re.findall(r"https?://[^\"'\s<>]*%s[^\"'\s<>]*" % re.escape(h), html, re.I)
    if not refs:
        return None
    return "cdn_only" if all(_ASSET_ONLY_PATH.search(r) for r in refs) else "portal"


def detect_vendor(html, url=""):
    """(vendor, signals, confidence). Deterministic marker matching; no guessing.

    `confidence` is `high` on two or more markers, `low` on one — a single mention of "bynder.com"
    could be a stray link, two is a platform. Reported rather than thresholded away, so a low-
    confidence hit is still a lead a human can check."""
    low = (html or "").lower() + " " + (url or "").lower()
    best = None
    for vendor, markers, connector in VENDOR_SIGS:
        hits = [m for m in markers if m in low]
        if hits and (best is None or len(hits) > len(best[1])):
            best = (vendor, hits, connector)
    if not best:
        return None, [], None, None
    vendor, hits, connector = best
    return vendor, hits, ("high" if len(hits) >= 2 else "low"), connector


def _kind_of(html):
    """When no vendor fingerprints, say what the page IS. A supplier with no DAM but a working press
    room is a `brand_events` source, and reporting it as a blank would hide that."""
    low = (html or "").lower()
    if re.search(r"(download|asset|image|photo|logo)s?\b[^<]{0,40}(pack|kit|library|gallery)"
                 r"|media (kit|library|assets)|brand assets|press kit", low):
        return "media_page"
    if re.search(r"press release|news release|newsroom|latest news|media release", low):
        return "press_room"
    return None


def looks_like_media_centre(html):
    low = (html or "").lower()
    return sum(1 for w in _DAM_WORDS if w in low) >= 1


def _vendor_host_links(html, base_url):
    """Links that point straight at a KNOWN DAM VENDOR's domain. The strongest possible signal — a
    supplier whose site links `acme.bynder.com` has told you the answer — and it needs no keyword to
    be in the link text at all, which is why it is looked for separately."""
    out, seen = [], set()
    hosts = {m for _v, markers, _c in VENDOR_SIGS for m in markers if "." in m and "/" not in m}
    for m in re.finditer(r'href=["\'](https?://[^"\']+)["\']', html or "", re.I):
        full = m.group(1)
        p = urllib.parse.urlsplit(full)
        if any(h in p.netloc.lower() for h in hosts) and p.netloc not in seen:
            seen.add(p.netloc)
            out.append(urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", "")))
    return out


def find_media_links(html, base_url):
    """PRIMARY discovery: the media links the supplier PUBLISHES on its own site, ranked.

    Order is (vendor-host, strong, weak) × (off-site before on-site). Off-site first because a
    supplier that hosts its DAM on a vendor domain is exactly what the census is looking for; a
    same-site `/news` is usually a press room, which is still a `brand_events` win."""
    if not html:
        return []
    host = urllib.parse.urlsplit(base_url).netloc.lower().lstrip("www.")
    found, seen = [], set()

    for u in _vendor_host_links(html, base_url):
        if u not in seen:
            seen.add(u)
            found.append((0, u, "vendor-host"))

    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        hay = href + " " + text
        strong = bool(_LINK_STRONG.search(hay))
        if not strong and not _LINK_WEAK.search(hay):
            continue
        full = urllib.parse.urljoin(base_url, href.strip())
        p = urllib.parse.urlsplit(full)
        if p.scheme not in ("http", "https") or not p.netloc:
            continue
        full = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))
        if full in seen or full.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(full)
        if _NOT_MEDIA.search(full):
            continue
        offsite = p.netloc.lower().lstrip("www.") != host
        # STRONG off-site first (a real portal on a vendor domain), but WEAK off-site LAST. Measured:
        # `secure.ethicspoint.com/domain/media/...` — a whistleblower hotline — outranked Diageo's own
        # `/en/news-and-media` purely for being off-site, and an off-site weak match is far more often
        # a third-party service that happens to have "media" in its path than a supplier's newsroom.
        rank = (1 if strong else 3) + ((0 if strong else 1) if offsite else (1 if strong else 0))
        found.append((rank, full, "published-link-strong" if strong else "published-link-weak"))
    return [(u, why) for _r, u, why in sorted(found, key=lambda t: t[0])]


def conventional_hosts(domain):
    return ["https://" + pat % domain for pat in _CONVENTIONAL]


# ── sitemap discovery: the answer to the age gate ─────────────────────────────────────────────────
# Six of 24 suppliers published no findable link on the first census, and the largest cause was the
# AGE GATE — bev-alc corporate sites serve an interstitial to a bare fetch, so the nav never renders.
#
# The wrong fix is to defeat the gate. It is a control the site chose to put up, and forging the
# consent it asks for is not something this capability does, whatever the mechanism.
#
# The right fix costs nothing and circumvents nothing: an age-gated site still publishes
# `sitemap.xml`, which is a DOCUMENTED PUBLIC INDEX of the very URLs it wants indexed — robots-
# checked like everything else. If the media section is public enough to be in the sitemap, it is
# public enough to look at.
_SITEMAP_HINT = re.compile(r"(media|press|news|brand[-_]?asset|newsroom)", re.I)


def sitemap_media_urls(domain, limit=8, log=print):
    """Media/press URLs from a supplier's own sitemap. Follows one level of sitemap index."""
    base = "https://" + domain
    seen_maps, urls = set(), []

    def _read(u, depth=0):
        if u in seen_maps or depth > 1 or len(urls) >= limit:
            return
        seen_maps.add(u)
        if not _robots_ok(u)[0]:
            return
        body, _st, err = _get(u, timeout=20)
        if err or not body:
            return
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body, re.I)
        # A sitemap INDEX lists more sitemaps; prefer the ones whose own name hints at media.
        children = [l for l in locs if l.lower().endswith((".xml", ".xml.gz"))]
        if children:
            for c in sorted(children, key=lambda c: 0 if _SITEMAP_HINT.search(c) else 1)[:4]:
                _read(c, depth + 1)
            return
        for loc in locs:
            if _SITEMAP_HINT.search(loc) and not _NOT_MEDIA.search(loc) and loc not in urls:
                urls.append(loc)
                if len(urls) >= limit:
                    return

    for candidate in ("%s/sitemap.xml" % base, "%s/sitemap_index.xml" % base,
                      "%s/sitemap-index.xml" % base):
        _read(candidate)
        if urls:
            break
    if urls:
        log("    sitemap: %d media/press URL(s) on %s" % (len(urls), domain))
    return urls


# ── the two plans (design §4) ─────────────────────────────────────────────────────────────────────
def extraction_plan(url, html=None, status=None):
    """How this media centre would be pulled: vendor, whether we already have a connector, and — for
    vendors we've cracked — the drive/tenant identifiers a TENANTS row needs."""
    if html is None:
        html, status, _err = _get(url)
    vendor, signals, conf, connector = detect_vendor(html, url)
    surface = portal_or_cdn(html, vendor)
    if surface == "cdn_only":
        # The vendor is real but there is no library here — record it as an asset page, not a DAM,
        # so it never enters the reachability denominator.
        vendor, connector = None, None
    plan = {"url": url, "http_status": status, "dam_vendor": vendor, "vendor_surface": surface,
            "vendor_signals": signals, "vendor_confidence": conf, "connector": connector,
            "kind": ("press_room" if vendor in _PRESS_ONLY
                    else "dam" if vendor else ("vendor_cdn_page" if surface == "cdn_only" else None)),
            "is_media_centre": looks_like_media_centre(html), "public": None,
            "drive_id": None, "company_id": None, "asset_types": []}
    if connector == "dam_dna":
        try:
            import dam_dna
            fp = dam_dna.fingerprint(url, log=lambda *a: None)
            plan.update(drive_id=fp.get("drive_id"), company_id=fp.get("company_id"),
                        public=bool(fp.get("is_public")) if fp.get("is_public") is not None else None)
            st, _err = dam_dna.parse_bootstrap(html or "")
            if st:
                files = ((st.get("tree") or {}).get("files") or [])
                plan["asset_types"] = sorted({f.get("type") for f in files if f.get("type")})
        except Exception:
            pass
    # PUBLIC vs PRIVATE is the column that decides whether a connector is even buildable, and it was
    # reading None for the case that matters most. Trinchero's Brandfolder prints "PRIVATE" next to
    # "12,610 Assets" on its own landing page, and its anonymous API token returns 403 — a portal we
    # must not, and cannot, enumerate. A vendor fingerprint with no public surface is a dead end, and
    # the census has to say so or it sends connector work at a login wall.
    if _PRIVATE_BADGE.search(html or ""):
        plan["public"] = False
        plan["gated_reason"] = "portal declares itself private / access-restricted"
        return plan
    if plan["public"] is None and html and not connector:
        # A login wall is the honest "gated" signal; its absence is not proof of public, so this only
        # ever sets False.
        #
        # And ONLY when we have no connector for the vendor. A media-centre LANDING page almost
        # always offers a sign-in — Bacardi's does — while the public drive behind it needs no auth
        # at all. Reading the landing page as "gated" would have marked the one supplier we have
        # fully working as a low-priority lead. For a known vendor, public-ness is answered by that
        # vendor's own read of a DRIVE url, not by a login link in the header.
        low = html.lower()
        if re.search(r"(sign in|log ?in|request access|username|password)\b", low):
            plan["public"] = False
    if connector and plan["public"] is None:
        plan["landing_page_only"] = True
    return plan


def rights_plan(url, log=print):
    """The permission half: robots posture + the best terms page we can reach for this host, run
    through `rights.classify`. PROVISIONAL — a machine read, never a rights record.

    Terms are looked for where sites actually put them: a link on the page, then the conventional
    paths on the SAME host. If the body is client-rendered (below `rights.MIN_TOS_CHARS`) that is
    reported as `client-rendered`, because a 94-character terms page classifies as `silent` for the
    wrong reason and must not read as 'they said nothing'."""
    allowed, _robots = _robots_ok(url)
    out = {"robots_allows": allowed, "tos_url": None, "tos_chars": 0, "tos_capture": None,
           "image_use": None, "scope": None, "confidence": None, "needs_counsel": None,
           "provisional": True}
    if not allowed:
        out["tos_capture"] = "not-fetched (robots disallows)"
        return out
    html, _st, _err = _get(url)
    cands = []
    for m in re.finditer(r'href=["\']([^"\']*(?:terms|legal|conditions|use-policy)[^"\']*)["\']',
                         html or "", re.I):
        cands.append(urllib.parse.urljoin(url, m.group(1)))
    p = urllib.parse.urlsplit(url)
    for path in ("/terms", "/terms-and-conditions", "/terms-of-use", "/legal"):
        cands.append("%s://%s%s" % (p.scheme, p.netloc, path))
    for cand in list(dict.fromkeys(cands))[:6]:
        text, note = rights.fetch_tos_text(cand)
        if not text.strip():
            continue
        out.update(tos_url=cand, tos_chars=len(text),
                   tos_capture=note or "http")
        if note == "client-rendered":
            # Do not classify a shell page — that is how "we couldn't read it" becomes "they were
            # silent", which under this capability's rules is a permission-shaped verdict.
            out["needs_counsel"] = True
            return out
        c = rights.classify(text)
        out.update(image_use=c["image_use"], scope=c["scope"], confidence=c["confidence"],
                   needs_counsel=c["needs_counsel"])
        return out
    out["tos_capture"] = "no terms page found"
    out["needs_counsel"] = True
    return out


def plan(url, log=print):
    """design §4: one media-centre URL → {extraction, rights}. No DAM ships off this — it is the
    research artefact that tells you whether authoring a rights record is worth the effort."""
    html, status, err = _get(url)
    ex = extraction_plan(url, html=html, status=status)
    ex["error"] = err
    return {"extraction": ex, "rights": rights_plan(url, log=log)}


# ── the census ────────────────────────────────────────────────────────────────────────────────────
def suppliers_from_warehouse(log=print):
    """Widen the seed from the master when it's reachable. Returns [] (not an error) when it isn't —
    the seed alone still satisfies the census's ~20-supplier target."""
    try:
        import warehouse
        rows = warehouse.query(
            "dim_supplier",
            "SELECT DISTINCT supplier_name FROM t WHERE supplier_name IS NOT NULL LIMIT 200")
        return [(r["supplier_name"], None) for r in rows or []]
    except Exception as e:
        log("dam_census: dim_supplier unavailable (%s) — seed list only" % str(e)[:80])
        return []


def census_one(supplier, domain, probe=None, log=print):
    """One supplier → its census row(s). Always returns at least one row: a supplier with no findable
    media centre is a RESULT (and a lead for a human), not an omission."""
    probe = (os.environ.get("DAM_CENSUS_PROBE") == "1") if probe is None else probe
    checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    base = {"supplier": supplier, "corporate_domain": domain, "checked_at": checked,
            "provisional": True}
    if not domain:
        return [dict(base, reachable=False, notes="no corporate domain known", media_url=None,
                     discovery_method="none")]

    home = "https://" + domain
    allowed, _r = _robots_ok(home)
    if not allowed:
        return [dict(base, reachable=False, robots_allows=False, media_url=None,
                     discovery_method="none", notes="robots.txt disallows the corporate site")]
    html, status, err = _get(home)
    cands = find_media_links(html, home)[:3]
    if not cands:
        # The site published nothing a bare fetch could see — age gate, JS shell, or a block. Its own
        # sitemap is a documented public index and circumvents nothing.
        cands = [(u, "sitemap") for u in sitemap_media_urls(domain, log=log)[:3]]
    if not cands and probe:
        cands = [(u, "conventional-host") for u in conventional_hosts(domain)]
    if not cands:
        # Name the reason. "No media centre" and "we were shown an age gate" are different findings,
        # and only one of them means a human should stop looking.
        why = err or "no media/press link published on %s" % domain
        if html and _AGE_GATE.search(html[:200000]):
            why = ("AGE GATE on the corporate site and no media/press URL in its sitemap — needs a "
                   "human to look (the gate is a control we do not defeat)")
        elif html is not None and 0 < len(html) < 4000:
            why = "corporate site is a client-rendered shell (%d bytes) — no links in the HTML" % len(html)
        return [dict(base, reachable=bool(html), http_status=status, robots_allows=True,
                     media_url=None, discovery_method="published-link", notes=why)]

    rows = []
    for url, method in cands:
        h, st, e = _get(url)
        if not h:
            rows.append(dict(base, media_url=url, media_host=urllib.parse.urlsplit(url).netloc,
                             discovery_method=method, reachable=False, http_status=st,
                             notes=e or "unreachable"))
            time.sleep(0.6)
            continue
        ex = extraction_plan(url, html=h, status=st)
        rp = rights_plan(url, log=log)
        rows.append(dict(
            base, media_url=url, media_host=urllib.parse.urlsplit(url).netloc,
            discovery_method=method, reachable=True, http_status=st,
            dam_vendor=ex["dam_vendor"], vendor_signals=",".join(ex["vendor_signals"] or []),
            vendor_confidence=ex["vendor_confidence"], kind=ex["kind"] or _kind_of(h),
            public=ex["public"],
            drive_id=ex["drive_id"], company_id=ex["company_id"], connector=ex["connector"],
            robots_allows=rp["robots_allows"], tos_url=rp["tos_url"], tos_chars=rp["tos_chars"],
            tos_capture=rp["tos_capture"], image_use=rp["image_use"], scope=rp["scope"],
            confidence=rp["confidence"], needs_counsel=rp["needs_counsel"],

            notes=(ex.get("gated_reason") or
                   "landing page — drives not enumerated; public-ness comes from the connector"
                   if (ex.get("gated_reason") or ex.get("landing_page_only")) else
                   ("" if ex["dam_vendor"] else
                    "vendor CDN links only (%s) — assets to link, not a library to walk"
                    % (",".join(ex["vendor_signals"] or []) or "?") if ex.get("vendor_surface") == "cdn_only" else
                    ("media centre, vendor unidentified" if ex["is_media_centre"]
                     else "not a media centre")))))
        time.sleep(0.8)
        # Stop at the first CONCLUSIVE candidate. Without this, a supplier whose newsroom links to
        # four sibling news pages produced four near-identical rows and read as four findings.
        if ex["dam_vendor"] and ex["dam_vendor"] not in _PRESS_ONLY:
            break                                   # a real DAM — nothing better to find
        if rp.get("image_use") and rows[-1].get("kind"):
            break                                   # a classified media/press page — enough
    return rows


def run(suppliers=None, land=True, probe=None, log=print):
    """The registry entrypoint. Walks the supplier list → `dam_census`."""
    sup = suppliers or (SUPPLIER_SEED + SUPPLIER_SEED_TIER2)
    started = int(time.time() * 1000)
    rows, warns = [], []
    for name, domain in sup:
        try:
            got = census_one(name, domain, probe=probe, log=log)
        except Exception as e:
            got = [{"supplier": name, "corporate_domain": domain, "reachable": False,
                    "notes": "census error: %s" % str(e)[:100],
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]
            warns.append("%s: %s" % (name, str(e)[:80]))
        rows += got
        for r in got:
            log("  %-26s %-34s %-18s %s" % (
                name[:26], (r.get("media_url") or "-")[:34],
                r.get("dam_vendor") or "-",
                "%s/%s" % (r.get("image_use") or "?", r.get("scope") or "?")))

    with_dam = sorted({r["supplier"] for r in rows
                       if r.get("dam_vendor") and r["dam_vendor"] not in _PRESS_ONLY})
    with_press = sorted({r["supplier"] for r in rows
                         if r.get("kind") in ("press_room", "media_page")
                         or r.get("dam_vendor") in _PRESS_ONLY})
    with_grant = sorted({r["supplier"] for r in rows if r.get("image_use") == "permitted"})
    # P5's actual score. "Identified DAM" is not the denominator — a PRIVATE portal cannot be
    # reached by any connector we are willing to write, so counting it would make the ratio a
    # measure of how much we are locked out of. The denominator is PUBLIC DAMs; the numerator is
    # public DAMs a proven connector covers.
    public_dams = sorted({r["supplier"] for r in rows
                          if r.get("dam_vendor") and r["dam_vendor"] not in _PRESS_ONLY
                          and r.get("public") is not False})
    gated_dams = sorted({r["supplier"] for r in rows
                         if r.get("dam_vendor") and r["dam_vendor"] not in _PRESS_ONLY
                         and r.get("public") is False})
    reachable = sorted({r["supplier"] for r in rows
                        if r.get("connector") and r.get("public") is not False})
    pct = round(100.0 * len(reachable) / len(public_dams), 1) if public_dams else None
    covered = sorted({r["supplier"] for r in rows if r.get("connector")})
    if not with_dam:
        warns.append("0 of %d suppliers resolved to a DAM vendor — link discovery may have drifted"
                     % len(sup))
    log("census: %d suppliers, %d rows | %d on an identified DAM vendor (%d already covered by a "
        "connector) | %d with a reachable press room | %d whose terms provisionally PERMIT image use"
        % (len(sup), len(rows), len(with_dam), len(covered), len(with_press), len(with_grant)))
    log("  DAM reachability: %d public / %d gated; %d covered by a proven connector%s"
        % (len(public_dams), len(gated_dams), len(reachable),
           " = %s%%" % pct if pct is not None else ""))

    if land and rows:
        try:
            import warehouse
            warehouse.write_accumulate(
                TABLE, rows,
                key=lambda r: "%s|%s" % (r.get("supplier"), r.get("media_url") or "-"),
                fields=CENSUS_FIELDS, coverage=False)
            log("landed %s: %d rows" % (TABLE, len(rows)))
        except Exception as e:
            log("%s land skipped: %s" % (TABLE, str(e)[:110]))

    status = "degraded" if warns else "success"
    print("HOODIE_RESULT " + json.dumps({
        "status": status, "items_done": len(rows), "items_total": len(rows),
        "suppliers": len(sup), "with_vendor": len(with_dam), "connector_covered": len(covered),
        "press_rooms": len(with_press), "provisional_grants": len(with_grant),
        "public_dams": len(public_dams), "gated_dams": len(gated_dams),
        "reachable_by_connector": len(reachable), "reachable_pct": pct}))
    return rows, {"status": status, "warnings": warns, "suppliers": len(sup),
                  "with_vendor": len(with_dam), "connector_covered": len(covered),
                  "press_rooms": len(with_press), "provisional_grants": len(with_grant),
                  "grant_suppliers": with_grant, "public_dams": public_dams,
                  "gated_dams": gated_dams, "reachable_by_connector": reachable,
                  "reachable_pct": pct}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Census of supplier media centres → DAM vendors.")
    ap.add_argument("--plan", metavar="URL", help="emit the two plans for one media-centre URL")
    ap.add_argument("--supplier", help="census one supplier by name (from the seed)")
    ap.add_argument("--tier", choices=["1", "2", "all"], default="all",
                    help="1 = the majors, 2 = mid-market/craft, all = both (default)")
    ap.add_argument("--probe", action="store_true",
                    help="also try conventional media.* / press.* hostnames (off by default)")
    ap.add_argument("--no-land", action="store_true")
    a = ap.parse_args(argv)
    if a.plan:
        print(json.dumps(plan(a.plan), indent=2, default=str))
        return
    sup = {"1": SUPPLIER_SEED, "2": SUPPLIER_SEED_TIER2,
           "all": SUPPLIER_SEED + SUPPLIER_SEED_TIER2}[a.tier]
    if a.supplier:
        sup = [s for s in (SUPPLIER_SEED + SUPPLIER_SEED_TIER2)
               if s[0].lower().startswith(a.supplier.lower())]
        if not sup:
            print("no seeded supplier matching %r" % a.supplier)
            return
    rows, rep = run(suppliers=sup, land=not a.no_land, probe=a.probe or None)
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
