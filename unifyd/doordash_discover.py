"""doordash_discover.py — enumerate DoorDash store ids per chain, across markets, WITHOUT an account.

Location only gates DISCOVERY; the catalog pull (doordash.py / doordash_full.py) is addressless. Two
account-free discovery methods, unioned:
  1. SERP harvest — DoorDash store pages are Google-indexed; `site:doordash.com/convenience/store "<chain>"`
     returns ids across markets via the Unlocker (light, no browser).
  2. BD Browser + Proxy.setLocation — pin the geo to a target metro and DoorDash's store search lists THAT
     market's stores (complete per-market; the Publix pattern). No address, no login. Verified: setLocation
     Denver returns Denver stores, all different from Orlando's.
Each confirmed id is enriched via a light Unlocker store-page fetch (chain name + city/state/geo), then
landed to `doordash_stores` — the spine that fans the pull connectors nationwide.

    python doordash_discover.py --chains totalwine,circlek,cvs,albertsons
    python doordash_discover.py --chains totalwine --metros 4 --no-browser
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import doordash as dd

# major US metros (lat, lon, label) for the setLocation sweep
METROS = [
    (40.7128, -74.0060, "New York NY"), (34.0522, -118.2437, "Los Angeles CA"), (41.8781, -87.6298, "Chicago IL"),
    (29.7604, -95.3698, "Houston TX"), (33.4484, -112.0740, "Phoenix AZ"), (39.7392, -104.9903, "Denver CO"),
    (47.6062, -122.3321, "Seattle WA"), (37.7749, -122.4194, "San Francisco CA"), (32.7157, -117.1611, "San Diego CA"),
    (28.5383, -81.3792, "Orlando FL"), (25.7617, -80.1918, "Miami FL"), (33.7490, -84.3880, "Atlanta GA"),
    (32.7767, -96.7970, "Dallas TX"), (30.2672, -97.7431, "Austin TX"), (36.1699, -115.1398, "Las Vegas NV"),
    (38.9072, -77.0369, "Washington DC"), (42.3601, -71.0589, "Boston MA"), (45.5152, -122.6784, "Portland OR"),
    (39.9526, -75.1652, "Philadelphia PA"), (35.2271, -80.8431, "Charlotte NC"),
]
# name regex to confirm a candidate really is this chain (Albertsons Cos. trades under many banners)
_CHAIN_MATCH = {
    "totalwine": r"total wine", "circlek": r"circle k", "cvs": r"\bcvs\b", "walgreens": r"walgreens",
    "seveneleven": r"7-?\s*eleven|speedway",
    "albertsons": r"albertsons|safeway|vons|jewel|acme|shaw'?s|randalls|tom thumb|pavilions|star market",
}


def _browser_auth():
    key = json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]
    r = urllib.request.Request("https://api.brightdata.com/zone/passwords?zone=cli_browser",
                               headers={"Authorization": "Bearer " + key})
    return "brd-customer-hl_32bcfbaa-zone-cli_browser:%s" % json.loads(
        urllib.request.urlopen(r, timeout=30).read())["passwords"][0]


def serp_ids(chain_name, key, pages=3):
    ids = set()
    for start in range(0, pages * 30, 30):
        u = "https://www.google.com/search?" + urllib.parse.urlencode(
            {"q": 'site:doordash.com/convenience/store "%s"' % chain_name, "num": "30", "start": start})
        try:
            h = dd._unlock(u, key)
        except Exception:
            continue
        ids.update(re.findall(r'doordash\.com/(?:convenience/)?store/(\d{4,9})', h))
        time.sleep(1)
    return ids


def market_ids(chain_key, chain_name, metros, auth, log=print):
    """Sweep metros via setLocation; return {store_id: name_text} filtered to THIS chain by tile name."""
    from playwright.sync_api import sync_playwright
    q = urllib.parse.quote(chain_name)
    rx = _CHAIN_MATCH.get(chain_key, re.escape(chain_name))
    out = {}
    with sync_playwright() as p:
        for lat, lon, label in metros:
            try:
                b = p.chromium.connect_over_cdp("wss://%s@brd.superproxy.io:9222" % auth, timeout=90000)
                ctx = b.contexts[0] if b.contexts else b.new_context()
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                cdp = ctx.new_cdp_session(pg)
                cdp.send("Proxy.setLocation", {"lat": lat, "lon": lon, "distance": 50, "strict": True})
                pg.goto("https://www.doordash.com/search/store/%s/" % q, wait_until="domcontentloaded", timeout=120000)
                time.sleep(9)
                tiles = pg.evaluate(r"""() => { const m={}; document.querySelectorAll('a[href*="/store/"]').forEach(a=>{
                    const id=((a.getAttribute('href')||'').match(/store\/(\d+)/)||[])[1]; if(!id) return;
                    const t=(a.textContent||'').trim().replace(/\s+/g,' '); if(t&&(!m[id]||t.length>m[id].length)) m[id]=t.slice(0,50);
                  }); return m; }""")
                b.close()
                hits = {i: n for i, n in tiles.items() if re.search(rx, n, re.I)}
                out.update(hits)
                log("  [%s] %-16s → %d %s of %d tiles (running %d)" % (chain_key, label, len(hits), chain_name, len(tiles), len(out)))
            except Exception as e:
                log("  [%s] %-16s failed: %s" % (chain_key, label, str(e)[:50]))
    return out


def enrich(store_id, chain_key, chain_name, key):
    """Light Unlocker store-page fetch → confirm the chain + city/state/geo. None if it's not this chain."""
    try:
        h = dd._unlock("https://www.doordash.com/convenience/store/%s/?pickup=false" % store_id, key)
    except Exception:
        return None
    import html as _html
    title = _html.unescape((re.search(r"<title>([^<|]+)", h) or [None, ""])[1].strip())
    if not re.search(_CHAIN_MATCH.get(chain_key, re.escape(chain_name)), title, re.I):
        return None
    o = dd._parse_outlet(h, store_id, chain_name)
    o.update(chain=chain_key, store_name=title[:70])
    return o


