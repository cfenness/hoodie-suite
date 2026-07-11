"""menu_site.py — pull the DINE-IN beverage menu from a restaurant's OWN website (first-party, persistable).

DoorDash gives complete menus for restaurants that DELIVER alcohol — but most bars/restaurants pour alcohol
DINE-IN ONLY (the NAOP sweep confirms it: most Orlando restaurants list 0–1 drinks for delivery). Their real
drink list lives on their own site. Those sites are wildly heterogeneous (census: no dominant provider —
WordPress/Squarespace/Wix shells around Toast/Square/Chownow embeds, PDFs, images, JS apps), so there is no
deterministic parser. Instead:
  1. DISCOVER the site via Google Maps (the place 'link'), skipping social/aggregator links.
  2. FIND + FETCH the menu page (Unlocker renders JS; markdown for clean text).
  3. EXTRACT beverages with Claude — robust to any layout (the "Claude where unsure" pattern, applied to menus).
  4. CLASSIFY via cocktail_taxonomy and LAND menu_beverages with price_basis='menu_list' (dine-in, NOT delivery-
     inflated). This is the restaurant's OWN content — persistable; Google is used only for discovery.

Needs ANTHROPIC_API_KEY (extraction) + BD creds (fetch), so it runs where the key is (Fly).
    python menu_site.py --name "AC Sky Bar" --site http://www.acskybar.com/
"""
import argparse, base64, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import cocktail_taxonomy as ctx

_SOCIAL = re.compile(r"instagram|facebook|twitter|x\.com|tiktok|yelp|tripadvisor|doordash|ubereats|grubhub|"
                     r"linktr|marriott|hilton|opentable|resy|linktree|youtube|google\.com", re.I)
_MENU_HINTS = re.compile(r"menu|drink|cocktail|wine|beer|bar\b|beverage|libation|spirits", re.I)


def _bd_key():
    k = os.environ.get("BRIGHTDATA_API_KEY", "").strip()      # env first (Fly/CI), else the CLI creds file (Mac)
    if k:
        return k
    return json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]


def _unlock(url, key, dataf=None):
    body = {"zone": "cli_unlocker", "url": url, "format": "raw"}
    if dataf:
        body["data_format"] = dataf
    r = urllib.request.Request("https://api.brightdata.com/request", data=json.dumps(body).encode(),
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=75).read().decode("utf-8", "replace")


def _fetch_bytes(url, key):
    """Raw bytes (no decode) — for PDF menus, which Claude reads natively as a document block."""
    body = {"zone": "cli_unlocker", "url": url, "format": "raw"}
    r = urllib.request.Request("https://api.brightdata.com/request", data=json.dumps(body).encode(),
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=90).read()


def discover_site(name, near="Orlando FL", key=None):
    """Google Maps (via BD) -> the restaurant's own website, skipping social / aggregator links."""
    key = key or _bd_key()
    q = urllib.parse.quote("%s %s" % (name, near))
    try:
        j = json.loads(_unlock("https://www.google.com/maps/search/%s/?brd_json=1&hl=en&gl=us" % q, key))
    except Exception:
        return None
    for p in j.get("organic", []):
        link = p.get("link") or ""
        if link and not _SOCIAL.search(link):
            return link
    return None


def find_menu_url(home_html, base):
    """Follow the first menu/drinks link on the homepage; fall back to the homepage itself."""
    for href in re.findall(r'href="([^"#]+)"', home_html or ""):
        if _MENU_HINTS.search(href) and not re.search(r"\.(jpg|jpeg|png|gif|css|js|ico|svg)(\?|$)", href, re.I):
            return urllib.parse.urljoin(base, href)
    return base


