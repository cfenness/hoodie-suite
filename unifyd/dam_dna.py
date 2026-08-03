#!/usr/bin/env python3
"""dam_dna.py — the **DNA** DAM platform connector (dna.online). One connector per DAM VENDOR.

WHY THIS IS A VENDOR RECIPE, NOT A BACARDI SCRAPER
  Bacardi's media centre is not bespoke: its footer is "Powered by DNA" (`dna.online`,
  `/a/global/dna-footer-logo.v2.svg`), and the whole surface is DNA's stock shape — a `company_id`
  tenant, numbered `company_drive_id` drives, `/drives/view-new/<drive>`, `/drives/get-tree/<drive>`,
  `/company-files/<company>/original/<token>.<ext>`, an Algolia index prefixed `DNA_`. So proving the
  recipe once proves every supplier on DNA: point `TENANTS` at another host + drive and that
  supplier's whole public drive is a deterministic pull. Same payoff as the VIP Brand Builder and
  SevenFifty platform recipes ([[system-scrape-recipes]]), one tenant at a time.

  `fingerprint()` is the discovery half: hand it any candidate media-centre URL and it says whether
  the host is a DNA tenant (and which drive), which is what turns the P4 vendor census into new
  sources without new code.

ONE CONNECTOR PER VENDOR, ONE RIGHTS RECORD PER SUPPLIER
  These are different axes and both matter. The TRANSPORT is a property of the vendor (DNA), so it
  lives here once. The PERMISSION is a property of the supplier — Bacardi's terms bind Bacardi's
  assets and say nothing about anyone else's — so every tenant is its own registry source with its
  own `rights_records/<id>.json`, and `pull()` loads that record before it fetches anything.

THE TWO READS (both robots-PERMITTED; `/api/` is disallowed and we never touch it)
    GET /drives/view-new/<drive>               → HTML bootstrapping `window.DriveViewState = {...}`
                                                 (drive, root/current folder, **all_folders**, files)
    GET /drives/get-tree/<drive>?folder_id=<n> → {"status":"ok","body":{"tree":{…}}} — that folder

  DNA's robots.txt (captured verbatim in each tenant's rights record) disallows `/api/`, `/users/`,
  `/administrators/`, `/dashboard`, `/settings`, `/seo/`, `/login`, `/shared/`, `/account/`. Both
  reads sit outside every one of those, and `_check_robots` enforces it per request against the
  snapshot. If DNA ever moves the tree endpoint under `/api/`, this connector STOPS — it does not
  look for another way in. Nothing here enumerates ids, tampers with parameters, or probes for
  non-public drives: the drive id is the one published in the tenant's own navigation.

DEGRADED, NEVER SILENT
  `file_amount` is a stale denormalized counter on this platform — measured live on Bacardi, "Videos"
  reports 2 and serves 4, "Media Files" reports 0 and serves 75 — so coverage is NOT gated on it.
  Coverage is gated on visiting every folder in `all_folders`; a folder that serves fewer files than
  its own counter claims is re-fetched and reported. A run that can't parse the bootstrap, sees 0
  files from a populated drive, or leaves a folder unvisited is `degraded`, never a quiet partial.

CLI:  python dam_dna.py --tenant bacardi              # full drive, land
      python dam_dna.py --tenant bacardi --no-land    # parse only
      python dam_dna.py --rights bacardi              # show the gate decisions and exit
      python dam_dna.py --fingerprint https://media.example.com/   # is this host a DNA tenant?
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

VENDOR_PLATFORM = "DNA (dna.online)"
_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "dam_dna")

# Bacardi's own published brand roster (bacardilimited.com "Our Brands" + the drive's file naming).
# Aliases carry the accent/typographic variants the headlines actually use; both sides of the match
# are normalized, so an alias may be written the natural way.
BACARDI_BRANDS = {
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
    "CORRALEJO": ["corralejo"],
    "NOILLY PRAT": ["noilly prat"],
    "DRAMBUIE": ["drambuie"],
    "LEBLON": ["leblon"],
    "BANKS": ["banks rum"],
}

# One entry per SUPPLIER on the DNA platform. `source_id` is both the registry id and the rights
# record filename — the record is a property of the supplier, never of the platform.
# `min_assets` is a collapse FLOOR (a parse break reads as degraded), never a cap: nothing here
# truncates ([[no-silent-caps-in-full-pulls]]).
TENANTS = {
    "bacardi": {
        "source_id": "dam-bacardi", "conn_id": "dam-bacardi",
        "vendor": "Bacardi Limited", "host": "media.bacardilimited.com",
        "drive": 42,               # "Bacardi Public" — published in the Media Centre's own nav
        "brands": BACARDI_BRANDS,
        "min_assets": 500,         # measured 2,490 on 2026-08-03
    },
}


def tenant(name):
    t = TENANTS.get(name)
    if not t:
        raise KeyError("unknown DNA tenant %r (known: %s)" % (name, ", ".join(sorted(TENANTS))))
    return t


def _get(url, timeout=45, as_json=True):
    req = urllib.request.Request(url, headers={
        "User-Agent": rights.UA,
        "Accept": "application/json, text/html;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if as_json else body


def _check_robots(rec, url):
    """Every fetched URL is checked against the record's VERBATIM robots snapshot BEFORE the request.
    Not a courtesy — 'robots-permitted paths only' is the method guardrail for this capability, and a
    guardrail that isn't executed is a comment."""
    if not rights.robots_allows(rec.get("robots_snapshot") or "", url):
        raise rights.RightsViolation("robots.txt disallows %s — refusing to fetch" % url)
    return True


