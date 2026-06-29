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


def _cred_paths():
    home = os.path.expanduser("~")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    appdata = os.environ.get("APPDATA")
    out = [os.path.join(home, "Library", "Application Support", "brightdata-cli", "credentials.json")]  # macOS
    if xdg:     out.append(os.path.join(xdg, "brightdata-cli", "credentials.json"))
    out.append(os.path.join(home, ".config", "brightdata-cli", "credentials.json"))                     # linux
    if appdata: out.append(os.path.join(appdata, "brightdata-cli", "credentials.json"))                 # windows
    return out


def _stored_key():
    """The API key `bdata login` saved locally — so the REST datasets API works without a
    manual export, the same way fetch() can use the logged-in CLI."""
    for p in _cred_paths():
        try:
            k = json.load(open(p)).get("api_key")
            if k: return k
        except Exception:
            continue
    return None


def _key():
    return os.environ.get("BRIGHTDATA_API_KEY") or _stored_key()


def enabled():
    # Usable via the REST key (env or `bdata login` store) OR the logged-in `bdata` CLI.
    return bool(_key()) or bool(_cli())


def zone():
    return os.environ.get("BRIGHTDATA_UNLOCKER_ZONE", "cli_unlocker")


def fetch(url, data_format="html", timeout=120):
    """Return the unlocked page as a string (data_format: 'html' | 'markdown').

    Two backends, same result: the REST API when BRIGHTDATA_API_KEY is set (used on the
    deployed container), else the logged-in `bdata` CLI (local dev after `bdata login`, no
    key export needed). Raises RuntimeError if neither is available; lets errors propagate
    so chain scrapers can mark the extract degraded/failed like the others."""
    key = _key()
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


# ---------------- Web Scraper API (managed datasets) ----------------
# For platforms behind hard bot walls that the Unlocker can't clear (e.g. Instacart 403s),
# Bright Data ships managed "datasets" addressed by a dataset_id (from the BD dashboard).
# Trigger a job over input URLs, poll until ready, return the parsed records. REST + key.
DATASETS_API = "https://api.brightdata.com/datasets/v3"


def _ds_req(url, key, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def dataset_run(dataset_id, inputs, fmt="json", poll_timeout=600, poll_every=10):
    """Run a managed dataset over `inputs` ([{"url": ...}, ...]); return the records list.
    Needs BRIGHTDATA_API_KEY (REST). Raises on misconfig / timeout / job failure."""
    key = _key()
    if not key:
        raise RuntimeError("BRIGHTDATA_API_KEY required for the Web Scraper API (managed datasets) — "
                           "export it or run `bdata login`")
    trig = _ds_req(f"{DATASETS_API}/trigger?dataset_id={dataset_id}&format={fmt}",
                   key, "POST", inputs)
    snap = trig.get("snapshot_id") or trig.get("snapshotId")
    if not snap:
        # sync responses (<=20 urls) may return records directly
        return trig if isinstance(trig, list) else trig.get("data", [])
    import time as _t
    waited = 0
    while waited < poll_timeout:
        st = _ds_req(f"{DATASETS_API}/progress/{snap}", key).get("status")
        if st == "ready":
            break
        if st == "failed":
            raise RuntimeError(f"dataset job {snap} failed")
        _t.sleep(poll_every); waited += poll_every
    else:
        raise RuntimeError(f"dataset job {snap} timed out after {poll_timeout}s")
    out = _ds_req(f"{DATASETS_API}/snapshot/{snap}?format={fmt}", key)
    return out if isinstance(out, list) else out.get("data", [])
