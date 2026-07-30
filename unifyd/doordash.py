"""doordash.py — generic beverage-alcohol connector for ANY DoorDash storefront.

DoorDash convenience/retail stores are a Next.js RSC app behind Forter + a 21+ age gate. The Bright
Data Unlocker defeats both, and the /convenience/store/<id>/search/<term> pages SERVER-RENDER the item
list into the RSC payload — so one recipe pulls per-store bev-alc (name, price, pack/size/config) with
NO login and NO headless browser per store. It also captures the physical outlet (schema.org address +
geo) while it's there. Proven on Circle K; the same shape covers CVS, Total Wine, Albertsons, 7-Eleven,
Walgreens — each is just a CHAINS entry (chain name + DoorDash store ids).

    python doordash.py --chain cvs
    python doordash.py --chain totalwine --stores 1862062
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

ALCOHOL_TERMS = ["beer", "wine", "seltzer", "liquor", "vodka", "whiskey", "tequila", "rum",
                 "bourbon", "gin", "malt beverage", "hard cider", "champagne", "prosecco",
                 "canned cocktail", "wine spritzer"]

# chain key -> {name, stores[]}. Store ids are DoorDash store ids (discover via the site search by market).
CHAINS = {
    "circlek":   {"name": "Circle K",          "stores": ["1696295", "1695349"]},
    "cvs":       {"name": "CVS",               "stores": ["1235440"]},
    "totalwine": {"name": "Total Wine & More", "stores": ["1862062"]},
    "albertsons":{"name": "Albertsons",        "stores": ["1473954"]},   # Denver = Safeway banner
    "seveneleven":{"name": "7-Eleven",         "stores": []},
    "walgreens": {"name": "Walgreens",         "stores": []},
}

_last_beat = [0.0]     # wall-clock of the last telemetry heartbeat — same gate ue_catalog.py uses

_ITEM_RE = re.compile(r'"(?:quality_labels|accessibility_labels)":\[(.*?)\]', re.S)   # DoorDash ships both
_LBL_RE = re.compile(r'"label":"(\w+)","priority_level":"[^"]*","value":"((?:[^"\\]|\\.)*)"')
_SIZE_RE = re.compile(r'\(([\d.]+)\s*(fl oz|oz|ml|mL|L|liter|litre)\s*(?:[x×*]\s*(\d+)\s*ct)?\s*\)', re.I)
_CONT_RE = re.compile(r'\b(Cans?|Bottles?|Carton|Tetra|Box|Keg|Pouch|Growler)\b', re.I)


def _api_key():
    return None    # Bright Data RETIRED — DoorDash is fetched $0 via the flat ISP pool (see _fetch). Kept for
                   # caller compat: `key` is threaded through _unlock but ignored. (No metered spend — user rule.)


def _session(key=None):
    """A persistent curl_cffi Session bound to ONE proxy exit for the life of the session — the fix for a
    store's full_catalog() walk paying a fresh TLS handshake + proxy connection on EVERY one of its ~166
    page fetches (measured ~4-5 MIN/store; connection reuse via keep-alive is the dominant lever, well
    before worker-count tuning matters). `key` (e.g. the store id) picks a deterministic exit so retries
    within one store's walk stay on the same IP a real browsing session would use; None round-robins.
    Returns None if curl_cffi/the ISP pool aren't available — callers fall back to the ad-hoc _fetch path."""
    import resi
    try:
        from curl_cffi import requests as cr
    except Exception:
        return None
    if not resi.isp_enabled():
        return None
    u = resi.isp_url(key)
    px = {"http": u, "https": u} if u else None
    return cr.Session(impersonate="safari17_0", proxies=px, timeout=60)


def _exit_of(session_or_url):
    """Best-effort exit IP for attribution — from a session's bound proxy if there is one, else from a
    fresh round-robin URL. Never raises: attribution is a nice-to-have, not a request-blocker."""
    try:
        px = getattr(session_or_url, "proxies", None) or session_or_url
        u = (px or {}).get("https") or (px or {}).get("http") if isinstance(px, dict) else px
        if u:
            return u.split("@")[-1].split(":")[0]
    except Exception:
        pass
    return None


def _beat(log, tally):
    """Liveness the log stream can't fake its way past. Gated the same way ue_catalog.py's _beat() is
    (>60s since the last one) so a chain of 16 concurrent workers doesn't spam 16x. Without this, the
    only way to tell "the crawl is slow" from "the crawl is degraded" was reading item counts off
    per-store log lines and doing the arithmetic by hand — which is exactly what made a real slowdown
    look identical to a deep-but-healthy category tree live on 2026-07-30."""
    if not tally or not log:
        return
    if (time.time() - _last_beat[0]) <= 60:
        return
    _last_beat[0] = time.time()
    flat = tally.flat()
    v = tally.exit_verdict()
    log("HOODIE_DD_PROGRESS %s exit_pattern=%s worst=%s"
        % (flat, v.get("pattern"), v.get("worst")))