# ── discovery (feeds the P4 vendor census) ────────────────────────────────────────────────────────
_DNA_MARKERS = ("window.DriveViewState", "dna-footer-logo", "DNAPostHogConfig",
                "/drives/view-new/", "/company-files/")


def fingerprint(url, log=print):
    """Is this host a DNA tenant? Returns {is_dna, markers, drive_id, company_id, drive_name} — the
    census primitive: a supplier whose media centre fingerprints as DNA needs a TENANTS row and a
    rights record, not a new connector."""
    try:
        html = _get(url, as_json=False)
    except Exception as e:
        return {"is_dna": False, "error": str(e)[:140], "markers": []}
    found = [m for m in _DNA_MARKERS if m in html]
    out = {"is_dna": len(found) >= 2, "markers": found, "drive_id": None,
           "company_id": None, "drive_name": None}
    state, _err = parse_bootstrap(html)
    if state:
        d = (state.get("tree") or {}).get("drive") or state.get("drive") or {}
        out.update(drive_id=d.get("id"), company_id=d.get("company_id"), drive_name=d.get("name"),
                   is_public=d.get("is_public"))
    m = re.search(r"/drives/view-new/(\d+)", html)
    if out["drive_id"] is None and m:
        out["drive_id"] = int(m.group(1))
    log("fingerprint %s: DNA=%s drive=%s (%s) markers=%s"
        % (url, out["is_dna"], out["drive_id"], out.get("drive_name"), ",".join(found) or "none"))
    return out


# ── parsing (pure — the fixture regression targets) ───────────────────────────────────────────────
def parse_bootstrap(html):
    """Extract `window.DriveViewState` from a DNA drive page.

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
    {"status":"ok","body":{"tree":{...}}}; a non-ok status is an ERROR, not an empty folder."""
    if not isinstance(payload, dict):
        return [], [], "get-tree returned %s, not an object" % type(payload).__name__
    if payload.get("status") != "ok":
        return [], [], "get-tree status=%r %s" % (payload.get("status"), str(payload.get("message"))[:80])
    tree = ((payload.get("body") or {}).get("tree")) or {}
    return list(tree.get("files") or []), list(tree.get("folders") or []), None


