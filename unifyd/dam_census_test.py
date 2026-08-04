"""Exercise the DAM vendor census — discovery, fingerprinting, and the two plans.

Every case here is one the live 24-supplier run actually produced, so this is a regression suite over
real corporate-site shapes. Pure stdlib; the network is mocked."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dam_census as dc  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# ── vendor fingerprinting ─────────────────────────────────────────────────────────────────────────
print("vendor fingerprint:")
v, sigs, conf, conn = dc.detect_vendor(
    '<footer><img src="/a/global/dna-footer-logo.v2.svg"></footer>'
    '<script>window.DriveViewState = {}; window.DNAPostHogConfig={};</script>', "https://media.x.com/")
check(v == "DNA" and conf == "high", "three DNA markers fingerprint DNA at high confidence")
check(conn == "dam_dna", "...and name the connector that already covers it")

v, _s, conf, _c = dc.detect_vendor('<a href="https://acme.bynder.com/media/">Brand assets</a>', "")
check(v == "Bynder" and conf == "low", "a single marker is a LOW-confidence lead, not a verdict")

v, _s, _c, _n = dc.detect_vendor("<html><body>just a website</body></html>", "")
check(v is None, "an ordinary page fingerprints as no vendor")

# The live false positive this exclusion exists for: a `res.cloudinary.com` image URL is on a large
# share of the web and is a delivery CDN, not an asset library.
v, _s, _c, _n = dc.detect_vendor('<img src="https://res.cloudinary.com/x/image/upload/a.jpg">', "")
check(v is None, "a Cloudinary CDN image does NOT fingerprint as a DAM")

check(any(x[0] == "Business Wire (press room)" for x in dc.VENDOR_SIGS),
      "press-room platforms are catalogued too (they are the brand_events half)")
check("Business Wire (press room)" in dc._PRESS_ONLY, "...and marked as NOT a DAM vendor")

# ── link discovery ────────────────────────────────────────────────────────────────────────────────
print("\nlink discovery (the shapes suppliers actually publish):")
HOME = """<html><body>
  <a href="/our-company/news-and-media.html">News</a>
  <a href="/news">News</a>
  <a href="/en/media?theme=1">Latest News</a>
  <a href="/press-kit">Press Kit</a>
  <a href="https://acme.bynder.com/portal/">Brand Portal</a>
  <a href="https://secure.ethicspoint.com/domain/media/en/gui/1/index.html">SpeakUp</a>
  <a href="https://investors.acme.com/investors/news-releases/">News Releases</a>
  <a href="https://twitter.com/acme">Follow our news</a>
  <a href="/our-brands">Our Brands</a>