def run(chains, metros=None, use_browser=True, log=print):
    key = dd._api_key()
    metros = METROS if metros is None else metros
    rows, seen = [], set()
    for ch in chains:
        name = dd.CHAINS.get(ch, {}).get("name", ch)
        serp = serp_ids(name, key)                       # ids only (no name) — confirm each via enrich
        log("[%s] SERP → %d ids" % (ch, len(serp)))
        market = market_ids(ch, name, metros, _browser_auth(), log=log) if (use_browser and metros) else {}
        # market ids are already chain-filtered by tile name → enrich only for city/state/geo.
        # serp ids are unnamed → enrich confirms the chain too.
        cand = [(sid, sid in market) for sid in (set(serp) | set(market)) if sid not in seen]
        log("[%s] %d candidate ids (%d market-confirmed) — enriching…" % (ch, len(cand), len(market)))
        kept = 0
        for sid, pre_confirmed in cand:
            seen.add(sid)
            r = enrich(sid, ch, name, key)
            if r:
                r["via"] = "market" if pre_confirmed else "serp"
                rows.append(r); kept += 1
            time.sleep(0.3)
        log("[%s] confirmed %d %s stores" % (ch, kept, name))
    if rows:
        warehouse.write_parquet("doordash_stores", rows)
        log("[discover] %d stores across %d chains -> doordash_stores"
            % (len(rows), len({r["chain"] for r in rows})))
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chains", default="totalwine")
    ap.add_argument("--metros", type=int, default=len(METROS), help="how many metros to sweep (0 = SERP only)")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    run([c.strip() for c in a.chains.split(",") if c.strip()],
        metros=METROS[:a.metros], use_browser=not a.no_browser and a.metros > 0)
