#!/usr/bin/env python3
"""vtinfo.py — brand → retailer distribution from the VTInfo / VIP "where to buy" finder.

WHAT THIS IS (and why it's the highest-leverage inventory path we have):
  Vermont Information Processing (VIP) runs the beverage-alcohol industry's depletion
  backbone. A huge share of brands' "where to buy" widgets are `finder.vtinfo.com` iframes
  driven off VIP SALES data — i.e. the accounts that ACTUALLY MOVED the brand, not a generic
  store list. Point it at a brand + a set of ZIPs and it returns every retailer/on-premise
  account carrying that brand, with street/city/state/zip/phone/distance. That's a real
  distribution + velocity signal (which accounts carry which brands), and it corroborates
  outlets_master (which accounts exist) from an independent source.

HOW IT WORKS (fully reverse-engineered, stdlib only):
  1. GET  /finder/web/v2/iframe?custID=<CID>&uuid=<UUID>&m=5
     → the page carries a STATELESS global `CSRFToken = "..."` (a signed token, no cookie
       needed) + the hidden `form#finder` fields (custID, uuid, pagesize, action=results…).
  2. POST /finder/web/v2/iframe/search  with those fields + `z=<zip>` + `page=<n>`
     → an HTML fragment of result rows. Pages are 1-indexed; a probe with page=0 returns
       "You must specify a page between 1 and N" — that N is the page count for the ZIP.
     → each row: .finder_dba_text (account) · address <a> (street) · .finder_address_city ·
       .finder_address_state · zip · .finder_phone · .finder_miles · row class `source-sales`
       (VIP sales/depletion) vs other source tags.
  Snapshot {brand|dba|street: {...}} and diff (new/dropped accounts) — repeatable.

CAVEATS: one `custID`+`uuid` per brand (Tito's = TTO, uuid below). Other brands' uuids are
harvested from each brand's own "where to buy" page (`discover` below reads the iframe src).
An `h-captcha` CAN be armed server-side (`showCaptcha`); this run bails to `degraded` if so
rather than trying to solve it. Be polite (delay); the token is per-iframe-load, refetched
per ZIP. No UPC/price — this is account-level CARRIAGE, matched to the outlet spine by
name+address, feeding [[chain-inventory-acquisition]] and the outlets master.
"""
import argparse, hashlib, json, os, re, time, urllib.request, urllib.parse, urllib.error
import html as _html

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
BASE = "https://finder.vtinfo.com/finder/web/v2/"
SEARCH = BASE + "iframe/search"
SNAP = "vtinfo_snapshot.json"
HEADER = ["Brand", "Account", "Street", "City", "State", "Zip", "Phone", "Miles",
          "Lat", "Lng", "StoreType", "Source", "Zip_Searched"]

# custID + uuid per brand, harvested from each brand's public "where to buy" iframe.
# Seed = Tito's Handmade Vodka (custID TTO). Grow via discover() below.
BRANDS = {
    # single brand, theme v1 (Tito's "where to buy")
    "titos": {"custID": "TTO", "uuid": "Ef3WupcOHGOCX9ATp7YSGFCnCZPJAsgOMJbp", "name": "Tito's Handmade Vodka", "m": "5"},
    # a DISTRIBUTOR's whole book, theme v3 (Florida Distributing brand-finder, Orlando) — carries
    # lat/lng + store_type per account. No uuid; custID alone drives the session.
    "fl-distributing": {"custID": "00177", "uuid": "", "name": "Florida Distributing Co.", "m": "100", "theme": "3"},
    # HEMP/THC beverage brands on VTInfo — a hemp brand's finder = every retailer carrying it, in any state,
    # which both VERIFIES the hemp chains (Total Wine/Kwik Trip/Cub/Coborn's/Target…) and discovers the
    # independents. Cann confirmed live (200 accounts in one MN zip). Add more via discover() on each brand.
    "cann": {"custID": "S5S", "uuid": "8QR7cJLcKti6m9rPPVGMfdo9AbCV9pv6ydQN", "name": "Cann (THC/hemp bev)", "m": "5"},
}


def _fetch(url, data=None, timeout=25, referer=None):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
         "Origin": "https://finder.vtinfo.com", "Referer": referer or BASE}
    if data is not None:
        h["X-Requested-With"] = "XMLHttpRequest"
        data = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def discover(where_to_buy_url, log=print):
    """Read a brand's public 'where to buy' page and pull the finder.vtinfo iframe's
    custID + uuid so the brand can be added to BRANDS. Returns a dict or None."""
    try:
        html = _fetch(where_to_buy_url)
    except Exception as e:
        log("discover: fetch failed: %s" % str(e)[:120]); return None
    m = re.search(r'finder\.vtinfo\.com/finder/web/v2/iframe\?([^"\'\s]+)', html)
    if not m:
        log("discover: no vtinfo iframe on %s" % where_to_buy_url); return None
    q = urllib.parse.parse_qs(_html.unescape(m.group(1)))
    cid = (q.get("custID") or [""])[0]
    uuid = (q.get("uuid") or [""])[0]
    if cid and uuid:
        log("discover: custID=%s uuid=%s" % (cid, uuid))
        return {"custID": cid, "uuid": uuid}
    return None


