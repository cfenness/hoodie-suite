"""publix.py — Publix WEEKLY AD (BOGO) capture. Publix's own savings API, NOT the Instacart-gated side.

STATUS (2026-07-10): Akamai defeated + API + store-scoping cracked; NOT yet landing deals — last mile is a
valid store with an active ad + confirming the deal XML shape (see below).

WHAT'S PROVEN:
  • The weekly ad is Publix's OWN API (not Instacart, not age-gated):
      GET https://services.publix.com/api/v4/savings?getSavingType=WeeklyAd&page=1&pageSize=N&isWeb=true&languageID=1
    Returns XML rooted at <SavingsResultWA> (… <Savings>…deals…</Savings> …). Store-scoped.
  • Akamai-protected → our Tier-2 Bright Data layer gets past it (polite use_proxy=True, or the Unlocker API
    POST api.brightdata.com/request {zone:cli_unlocker,url,format:raw}, or `bdata browser`). Confirmed a live
    response past Akamai.
  • STORE CONTEXT is required: v4/savings is keyed by StoreNbr via COOKIE/session (a cold param call errors
    with a ~3.5KB page; the param StoreNbr= alone didn't populate it). In a `bdata browser` session the store
    auto-set to StoreNbr=1425 from the residential IP — but 1425 returned an EMPTY <Savings/> (likely a store
    outside Publix's ad footprint / between cycles, or a stale-nav read).

RESOLVED SINCE (2026-07-10):
  • Store locator works: GET https://services.publix.com/api/v1/storelocation?zipCode=<zip> -> XML <StoreInfo>
    blocks with <KEY> (store #), <ADDR>, <WASTORENUM>, <SHORTNAME>. Kirkman & Conroy Orlando =
    "Kirkman Oaks Shopping Center", 4606 S Kirkman Rd, Orlando FL 32819 = STORE #331 (KEY 00331,
    WASTORENUM 2500467). Nearby: Plaza at Millenia = KEY 00605.
  • The empty ads were GEO, not params: v4/savings returns the SESSION store's ad, and the BD exit IP kept
    landing OUTSIDE Publix's footprint (Texas, then Connecticut; cookie pblx_ot_session_geo shows the state).
    Publix operates only in FL/GA/AL/SC/NC/TN/VA/KY, so a non-footprint IP -> no valid store -> empty
    <Savings/>. The StoreNbr URL param is IGNORED (store is session/cookie-based, store_selection_method=browser).

LAST MILE (now well-defined): route through a FLORIDA BD exit — the proxy username supports state targeting:
brd-customer-hl_32bcfbaa-zone-cli_unlocker-country-us-state-fl (or a sticky FL residential session). With a FL
exit, publix.com auto-selects a real FL store and v4/savings returns a NON-empty <SavingsResultWA>. Then confirm
the <Saving> element shape, parse, and land (publix_products + retail_observations). To pin store #331
specifically, also select it in-session. Reuse the ban-safe Tier-2 layer.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse
import observe

SAVINGS = "https://services.publix.com/api/v4/savings"
STORE_LOCATOR = "https://services.publix.com/api/v1/storelocation/storesearch"   # by zip — for FL store nbrs


def _unlocker(url, api_key, cookies=None):
    """Fetch a URL through the Bright Data Unlocker (raw), past Akamai. cookies = a Cookie header string
    (needed to carry the StoreNbr session)."""
    headers = {"Accept": "application/xml,application/json", "Referer": "https://www.publix.com/"}
    if cookies:
        headers["Cookie"] = cookies
    body = json.dumps({"zone": "cli_unlocker", "url": url, "format": "raw", "headers": headers}).encode()
    req = urllib.request.Request("https://api.brightdata.com/request", data=body,
                                 headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")


def savings_url(store_nbr, page=1, page_size=60):
    q = {"page": page, "pageSize": page_size, "isWeb": "true", "languageID": 1,
         "getSavingType": "WeeklyAd", "StoreNbr": store_nbr}
    return SAVINGS + "?" + urllib.parse.urlencode(q)


def parse_savings(xml_text):
    """Parse <SavingsResultWA> weekly-ad XML into deal rows. TODO: confirm element names on a non-empty
    response (store 1425 returned empty). Placeholder maps the likely fields."""
    rows = []
    for block in re.findall(r"<Saving\b.*?</Saving>", xml_text, re.S):
        def g(tag):
            m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), block, re.S)
            return (m.group(1).strip() if m else "")
        title = g("Title") or g("Description")
        if not title:
            continue
        price = g("Price") or g("FinalPrice")
        rows.append(dict(name=title, brand=g("Brand"), price=price, deal_type=g("DealType") or g("SavingType"),
                         is_bogo=("bogo" in block.lower() or "buy one" in block.lower()),
                         image_url=g("ImageUrl") or g("Image"), is_hemp=observe.is_hemp(title)))
    return rows


def run(store_nbrs, api_key=None):
    """Pull the weekly ad for each FL store, land as publix_products + retail_observations."""
    api_key = api_key or json.load(open(os.path.expanduser(
        "~/Library/Application Support/brightdata-cli/credentials.json")))["api_key"]
    all_rows = []
    for sn in store_nbrs:
        try:
            xml = _unlocker(savings_url(sn), api_key)
            deals = parse_savings(xml)
            for d in deals:
                d["store"] = str(sn); d["source"] = "publix"
            all_rows += deals
            print("  [publix] store %s — %d weekly-ad deals" % (sn, len(deals)))
            time.sleep(2)
        except Exception as e:
            print("  [publix] store %s failed: %s" % (sn, str(e)[:80]))
    if all_rows:
        warehouse.write_parquet("publix_products", all_rows)
        observe.record("publix", [dict(store=r["store"], product_id="", name=r["name"], brand=r.get("brand"),
                                        price=r.get("price"), on_promo=True, in_stock=True,
                                        is_hemp=r.get("is_hemp")) for r in all_rows])
    return len(all_rows)