</body></html>"""
links = dc.find_media_links(HOME, "https://acme.com")
urls = [u for u, _w in links]

check(urls and "bynder.com" in urls[0],
      "a link to a KNOWN VENDOR's domain ranks first (%s)" % (urls[0] if urls else "none"))
check(links[0][1] == "vendor-host", "...and is labelled vendor-host")
check(any(u.endswith("/press-kit") for u in urls), "a strong hint ('Press Kit') is found")
check(any("news-and-media" in u for u in urls), "'news-and-media' is found (the shape that was missed)")
check(any(u.endswith("/news") for u in urls), "a bare /news section is found")
check(any("/en/media" in u for u in urls), "a bare /media section is found")

# Each of these cost a wasted fetch and a junk row on the live run.
check(not any("ethicspoint" in u for u in urls), "a whistleblower hotline is NOT a media centre")
check(not any("investors" in u for u in urls), "investor-relations news is excluded")
check(not any("twitter.com" in u for u in urls), "a social link is excluded")
check(not any(u.endswith("/our-brands") for u in urls), "an unrelated section is not picked up")

strong_i = next(i for i, u in enumerate(urls) if u.endswith("/press-kit"))
weak_i = next(i for i, u in enumerate(urls) if u.endswith("/news"))
check(strong_i < weak_i, "strong hints outrank weak ones")
check(dc.find_media_links("", "https://acme.com") == [], "no HTML yields no candidates")

# ── the failure classes are NAMED ─────────────────────────────────────────────────────────────────
print("\nfailures are named, not counted as absence:")
check(dc._AGE_GATE.search("<h1>Age Gate</h1><p>Are you of legal drinking age?</p>"),
      "an age gate is recognised (3 of 24 live suppliers)")
check(dc._AGE_GATE.search("please verify your age to continue"), "another age-gate phrasing")
check(not dc._AGE_GATE.search("<p>Our news and media centre</p>"), "an ordinary page is not an age gate")

print("\npage kind when no vendor fingerprints:")
check(dc._kind_of("<h2>Press Releases</h2><p>Latest news from us</p>") == "press_room",
      "a news listing is a press_room (a brand_events source)")
check(dc._kind_of("<h2>Media Kit</h2><a>Download our brand assets</a>") == "media_page",
      "an asset page is a media_page")
check(dc._kind_of("<p>we make drinks</p>") is None, "an ordinary page is neither")

# ── the two plans (design §4) ─────────────────────────────────────────────────────────────────────
print("\nthe two plans:")
DNA_PAGE = ('<html><script>window.DriveViewState = {"tree":{"drive":{"id":42,"company_id":3,'
            '"name":"Public","is_public":1},"files":[{"type":"image"},{"type":"pdf"}],'
            '"all_folders":[{"id":1,"is_root":1,"name":"Home"}]}};</script>'
            '<footer><img src="/a/global/dna-footer-logo.v2.svg"></footer></html>')
ex = dc.extraction_plan("https://media.acme.com/drives/view-new/42", html=DNA_PAGE, status=200)
check(ex["dam_vendor"] == "DNA" and ex["connector"] == "dam_dna",
      "the extraction plan names the vendor and its connector")
check(ex["kind"] == "dam", "a DAM is classified as a dam, not a press room")

# The live misread this guards: a media-centre LANDING page shows a sign-in while the public drive
# behind it needs none. Marking the one supplier we have fully working as 'gated' is the failure.
LANDING = ('<html><body>Media Centre <a href="/login">Sign in</a>'
           '<img src="/a/global/dna-footer-logo.v2.svg">'
           '<script>window.DNAPostHogConfig={}</script></body></html>')
ex2 = dc.extraction_plan("https://media.acme.com/", html=LANDING, status=200)
check(ex2["public"] is None,
      "a sign-in link on a KNOWN vendor's landing page does not mark the supplier gated")
check(ex2.get("landing_page_only") is True, "...it says the drives simply weren't enumerated")

NOVENDOR = '<html><body>Media Library <a href="/login">Log in</a> username password</body></html>'
ex3 = dc.extraction_plan("https://media.other.com/", html=NOVENDOR, status=200)
check(ex3["public"] is False, "for an UNKNOWN vendor a login wall does mark it gated")

# ── the census row ────────────────────────────────────────────────────────────────────────────────
print("\ncensus row shape:")
_saved_get, _saved_robots, _saved_rights = dc._get, dc._robots_ok, dc.rights_plan
PAGES = {"https://acme.com": (HOME, 200, None),
         "https://acme.bynder.com/portal/": ("<html>bynder.com portal brand assets</html>", 200, None)}
dc._get = lambda url, timeout=20: PAGES.get(url.rstrip("/"), PAGES.get(url, ("", 404, "not mocked")))
dc._robots_ok = lambda url: (True, "")
dc.rights_plan = lambda url, log=print: {"robots_allows": True, "tos_url": None, "tos_chars": 0,
                                         "tos_capture": None, "image_use": None, "scope": None,
                                         "confidence": None, "needs_counsel": True, "provisional": True}
rows = dc.census_one("Acme", "acme.com", probe=False, log=lambda *a: None)
dc._get, dc._robots_ok, dc.rights_plan = _saved_get, _saved_robots, _saved_rights

check(rows, "a supplier always produces at least one row")
check(all(set(r) <= set(dc.CENSUS_FIELDS) | {"landing_page_only"} for r in rows),
      "every row key is a declared census field")
check(all(r.get("provisional") for r in rows),
      "EVERY row is marked provisional — a census row is never a rights record")
check(rows[0]["discovery_method"] in ("vendor-host", "published-link-strong", "published-link-weak"),
      "the row records HOW it was discovered (%s)" % rows[0]["discovery_method"])
check(rows[0]["discovery_method"] != "conventional-host",
      "hostname probing did NOT run (it is opt-in, per the method guardrail)")

print("\nno hostname guessing by default:")
check(os.environ.get("DAM_CENSUS_PROBE") != "1", "the probe env flag is off in this environment")
check(dc.conventional_hosts("acme.com")[0] == "https://media.acme.com",
      "the conventional patterns exist but are only reachable through the opt-in flag")

print("\nvendor SURFACE — portal vs CDN (the P5 denominator question):")
CDN_PAGE = ('<html><body><h1>Brand Assets</h1>'
            '<a href="https://acme.bynder.com/m/2ad290b6dd0a885e/original/Logo.eps">Download logo</a>'
            '<a href="https://acme.bynder.com/m/3e757a628fb4ecf9/original/Bottle.png">Download bottle</a>'
            '</body></html>')
check(dc.portal_or_cdn(CDN_PAGE, "Bynder") == "cdn_only",
      "a page whose only vendor links are /m/<hash>/original/ assets is cdn_only")
ex = dc.extraction_plan("https://acme.com/brand-assets", html=CDN_PAGE, status=200)
check(ex["dam_vendor"] is None,
      "a CDN-only page is NOT reported as a DAM (it would inflate the reachability denominator)")
check(ex["kind"] == "vendor_cdn_page", "...it is reported as a vendor CDN page, which is what it is")

PORTAL = ('<html><body><a href="https://acme.bynder.com/portal/">Brand Portal</a>'
          '<script>window.bynder = {};</script>Media Library</body></html>')
check(dc.portal_or_cdn(PORTAL, "Bynder") == "portal", "a browsable portal link is a portal")
check(dc.extraction_plan("https://acme.com/x", html=PORTAL, status=200)["dam_vendor"] == "Bynder",
      "...and IS reported as a DAM")

print("\ngated portals:")
PRIVATE = '<html><body><h1>Acme Portal</h1><span class="badge">PRIVATE</span>12,610 Assets</body></html>'
p = dc.extraction_plan("https://acme-portal.com/", html=PRIVATE, status=200)
check(p["public"] is False, "a portal declaring PRIVATE is marked gated")
check(p.get("gated_reason"), "...with the reason recorded")
check(dc._PRIVATE_BADGE.search("Please sign in to view these assets"), "a sign-in wall reads as gated")

print("\nsitemap discovery (the answer to the age gate):")
check(callable(dc.sitemap_media_urls), "sitemap discovery exists")
check(dc._SITEMAP_HINT.search("https://x.com/newsroom/media-releases"), "a media URL is recognised")
check(not dc._SITEMAP_HINT.search("https://x.com/careers/apply"), "an unrelated URL is not")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
