#!/usr/bin/env python3
"""preview_shot.py — screenshot + diff a URL for Cockpit's Live Preview panel.

Live Preview (apps/cockpit.html, "Watch it happen") only ever shows CURRENT state — an iframe on a
timer, reloading live while you work. There was no way to see what actually CHANGED: "here's before,
here's after, here's what moved." This adds that, cheaply, with nothing new in requirements.txt:

  capture(url)          one-shot HEADLESS screenshot (PNG bytes) via browser_warm's shared driver
                         resolver. Deliberately NOT Warmer's persistent-profile session — that's built
                         for multi-day anti-bot cookie warming on a handful of known domains; a
                         profile dir per arbitrary pasted preview URL would just accumulate on disk
                         forever for no anti-bot reason. channel="chrome" because the Fly image
                         installs the real Google Chrome binary but never runs `patchright install
                         chromium` — patchright's OWN bundled browser was never downloaded, so a launch
                         without channel="chrome" fails "browser not found" in production (see
                         browser_warm.Warmer's identical choice).
  save(url, png)        stores the PNG + appends to a small per-URL index, via warehouse.put_bytes —
                         same mechanism deploy_drift/dq_aggregate already use for state, just binary
                         instead of JSON. Keeps the last MAX_SNAPSHOTS per URL (a disclosed retention
                         cap on preview history — nothing here is a source of record, unlike a scrape).
  list_snapshots(url)    the stored index for a URL, newest first.
  diff(png_a, png_b)     PIL.ImageChops — no new dependency, Pillow is already a hard requirement (OCR/
                         barcode). All vectorized (PIL C-level ops), never a manual per-pixel Python
                         loop — that would take seconds on a 1300x1400 screenshot. Mismatched sizes are
                         reported and cropped to the shared region, never silently stretched (stretching
                         would fabricate pixel agreement that never existed).

capture() itself has no unit test, same precedent as browser_warm.Warmer: it needs a real browser, so
it's exercised live, not mocked. Everything else here (diff, index bookkeeping, the URL guard) is pure
and fully covered by preview_shot_test.py.
"""
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse

_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_SNAPSHOTS = 20
KEY_RE = re.compile(r"^cockpit_preview/[0-9a-f]{16}/\d+\.png$")

# Only the cloud metadata endpoint is blocked, deliberately narrower than label_reader.ok_url()'s
# guard: THIS feature's primary intended use is previewing a LOCAL dev server while AI works on it
# (the literal ask that started Live Preview — "watch a website update live") so localhost/private
# ranges must stay allowed. What must never be reachable is the instance-credential metadata service.
_METADATA_HOSTS = re.compile(r"^(169\.254\.169\.254|metadata\.google\.internal|fd00:ec2::254)$", re.I)


def ok_preview_url(url):
    """(ok, reason). Blocks non-http(s) schemes and the cloud metadata endpoint only."""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False, "unparseable URL"
    if u.scheme not in ("http", "https"):
        return False, "only http/https URLs are supported"
    host = (u.hostname or "").strip()
    if not host:
        return False, "a URL with a host is required"
    if _METADATA_HOSTS.match(host):
        return False, "cloud metadata endpoints are not allowed"
    return True, ""


def _url_hash(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _index_key(url):
    return "cockpit_preview/%s/index.json" % _url_hash(url)


def _shot_key(url, ts):
    return "cockpit_preview/%s/%d.png" % (_url_hash(url), int(ts))


def valid_key(key):
    """Every reader of a caller-supplied key MUST check this first — it's the only thing standing
    between an authenticated Cockpit user and an arbitrary warehouse.get_bytes() read."""
    return bool(key) and bool(KEY_RE.match(key))


# ── capture (real browser, not unit-tested — see module docstring) ──────────────────────────────────
def capture(url, timeout_ms=15000, width=1300, height=1400):
    ok, reason = ok_preview_url(url)
    if not ok:
        raise ValueError(reason)
    sys.path.insert(0, _DIR)
    import browser_warm
    sync_playwright = browser_warm.sync_playwright_api()
    args = ["--no-sandbox", "--disable-dev-shm-usage"] if os.environ.get("BROWSER_NO_SANDBOX") else []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=args)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(url, timeout=timeout_ms, wait_until="load")
            page.wait_for_timeout(400)          # let late paints/webfonts settle
            return page.screenshot()
        finally:
            browser.close()


