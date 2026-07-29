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
import cuisine as cui

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
    # TEXAS — the state DoorDash's own sitemap cannot give us. sitemap-doordash-tx-stores.xml serves a
    # 270-byte stub with ONE store (California's has 103,811) and there is no alternate URL: tx-1,
    # texas, .gz, hou and dal all 403. Their feed is broken, so the only route to Texas is to sweep it
    # geographically. These four metros are ~2/3 of the state's population.
    "houston":     _grid(29.55, 30.11, -95.79, -95.06, 0.07),
    "dallas":      _grid(32.60, 33.05, -97.05, -96.55, 0.07),   # Dallas proper + Irving / Plano edge
    "fortworth":   _grid(32.63, 32.92, -97.45, -97.10, 0.07),
    "austin":      _grid(30.13, 30.51, -97.94, -97.57, 0.07),
    "sanantonio":  _grid(29.30, 29.65, -98.70, -98.30, 0.07),
}

# Convenience: sweep the whole Texas set in one run rather than remembering five market names.
MARKET_GROUPS = {"texas": ["houston", "dallas", "fortworth", "austin", "sanantonio"]}

# known chain name-stems -> chain vs independent
_CHAINS = re.compile(
    r"total wine|abc fine|abc liquor|circle k|7-?eleven|\bcvs\b|walgreens|publix|winn.?dixie|walmart|target|"
    r"bj'?s|costco|sam'?s club|\baldi\b|whole foods|sprouts|speedway|wawa|racetrac|\bgate\b|applebee|chili'?s|"
    r"tgi friday|buffalo wild|outback|olive garden|texas roadhouse|dave.*buster|twin peaks|miller'?s ale|"
    r"yard house|hooters|wingstop|bar louie|cheddar'?s|longhorn|red lobster|bahama breeze|kona grill", re.I)


# ── FREE BROWSER — no Bright Data ────────────────────────────────────────────────────────────────
# This used to connect to BD Browser over CDP (wss://brd.superproxy.io:9222), the metered per-GB tier,
# with credentials read from ~/Library/Application Support/brightdata-cli/ — a path that only exists on
# one Mac, so the sweep could not run on Fly at all. Two substitutions kill both problems:
#
#   1. A LOCAL Chromium (real Chrome, bundled Chromium fallback) — the instacart.py pattern, proven to
#      clear anti-bot from a bare datacenter IP with no BD and no proxy.
#   2. `Emulation.setGeolocationOverride`, which is STANDARD CDP, in place of BD's proprietary
#      `Proxy.setLocation`. Both pin the browser's location; only one costs per-GB.
#
# Egress goes through the flat-rate ISP pool (fixed per-IP, unlimited bandwidth) — never the per-GB
# rotating tier, which stays gated behind FETCH_POLICY=paid.
def _sync_playwright():
    """The driver that is ACTUALLY installed. Measured on the Fly image: `patchright` is present and
    `playwright` is not, so importing playwright directly is a ModuleNotFoundError at runtime — the
    port compiled, tested and deployed clean and would still have failed on its first real pin.

    The resolution itself now lives in ONE place (`browser_warm.sync_playwright_api`), because this
    same break was later found in six other modules that each imported playwright directly. A local
    copy per module is a per-module chance to get it wrong; this delegates instead.
    """
    import browser_warm
    return browser_warm.sync_playwright_api()


def _launch(pw, log=print):
    """(browser, proxy_label). Real Chrome first, bundled Chromium second."""
    import resi
    px = None
    try:
        if resi.isp_enabled():
            px = resi.isp_url()
    except Exception:
        px = None
    # SPLIT THE CREDENTIALS. resi.isp_url() returns http://user:pass@host:port, and Chromium IGNORES
    # credentials embedded in a proxy URL — Playwright wants username/password as separate keys.
    # Passing the whole URL as `server` produced ERR_INVALID_AUTH_CREDENTIALS on every navigation,
    # which _point_harvest swallowed per-term, so a totally failed sweep reported "0 merchants" and
    # looked like a market with no stores.
    proxy = None
    if px:
        try:
            u = urllib.parse.urlparse(px)
            proxy = {"server": "%s://%s:%s" % (u.scheme or "http", u.hostname, u.port or 80)}
            if u.username:
                proxy["username"] = urllib.parse.unquote(u.username)
                proxy["password"] = urllib.parse.unquote(u.password or "")
        except Exception:
            proxy = {"server": px}
    headful = os.environ.get("BROWSER_HEADFUL", "0") != "0"
    # BUNDLED CHROMIUM FIRST — channel="chrome" is opt-in via DD_GEO_CHANNEL now.
    #
    # Measured across three ephemeral runs: the sweep produced NO output at all, headful or headless,
    # with a 90s and then a 25s nav timeout. Nothing in run() logs before _launch returns, so the hang
    # was provably inside launch — and the `for ch in ("chrome", None)` fallback could never save it,
    # because it catches EXCEPTIONS and a blocked launch never raises one. /usr/bin/google-chrome
    # exists on the image, so the attempt was made and simply never came back.
    #
    # Each attempt is now announced BEFORE it runs. A hang that prints nothing is indistinguishable
    # from a hang that prints which browser it was trying — and the second one is diagnosable.
    chans = [c for c in os.environ.get("DD_GEO_CHANNEL", "").split(",") if c] or [None]
    last = None
    for ch in chans:
        label = ch or "bundled-chromium"
        log("  [dd-geo] launching %s (headful=%s, proxy=%s)…"
            % (label, headful, "yes" if proxy else "no"))
        try:
            kw = {"headless": not headful, "args": ["--no-sandbox"], "proxy": proxy}
            b = pw.chromium.launch(channel=ch, **kw) if ch else pw.chromium.launch(**kw)
            log("  [dd-geo] %s launched" % label)
            return b, (px.split("@")[-1].split(":")[0] if px else "direct")
        except Exception as e:
            last = e
            log("  [dd-geo] %s failed: %s" % (label, str(e)[:120]))
    raise RuntimeError("could not launch a browser (%s): %s" % (",".join(str(c) for c in chans), last))