def claude_extract(text, restaurant, model="claude-opus-4-8"):
    """Claude reads the menu text -> [{name, description, price}] for DRINKS only. Opt-in on ANTHROPIC_API_KEY;
    no-op (returns []) without it, so the caller degrades gracefully. Persists OUR extraction, not a source copy."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not text:
        return []
    prompt = ("This is text from the restaurant \"%s\". Extract ONLY the BEVERAGES that are (or could be) "
              "alcoholic — cocktails, beer, wine, sake, spirits, hard seltzer/cider — PLUS any non-alcoholic "
              "cocktails / mocktails. Ignore food, soda, coffee, tea, juice, water, and section headers. Return "
              "ONLY a JSON array of objects {\"name\": string, \"description\": string, \"price\": number|null}. "
              "If there are no drinks, return []. Menu text:\n\n%s" % (restaurant, text[:14000]))
    body = json.dumps({"model": model, "max_tokens": 4096,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=120).read())
        txt = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        return json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
    except Exception:
        return []


def _browser_auth():
    key = _bd_key()
    r = urllib.request.Request("https://api.brightdata.com/zone/passwords?zone=cli_browser",
                               headers={"Authorization": "Bearer " + key})
    return "brd-customer-hl_32bcfbaa-zone-cli_browser:%s" % json.loads(
        urllib.request.urlopen(r, timeout=30).read())["passwords"][0]


_MENU_PATHS = ["/menu", "/menus", "/drinks", "/drink-menu", "/cocktails", "/bar", "/bar-menu",
               "/wine", "/wine-list", "/beverages", "/food-and-drink"]


_DRINK_LINK = re.compile(r"cocktail|bottle|\bdrink|wine|beer|\bbar\b|beverage|libation|spirit|liquor|"
                         r"\bmenu|\blist\b|tap\b|draft|by the glass", re.I)


def render_menu_assets(site, key, log=print, max_items=8, max_pages=8):
    """BD Browser walks the drink/menu nav TWO levels deep (menus nest: /menu -> COCKTAILS/BOTTLES ->
    the list, often a PDF). HTML/JS pages -> viewport screenshots; PDF menus -> raw bytes (Claude reads PDFs
    natively). -> [{'kind':'image'|'pdf','data':bytes,'url':str}] — the uniform substrate for any menu format."""
    from playwright.sync_api import sync_playwright
    auth = _browser_auth()
    base = re.match(r"https?://[^/]+", site).group(0)
    items, visited, pages = [], set(), 0

    def is_pdf(u):
        return u.lower().split("?")[0].endswith(".pdf")

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("wss://%s@brd.superproxy.io:9222" % auth, timeout=90000)
        try:
            ctx = b.contexts[0] if b.contexts else b.new_context()
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.set_viewport_size({"width": 1280, "height": 1600})

            def drink_links():
                links = pg.eval_on_selector_all(
                    "a", "els=>els.map(a=>[(a.textContent||'').trim().toLowerCase(), a.href])")
                return [h.split("#")[0] for t, h in links
                        if h and h.startswith(base) and _DRINK_LINK.search(t or "") and h.split("#")[0] not in visited]

            try:                                               # homepage: collect nav (don't shoot the hero)
                pg.goto(site, wait_until="domcontentloaded", timeout=45000); time.sleep(3.5)
                queue = drink_links()
            except Exception:
                queue = []
            for pth in _MENU_PATHS:
                if base + pth not in queue:
                    queue.append(base + pth)

            while queue and len(items) < max_items and pages < max_pages:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                if is_pdf(url):                                # PDF menu -> fetch bytes, no render needed
                    try:
                        items.append({"kind": "pdf", "data": _fetch_bytes(url, key), "url": url}); pages += 1
                    except Exception:
                        pass
                    continue
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    continue
                time.sleep(3.0)
                blurb = pg.evaluate("(document.title + ' ' + (document.body ? document.body.innerText : '')).slice(0,400)").lower()
                if re.search(r"not found|can'?t be found|\b404\b|doesn'?t exist|page you.{0,20}looking for", blurb):
                    continue                                   # 404 / missing (guessed path) — skip
                pages += 1
                total = pg.evaluate("Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
                y = 0
                while y < total and len(items) < max_items:
                    pg.evaluate("window.scrollTo(0, %d)" % y); time.sleep(0.6)
                    items.append({"kind": "image", "data": pg.screenshot(), "url": url}); y += 1600
                    if y >= 1600 * 4:
                        break
                for h in drink_links():                        # follow COCKTAILS/BOTTLES tiles + PDF links
                    if h not in queue:
                        queue.append(h)
        finally:
            b.close()
    log("  [menu] %d menu assets (%d PDF) across %d pages from %s"
        % (len(items), sum(1 for i in items if i["kind"] == "pdf"), pages, site))
    return items


def claude_vision_extract(assets, name, model="claude-opus-4-8"):
    """Claude reads menu screenshots (image blocks) AND PDF menus (document blocks) -> [{name, description,
    price}] for drinks. Opt-in on ANTHROPIC_API_KEY; handles any menu format uniformly. Persists OUR extraction."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not assets:
        return []
    content = []
    for a in assets[:8]:
        d = base64.b64encode(a["data"]).decode()
        if a["kind"] == "pdf":
            content.append({"type": "document", "source": {"type": "base64",
                            "media_type": "application/pdf", "data": d}})
        else:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": d}})
    content.append({"type": "text", "text":
        "These are screenshots of the menu for \"%s\". Extract every BEVERAGE that is (or could be) alcoholic "
        "— cocktails, beer, wine, sake, spirits, hard seltzer/cider — PLUS non-alcoholic cocktails / mocktails. "
        "Ignore food, soda, coffee, tea, juice, water, and section headers. Return ONLY a JSON array of "
        "{\"name\": string, \"description\": string, \"price\": number|null}. If none, return []." % name})
    body = json.dumps({"model": model, "max_tokens": 4096,
                       "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=180).read())
        txt = "".join(bl.get("text", "") for bl in r.get("content", []) if bl.get("type") == "text")
        return json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
    except Exception:
        return []


def _menu_text(url, key):
    """Menu page as clean text — markdown first; if JS left it near-empty, strip tags off the raw render."""
    md = _unlock(url, key, dataf="markdown")
    if len(md) >= 500:
        return md
    raw = _unlock(url, key)
    return re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I))
    # note: image/PDF-only menus still yield little — those need vision/OCR (a follow-on layer)