def _fetch(url, retries=4, log=None, session=None):
    """$0 DoorDash fetch — curl_cffi Safari-17 TLS impersonation through the flat-rate residential ISP pool.
    Safari impersonation clears DoorDash's Forter where Chrome AND plain-HTTP get 403 (verified 2026-07-24:
    200 + the full RSC menu/retail payload). NO Bright Data — replaces the old api.brightdata.com Unlocker.
    If no ISP pool is configured there is no paid fallback: it returns '' (skip), never spends.

    `session` (from _session()) reuses one connection/exit across many calls — pass it for any multi-request
    walk (full_catalog's ~166 fetches/store). Without it, each call opens its own connection + picks a fresh
    round-robin exit (fine for a one-off fetch like doordash_naop's single menu page).

    TELEMETRY: every attempt is classified (blocks.classify — the same taxonomy ue_catalog.py uses:
    ok/soft_block/captcha/timeout/network/...) and recorded if a caller has called blocks.install() for
    this run. Before this, a slow or degraded crawl was invisible except by eyeballing log-line
    timestamps and doing the arithmetic by hand — exactly the gap that made a real slowdown
    indistinguishable from "just a deep category tree" live on 2026-07-30. Also logs any single attempt
    slower than SLOW_FETCH_S directly, so a stalled-feeling crawl shows ITS OWN evidence in the log
    instead of requiring an external timing probe to notice."""
    import resi
    import blocks
    SLOW_FETCH_S = 15.0
    try:
        from curl_cffi import requests as cr
    except Exception as e:
        if log:
            log("  [dd] curl_cffi unavailable: %s" % str(e)[:60])
        return ""
    tally = blocks.get()
    last = ""
    for a in range(retries):
        t0 = time.time()
        exit_ip = _exit_of(session) if session is not None else None
        try:
            if session is not None:
                r = session.get(url)
            else:
                u = resi.isp_url() if resi.isp_enabled() else None      # round-robin exit; a fresh IP each retry
                exit_ip = _exit_of(u)
                px = {"http": u, "https": u} if u else None
                r = cr.get(url, impersonate="safari17_0", proxies=px, timeout=60)
            el = time.time() - t0
            has_payload = "__next_f" in r.text
            # has_items is left None (unknown) here — this is the raw-page fetch, not the item parse,
            # so "OK" here means "the shape we asked for arrived", not "it had alcohol items in it".
            cls = blocks.classify(status=r.status_code, body=r.text, has_payload=has_payload)
            if tally:
                tally.record(cls, method="isp", exit=exit_ip)
                _beat(log, tally)
            if el >= SLOW_FETCH_S and log:
                log("  [dd] slow fetch: %.1fs status=%s cls=%s exit=%s %s"
                    % (el, r.status_code, cls, exit_ip, url[:70]))
            if cls == blocks.OK:
                return r.text
            last = "status %s (%s)" % (r.status_code, cls)
        except Exception as e:
            el = time.time() - t0
            cls = blocks.classify(exc=e)
            if tally:
                tally.record(cls, method="isp", exit=exit_ip)
                _beat(log, tally)
            if el >= SLOW_FETCH_S and log:
                log("  [dd] slow fetch: %.1fs exc cls=%s exit=%s %s" % (el, cls, exit_ip, url[:70]))
            last = str(e)[:80]
        time.sleep(1 + a)
    if log:
        log("  [dd] ISP fetch failed after %d tries (%s): %s" % (retries, last, url[:60]))
    return ""


def _unlock(url, key=None, retries=2, session=None, log=None):
    """Back-compatible shim — DoorDash now fetches $0 through the ISP pool (_fetch); `key` (the old Bright Data
    token) is ignored. Kept so doordash_naop and other callers don't have to change.

    `log` didn't used to matter here — the shim swallowed it — but _fetch's telemetry heartbeat and
    slow-fetch warnings are gated on `log` being callable, and full_catalog()'s ~166 fetches/store are
    ALL routed through this shim. Without forwarding it, the tally still filled up correctly (record()
    doesn't need log) but nothing ever printed: verified live 2026-07-30 — 0 HOODIE_DD_PROGRESS lines
    after 4+ minutes of real, healthy crawling, because every one of those fetches called this with no
    log at all."""
    return _fetch(url, retries=max(3, retries + 1), session=session, log=log)


def search_store(store, term, key=None, session=None, log=None):
    """/convenience/store/<id>/search/<term> — server-rendered search results, same RSC shape as a category
    page (see the module docstring). Was documented but never implemented (full_catalog referenced this by
    name for the completeness union; every call silently AttributeError'd, caught by a bare except, wasting
    a fetch+sleep per term for nothing and never actually catching the off-tree items it was meant to)."""
    url = "https://www.doordash.com/convenience/store/%s/search/%s" % (store, urllib.parse.quote(term))
    html = _fetch(url, session=session, log=log)
    if not html:
        return []
    return _parse_items(_rsc(html))