def _set_location(ctx, pg, lat, lon):
    """Pin the browser's geolocation. Standard CDP, so it needs no vendor.

    BD's `Proxy.setLocation` moved the EXIT IP as well; this moves only the reported coordinates, so
    the permission grant matters — without it DoorDash's own geolocation call is denied and the page
    silently falls back to a default market, which would look like a market with no stores rather
    than a sweep that never moved.
    """
    ctx.grant_permissions(["geolocation"], origin="https://www.doordash.com")
    ctx.set_geolocation({"latitude": float(lat), "longitude": float(lon), "accuracy": 50})
    cdp = ctx.new_cdp_session(pg)
    try:
        cdp.send("Emulation.setGeolocationOverride",
                 {"latitude": float(lat), "longitude": float(lon), "accuracy": 50})
    except Exception:
        pass                                   # context-level geolocation already applied above
    return cdp


_NAV_MS = int(os.environ.get("DD_GEO_NAV_MS", "25000"))
_SETTLE_S = float(os.environ.get("DD_GEO_SETTLE_S", "4"))
_VERBOSE = os.environ.get("DD_GEO_VERBOSE", "1") != "0"


def _point_harvest(pg, cdp, lat, lon, log=print):
    # Location is pinned by _set_location() before this runs (standard CDP). The old
    # `Proxy.setLocation` here was Bright Data's own command and 404s on any other browser.
    found, ok, errs = {}, 0, []
    for term in ALCOHOL_TERMS:
        try:
            # 25s, not 90s. At 90s a pin whose navigations all hang takes ~9 minutes before it says
            # ANYTHING — measured live: a Texas sweep sat 20 minutes without reporting a single pin,
            # so there was no way to tell a slow crawl from a wedged one. A page that has not reached
            # domcontentloaded in 25s is not going to produce tiles; failing fast turns a silent hang
            # into a diagnosable error.
            pg.goto("https://www.doordash.com/search/store/%s/" % urllib.parse.quote(term),
                    wait_until="domcontentloaded", timeout=_NAV_MS)
            time.sleep(_SETTLE_S)
            tiles = pg.evaluate(r"""() => { const m={}; document.querySelectorAll('a[href*="/store/"]').forEach(a=>{
                const href=a.getAttribute('href')||''; const mm=href.match(/\/(convenience\/store|store)\/(?:[\w%'-]*?-)?(\d{4,9})/);
                if(!mm) return; const id=mm[2], typ=(mm[1]==='convenience/store')?'retail':'restaurant';
                // tile text = "<name><rating>Icon Loading(<reviews>)•<dist> mi•<eta> min$<fee>" — cut at the first marker
                const t=(a.textContent||'').trim().replace(/\s+/g,' ').split(/\s*(?:\d\.\d|Icon Loading|•|\$\d)/)[0].trim().slice(0,60);
                if(t&&(!m[id]||t.length>(m[id].name||'').length)) m[id]={name:t, type:typ}; }); return m; }""")
            for k, v in tiles.items():
                found.setdefault(k, v)
            ok += 1
            if _VERBOSE:
                log("      term %-16s -> %d tiles" % (term, len(tiles)))
        except Exception as e:                               # noqa: BLE001
            errs.append("%s: %s" % (term, str(e)[:70]))
            if _VERBOSE:
                log("      term %-16s FAILED %s" % (term, str(e)[:70]))
    # A pin where EVERY term failed is not an empty market — it is a failed pin, and the two must not
    # look alike. Reporting 0 merchants for a broken proxy is how a dead sweep passed for a real
    # measurement of Houston.
    if ok == 0 and errs:
        raise RuntimeError("all %d search terms failed at this pin: %s" % (len(errs), errs[0]))
    return found