def pull(name, site=None, near="Orlando FL", log=print):
    """Discover -> render (JS/image/PDF) -> Claude extract -> classify. Returns menu_beverages rows (no write)."""
    key = _bd_key()
    site = site or discover_site(name, near, key)
    if not site:
        log("  [menu] %-30s -> no website" % name[:30]); return []
    assets = render_menu_assets(site, key, log=lambda *a: None)
    items = claude_vision_extract(assets, name)
    menu_url = assets[0]["url"] if assets else site
    if not items:                                               # fallback: text extraction (plain-HTML menus)
        try:
            items = claude_extract(_menu_text(find_menu_url(_unlock(site, key), site), key), name)
        except Exception:
            items = []
    run_id = "menu-" + time.strftime("%Y%m%d-%H%M%S")
    rows = []
    for it in items:
        nm = (it.get("name") or "").strip()
        if not nm:
            continue
        b = ctx.classify_beverage(nm, it.get("description") or "")
        if not (b["is_alcoholic"] or b["category"] == "mocktail"):
            continue
        rows.append(dict(account=name, name=nm, description=(it.get("description") or "")[:300],
                         price=it.get("price"), price_basis="menu_list", category=b["category"],
                         is_alcoholic=b["is_alcoholic"], root=b.get("root", ""), sub=b.get("sub", ""),
                         base_spirit=b.get("base_spirit", ""), beer_style=b.get("beer_style", ""),
                         source="website", source_url=menu_url, run_id=run_id))
    log("  [menu] %-30s -> %d beverages (%s)" % (name[:30], len(rows), menu_url.split("/")[-1][:30]))
    return rows


def run(name, site=None, near="Orlando FL", log=print):
    rows = pull(name, site, near, log)
    if rows:
        warehouse.write_parquet("menu_beverages", rows)
    return len(rows)


def fan(names, near="Orlando FL", log=print):
    """Fan the pull across many accounts; accumulate and MERGE with existing menu_beverages, write once
    (re-pulled accounts replaced, others preserved). This is how we sweep the dine-in on-premise gap."""
    def _merge_write(rows):
        try:
            existing = warehouse.query("menu_beverages", "SELECT * FROM t")
        except Exception:
            existing = []
        pulled = {r["account"] for r in rows}
        merged = [r for r in existing if r.get("account") not in pulled] + rows
        if merged:
            warehouse.write_parquet("menu_beverages", merged)
        return len(merged)

    allrows, hit = [], 0
    for i, nm in enumerate(names):
        try:
            r = pull(nm, near=near, log=log)
            allrows += r; hit += 1 if r else 0
        except Exception as e:
            log("  [menu] %-30s -> FAILED %s" % (nm[:30], str(e)[:40]))
        if (i + 1) % 15 == 0:                                    # checkpoint — a multi-hour sweep saves progress
            tot = _merge_write(allrows)
            log("  ...%d/%d accounts · %d with drinks · %d beverages (checkpoint -> %d total)"
                % (i + 1, len(names), hit, len(allrows), tot))
    tot = _merge_write(allrows)
    log("[menu] FAN done: %d/%d accounts had drinks · %d beverages -> menu_beverages (%d total)"
        % (hit, len(names), len(allrows), tot))
    return len(allrows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--site", default="")
    ap.add_argument("--near", default="Orlando FL")
    a = ap.parse_args()
    run(a.name, site=a.site or None, near=a.near)
