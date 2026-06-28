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
import json, os, re, shutil, subprocess, urllib.request

API = "https://api.brightdata.com/request"


def _cli():
    return shutil.which("bdata") or shutil.which("brightdata")


def enabled():
    # Usable via the REST key (deploy) OR a logged-in `bdata` CLI (local, after `bdata login`).
    return bool(os.environ.get("BRIGHTDATA_API_KEY")) or bool(_cli())


def zone():
    return os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "cli_unlocker")


def fetch(url, data_format="html", timeout=120):
    """Return the unlocked page as a string (data_format: 'html' | 'markdown').

    Two backends, same result: the REST API when BRIGHTDATA_API_KEY is set (used on the
    deployed container), else the logged-in `bdata` CLI (local dev after `bdata login`, no
    key export needed). Raises RuntimeError if neither is available; lets errors propagate
    so chain scrapers can mark the extract degraded/failed like the others."""
    key = os.environ.get("BRIGHTDATA_API_KEY")
    if key:
        payload = json.dumps({"url": url, "zone": zone(), "format": "raw",
                              "data_format": data_format}).encode()
        req = urllib.request.Request(API, data=payload, method="POST", headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    cli = _cli()
    if cli:
        fmt = "markdown" if data_format == "markdown" else "html"
        p = subprocess.run([cli, "scrape", url, "-f", fmt],
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise RuntimeError("bdata scrape failed: " + (p.stderr or p.stdout or "")[:200])
        # strip the CLI's "Scraping <url>..." status line if it lands on stdout
        return re.sub(r"^\s*Scraping [^\n]*\.\.\.\s*\n", "", p.stdout)
    raise RuntimeError("Bright Data not configured — set BRIGHTDATA_API_KEY or run `bdata login`")
