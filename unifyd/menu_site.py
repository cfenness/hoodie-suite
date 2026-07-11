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
import argparse, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import cocktail_taxonomy as ctx

_SOCIAL = re.compile(r"instagram|facebook|twitter|x\.com|tiktok|yelp|tripadvisor|doordash|ubereats|grubhub|"
                     r"linktr|marriott|hilton|opentable|resy|linktree|youtube|google\.com", re.I)
_MENU_HINTS = re.compile(r"menu|drink|cocktail|wine|beer|bar\b|beverage|libation|spirits", re.I)


def _bd_key():
    return json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]


def _unlock(url, key, dataf=None):
    body = {"zone": "cli_unlocker", "url": url, "format": "raw"}
    if dataf:
        body["data_format"] = dataf
    r = urllib.request.Request("https://api.brightdata.com/request", data=json.dumps(body).encode(),
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    return urllib.request.urlopen(r, timeout=75).read().decode("utf-8", "replace")


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


def _menu_text(url, key):
    """Menu page as clean text — markdown first; if JS left it near-empty, strip tags off the raw render."""
    md = _unlock(url, key, dataf="markdown")
    if len(md) >= 500:
        return md
    raw = _unlock(url, key)
    return re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I))
    # note: image/PDF-only menus still yield little — those need vision/OCR (a follow-on layer)


def run(name, site=None, near="Orlando FL", log=print):
    key = _bd_key()
    site = site or discover_site(name, near, key)
    if not site:
        log("[menu] no website found for %s" % name); return 0
    try:
        home = _unlock(site, key)
    except Exception as e:
        log("[menu] %s fetch failed: %s" % (site, str(e)[:40])); return 0
    menu_url = find_menu_url(home, site)
    text = _menu_text(menu_url, key)
    items = claude_extract(text, name)
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
    if rows:
        warehouse.write_parquet("menu_beverages", rows)
    log("[menu] %-30s -> %d beverages (menu: %s)" % (name[:30], len(rows), menu_url))
    return len(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--site", default="")
    ap.add_argument("--near", default="Orlando FL")
    a = ap.parse_args()
    run(a.name, site=a.site or None, near=a.near)
