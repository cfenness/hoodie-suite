#!/usr/bin/env python3
"""dam_bacardi.py — Bacardi Media Centre public drive (media.bacardilimited.com), connId `dam-bacardi`.

THE RECIPE (first `dam`-class source; the platform pattern the next vendors reuse)
  The Media Centre is a server-rendered shell around a JSON tree. Two robots-PERMITTED reads cover
  the whole drive:

    GET /drives/view-new/<drive>              → HTML that bootstraps `window.DriveViewState = {...}`
                                                — drive, root/current folder, **all_folders** (the
                                                complete folder list) and the files already loaded.
    GET /drives/get-tree/<drive>?folder_id=<n> → {"status":"ok","body":{"tree":{...}}} — that
                                                folder's files. This is the JSON API the SPA itself
                                                calls; its path is `/drives/`, NOT `/api/`.

  ROBOTS MATTERS HERE AND IT IS NOT AN AFTERTHOUGHT. media.bacardilimited.com disallows `/api/`,
  `/users/`, `/administrators/`, `/dashboard`, `/settings`, `/seo/`, `/login`, `/shared/`,
  `/account/`. Both endpoints above sit outside every one of those prefixes, and the rights record
  stores the verbatim robots body plus a per-URL decision so the claim is checkable rather than
  asserted. If the platform ever moves the tree endpoint under `/api/`, this connector must stop —
  not "find another way in".

  No auth, no cookie, no token, no browser: drive 42 ("Bacardi Public") is served to an anonymous
  GET. Nothing here enumerates ids, tampers with parameters, or probes for non-public drives; the
  drive id is the one published in the Media Centre's own navigation.

WHAT THE RIGHTS RECORD SAYS, AND WHAT THAT COSTS US
  Bacardi's terms (bacardilimited.com/terms-and-conditions, which by their own §1 cover "any and all
  other online or digital platforms … which we maintain") grant NO reuse licence: §3 "the use of this
  Site does not grant you any rights, title, interest or license to any Materials", downloads are for
  "your lawful, personal, non-commercial use", "You must not use any part of the Materials … for
  commercial purposes"; §4 "You are not permitted to use the Materials outside of the Site". A full
  scan of the 23.8k-character terms finds no press/editorial carve-out — no `press`, `editorial`,
  `journalis`, `broadcast`, `royalty`, `attribution` or `credit` clause exists to rely on.

  So this source runs at `image_use=prohibited`, `scope=none`: it lands 2,490 asset POINTERS and the
  brand-event feed, and it fetches, retains, hashes and embeds exactly ZERO assets. That is the
  connector working, not failing. The escalation path is recorded in the rights record (ToS §13: a
  written request to Bacardi's Digital Director), and widening scope requires a new record revision
  with `counsel_cleared`, not a code change here.

DEGRADED, NEVER SILENT
  `file_amount` on this platform is a stale denormalized counter — measured live, "Videos" reports 2
  and serves 4, "Media Files" reports 0 and serves 75 — so coverage is NOT gated on it. Coverage is
  gated on visiting every folder in `all_folders`, and any folder whose served count falls SHORT of
  its own counter is re-fetched and reported. A run that cannot parse the bootstrap, sees 0 files
  from a populated drive, or fails to visit every folder is `degraded` with warnings[], never a quiet
  partial.

CLI:  python dam_bacardi.py                 # full drive, land
      python dam_bacardi.py --no-land       # parse only
      python dam_bacardi.py --rights        # show the gate decisions and exit
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

import dam
import rights

HOST = "media.bacardilimited.com"
BASE = "https://%s" % HOST
CONN_ID = "dam-bacardi"
SOURCE_ID = "dam-bacardi"
VENDOR = "Bacardi Limited"
DRIVE = 42                       # "Bacardi Public" — the drive published in the Media Centre nav
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "dam_bacardi")

# The vendor's own published brand roster (bacardilimited.com "Our Brands" + the drive's own folder
# and file naming). Aliases carry the accent-stripped and typographic variants the headlines actually
# use, so the match stays an exact literal comparison rather than a fuzzy one.
BRANDS = {
    "BACARDÍ": ["bacardí", "bacardi"],
    "GREY GOOSE": ["grey goose"],
    "PATRÓN": ["patrón", "patron"],
    "MARTINI": ["martini", "martini & rossi", "martini and rossi"],
    "DEWAR'S": ["dewar's", "dewars", "dewar’s", "john dewar", "john dewar & sons"],
    "BOMBAY SAPPHIRE": ["bombay sapphire", "bombay"],
    "ST~GERMAIN": ["st~germain", "st-germain", "st germain", "st.germain"],
    "CAZADORES": ["cazadores"],
    "ANGEL'S ENVY": ["angel's envy", "angels envy", "angel’s envy"],
    "WILLIAM LAWSON'S": ["william lawson's", "william lawsons", "william lawson’s"],
    "ERISTOFF": ["eristoff"],
    "BENRIACH": ["benriach"],
    "GLENDRONACH": ["glendronach", "the glendronach"],
    "ABERFELDY": ["aberfeldy"],
    "CRAIGELLACHIE": ["craigellachie"],
    "ROYAL BRACKLA": ["royal brackla"],
    "AULTMORE": ["aultmore"],
    "NOILLY PRAT": ["noilly prat"],
    "DRAMBUIE": ["drambuie"],
    "LEBLON": ["leblon"],
    "BANKS": ["banks rum"],
}

# Minimum assets a healthy full run of this drive lands. Measured 2,490 on 2026-08-03; the floor sits
# well below that so real growth/pruning is not flagged, while a collapse (a parse break, a moved
# bootstrap variable) is. This is a floor, never a cap — nothing here truncates.
MIN_ASSETS = 500


def _get(url, timeout=45, as_json=True):
    req = urllib.request.Request(url, headers={
        "User-Agent": rights.UA,
        "Accept": "application/json, text/html;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def _check_robots(rec, url):
    """Every fetched URL is checked against the record's VERBATIM robots snapshot before the request.
    Not a courtesy — the method guardrail for this capability is 'robots-permitted paths only', and a
    guardrail that isn't executed is a comment."""
    if not rights.robots_allows(rec.get("robots_snapshot") or "", url):
        raise rights.RightsViolation("robots.txt disallows %s — refusing to fetch" % url)
    return True


