"""Exercise the DNA DAM platform connector (Bacardi tenant) end-to-end against the FROZEN fixture, with the network and
the warehouse mocked.

The point of this suite is not "does it parse" — it is the claim the capability makes: a full run
against a source whose terms forbid reuse lands every FACT and fetches ZERO asset bytes. So the
network mock counts asset requests and the assertions are about what did NOT happen."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import dam           # noqa: E402
import dam_dna as db      # noqa: E402
import rights        # noqa: E402

FAILS = []
FIXTURE = os.path.join(HERE, "fixtures", "dam_dna_bacardi_drive42.html")
GT_FIXTURE = os.path.join(HERE, "fixtures", "dam_dna_gettree_3906.json")


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


# ── network mock: serves the fixtures, and REFUSES to serve an asset ──────────────────────────────
FETCHED = []
ASSET_FETCHES = []
_ASSET_PAT = re.compile(r"/company-files/|\.(jpg|jpeg|png|mp4|mov|pdf|docx?|zip|tif|svg|ttf)$", re.I)


def fake_get(url, timeout=45, as_json=True):
    FETCHED.append(url)
    if _ASSET_PAT.search(url):
        ASSET_FETCHES.append(url)
        raise AssertionError("connector fetched an ASSET url: %s" % url)
    if "/drives/view-new/" in url:
        return open(FIXTURE, encoding="utf-8").read()
    if "/drives/get-tree/" in url:
        payload = json.load(open(GT_FIXTURE, encoding="utf-8"))
        fid = int(re.search(r"folder_id=(\d+)", url).group(1))
        # Serve an EMPTY folder for anything but the captured one, so the "confirm empty" path runs.
        t = payload["body"]["tree"]
        if fid != 3906:
            t = dict(t, files=[], folders=[], loaded_parent_folder_ids=[fid])
            t["current_folder"] = dict(t["current_folder"], id=fid)
            payload = {"status": "ok", "message": "", "body": {"tree": t}}
        return payload
    raise AssertionError("unexpected fetch: %s" % url)


db._get = fake_get

# `dam.document_text` opens urllib DIRECTLY (it is its own chokepoint, deliberately not routed through
# the connector's page fetcher), so patching db._get alone would let this suite make REAL requests to
# a live media server — and the "zero asset fetches" assertion below would not even see them. Spy on
# it instead, and make the spy REFUSE anything that is not a text document, so the suite proves the
# type gate rather than assuming it.
DOC_READS = []
_PRESS = ("HOST WITH FLAIR: A NEW EDIT\n\nPriced at £150\nAvailable from: example.com\n\n"
          "[LONDON, UK, 9th OCTOBER 2025]  ST~GERMAIN, the French Elderflower Liqueur, has partnered.")


def fake_document_text(rec, asset, timeout=60, log=print):
    ext = (asset.get("extension") or "").lower()
    if ext not in rights.DOCUMENT_EXTENSIONS:
        raise AssertionError("document_text was handed a non-document (.%s)" % ext)
    DOC_READS.append(asset.get("asset_id"))
    return _PRESS


dam.document_text = fake_document_text


class FakeWarehouse:
    written = {}

    @staticmethod
    def write_accumulate(name, rows, key=None, fields=None, coverage=True):
        assert callable(key) or isinstance(key, str), "key must be a callable or a column name"
        FakeWarehouse.written.setdefault(name, []).extend(rows)
        return {"rows": len(rows)}

    @staticmethod
    def write_partition(name, part, rows, fields=None, dtypes=None):
        FakeWarehouse.written.setdefault(name, []).extend(rows)
        return {"rows": len(rows)}


sys.modules["warehouse"] = FakeWarehouse

# ── parse ─────────────────────────────────────────────────────────────────────────────────────────
print("bootstrap parse:")
html = open(FIXTURE, encoding="utf-8").read()
state, err = db.parse_bootstrap(html)
check(err is None and state, "the frozen drive page parses (err=%s)" % err)
tree = (state or {}).get("tree", {})
check(len(tree.get("all_folders") or []) == 17, "17 folders recovered from the bootstrap")
check(len(tree.get("files") or []) > 100, "the sampled file set is recovered")
check((state or {}).get("rootFolderId") == 3616, "root folder id is read from the bootstrap")

bad, err = db.parse_bootstrap("<html><body>no bootstrap here</body></html>")
check(bad is None and "DriveViewState" in err,
      "a page WITHOUT the bootstrap reports an error rather than an empty drive")
bad, err = db.parse_bootstrap('<script>window.DriveViewState = {"tree": {"files": [</script>')
check(bad is None and err, "a truncated bootstrap reports an error, never a partial parse")

# A description containing a brace must not truncate the object (the reason for brace matching).
tricky = '<script>window.DriveViewState = {"a": "close } brace", "b": {"c": 1}};</script>'
obj, err = db.parse_bootstrap(tricky)
check(err is None and obj and obj["b"]["c"] == 1, "a '}' inside a string does not truncate the parse")

print("\nget-tree envelope:")
files, folders, terr = db.parse_tree(json.load(open(GT_FIXTURE, encoding="utf-8")))
check(terr is None and len(files) == 15, "an ok envelope yields its files")
_f, _d, terr = db.parse_tree({"status": "error", "message": "nope"})
check(terr and "error" in terr, "a non-ok envelope is an ERROR, not an empty folder")
_f, _d, terr = db.parse_tree("<html>")
check(terr, "a non-object response is an error")

# ── a full run ────────────────────────────────────────────────────────────────────────────────────
print("\nfull pull (rights: prohibited):")
rec = rights.load("dam-bacardi")
ds, runs, mv = db.pull(land=True, state_dir=os.path.join(HERE, "agent_state", "_test_dam"), log=lambda *a: None)
run = runs[0]
assets = FakeWarehouse.written.get(dam.ASSETS_TABLE, [])
events = FakeWarehouse.written.get(dam.EVENTS_TABLE, [])

check(len(assets) > 100, "the run landed asset pointers (%d)" % len(assets))
check(run["status"] in ("success", "degraded"), "the run reports a status (%s)" % run["status"])
check(not ASSET_FETCHES, "ZERO asset urls were fetched during a full run")
check(all("/drives/" in u for u in FETCHED),
      "every request went to a /drives/ path — nothing touched /api/ or an asset")

print("\nwhat was withheld:")
check(all(a["retention"] == "pointer_only" for a in assets),
      "every asset row is retention=pointer_only under a prohibited record")
check(all(a["phash"] is None for a in assets), "no perceptual hash was derived")
check(all(a["embedding_ref"] is None for a in assets), "no embedding was derived")
check(all(a["withheld_reason"] for a in assets), "every withheld asset carries the REASON it was withheld")
check(all(a["rights_ref"] == rights.rights_ref(rec) for a in assets),
      "every asset row cites the exact rights record revision it was cleared under")
check(all(a["image_use"] == "prohibited" and a["image_scope"] == "none" for a in assets),
      "the rights verdict travels on the data, not just in a log line")

print("\nfacts still flowed:")
check(len(events) > 0, "brand events were derived (%d)" % len(events))
check(all(e["rights_ref"] for e in events), "every event cites its rights record")
brands = {e["brand"] for e in events}
check("BACARDÍ" in brands or "GREY GOOSE" in brands,
      "known vendor brands were matched deterministically (%s)" % sorted(brands)[:4])
check(all(e["hoodie_brand_id"].startswith("dam-bacardi:") for e in events),
      "hoodie_brand_id is a vendor-scoped slug, not an invented canon id")

print("\nprovenance labelling:")
prov = [json.loads(e["field_provenance"]) for e in events]
check(all(p.get("brand") in (dam.DETERMINISTIC, dam.INFERENCE) for p in prov),
      "every event labels its brand claim as DETERMINISTIC or INFERENCE — never unlabelled")
unamb = [(e, p) for e, p in zip(events, prov)
         if dam._mnorm(p.get("brand_alias") or "") not in dam.AMBIGUOUS_ALIASES]
check(unamb and all(p["brand"] == dam.DETERMINISTIC for _e, p in unamb),
      "a match on an unambiguous brand literal is DETERMINISTIC (%d events)" % len(unamb))
amb = [(e, p) for e, p in zip(events, prov)
       if dam._mnorm(p.get("brand_alias") or "") in dam.AMBIGUOUS_ALIASES]
check(all(p["brand"] == dam.INFERENCE for _e, p in amb),
      "a match on a common-word alias (MARTINI, BOMBAY, …) is INFERENCE (%d events)" % len(amb))
check(all(p.get("event_type") == dam.INFERENCE for p in prov),
      "event_type is labelled INFERENCE (a keyword read of free text)")
dated = [e for e in events if e["event_date"]]
check(dated and all(e["event_date_precision"] in ("year", "day") for e in dated),
      "every dated event states its precision as year (folder) or day (document)")
check(all(json.loads(e["field_provenance"]).get("event_date") == dam.DETERMINISTIC for e in dated),
      "every date — folder-derived or document-derived — is DETERMINISTIC")
year_only = [e for e in events if e["event_date_precision"] == "year"]
check(year_only and all(e["event_date"].endswith("-01-01") for e in year_only),
      "a folder-derived date is the year boundary, not a fabricated day")
undated = [e for e in events if not e["event_date"]]
check(all(e["event_date_precision"] == "unknown" for e in undated),
      "an event with no year folder is precision=unknown, NOT back-filled from the upload stamp")
# The trap this rule exists for: every 2018 asset carries created_on 2018-04-11 (a bulk migration).
check(not any(e["event_date"] and e["event_date"].endswith("-04-11") for e in events),
      "no event date was copied from the bulk-upload timestamp")

print("\nevent grain:")
titles = [e["title"] for e in events]
check(len(titles) == len(set((e["brand"], e["title"], e["event_date"]) for e in events)),
      "one row per (brand, story, year) — duplicate assets of one story collapse")
multi = [e for e in events if e["asset_count"] > 1]
check(multi, "stories with several assets report asset_count > 1 (%d such)" % len(multi))

# ── the gate refuses the byte path ────────────────────────────────────────────────────────────────
print("\nfacts read from the documents (under a record that forbids every image action):")
check(DOC_READS, "press-release documents were read (%d)" % len(DOC_READS))
by_id = {a["asset_id"]: a for a in assets}
check(all((by_id[i]["extension"] or "").lower() in rights.DOCUMENT_EXTENSIONS for i in DOC_READS),
      "every document read was a text type — the image assets were never offered to it")
day = [e for e in events if e["event_date_precision"] == "day"]
check(day, "documents upgraded events to DAY precision (%d)" % len(day))
check(all(json.loads(e["field_provenance"]).get("event_date_source") == "dateline" for e in day),
      "a day-precision date cites the dateline it was read from")
priced = [e for e in events if e["price"] is not None]
check(priced and all(json.loads(e["field_provenance"]).get("price") == dam.DETERMINISTIC
                     for e in priced),
      "a price read out of a document body is DETERMINISTIC (%d priced)" % len(priced))
check(not ASSET_FETCHES, "reading documents fetched ZERO image/video assets")

print("\nprose never lands:")
check(dam.assert_no_prose(events), "no landed event field carries body-length text")
long_field = max((len(v) for e in events for v in e.values() if isinstance(v, str)), default=0)
check(long_field <= dam.MAX_LANDED_FIELD_CHARS,
      "longest landed string is %d chars, under the %d ceiling" % (long_field, dam.MAX_LANDED_FIELD_CHARS))
check(not any(_PRESS[40:120] in str(v) for e in events for v in e.values()),
      "no fragment of the press-release body appears in any landed field")

print("\nthe chokepoint:")
sample = dict(assets[0])
try:
    dam.asset_bytes(rec, sample)
    check(False, "asset_bytes must raise under a prohibited record")
except rights.RightsViolation as e:
    check("fetch_asset" in str(e), "asset_bytes raises RightsViolation before any network call")
except Exception as e:
    check(False, "asset_bytes raised %s, expected RightsViolation" % type(e).__name__)

ph, emb, why = dam.derive_cv_reference(rec, sample, log=lambda *a: None)
check(ph is None and emb is None and why,
      "derive_cv_reference withholds and returns a reason, without fetching")

# ── the mechanical ratchet ────────────────────────────────────────────────────────────────────────
# A guard nobody can bypass by accident. Every asset-bytes path must go through dam.asset_bytes; a
# connector that opens an asset url itself would sail past every assertion above, because the
# assertions only see what the connector chose to land.
print("\nsource scan (no ungated asset fetch):")
FETCHERS = re.compile(r"\b(urlopen|urlretrieve|requests\.get|polite\.get|curl_cffi|httpx\.)")
ASSET_REFS = re.compile(r"asset_url|download_url|\[.url.\]|company-files")
offenders = []
for fn in sorted(f for f in os.listdir(HERE) if f.startswith("dam") and f.endswith(".py")):
    src = open(os.path.join(HERE, fn), encoding="utf-8").read()
    # dam.asset_bytes is the sanctioned exception; blank it out before scanning.
    src = re.sub(r"def asset_bytes\(.*?(?=\ndef |\Z)", "", src, flags=re.S)
    for i, line in enumerate(src.split("\n"), 1):
        code = line.split("#", 1)[0]
        if FETCHERS.search(code) and ASSET_REFS.search(code):
            offenders.append("%s:%d %s" % (fn, i, line.strip()[:80]))
check(not offenders, "no dam module fetches an asset url outside dam.asset_bytes (%s)" % (offenders or "clean"))

# ── degraded, never silent ────────────────────────────────────────────────────────────────────────
print("\ndegraded paths:")
db._get = lambda url, timeout=45, as_json=True: "<html>bootstrap gone</html>"
ds2, runs2, mv2 = db.pull(land=False, state_dir=os.path.join(HERE, "agent_state", "_test_dam"),
                          log=lambda *a: None)
check(runs2[0]["status"] in ("failed", "degraded"),
      "a broken bootstrap reports %s, not success" % runs2[0]["status"])
check(runs2[0]["warnings"], "the failure carries a warning naming the cause")
check(mv2["sampled"] == 0, "a broken run lands nothing rather than a partial catalog")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