# ── the walk ──────────────────────────────────────────────────────────────────────────────────────
def harvest(rec, host, drive, log=print):
    """Walk a whole DNA drive → (assets_raw, folders, drive_meta, warnings, coverage).

    Complete by construction and cheap by accident: the root bootstrap already carries every folder
    AND (measured) every file, so the only extra requests are one per folder the bootstrap returned
    nothing for — which CONFIRMS an empty folder rather than assuming it — plus one per folder that
    served fewer files than its own counter claims. Measured on Bacardi: 3 requests, 2,490 assets."""
    base = "https://%s" % host
    warns = []
    url = "%s/drives/view-new/%d" % (base, drive)
    _check_robots(rec, url)
    rights.emit(rec, "catalog_metadata", url, surface="dam-harvest")
    html = _get(url, as_json=False)
    state, err = parse_bootstrap(html)
    if err:
        return [], [], {}, [err], {}

    tree = state.get("tree") or {}
    drive_meta = tree.get("drive") or state.get("drive") or {}
    folders = list(tree.get("all_folders") or tree.get("folders") or [])
    if not folders:
        warns.append("bootstrap carried no folder list (all_folders missing) — tree shape drifted")

    by_id = {f.get("id"): f for f in (tree.get("files") or [])}
    counts = {}
    for f in by_id.values():
        counts[f.get("parent_folder_id")] = counts.get(f.get("parent_folder_id"), 0) + 1

    todo = [(f.get("id"), f.get("name"), int(f.get("file_amount") or 0), counts.get(f.get("id"), 0))
            for f in folders
            if counts.get(f.get("id"), 0) == 0
            or counts.get(f.get("id"), 0) < int(f.get("file_amount") or 0)]

    visited = set(counts)
    for fid, name, claimed, got in todo:
        turl = "%s/drives/get-tree/%d?folder_id=%s" % (base, drive, fid)
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
        if claimed and len(files) < claimed:
            # Reported honestly rather than silently accepted: the platform's counter is known stale
            # in BOTH directions, so a shortfall is a warning, not an assumed truncation.
            warns.append("folder %s (%s) served %d of a claimed %d files"
                         % (fid, name, len(files), claimed))
        log("  folder %-6s %-22s %d files%s" % (fid, (name or "")[:22], len(files),
                                                "" if got == 0 else " (recheck, had %d)" % got))
        time.sleep(1.0)          # one request per folder; pace it anyway — this is someone's server

    unvisited = [f.get("id") for f in folders if f.get("id") not in visited]
    if unvisited:
        warns.append("%d folder(s) never returned files: %s" % (len(unvisited), unvisited[:10]))
    coverage = {"folders_total": len(folders), "folders_covered": len(visited),
                "folders_remaining": len(unvisited), "assets": len(by_id)}
    return list(by_id.values()), folders, drive_meta, warns, coverage