def _session(custID, uuid, m="5", theme="1", loc=""):
    """One iframe load → (stateless CSRFToken, base form fields). Raises on captcha wall.
    m + themeVersion vary by finder: a single brand is m=5/theme=1 (Tito's); a distributor
    portfolio is m=100/theme=3 (FL Distributing) and carries lat/lng + store_type per row."""
    q = {"custID": custID, "m": m, "themeVersion": theme, "action": "view"}
    if uuid:
        q["uuid"] = uuid
    if loc:
        q["d"] = loc
    iframe = BASE + "iframe?" + urllib.parse.urlencode(q)
    page = _fetch(iframe, referer=iframe)
    if re.search(r'showCaptcha\s*=\s*true', page):
        raise RuntimeError("captcha armed (showCaptcha=true)")
    tm = re.search(r'CSRFToken\s*=\s*"((?:[^"\\]|\\.)*)"', page)
    if not tm:
        raise RuntimeError("no CSRFToken in iframe page")
    tok = tm.group(1).replace('\\/', '/')
    fields = {}
    for mm in re.finditer(r'<(input|select|textarea)\b([^>]*)>', page):
        a = mm.group(2)
        nm = re.search(r'name="([^"]+)"', a)
        if not nm:
            continue
        v = re.search(r'value="([^"]*)"', a)
        fields[nm.group(1)] = _html.unescape(v.group(1)) if v else ""
    fields["CSRFToken"] = tok
    fields["themeVersion"] = fields.get("themeVersion", "1")
    return iframe, fields


def _parse_rows(resp, brand, zip_searched):
    """Theme-agnostic: v1 rows are <tr class="finder_row …">, v3 rows are
    <div/li class="finder_row finder_location …" data-latitude=…>. Split on the row
    boundary that both share (a tag opening whose class contains finder_row), and pull
    the richer v3 fields (lat/lng + store_type) when present."""
    rows = []
    # each row starts at a tag whose class list contains finder_row; slice between them
    starts = [m.start() for m in re.finditer(r'<(?:tr|div|li|article|section)\b[^>]*class="[^"]*finder_row', resp)]
    blocks = [resp[starts[i]:(starts[i + 1] if i + 1 < len(starts) else len(resp))] for i in range(len(starts))]
    for tr in blocks:
        cls = re.search(r'class="([^"]*finder_row[^"]*)"', tr)
        dba = re.search(r'finder_dba_text[^>]*>\s*([^<]+)', tr)
        if not (cls and dba):
            continue
        acct = _html.unescape(dba.group(1).strip())
        sa = re.search(r'finder_address_container[^>]*>.*?<a[^>]*>\s*<span>\s*([^<]+)', tr, re.S) \
            or re.search(r'finder_address(?!_)[^>]*>\s*(?:<a[^>]*>)?\s*<span>\s*([^<]+)', tr, re.S)
        street = _html.unescape(sa.group(1).strip()) if sa else ""
        city = re.search(r'finder_address_city[^>]*>\s*([^<,]+)', tr)
        state = re.search(r'finder_address_state[^>]*>\s*([A-Z]{2})', tr)
        zc = re.search(r'finder_address_state[^>]*>[A-Z]{2}</span>[\s,]*(?:<[^>]*>\s*)?(\d{5})(?:-\d{4})?\b', tr)
        phone = re.search(r'finder_phone[^>]*>\s*<[^>]*>?\s*([0-9][0-9()\-.\s]{6,})', tr) \
            or re.search(r'finder_phone[^>]*>\s*([0-9][0-9()\-.\s]{6,})', tr)
        miles = re.search(r'finder_miles[^"]*"[^>]*>\s*<?[^>]*>?\s*([0-9][0-9.]*)', tr)
        lat = re.search(r'data-latitude="(-?[0-9.]+)"', tr)
        lng = re.search(r'data-longitude="(-?[0-9.]+)"', tr)
        stype = re.search(r'finder_store_type[^>]*>\s*([^<]+)', tr)
        src = (re.search(r'source-([a-z]+)', cls.group(1)) or [None, ""])
        src = src.group(1) if hasattr(src, "group") else ""
        rows.append({"brand": brand, "account": acct, "street": street,
                     "city": _html.unescape(city.group(1).strip()) if city else "",
                     "state": state.group(1) if state else "",
                     "zip": zc.group(1) if zc else "",
                     "phone": (phone.group(1).strip() if phone else ""),
                     "miles": (miles.group(1) if miles else ""),
                     "lat": lat.group(1) if lat else "", "lng": lng.group(1) if lng else "",
                     "store_type": _html.unescape(stype.group(1).strip()) if stype else "",
                     "source": src, "zip_searched": zip_searched})
    return rows


