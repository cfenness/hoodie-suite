"""doordash_geo.py — GEOGRAPHIC harvest: every alcohol-delivering merchant in a market (chains + INDEPENDENTS).

Chain-targeting misses the long tail; a market's independents (bottle shops, neighborhood bars, single-
location restaurants) are the scan-dark differentiator. We sweep a GRID of setLocation points across the
metro — one pin only sees merchants whose delivery zone reaches it (~few-mi radius), so a lattice with
spacing < that radius, unioned, covers the whole city. At each pin we search alcohol terms, dedup by store
id, and classify each merchant (retail|restaurant · chain|independent). Run to saturation; ground-truth the
found set against the FL license universe (DBPR/ABT) to measure coverage and find gaps.

    python doordash_geo.py --market orlando            # full grid (background job)
    python doordash_geo.py --market orlando --points 3 # smoke test
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse

ALCOHOL_TERMS = ["liquor", "wine", "beer", "spirits", "bar", "cocktails"]


def _grid(lat0, lat1, lon0, lon1, step=0.07):
    pts, lat = [], lat0
    while lat <= lat1:
        lon = lon0
        while lon <= lon1:
            pts.append((round(lat, 4), round(lon, 4))); lon += step
        lat += step
    return pts


MARKETS = {
    # (lat, lon) lattice ~4-5 mi spacing across the metro + suburbs
    "orlando": _grid(28.36, 28.72, -81.52, -81.16, 0.07),   # + Winter Park / Kissimmee / Sanford / UCF / Lake Nona
    "miami":   _grid(25.60, 26.05, -80.45, -80.12, 0.07),
}

# known chain name-stems -> chain vs independent
_CHAINS = re.compile(
    r"total wine|abc fine|abc liquor|circle k|7-?eleven|\bcvs\b|walgreens|publix|winn.?dixie|walmart|target|"
    r"bj'?s|costco|sam'?s club|\baldi\b|whole foods|sprouts|speedway|wawa|racetrac|\bgate\b|applebee|chili'?s|"
    r"tgi friday|buffalo wild|outback|olive garden|texas roadhouse|dave.*buster|twin peaks|miller'?s ale|"
    r"yard house|hooters|wingstop|bar louie|cheddar'?s|longhorn|red lobster|bahama breeze|kona grill", re.I)


def _browser_auth():
    k = json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]
    r = urllib.request.Request("https://api.brightdata.com/zone/passwords?zone=cli_browser",
                               headers={"Authorization": "Bearer " + k})
    return "brd-customer-hl_32bcfbaa-zone-cli_browser:%s" % json.loads(
        urllib.request.urlopen(r, timeout=30).read())["passwords"][0]


def _point_harvest(pg, cdp, lat, lon):
    cdp.send("Proxy.setLocation", {"lat": lat, "lon": lon, "distance": 30, "strict": True})
    found = {}
    for term in ALCOHOL_TERMS:
        try:
            pg.goto("https://www.doordash.com/search/store/%s/" % urllib.parse.quote(term),
                    wait_until="domcontentloaded", timeout=90000)
            time.sleep(6)
            tiles = pg.evaluate(r"""() => { const m={}; document.querySelectorAll('a[href*="/store/"]').forEach(a=>{
                const href=a.getAttribute('href')||''; const mm=href.match(/\/(convenience\/store|store)\/(?:[\w%'-]*?-)?(\d{4,9})/);
                if(!mm) return; const id=mm[2], typ=(mm[1]==='convenience/store')?'retail':'restaurant';
                // tile text = "<name><rating>Icon Loading(<reviews>)•<dist> mi•<eta> min$<fee>" — cut at the first marker
                const t=(a.textContent||'').trim().replace(/\s+/g,' ').split(/\s*(?:\d\.\d|Icon Loading|•|\$\d)/)[0].trim().slice(0,60);
                if(t&&(!m[id]||t.length>(m[id].name||'').length)) m[id]={name:t, type:typ}; }); return m; }""")
            for k, v in tiles.items():
                found.setdefault(k, v)
        except Exception:
            pass
    return found


def run(market="orlando", points=None, log=print):
    from playwright.sync_api import sync_playwright
    grid = MARKETS[market]
    if points:
        grid = grid[:points]
    auth = _browser_auth()
    merchants = {}
    with sync_playwright() as p:
        for i, (lat, lon) in enumerate(grid):
            try:
                b = p.chromium.connect_over_cdp("wss://%s@brd.superproxy.io:9222" % auth, timeout=90000)
                ctx = b.contexts[0] if b.contexts else b.new_context()
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                cdp = ctx.new_cdp_session(pg)
                pts = _point_harvest(pg, cdp, lat, lon)
                b.close()
            except Exception as e:
                log("  pt %d (%.3f,%.3f) failed: %s" % (i, lat, lon, str(e)[:40])); continue
            new = 0
            for k, v in pts.items():
                if k not in merchants:
                    is_chain = bool(_CHAINS.search(v["name"]))
                    merchants[k] = dict(store_id=k, name=v["name"], type=v["type"], is_chain=is_chain,
                                        chain=(v["name"] if is_chain else ""), market=market)
                    new += 1
            log("  [%s] pt %d/%d (%.3f,%.3f) — +%d new (total %d)" % (market, i + 1, len(grid), lat, lon, new, len(merchants)))
    rows = list(merchants.values())
    if rows:
        warehouse.write_parquet("%s_merchants" % market, rows)
        ind = sum(1 for r in rows if not r["is_chain"]); rest = sum(1 for r in rows if r["type"] == "restaurant")
        log("[%s] DONE %d alcohol merchants (%d chain, %d independent · %d retail, %d restaurant) -> %s_merchants"
            % (market, len(rows), len(rows) - ind, ind, len(rows) - rest, rest, market))
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="orlando")
    ap.add_argument("--points", type=int, default=0)
    a = ap.parse_args()
    run(a.market, points=a.points or None)