def _rsc(html):
    """Concatenate + hand-unescape the Next.js RSC streamed payload (unicode_escape mangles UTF-8 '×')."""
    s = "".join(re.findall(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)', html))
    s = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), s)
    return (s.replace('\\\\', '\x00').replace('\\"', '"').replace('\\/', '/')
             .replace('\\n', '\n').replace('\\t', '\t').replace('\x00', '\\'))


def _parse_pack(name):
    out = {"container": "", "unit_size": None, "size_uom": "", "pack_count": 1, "total_size": None}
    cm = _CONT_RE.search(name)
    if cm:
        out["container"] = cm.group(1).rstrip("sS").title()
    sm = _SIZE_RE.search(name)
    if sm:
        try:
            out["unit_size"] = float(sm.group(1))
        except ValueError:
            pass
        out["size_uom"] = sm.group(2).lower().replace("litre", "l").replace("liter", "l")
        out["pack_count"] = int(sm.group(3)) if sm.group(3) else 1
        if out["unit_size"] is not None:
            out["total_size"] = round(out["unit_size"] * out["pack_count"], 2)
    return out


def _parse_items(blob):
    out = []
    for m in _ITEM_RE.finditer(blob):
        labels = dict(_LBL_RE.findall(m.group(1)))
        name = labels.get("item_name")
        if not name:
            continue
        im = re.search(r'"image":\{"remote":\{"uri":"([^"\\]+)"', blob[m.end():m.end() + 500])
        out.append(dict(name=name, price=labels.get("item_price", ""), image_url=im.group(1) if im else "",
                        **_parse_pack(name)))
    return out


def _parse_outlet(html, store_id, chain):
    def g(pat, d=""):
        mm = re.search(pat, html)
        return mm.group(1) if mm else d
    street = g(r'"streetAddress":"([^"]*)"') or g(re.escape(chain) + r' \(([^)]+)\)')
    return dict(store_id=str(store_id), source=chain, chain=chain, street=street,
                city=g(r'"addressLocality":"([^"]*)"'), state=g(r'"addressRegion":"([^"]*)"'),
                zip=g(r'"postalCode":"([^"]*)"'), lat=g(r'"latitude":([-\d.]+)'), lon=g(r'"longitude":([-\d.]+)'))


def _price_val(p):
    if p and p.startswith("$"):
        try:
            return float(p[1:].replace(",", ""))
        except ValueError:
            return None
    return None


def run(chain, stores=None, terms=None, log=print):
    cfg = CHAINS.get(chain, {"name": chain, "stores": []})
    stores = stores or cfg["stores"]
    if not stores:
        log("[%s] no store ids — discover them via the DoorDash site search first" % chain); return None, 0
    terms = terms or ALCOHOL_TERMS
    key = _api_key()
    run_id = "%s-%s" % (chain, time.strftime("%Y%m%d-%H%M%S"))
    all_rows, outlets = [], []
    for store in stores:
        seen, outlet = {}, None
        for term in terms:
            url = "https://www.doordash.com/convenience/store/%s/search/%s?attr_src=home" % (store, urllib.parse.quote(term))
            try:
                html = _unlock(url, key, log=log)
                items = _parse_items(_rsc(html))
            except Exception as e:
                log("  %s/%s failed: %s" % (store, term, str(e)[:60])); continue
            if outlet is None and ('"latitude"' in html or (cfg["name"] + " (") in html):
                outlet = _parse_outlet(html, store, cfg["name"])
            for it in items:
                if it["name"] not in seen:
                    seen[it["name"]] = dict(it, price_value=_price_val(it["price"]))
            time.sleep(0.6)
        if outlet:
            outlets.append(outlet)
        rows = [dict(v, store=store, store_id=store, product_id=v["name"][:90], source=chain,
                     is_hemp=observe.is_hemp(v["name"]), run_id=run_id) for v in seen.values()]
        all_rows.extend(rows)
        loc = ("%s, %s" % (outlet["city"], outlet["state"])) if outlet and outlet.get("state") else "?"
        log("  [%s] store %s (%s) — %d bev-alc items" % (chain, store, loc, len(rows)))
    if all_rows:
        warehouse.write_parquet(chain + "_products", all_rows)
        observe.record(chain, [dict(store=r["store"], store_id=r["store_id"], product_id=r["product_id"],
                                    brand="", name=r["name"], price=r.get("price_value"),
                                    in_stock=True, qty=None, is_hemp=r.get("is_hemp")) for r in all_rows])
    if outlets:
        warehouse.write_parquet(chain + "_outlets", outlets)
    log("[%s] DONE %d items + %d outlets across %d stores -> %s_products / %s_outlets / retail_observations"
        % (chain, len(all_rows), len(outlets), len(stores), chain, chain))
    return run_id, len(all_rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", required=True, help="chain key: " + ", ".join(CHAINS))
    ap.add_argument("--stores", default="")
    ap.add_argument("--terms", default="")
    a = ap.parse_args()
    run(a.chain, stores=[s.strip() for s in a.stores.split(",") if s.strip()] or None,
        terms=[t.strip() for t in a.terms.split(",") if t.strip()] or None)