def search_zip(custID, uuid, zip_code, delay=1.0, max_pages=40, m="5", theme="1", log=print):
    """All accounts carrying (custID) near zip_code, across every result page."""
    iframe, fields = _session(custID, uuid, m=m, theme=theme)
    fields = {**fields, "z": str(zip_code)}
    # probe page 0 → server tells us the page range: "between 1 and N"
    probe = _fetch(SEARCH, {**fields, "page": "0"}, referer=iframe)
    npages = 1
    mm = re.search(r'between 1 and (\d+)', probe)
    if mm:
        npages = min(int(mm.group(1)), max_pages)
    out, seen = [], set()
    for p in range(1, npages + 1):
        try:
            resp = _fetch(SEARCH, {**fields, "page": str(p)}, referer=iframe)
        except Exception as e:
            log("  zip %s page %d failed: %s" % (zip_code, p, str(e)[:80])); break
        rs = _parse_rows(resp, custID, str(zip_code))
        if not rs:
            break
        for r in rs:
            k = (r["account"], r["street"])
            if k not in seen:
                seen.add(k); out.append(r)
        if delay:
            time.sleep(delay)
    log("  zip %s: %d pages, %d accounts" % (zip_code, npages, len(out)))
    return out


def pull(brand="titos", zips=None, custID=None, uuid=None, delay=1.0, out=".", state_dir=None, log=print):
    state_dir = state_dir or out
    os.makedirs(out, exist_ok=True)
    started = int(time.time() * 1000)
    b = BRANDS.get(brand, {})
    custID = custID or b.get("custID")
    uuid = uuid if uuid is not None else b.get("uuid", "")
    uuid = uuid or b.get("uuid", "")
    if not custID:
        run = _run(brand, 0, 0, "failed", ["Unknown brand %r — no custID (add to BRANDS or pass discover())." % brand], started)
        return {}, [run], {"sampled": 0, "changed": 0}
    zips = zips or ["33601"]
    m = b.get("m", "5"); theme = b.get("theme", "1")
    cur, warns = {}, []
    for z in zips:
        try:
            for r in search_zip(custID, uuid, z, delay=delay, m=m, theme=theme, log=log):
                cur["%s|%s|%s" % (custID, r["account"], r["street"])] = r
        except RuntimeError as e:
            warns.append("zip %s: %s" % (z, e))
        except Exception as e:
            warns.append("zip %s: %s" % (z, str(e)[:100]))

    prev = {}
    sp = os.path.join(state_dir, "vtinfo_%s_snapshot.json" % custID)  # per-custID so diffs stay within one brand/distributor
    if os.path.exists(sp):
        try: prev = json.load(open(sp)).get("cells", {})
        except Exception: prev = {}
    changed = sum(1 for k in cur if k not in prev)
    dropped = sum(1 for k in prev if k not in cur)
    json.dump({"__ts__": started, "cells": cur}, open(sp, "w"))

    rows = [[v["brand"], v["account"], v["street"], v["city"], v["state"], v["zip"],
             v["phone"], v["miles"], v.get("lat", ""), v.get("lng", ""), v.get("store_type", ""),
             v["source"], v["zip_searched"]]
            for v in sorted(cur.values(), key=lambda x: (x["state"], x["city"], x["account"]))]
    status = "degraded" if (warns and not cur) else "success"
    ds = {"vtinfo_%s" % brand: {"header": HEADER, "rows": rows[:1000], "total": len(rows), "_rows_full": rows}}
    run = _run(brand, len(cur), changed, status, warns, started, dropped)
    log("vtinfo/%s: %d accounts (%d new, %d dropped), status %s" % (brand, len(cur), changed, dropped, status))
    return ds, [run], {"sampled": len(cur), "changed": changed, "dropped": dropped}


def _run(brand, n, changed, status, warnings, started, dropped=0):
    return {"id": "R-VT" + hashlib.sha1((brand + str(n)).encode()).hexdigest()[:3].upper(),
            "connId": "vtinfo", "startedAt": started, "finishedAt": int(time.time() * 1000),
            "durationMs": 0, "status": status, "trigger": "manual", "total": n,
            "degraded": status != "success", "warnings": warnings, "healed": [],
            "extracts": [{"id": "vtinfo_%s" % brand, "rows": n, "delta": changed, "dropped": dropped, "status": status}]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="VTInfo/VIP brand→retailer distribution finder.")
    ap.add_argument("--brand", default="titos")
    ap.add_argument("--zips", default="33601", help="comma-separated ZIPs")
    ap.add_argument("--custID"); ap.add_argument("--uuid")
    ap.add_argument("--discover", help="a brand 'where to buy' URL → print its custID/uuid and exit")
    ap.add_argument("--delay", type=float, default=1.0); ap.add_argument("--out", default="./vt_out")
    a = ap.parse_args(argv)
    if a.discover:
        print(discover(a.discover)); return
    ds, runs, mv = pull(brand=a.brand, zips=[z.strip() for z in a.zips.split(",") if z.strip()],
                         custID=a.custID, uuid=a.uuid, delay=a.delay, out=a.out, state_dir=a.out)
    r = list(ds.values())[0] if ds else {"rows": []}
    for row in r["rows"][:15]:
        print("  •", row[1][:32], "|", row[3], row[4], "|", row[6], "|", row[8])
    print("status:", runs[0]["status"], "| accounts:", runs[0]["total"], "| movement:", mv)


if __name__ == "__main__":
    main()