def parse_bootstrap(html):
    """Extract `window.DriveViewState` from the drive page. PURE — the fixture regression target.

    Brace-matched rather than regex-terminated: folder descriptions contain '}' and newlines, and a
    lazy `.*?}` would truncate the tree into a parse error that looks like site drift."""
    i = html.find("window.DriveViewState")
    if i < 0:
        return None, "no window.DriveViewState in the drive page (bootstrap moved)"
    j = html.find("{", i)
    if j < 0:
        return None, "DriveViewState present but has no object literal"
    depth, end, in_str, esc = 0, None, False, False
    for k in range(j, len(html)):
        ch = html[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end is None:
        return None, "DriveViewState object literal is unterminated"
    try:
        return json.loads(html[j:end]), None
    except Exception as e:
        return None, "DriveViewState is not valid JSON: %s" % str(e)[:120]


def parse_tree(payload):
    """Normalize a get-tree response → (files, folders, error). The envelope is
    {"status":"ok","body":{"tree":{...}}}; a non-ok status is an error, not an empty folder."""
    if not isinstance(payload, dict):
        return [], [], "get-tree returned %s, not an object" % type(payload).__name__
    if payload.get("status") != "ok":
        return [], [], "get-tree status=%r %s" % (payload.get("status"), str(payload.get("message"))[:80])
    tree = ((payload.get("body") or {}).get("tree")) or {}
    return list(tree.get("files") or []), list(tree.get("folders") or []), None


def harvest(rec, drive=DRIVE, log=print):
    """Walk the whole drive → (assets_raw, folders, drive_meta, warnings, coverage).

    The walk is complete by construction and cheap by accident: the root bootstrap already carries
    every folder AND (measured) every file, so the only extra requests are one per folder the
    bootstrap returned nothing for — which CONFIRMS an empty folder instead of assuming it — plus one
    per folder that served fewer files than its own counter claims. No caps, no sampling, no limit
    parameter anywhere ([[no-silent-caps-in-full-pulls]])."""
    warns = []
    url = "%s/drives/view-new/%d" % (BASE, drive)
    _check_robots(rec, url)
    rights.emit(rec, "catalog_metadata", url, surface="dam-harvest")
    html = _get(url, as_json=False)
    state, err = parse_bootstrap(html)
    if err:
        return [], [], {}, [err], {}

    tree = state.get("tree") or {}
    drive_meta = tree.get("drive") or state.get("drive") or {}
    root_id = state.get("rootFolderId") or (tree.get("root_folder") or {}).get("id")
    folders = list(tree.get("all_folders") or tree.get("folders") or [])
    if not folders:
        warns.append("bootstrap carried no folder list (all_folders missing) — tree shape drifted")

    by_id = {}
    for f in (tree.get("files") or []):
        by_id[f.get("id")] = f
    counts = {}
    for f in by_id.values():
        counts[f.get("parent_folder_id")] = counts.get(f.get("parent_folder_id"), 0) + 1

    # Folders the bootstrap said nothing about, and folders it under-served versus their own counter.
    todo = []
    for f in folders:
        fid, claimed, got = f.get("id"), int(f.get("file_amount") or 0), counts.get(f.get("id"), 0)
        if got == 0 or got < claimed:
            todo.append((fid, f.get("name"), claimed, got))

    visited = set(counts)
    for fid, name, claimed, got in todo:
        turl = "%s/drives/get-tree/%d?folder_id=%s" % (BASE, drive, fid)
        try:
            _check_robots(rec, turl)
            files, _subs, terr = parse_tree(_get(turl))
        except rights.RightsViolation:
            raise
        except Exception as e:
            warns.append("folder %s (%s): fetch failed: %s" % (fid, name, str(e)[:100]))
            continue
        if terr:
            warns.append("folder %s (%s): %s" % (fid, name, terr))
            continue
        visited.add(fid)
        for f in files:
            by_id.setdefault(f.get("id"), f)
        n = len(files)
        if claimed and n < claimed:
            # Reported honestly rather than silently accepted: the platform's counter is known to be
            # stale in BOTH directions, so a shortfall is a warning, not an assumed truncation.
            warns.append("folder %s (%s) served %d of a claimed %d files" % (fid, name, n, claimed))
        log("  folder %-6s %-22s %d files%s" % (fid, (name or "")[:22], n,
                                                "" if got == 0 else " (recheck, had %d)" % got))
        time.sleep(1.0)          # one request per folder; pace it anyway — this is someone's server

    unvisited = [f.get("id") for f in folders if f.get("id") not in visited]
    if unvisited:
        warns.append("%d folder(s) never returned files: %s" % (len(unvisited), unvisited[:10]))
    coverage = {"folders_total": len(folders), "folders_covered": len(visited),
                "folders_remaining": len(unvisited), "assets": len(by_id)}
    return list(by_id.values()), folders, drive_meta, warns, coverage


def pull(drive=DRIVE, land=True, state_dir=None, log=print):
    """The registry entrypoint. Lands `dam_assets` + `brand_events` + the rights ledger."""
    state_dir = state_dir or _STATE
    started = int(time.time() * 1000)
    pulled_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    rec = rights.load(SOURCE_ID)                 # raises if the source has no reviewed record
    perms = rec.get("permissions") or {}
    log("%s: rights %s / scope=%s (confidence %s)%s"
        % (SOURCE_ID, perms.get("image_use"), perms.get("scope"), perms.get("confidence"),
           " — COUNSEL REQUIRED" if perms.get("needs_counsel") and not rec.get("counsel_cleared") else ""))

    raw, folders, drive_meta, warns, coverage = harvest(rec, drive=drive, log=log)
    if not raw:
        status = "failed" if warns else "degraded"
        run = dam.run_record(SOURCE_ID, CONN_ID, started, 0, 0, status,
                             warns or ["drive returned 0 assets"],
                             [{"id": dam.ASSETS_TABLE, "rows": 0, "delta": 0, "dropped": 0,
                               "status": status}])
        log("%s: %s — %s" % (SOURCE_ID, status, "; ".join(warns) or "no assets"))
        _emit_result(status, coverage, 0, 0)
        return {}, [run], {"sampled": 0, "changed": 0, "dropped": 0}

    root_id = next((f.get("id") for f in folders if f.get("is_root")), None)
    paths = dam.folder_paths(folders, root_id)
    years = dam.year_map(folders)

    assets = [dam.asset_row(rec, VENDOR, drive_meta, paths.get(f.get("parent_folder_id"), ""),
                            f, pulled_at) for f in raw]
    events = dam.derive_events(rec, assets, BRANDS, folder_years=years, log=log)

    # Honesty checks — a healthy run that lands nothing usable must not read as success.
    if len(assets) < MIN_ASSETS:
        warns.append("only %d assets (floor %d) — bootstrap/tree parse likely drifted"
                     % (len(assets), MIN_ASSETS))
    titled = sum(1 for a in assets if (a.get("title") or a.get("name")))
    if assets and titled / len(assets) < 0.9:
        warns.append("title fill %d%% (<90%%) — file record schema drift" % round(100 * titled / len(assets)))
    if assets and not events:
        warns.append("%d assets but 0 brand events — brand roster matched nothing" % len(assets))

    # The claim this whole capability rests on: nothing left the granted scope. Assert it on the data
    # actually produced, not on intent — a retained byte or a derived hash under a prohibited record
    # is the one bug that must never ship quietly.
    leaked = [a for a in assets if a["retention"] != "pointer_only" or a["phash"] or a["embedding_ref"]]
    if leaked and not rights.may(rec, "retain_asset")[0]:
        raise rights.RightsViolation(
            "%d asset(s) carry retained bytes/derivatives under a record that forbids it — refusing "
            "to land (first: %s)" % (len(leaked), leaked[0].get("asset_id")))

    new, dropped = dam.snapshot_diff([a["asset_id"] for a in assets],
                                     os.path.join(state_dir, "dam_bacardi_%d.json" % drive))
    status = "degraded" if warns else "success"
    if land:
        dam.land(rec, assets, events, log=log)
    rights.land_emissions(
        [rights._log_emission(rec, "catalog_metadata", "%d assets / %d events" % (len(assets), len(events)),
                              True, "facts and pointers are ungated", surface="warehouse")], log=log)

    pointer_only = sum(1 for a in assets if a["retention"] == "pointer_only")
    log("%s: %d assets (%d new, %d dropped), %d brand events, %d/%d folders — %s"
        % (SOURCE_ID, len(assets), new, dropped, len(events),
           coverage.get("folders_covered", 0), coverage.get("folders_total", 0), status))
    log("  rights: %d/%d assets pointer-only (no bytes fetched, no hashes, no embeddings)"
        % (pointer_only, len(assets)))
    for w in warns:
        log("  warn: %s" % w)

    run = dam.run_record(
        SOURCE_ID, CONN_ID, started, len(assets), new, status, warns,
        [{"id": dam.ASSETS_TABLE, "rows": len(assets), "delta": new, "dropped": dropped, "status": status},
         {"id": dam.EVENTS_TABLE, "rows": len(events), "delta": len(events), "dropped": 0, "status": status}])
    _emit_result(status, coverage, len(assets), len(events))

    header = ["Folder", "Asset", "Type", "Size", "Retention", "Scope"]
    prev = [[a["folder_path"], (a["title"] or a["name"] or "")[:60], a["asset_type"],
             a["size_bytes"], a["retention"], a["image_scope"]] for a in assets]
    ds = {dam.ASSETS_TABLE: {"header": header, "rows": prev[:1000], "total": len(assets),
                             "_rows_full": prev}}
    return ds, [run], {"sampled": len(assets), "changed": new, "dropped": dropped}


def _emit_result(status, coverage, assets, events):
    """The marker run_sources.py reads (`HOODIE_RESULT`), so a degraded run can never close as ok."""
    print("HOODIE_RESULT " + json.dumps({
        "status": status, "items_done": assets, "items_total": assets,
        "events": events, "coverage": coverage}))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bacardi Media Centre public drive (dam-bacardi).")
    ap.add_argument("--drive", type=int, default=DRIVE, help="drive id (default 42, 'Bacardi Public')")
    ap.add_argument("--no-land", action="store_true", help="parse only, don't write to the warehouse")
    ap.add_argument("--rights", action="store_true", help="print the rights record + gate and exit")
    a = ap.parse_args(argv)
    if a.rights:
        rights.main([SOURCE_ID])
        return
    ds, runs, mv = pull(drive=a.drive, land=not a.no_land)
    if ds:
        r = list(ds.values())[0]
        for row in r["rows"][:12]:
            print("  •", row[0], "|", str(row[1])[:52], "|", row[2], "|", row[4])
    print("runs:", [(x["status"], x["total"], x["warnings"][:2]) for x in runs])
    print("movement:", mv)


if __name__ == "__main__":
    sys.exit(main())