# ── storage (pure, injectable for tests) ─────────────────────────────────────────────────────────────
def _load_index(url, get_bytes_fn):
    try:
        raw = get_bytes_fn(_index_key(url))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {"url": url, "snapshots": []}


def save(url, png, ts=None, put_bytes_fn=None, get_bytes_fn=None):
    sys.path.insert(0, _DIR)
    import warehouse
    put_bytes_fn = put_bytes_fn or warehouse.put_bytes
    get_bytes_fn = get_bytes_fn or warehouse.get_bytes
    ts = int(ts if ts is not None else time.time())
    key = _shot_key(url, ts)
    put_bytes_fn(key, png)
    idx = _load_index(url, get_bytes_fn)
    idx["url"] = url
    idx["snapshots"] = [{"ts": ts, "key": key}] + idx.get("snapshots", [])
    idx["snapshots"] = idx["snapshots"][:MAX_SNAPSHOTS]   # disclosed cap, not a data-completeness claim
    put_bytes_fn(_index_key(url), json.dumps(idx).encode())
    return {"ts": ts, "key": key}


def list_snapshots(url, get_bytes_fn=None):
    sys.path.insert(0, _DIR)
    import warehouse
    get_bytes_fn = get_bytes_fn or warehouse.get_bytes
    return _load_index(url, get_bytes_fn).get("snapshots", [])


def load_shot(key, get_bytes_fn=None):
    if not valid_key(key):
        return None
    sys.path.insert(0, _DIR)
    import warehouse
    get_bytes_fn = get_bytes_fn or warehouse.get_bytes
    return get_bytes_fn(key)


# ── diff (pure Pillow, no numpy — Pillow is the only image dependency this repo already carries) ─────
def diff(png_a, png_b, thresh=12):
    """{diff_pct, bbox, resized, size, overlay_png}. `overlay_png` is `b` darkened everywhere, with
    changed pixels painted red — legible as an image, not just a percentage. `thresh` (per-channel,
    0-255) absorbs anti-aliasing/font-hinting jitter between two renders of otherwise-identical
    content; without it, every screenshot would show ~100% "changed" from sub-pixel noise alone."""
    from PIL import Image, ImageChops
    a = Image.open(io.BytesIO(png_a)).convert("RGB")
    b = Image.open(io.BytesIO(png_b)).convert("RGB")
    resized = a.size != b.size
    if resized:
        w, h = min(a.size[0], b.size[0]), min(a.size[1], b.size[1])
        a, b = a.crop((0, 0, w, h)), b.crop((0, 0, w, h))

    delta = ImageChops.difference(a, b)
    r, g, bch = delta.split()
    maxch = ImageChops.lighter(ImageChops.lighter(r, g), bch)      # per-pixel max(|Δr|,|Δg|,|Δb|)
    mask = maxch.point(lambda p: 255 if p > thresh else 0)         # binary changed/unchanged, C-level
    w, h = mask.size
    total = w * h
    changed = mask.histogram()[255] if total else 0
    pct = round(100 * changed / total, 2) if total else 0.0
    bbox = mask.getbbox()

    overlay = Image.eval(b, lambda p: p // 3)                      # darken the "after" image uniformly
    overlay.paste(Image.new("RGB", b.size, (255, 40, 40)), mask=mask)   # paint changed pixels red
    buf = io.BytesIO()
    overlay.save(buf, format="PNG")

    return {"diff_pct": pct, "bbox": list(bbox) if bbox else None,
            "resized": resized, "size": [w, h], "overlay_png": buf.getvalue()}


def main(argv):
    if not argv or argv[0] not in ("capture", "diff"):
        print("usage: preview_shot.py capture <url> <out.png>", file=sys.stderr)
        print("       preview_shot.py diff <a.png> <b.png> <out.png>", file=sys.stderr)
        return 1
    if argv[0] == "capture":
        png = capture(argv[1])
        with open(argv[2], "wb") as f:
            f.write(png)
        print("wrote %s (%d bytes)" % (argv[2], len(png)))
        return 0
    with open(argv[1], "rb") as f:
        a = f.read()
    with open(argv[2], "rb") as f:
        b = f.read()
    d = diff(a, b)
    with open(argv[3], "wb") as f:
        f.write(d.pop("overlay_png"))
    print(json.dumps(d))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