def pull(tenant_name="bacardi", land=True, state_dir=None, log=print):
    """The registry entrypoint, per TENANT. Lands `dam_assets` + `brand_events` + the rights ledger."""
    t = tenant(tenant_name)
    sid = t["source_id"]
    state_dir = state_dir or _STATE
    started = int(time.time() * 1000)
    pulled_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    rec = rights.load(sid)                       # raises if this supplier has no reviewed record
    perms = rec.get("permissions") or {}
    log("%s (%s on %s): rights %s / scope=%s (confidence %s)%s"
        % (sid, t["vendor"], VENDOR_PLATFORM, perms.get("image_use"), perms.get("scope"),
           perms.get("confidence"),
           " — COUNSEL REQUIRED" if perms.get("needs_counsel") and not rec.get("counsel_cleared") else ""))

    raw, folders, drive_meta, warns, coverage = harvest(rec, t["host"], t["drive"], log=log)
    if not raw:
        status = "failed" if warns else "degraded"
        run = dam.run_record(sid, t["conn_id"], started, 0, 0, status,
                             warns or ["drive returned 0 assets"],
                             [{"id": dam.ASSETS_TABLE, "rows": 0, "delta": 0, "dropped": 0,
                               "status": status}])
        log("%s: %s — %s" % (sid, status, "; ".join(warns) or "no assets"))
        _emit_result(status, coverage, 0, 0)
        return {}, [run], {"sampled": 0, "changed": 0, "dropped": 0}

    root_id = next((f.get("id") for f in folders if f.get("is_root")), None)
    paths = dam.folder_paths(folders, root_id)
    years = dam.year_map(folders)

    assets = [dam.asset_row(rec, t["vendor"], drive_meta, paths.get(f.get("parent_folder_id"), ""),
                            f, pulled_at) for f in raw]
    events = dam.derive_events(rec, assets, t["brands"], folder_years=years, log=log)

    # Read the press releases for the facts they state. Gated on `fetch_document_facts` — text
    # documents only, bytes transient, prose never landed — so this runs even under a record that
    # forbids every image action, which is the whole point: facts always flow. DAM_DOCS=0 disables;
    # DAM_DOC_LIMIT bounds a run and REPORTS what it left ([[no-silent-caps-in-full-pulls]]).
    doc_cov = {}
    if os.environ.get("DAM_DOCS", "1") != "0":
        _lim = int(os.environ.get("DAM_DOC_LIMIT") or 0) or None
        doc_cov = dam.enrich_events_from_documents(rec, events, assets, limit=_lim, log=log)
        coverage.update(doc_cov)
        if doc_cov.get("documents_remaining"):
            warns.append("%d document(s) not read (DAM_DOC_LIMIT=%s) — event dates/prices for those "
                         "stories stay at folder-year precision"
                         % (doc_cov["documents_remaining"], _lim))
        if doc_cov.get("documents_matched") and not doc_cov.get("documents_read"):
            warns.append("%d documents matched but NONE could be read — press-release facts are "
                         "silently absent (pypdf missing? fetch blocked?)" % doc_cov["documents_matched"])

    # Honesty checks — a run that lands nothing usable must not read as success.
    if len(assets) < t["min_assets"]:
        warns.append("only %d assets (floor %d) — bootstrap/tree parse likely drifted"
                     % (len(assets), t["min_assets"]))
    titled = sum(1 for a in assets if (a.get("title") or a.get("name")))
    if assets and titled / len(assets) < 0.9:
        warns.append("title fill %d%% (<90%%) — file record schema drift"
                     % round(100 * titled / len(assets)))
    if assets and not events:
        warns.append("%d assets but 0 brand events — brand roster matched nothing" % len(assets))

    # The claim this capability rests on: nothing left the granted scope. Asserted on the data
    # actually produced, not on intent — a retained byte or a derived hash under a prohibited record
    # is the one bug that must never ship quietly.
    leaked = [a for a in assets if a["retention"] != "pointer_only" or a["phash"] or a["embedding_ref"]]
    if leaked and not rights.may(rec, "retain_asset")[0]:
        raise rights.RightsViolation(
            "%d asset(s) carry retained bytes/derivatives under a record that forbids it — refusing "
            "to land (first: %s)" % (len(leaked), leaked[0].get("asset_id")))

    new, dropped = dam.snapshot_diff([a["asset_id"] for a in assets],
                                     os.path.join(state_dir, "%s_%d.json" % (sid, t["drive"])))
    status = "degraded" if warns else "success"
    if land:
        dam.land(rec, assets, events, log=log)
    rights.land_emissions(
        [rights._log_emission(rec, "catalog_metadata",
                              "%d assets / %d events" % (len(assets), len(events)),
                              True, "facts and pointers are ungated", surface="warehouse")], log=log)

    pointer_only = sum(1 for a in assets if a["retention"] == "pointer_only")
    log("%s: %d assets (%d new, %d dropped), %d brand events, %d/%d folders — %s"
        % (sid, len(assets), new, dropped, len(events),
           coverage.get("folders_covered", 0), coverage.get("folders_total", 0), status))
    log("  rights: %d/%d assets pointer-only (no bytes fetched, no hashes, no embeddings)"
        % (pointer_only, len(assets)))
    for w in warns:
        log("  warn: %s" % w)

    run = dam.run_record(
        sid, t["conn_id"], started, len(assets), new, status, warns,
        [{"id": dam.ASSETS_TABLE, "rows": len(assets), "delta": new, "dropped": dropped,
          "status": status},
         {"id": dam.EVENTS_TABLE, "rows": len(events), "delta": len(events), "dropped": 0,
          "status": status}])
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
    ap = argparse.ArgumentParser(description="DNA DAM platform connector (dna.online).")
    ap.add_argument("--tenant", default="bacardi", help="tenant key (default bacardi); see TENANTS")
    ap.add_argument("--no-land", action="store_true", help="parse only, don't write to the warehouse")
    ap.add_argument("--rights", metavar="TENANT", help="print that tenant's rights record + gate, exit")
    ap.add_argument("--fingerprint", metavar="URL", help="is this media-centre URL a DNA tenant?")
    a = ap.parse_args(argv)
    if a.fingerprint:
        print(json.dumps(fingerprint(a.fingerprint), indent=2))
        return
    if a.rights:
        rights.main([tenant(a.rights)["source_id"]])
        return
    ds, runs, mv = pull(tenant_name=a.tenant, land=not a.no_land)
    if ds:
        for row in list(ds.values())[0]["rows"][:12]:
            print("  •", row[0], "|", str(row[1])[:52], "|", row[2], "|", row[4])
    print("runs:", [(x["status"], x["total"], x["warnings"][:2]) for x in runs])
    print("movement:", mv)


if __name__ == "__main__":
    sys.exit(main())