def run(market="orlando", points=None, log=print):
    sync_playwright = _sync_playwright()
    grid = MARKETS[market]
    if points:
        grid = grid[:points]
    merchants = {}
    # Announce the driver too. If the run still goes silent, this separates "the patchright driver
    # never started" from "the browser never launched" — two different fixes that looked identical
    # when neither printed anything.
    log("  [dd-geo] %s: %d grid points — starting driver…" % (market, len(grid)))
    with sync_playwright() as p:
        log("  [dd-geo] driver up")
        # ONE browser for the whole grid, a fresh CONTEXT per point. The BD version reconnected a
        # remote browser at every pin — ~90s of setup per point, which is what made a full metro grid
        # an overnight job. A context is cheap and still gives each point a clean cookie jar, so the
        # geolocation override is the only thing that carries between pins.
        browser, exit_label = _launch(p, log=log)
        log("  [dd-geo] free browser up (egress=%s) — no Bright Data" % exit_label)
        try:
            for i, (lat, lon) in enumerate(grid):
                ctx = None
                try:
                    ctx = browser.new_context(locale="en-US")
                    pg = ctx.new_page()
                    cdp = _set_location(ctx, pg, lat, lon)
                    pts = _point_harvest(pg, cdp, lat, lon, log=log)
                except Exception as e:
                    log("  pt %d (%.3f,%.3f) failed: %s" % (i, lat, lon, str(e)[:60])); continue
                finally:
                    if ctx is not None:
                        try: ctx.close()
                        except Exception: pass
                new = 0
                for k, v in pts.items():
                    if k not in merchants:
                        is_chain = bool(_CHAINS.search(v["name"]))
                        # provisional cuisine from the name (restaurants); the pull upgrades it to page servesCuisine
                        cz = cui.from_name(v["name"]) if v["type"] == "restaurant" else ""
                        merchants[k] = dict(store_id=k, name=v["name"], type=v["type"], is_chain=is_chain,
                                            chain=(v["name"] if is_chain else ""), cuisine=cz,
                                            cuisine_source=("name" if cz else ""), market=market)
                        new += 1
                log("  [%s] pt %d/%d (%.3f,%.3f) — +%d new (total %d)"
                    % (market, i + 1, len(grid), lat, lon, new, len(merchants)))
        finally:
            try:
                browser.close()
            except Exception:
                pass
    unsure = [m["name"] for m in merchants.values() if m["type"] == "restaurant" and not m["cuisine"]]
    if unsure:                                                     # Claude fallback for name-only unknowns
        guess = cui.claude_cuisine(unsure)
        for m in merchants.values():
            if not m["cuisine"] and m["name"] in guess:
                m["cuisine"], m["cuisine_source"] = guess[m["name"]], "claude"
        if guess:
            log("  [%s] Claude cuisine filled %d/%d unsure restaurants" % (market, len(guess), len(unsure)))
    rows = list(merchants.values())
    if rows:
        warehouse.write_parquet("%s_merchants" % market, rows)
        ind = sum(1 for r in rows if not r["is_chain"]); rest = sum(1 for r in rows if r["type"] == "restaurant")
        log("[%s] DONE %d alcohol merchants (%d chain, %d independent · %d retail, %d restaurant) -> %s_merchants"
            % (market, len(rows), len(rows) - ind, ind, len(rows) - rest, rest, market))
    return rows


def run_group(group="texas", points=None, log=print):
    """Sweep every market in a group (e.g. all five Texas metros) in one process.

    Sequential on purpose: the markets share one endpoint's quota, so running them concurrently would
    just spend the same budget faster and trip the throttle that the per-point pacing avoids.
    """
    names = MARKET_GROUPS.get(group) or [group]
    total = 0
    for m in names:
        if m not in MARKETS:
            log("  [dd-geo] unknown market %r — skipped" % m)
            continue
        try:
            total += (run(m, points=points, log=log) or 0)
        except Exception as e:                                # noqa: BLE001 — one metro must not
            log("  [dd-geo] market %s failed: %s" % (m, str(e)[:120]))   # abort the rest
    return total


def run_texas(log=print):
    """Registry entrypoint. Texas exists as its own source because DoorDash's TX sitemap is broken
    (one store statewide), so the geographic sweep is the ONLY route to those outlets."""
    return run_group("texas", log=log)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="orlando", help="a market name, or a group like 'texas'")
    ap.add_argument("--points", type=int, default=0)
    a = ap.parse_args()
    if a.market in MARKET_GROUPS:
        run_group(a.market, points=a.points or None)
    else:
        run(a.market, points=a.points or None)
