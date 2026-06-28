"""brightdata.py — fetch bot-walled / JS-rendered retailer pages via Bright Data Web Unlocker.

The polite stdlib scraper (abc_fws_scraper) works for ABC FWS because abcfws.com serves
bots and renders prices server-side. The other chains can't be reached that way:
  • Total Wine / Binny's / Kroger return 403 to direct requests (CDN bot management).
  • Spec's serves bots but renders prices client-side (Next.js) — nothing in the static HTML.
Bright Data's Web Unlocker handles both: it returns clean HTML/markdown with JS executed and
bot defenses cleared. One authenticated POST per page.

Gated on BRIGHTDATA_API_KEY (set by `bdata login` or exported manually) — inert with no
dependency when unset, so the engine has no new hard requirement. stdlib-only (urllib).

    export BRIGHTDATA_API_KEY=...            # from `bdata login`
    export BRIGHTDATA_UNLOCKER_ZONE=cli_unlocker   # optional; this is the default zone
"""
import json, os, urllib.request

API = "https://api.brightdata.com/request"


def enabled():
    return bool(os.environ.get("BRIGHTDATA_API_KEY"))


def zone():
    return os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "cli_unlocker")


def fetch(url, data_format="html", timeout=90):
    """Return the unlocked page as a string (data_format: 'html' | 'markdown').
    Raises RuntimeError if not configured; lets urllib errors propagate to the caller
    (chain scrapers treat a failure as a degraded/failed extract, like the others)."""
    key = os.environ.get("BRIGHTDATA_API_KEY")
    if not key:
        raise RuntimeError("BRIGHTDATA_API_KEY not set — run `bdata login` or export it")
    payload = json.dumps({"url": url, "zone": zone(), "format": "raw",
                          "data_format": data_format}).encode()
    req = urllib.request.Request(API, data=payload, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")
