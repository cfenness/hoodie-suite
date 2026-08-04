#!/usr/bin/env python3
"""dam.py — the shared spine for harvesting supplier/brand DIGITAL ASSET LIBRARIES (media centres).

One recipe per PLATFORM, one rights record per SOURCE — the same shape as the distributor-catalog
recipes ([[distributor-catalog-recipes]]), with one addition that changes everything: a DAM's payload
is somebody else's copyrighted work, so the rights record is not documentation, it is a gate that
runs (see `rights.py`).

WHAT A DAM CONNECTOR PRODUCES (two outputs, both canon-keyed)
  1. `dam_assets`   — a POINTER row per asset: identity, folder path, filename, type/extension, size,
                      timestamps, and the URL. Never the bytes. Pointers and metadata are facts about
                      a file, ungated by design.
  2. `brand_events` — a dated product-event feed derived from press-release titles and asset metadata:
                      launches, limited editions, availability, partnerships, awards. Facts always
                      flow, so this lands regardless of what the image rights say.

  The CV reference gallery (phash + embedding) is the third output, and it is the one the gate can
  refuse. `dam_assets` carries `retention` / `phash` / `embedding_ref` columns that stay
  `pointer_only` / NULL when the source's scope doesn't cover derivative works — so the row itself
  shows you what was withheld and why (`rights_ref`), instead of the gallery quietly having a hole.

THE CHOKEPOINT
  `asset_bytes()` is the ONLY function in this package that may fetch an asset's bytes, and it calls
  `rights.require(rec, "fetch_asset")` first. A connector NEVER opens an asset URL itself. This is
  enforced mechanically by `dam_gate_test.py`, which scans this package's source for any fetch of an
  asset/download URL outside this function — the same ratchet shape as `browser_driver_test.py`,
  because "we would never do that" is not a control and a quiet degrade is indistinguishable from
  success ([[quiet-degrades]]).

THE HONESTY CONTRACT (dq.js spirit)
  Every derived event field is labelled DETERMINISTIC (read verbatim out of the source, or computed
  by an exact rule over it) or INFERENCE (a classifier's read of free text), per field, per row, in
  `field_provenance`. The distinction that matters most here is the DATE: a DAM's `created_on` is the
  UPLOAD timestamp, not the event date — Bacardi's entire 2018 folder carries `2018-04-11`, the day
  it was bulk-migrated. So the event date comes from the year FOLDER when the source organizes that
  way (precision `year`, deterministic) and is otherwise NULL with precision `unknown`. It is never
  back-filled from an upload stamp; a confident wrong date is worse than an absent one.
"""
import hashlib
import json
import os
import re
import time

import rights

ASSETS_TABLE = "dam_assets"
EVENTS_TABLE = "brand_events"

ASSET_FIELDS = [
    "source_id", "vendor", "drive_id", "drive_name", "folder_id", "folder_path", "asset_id",
    "asset_token", "name", "title", "description", "asset_type", "extension", "mime_type",
    "size_bytes", "asset_url", "thumb_url", "download_url", "created_on", "updated_on",
    # rights-governed columns — the visible record of what was and wasn't allowed
    "rights_ref", "image_use", "image_scope", "retention", "phash", "embedding_ref",
    "withheld_reason", "pulled_at",
]

EVENT_FIELDS = [
    "event_id", "hoodie_brand_id", "brand_key", "canon_brand", "brand_resolution", "brand",
    "sku_id", "event_type", "event_date", "event_date_precision", "market", "price", "currency",
    "abv", "title", "asset_count", "source", "source_id", "source_asset_ids", "source_url",
    "rights_ref", "field_provenance", "fetched_at",
]

DETERMINISTIC = "DETERMINISTIC"
INFERENCE = "INFERENCE"

# ── event classification (INFERENCE — a keyword read of a headline, labelled as such) ──────────────
# Ordered: the first matching type wins, most specific first. `launch` sits above `partnership`
# because "X LAUNCHES Y WITH PARTNER Z" is a launch story that mentions a partner.
#
# `leadership` and `corporate_legal` sit ABOVE `launch` for the same reason, learned from the live
# run: "BACARDI LIMITED ANNOUNCES NEW EUROPE REGIONAL PRESIDENT" matched `announces (the) new` and
# landed as a product launch. A personnel announcement is not a product event, and a feed that files
# it as one is worse than a feed that files it as `other`.
_EVENT_TYPES = [
    # The gap window is 60, not 40: "NAMES K.C. KAVANAGH CHIEF GLOBAL COMMUNICATIONS OFFICER" puts 42
    # characters of name and title between the verb and the role, so a 40-char window filed a
    # personnel announcement as `other`.
    ("leadership", r"\bnames?\b.{0,60}?\b(?:officer|president|ceo|cfo|coo|chairman|director|head of)\b"
                   r"|\bannounces?\b.{0,40}?\b(?:president|ceo|cfo|coo|chief|chairman)\b"
                   r"|\bappoint(?:s|ed|ment)\b|\bsuccession\b|\bsteps? down\b|\bretires?\b"),
    ("corporate_legal", r"\btrademark\b|\blawsuit\b|\bcourt\b|\btestifies\b|\blitigation\b"
                        r"|\bpetition\b|\bfreedom of information\b|\bfiles?\b.{0,30}\brequest\b"),
    ("limited_edition", r"\blimited[- ]edition|\bspecial edition|\bone[- ]off release|\bcollector'?s edition"),
    ("launch", r"\blaunch(?:es|ed|ing)?\b|\bintroduc(?:es|ing|ed)\b|\bunveil(?:s|ed|ing)?\b"
               r"|\bdebut(?:s|ed|ing)?\b|\bannounces? (?:the )?(?:new|arrival)|\bpresents\b|\breleases?\b"),
    ("availability", r"\bnow available\b|\bavailable (?:in|at|from|nationwide)|\brolls? out\b"
                     r"|\bexpand(?:s|ing|ed)? (?:to|into)\b|\barrives? in\b"),
    ("partnership", r"\bpartner(?:s|ship|ing)?\b|\bcollaborat(?:es|ion|ing)\b|\bteams? up\b"
                    r"|\bjoins? forces\b|\bx\b(?= [A-Z])"),
    ("award", r"\baward(?:s|ed)?\b|\bwins?\b|\bwinner\b|\bhonou?r(?:s|ed)\b|\btop honou?rs\b"
              r"|\bgold medal\b|\bnamed (?:best|the)\b"),
    ("sustainability", r"\bsustainab|\brecycl|\bcarbon\b|\bplastic\b|\bno-?straw\b|\bgood spirited\b"),
    # `cocktail_program` outranks `sponsorship`: "USBG LEGACY COCKTAIL COMPETITION, SPONSORED BY
    # BACARDÍ" is a cocktail programme whose funding mechanism happens to be a sponsorship, and the
    # programme is the part a bev-alc event feed is for. A pure "OFFICIAL SPIRIT OF <event>" headline
    # has no cocktail keyword and still lands as sponsorship.
    ("cocktail_program", r"\bcocktail (?:competition|coverage|program)|\blegacy cocktail\b|\brecipe\b"),
    ("sponsorship", r"\bsponsor(?:s|ed|ship)\b|\bofficial (?:partner|spirit|drink)\b|\bpresenting partner\b"),
]

# Market gazetteer — deliberately conservative. A headline saying "TORONTO" is evidence of a market;
# a headline saying "MARTINI" is not evidence of Italy. Word-boundary matched, longest first.
_MARKETS = [
    "United States", "United Kingdom", "Puerto Rico", "South Africa", "New Zealand", "Hong Kong",
    "Australia", "Argentina", "Singapore", "Germany", "Scotland", "Ireland", "Jamaica", "Mexico",
    "Canada", "France", "Brazil", "Poland", "Spain", "Italy", "India", "China", "Japan", "Cuba",
    "UK", "USA", "US",
    "New York", "Los Angeles", "San Francisco", "Las Vegas", "Miami", "Chicago", "Toronto",
    "London", "Paris", "Berlin", "Madrid", "Milan", "Sydney", "Dubai", "Amsterdam", "Edinburgh",
]
_MARKET_RE = [(m, re.compile(r"\b%s\b" % re.escape(m), re.I)) for m in _MARKETS]

# Price: the literal currency amount as written. Extraction is DETERMINISTIC (the string is verbatim
# in the source); whether that price refers to THIS product is not, which is why `price` is only
# emitted when it co-occurs with a launch/availability event and the raw match is kept in the title.
_PRICE_RE = re.compile(r"(?P<cur>[$£€]|USD|GBP|EUR)\s?(?P<amt>\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.I)
_CURRENCY = {"$": "USD", "£": "GBP", "€": "EUR", "USD": "USD", "GBP": "GBP", "EUR": "EUR"}

_YEAR_FOLDER = re.compile(r"^(19|20)\d{2}$")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── the chokepoint ────────────────────────────────────────────────────────────────────────────────
def asset_bytes(rec, asset, timeout=60, max_bytes=25_000_000):
    """THE ONLY place an asset's bytes may be fetched. Gated on `fetch_asset`, so a source whose terms
    prohibit reuse (or say nothing) raises RightsViolation here rather than quietly downloading.

    Deliberately not called anywhere in the P1 path: with the first vendor's terms classified
    `prohibited`, this function exists to be refused, and the test suite asserts exactly that."""
    url = asset.get("asset_url") or asset.get("url")
    rights.require(rec, "fetch_asset", url)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": rights.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes)


# ── the SECOND chokepoint: reading a text document for its facts ──────────────────────────────────
# Deliberately a separate function from `asset_bytes`, not a flag on it. Same reason the actions are
# separate in rights.py: one of these may run under a record that forbids the other, so they must not
# share a code path that a future edit could accidentally merge.
MAX_DOC_BYTES = 8_000_000
# Body prose is the copyrightable expression; the facts extracted FROM it are not. Nothing longer
# than this may appear in a landed event field — `land()` refuses the write. (`title` is capped at
# 400 by derive_events, so this floor sits above every legitimate value.)
MAX_LANDED_FIELD_CHARS = 500


def document_text(rec, asset, timeout=60, log=print):
    """Fetch a TEXT document transiently and return its text, for fact extraction only.

    Three refusals, in order, because the type check must happen BEFORE the network call — a gate
    that fetches first and validates after has already done the thing it was meant to prevent:
      1. not a text-bearing document type  → RightsViolation (this is not a second door to images)
      2. `fetch_document_facts` not allowed → RightsViolation (stale record, robots, …)
      3. oversized payload                  → truncated at MAX_DOC_BYTES

    The bytes are never returned, written, or cached. The caller gets text and is expected to reduce
    it to facts; `land()` enforces that the prose itself never reaches the warehouse."""
    ext = (asset.get("extension") or "").lower().lstrip(".")
    url = asset.get("asset_url") or asset.get("url")
    if ext not in rights.DOCUMENT_EXTENSIONS:
        raise rights.RightsViolation(
            "document_text refused %r (.%s): only text documents %s may be read for facts — an "
            "image or video must go through the gated asset path"
            % (url, ext or "?", "/".join(rights.DOCUMENT_EXTENSIONS)))
    rights.require(rec, "fetch_document_facts", url)
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": rights.UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_DOC_BYTES)
    rights._log_emission(rec, "fetch_document_facts", url, True,
                         "text document read for facts (transient, prose not retained)",
                         surface="brand_events")
    text = _text_from(data, ext, log=log)
    del data
    return text


def _text_from(data, ext, log=print):
    """Bytes → text. DOCX is stdlib (a zip of XML, same technique menu_ingest uses for xlsx); PDF
    needs `pypdf`, which is OPTIONAL and probed by capability.py — a missing lib is a loud degrade
    (the run reports it), never a silent 'that press release had no text'."""
    if ext in ("txt", "md"):
        return data.decode("utf-8", "replace")
    if ext in ("docx", "odt"):
        import io
        import zipfile
        member = "word/document.xml" if ext == "docx" else "content.xml"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read(member).decode("utf-8", "replace")
        except Exception as e:
            log("  docx parse failed: %s" % str(e)[:100])
            return ""
        xml = re.sub(r"</(w:p|text:p)>", "\n", xml)
        xml = re.sub(r"<[^>]+>", "", xml)
        import html as _html
        return re.sub(r"[ \t]+", " ", _html.unescape(xml))
    if ext == "pdf":
        try:
            import io
            import pypdf
        except ImportError:
            log("  pypdf missing — PDF press releases contribute no facts (declared cap 'pypdf')")
            return ""
        # pypdf logs a line per malformed xref entry ("Ignoring wrong pointing object …"), and real
        # press-release PDFs are full of them — dozens of lines per file, drowning the run log that
        # a degraded verdict has to be readable in. Recoverable parse noise, not our signal.
        import logging
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        try:
            rdr = pypdf.PdfReader(io.BytesIO(data))
            return "\n".join((p.extract_text() or "") for p in rdr.pages)
        except Exception as e:
            log("  pdf parse failed: %s" % str(e)[:100])
            return ""
    return ""


# ── deterministic fact extraction from a press-release body ───────────────────────────────────────
_MONTHS = ("January February March April May June July August September October November December"
           ).split()
_MON_IDX = {m.lower(): i + 1 for i, m in enumerate(_MONTHS)}
_MON_IDX.update({m[:3].lower(): i + 1 for i, m in enumerate(_MONTHS)})
_MON_RE = "|".join(list(_MONTHS) + [m[:3] for m in _MONTHS])

# A press release opens with a DATELINE: "MIAMI, FL – August 12, 2021" / "LONDON, 12 August 2021".
# That is the publication date stated by the publisher, read verbatim — deterministic, and the one
# place a full-precision date legitimately comes from (a DAM's created_on is the upload stamp).
# Dateline shapes, all measured on real Bacardi releases rather than assumed:
#   MIAMI, FL – August 12, 2021        dash separator, region
#   LONDON, 12 August 2021             COMMA separator (requiring the dash demoted these to a weak
#                                      body-scan match)
#   [LONDON, UK, 9th OCTOBER 2025]     BRACKETED, and an ORDINAL day — the ST-Germain × Glassette
#                                      release, which yielded no date at all until both were handled
_ORD = r"\d{1,2}(?:st|nd|rd|th)?"
_DATELINE = re.compile(
    r"(?:^|\n)\s*[\[(]?\s*(?P<city>[A-Z][A-Za-z.\-' ]{2,28}?)"
    r"(?:\s*,\s*(?P<region>[A-Z][A-Za-z.\- ]{1,20}?))?\s*"
    r"[,–—\-−:]\s*(?P<date>(?:%s)\s+%s,?\s+\d{4}|%s\s+(?:%s),?\s+\d{4})" % (_MON_RE, _ORD, _ORD, _MON_RE),
    re.I)
_ANYDATE = re.compile(r"\b(?:(?P<m1>%s)\s+(?P<d1>%s),?\s+(?P<y1>\d{4})"
                      r"|(?P<d2>%s)\s+(?P<m2>%s),?\s+(?P<y2>\d{4}))\b" % (_MON_RE, _ORD, _ORD, _MON_RE),
                      re.I)
# A price is a PRICE POINT only next to a retail marker. Without one, a currency amount in a press
# release is as likely to be a donation or a revenue figure — the trap that already forced the same
# rule on headlines. The marker may FOLLOW the amount as well as precede it: "…Tablescape Edit,
# curated by Laura Jackson, £150 / Available from: glassette.com" is a price, and a leading-marker-
# only rule read it as no price at all.
_SRP = re.compile(r"(?:SRP|MSRP|RRP|RSP|suggested retail(?: price)?|retail(?:s|ing)? (?:for|at)"
                  r"|priced at|available for)\D{0,24}?([$£€])\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", re.I)
_SRP_TRAILING = re.compile(r"([$£€])\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s*[\s\n]{0,4}"
                           r"(?:available (?:from|at|now|in)|on sale|buy (?:it )?(?:from|at))", re.I)
_ABV = re.compile(r"(\d{1,2}(?:\.\d)?)\s*%\s*(?:ABV|alc(?:ohol)?(?:\.|\s)*(?:by|/)\s*vol)", re.I)


def _iso(y, mon, d):
    try:
        mi = _MON_IDX[mon.lower()]
        y, d = int(y), int(re.sub(r"(?i)(?:st|nd|rd|th)$", "", str(d)))   # "9th" -> 9
        if 1900 <= y <= 2100 and 1 <= d <= 31:
            return "%04d-%02d-%02d" % (y, mi, d)
    except Exception:
        pass
    return None


def extract_facts(text, markets=None):
    """Press-release body → the deterministic facts. PURE. Returns a dict with only what was
    actually found; a field the document doesn't state is simply absent, never inferred.

    Every value here is read VERBATIM out of the document (a dateline date, a marked retail price,
    a stated ABV), which is what makes them DETERMINISTIC. The narrative read — what kind of event
    this is — stays INFERENCE and stays with the headline classifier."""
    out = {}
    if not text or not text.strip():
        return out
    head = text[:1200]                      # the dateline lives at the top, by convention

    m = _DATELINE.search(head)
    if m:
        d = m.group("date")
        a = _ANYDATE.search(d)
        if a:
            iso = (_iso(a.group("y1"), a.group("m1"), a.group("d1")) if a.group("m1")
                   else _iso(a.group("y2"), a.group("m2"), a.group("d2")))
            if iso:
                out["event_date"] = iso
                out["event_date_precision"] = "day"
                out["date_source"] = "dateline"
        city = (m.group("city") or "").strip()
        if city:
            out["dateline_place"] = city
            for name, rx in _MARKET_RE:
                if rx.search(city):
                    out["market"] = name
                    out["market_source"] = "dateline"
                    break
    if "event_date" not in out:
        a = _ANYDATE.search(head)
        if a:
            iso = (_iso(a.group("y1"), a.group("m1"), a.group("d1")) if a.group("m1")
                   else _iso(a.group("y2"), a.group("m2"), a.group("d2")))
            if iso:
                out["event_date"] = iso
                out["event_date_precision"] = "day"
                out["date_source"] = "body-date"

    p, src = _SRP.search(text), "srp-marker"
    if not p:
        p, src = _SRP_TRAILING.search(text), "availability-marker"
    if p:
        try:
            out["price"] = float(p.group(2).replace(",", ""))
            out["currency"] = _CURRENCY.get(p.group(1), p.group(1))
            out["price_source"] = src
        except ValueError:
            pass
    a = _ABV.search(text)
    if a:
        try:
            out["abv"] = float(a.group(1))
        except ValueError:
            pass
    if "market" not in out:
        for name, rx in _MARKET_RE:
            if rx.search(head):
                out["market"] = name
                out["market_source"] = "body"
                break
    return out


# ── the LLM narrative pass (design §3: "deterministic … + LLM for the narrative") ─────────────────
# OFF by default (DAM_LLM=1 to enable) and structurally incapable of overwriting a deterministic
# value: it is only ever consulted for fields the deterministic pass left EMPTY, and everything it
# returns is written as INFERENCE. Same posture as menu_ingest.parse_smart — the cheap exact path
# runs first and the model only sees what it couldn't answer, so a clean document costs nothing.
_LLM_TOOL = {
    "name": "record_event_facts",
    "description": "Record ONLY facts stated explicitly in the press release. Omit anything not stated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "event_date": {"type": "string", "description": "ISO YYYY-MM-DD, only if the release states it"},
            "market": {"type": "string", "description": "country or city the release says this is for"},
            "price": {"type": "number", "description": "retail price, only if stated as a retail price"},
            "currency": {"type": "string", "enum": ["USD", "GBP", "EUR"]},
            "abv": {"type": "number"},
            "product_name": {"type": "string", "description": "the specific product being announced"},
        },
        "additionalProperties": False,
    },
}


def _llm_available():
    if os.environ.get("DAM_LLM", "0") != "1" or not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def llm_facts(text, missing, log=print):
    """Ask Claude only for `missing` fields, from the document text. Returns {} on any failure —
    an unavailable model degrades the enrichment, never the run."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-5", max_tokens=512,
            tools=[_LLM_TOOL], tool_choice={"type": "tool", "name": "record_event_facts"},
            messages=[{"role": "user", "content":
                       "Press release below. Report ONLY these fields, and ONLY if the text states "
                       "them explicitly: %s. Do not guess or infer from context.\n\n%s"
                       % (", ".join(sorted(missing)), text[:12000])}])
        for block in msg.content:
            if getattr(block, "type", "") == "tool_use":
                return {k: v for k, v in (block.input or {}).items() if k in missing}
    except Exception as e:
        log("  llm pass failed: %s" % str(e)[:110])
    return {}


def enrich_events_from_documents(rec, events, assets, limit=None, use_llm=None, log=print):
    """Upgrade events with the facts stated in their own press-release documents.

    Coverage is REPORTED, never silently truncated ([[no-silent-caps-in-full-pulls]]): the return
    value carries matched / covered / remaining, and `limit` (DAM_DOC_LIMIT) is an explicit operator
    bound whose leftovers are named in the log, not a cap baked into a 'full' run."""
    use_llm = _llm_available() if use_llm is None else use_llm
    by_id = {a.get("asset_id"): a for a in assets}
    todo = []
    for e in events:
        ids = [i for i in (e.get("source_asset_ids") or "").split(",") if i]
        docs = [by_id.get(int(i)) for i in ids if i.isdigit()]
        docs = [d for d in docs
                if d and (d.get("extension") or "").lower() in rights.DOCUMENT_EXTENSIONS]
        if docs:
            todo.append((e, docs[0]))

    matched, remaining = len(todo), 0
    if limit and len(todo) > limit:
        remaining = len(todo) - limit
        todo = todo[:limit]

    covered, upgraded, failures, unparsed = 0, 0, 0, 0
    for e, doc in todo:
        try:
            text = document_text(rec, doc, log=log)
        except rights.RightsViolation:
            raise
        except Exception as ex:
            failures += 1
            log("  doc %s: fetch failed: %s" % (doc.get("asset_id"), str(ex)[:90]))
            continue
        covered += 1
        # A document we FETCHED but could not parse is not a document that said nothing. Counted
        # separately, because folding the two together is how "we read all 86" comes to mean "we read
        # 84 and shrugged at 2".
        if not (text or "").strip():
            unparsed += 1
        facts = extract_facts(text)
        # The LLM sees ONLY genuine gaps: a field neither stated in the DOCUMENT nor already carried
        # DETERMINISTICALLY on the EVENT. Both halves matter — checking the document alone let the
        # model overwrite a folder-derived (deterministic) date with a guess, because the document
        # happened not to repeat it. It can add a fact, never replace one.
        missing = {f for f in ("event_date", "market", "price", "abv")
                   if not facts.get(f) and e.get(f) in (None, "")}
        if missing and use_llm:
            for k, v in (llm_facts(text, missing, log=log) or {}).items():
                if v not in (None, "") and k in missing and not facts.get(k):
                    facts[k] = v
                    facts.setdefault("_llm", set()).add(k)
                    if k == "event_date":
                        facts["event_date_precision"] = "day"
        del text
        if not facts:
            continue
        prov = json.loads(e["field_provenance"])
        changed = False
        llm_fields = facts.get("_llm") or set()
        if facts.get("event_date"):
            e["event_date"] = facts["event_date"]
            e["event_date_precision"] = facts.get("event_date_precision") or "day"
            prov["event_date"] = INFERENCE if "event_date" in llm_fields else DETERMINISTIC
            prov["event_date_source"] = "llm" if "event_date" in llm_fields else facts.get("date_source")
            changed = True
        if facts.get("market"):
            e["market"] = facts["market"]
            # A DATELINE place is stated by the publisher — deterministic. A place name spotted
            # elsewhere in the body is still just a keyword hit, so it stays INFERENCE.
            prov["market"] = (DETERMINISTIC if (facts.get("market_source") == "dateline"
                                                and "market" not in llm_fields) else INFERENCE)
            changed = True
        if facts.get("price") is not None:
            e["price"] = facts["price"]
            e["currency"] = facts.get("currency")
            prov["price"] = INFERENCE if "price" in llm_fields else DETERMINISTIC
            changed = True
        if facts.get("abv") is not None:
            e["abv"] = facts["abv"]
            prov["abv"] = INFERENCE if "abv" in llm_fields else DETERMINISTIC
            changed = True
        if changed:
            e["field_provenance"] = json.dumps(prov)
            upgraded += 1
        time.sleep(0.4)                     # someone's server; pace it

    cov = {"llm_pass": bool(use_llm), "documents_matched": matched, "documents_read": covered,
           "documents_remaining": remaining, "documents_unparsed": unparsed,
           "events_upgraded": upgraded, "read_failures": failures}
    log("  documents: read %d/%d, upgraded %d event(s)%s%s"
        % (covered, matched, upgraded,
           ", %d unparsable" % unparsed if unparsed else "",
           ", %d NOT read (DAM_DOC_LIMIT)" % remaining if remaining else ""))
    return cov


def derive_cv_reference(rec, asset, data=None, log=print):
    """Perceptual hash + embedding for the CV reference gallery, gated on `derive_hash` /
    `derive_embedding`. Returns (phash, embedding_ref, withheld_reason).

    A derivative work of a copyrighted image is still governed by the licence on that image, so this
    is gated at the SAME level as retention — not treated as "just a number we computed". When the
    gate refuses, the reason is returned (and lands on the asset row) instead of being swallowed."""
    ok, why = rights.may(rec, "derive_hash")
    if not ok:
        return None, None, why
    if data is None:
        data = asset_bytes(rec, asset)
    phash = hashlib.sha256(data).hexdigest()[:32]     # placeholder digest; true pHash lands with P3
    emb_ref = None
    ok_emb, why_emb = rights.may(rec, "derive_embedding")
    if ok_emb:
        emb_ref = "pending-p3"                        # the CV gallery build is P3, not P1
    else:
        log("embedding withheld: %s" % why_emb)
    return phash, emb_ref, None


# ── asset rows ────────────────────────────────────────────────────────────────────────────────────
def asset_row(rec, vendor, drive, folder_path, f, pulled_at):
    """Normalize one platform file record → a `dam_assets` pointer row, with the rights columns
    resolved from the record. `retention` is the honest statement of what we kept: `pointer_only`
    whenever the gate refuses the bytes."""
    perms = rec.get("permissions") or {}
    may_retain, why = rights.may(rec, "retain_asset")
    return {
        "source_id": rec.get("source_id"), "vendor": vendor,
        "drive_id": drive.get("id"), "drive_name": drive.get("name"),
        "folder_id": f.get("parent_folder_id"), "folder_path": folder_path,
        "asset_id": f.get("id"), "asset_token": f.get("file_token"),
        "name": f.get("name"), "title": f.get("title"),
        "description": (f.get("description") or "").strip(),
        "asset_type": f.get("type"), "extension": (f.get("extension") or "").lower(),
        "mime_type": f.get("mime_type"), "size_bytes": f.get("size"),
        "asset_url": f.get("url"), "thumb_url": f.get("thumb_url"),
        "download_url": f.get("download_url"),
        "created_on": f.get("created_on"), "updated_on": f.get("updated_on"),
        "rights_ref": rights.rights_ref(rec),
        "image_use": perms.get("image_use"), "image_scope": perms.get("scope"),
        "retention": "full" if may_retain else "pointer_only",
        "phash": None, "embedding_ref": None,
        "withheld_reason": None if may_retain else why,
        "pulled_at": pulled_at,
    }


# ── brand events ──────────────────────────────────────────────────────────────────────────────────
_UUID_PREFIX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-", re.I)
_EXT = re.compile(r"\.(pdf|docx?|jpe?g|png|mp4|mov|zip|tif+|svg|webp)\s*$", re.I)
_VARIANT = re.compile(r"[\s,]*\(\s*\d+\s*\)\s*$")


def display_title(t):
    """The human-readable event label: the filename with the upload artefacts taken off, but case,
    punctuation and words intact. `_norm_title` destroys those to build a comparison key — showing
    that key to a person would be a different bug from the one it fixes, and leaving DNA's raw
    `<uuid>-PRESS RELEASE - … .docx` on the event is a third. The untouched filename is still on the
    asset row, which is where provenance belongs."""
    t = _UUID_PREFIX.sub("", (t or "").strip())
    for _ in range(3):
        prev = t
        t = _VARIANT.sub("", _EXT.sub("", t)).strip(" -–—_,")
        if t == prev:
            break
    return t or (_UUID_PREFIX.sub("", (t or "").strip()))


def _norm_title(t):
    """Collapse a headline to a comparison key, so the assets of ONE story become ONE event.

    Three normalizations, each earned from the live drive rather than guessed:
      • a leading upload UUID — DNA prefixes re-uploaded files with a fresh uuid, so five copies of
        one press kit differ ONLY by that prefix and fragmented into five events;
      • repeated/trailing extensions — the drive really does contain "… Hero Image.jpg.jpg";
      • a trailing "(n)" variant counter — "Fleuriste St-Germain Aug 12 (7)" is the seventh frame of
        one shoot, not the seventh launch.
    Applied in a loop because they stack (a UUID-prefixed ".jpg.jpg" needs two extension passes)."""
    t = _UUID_PREFIX.sub("", (t or "").strip())
    for _ in range(3):
        prev = t
        t = _VARIANT.sub("", _EXT.sub("", t)).strip()
        if t == prev:
            break
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


# Brand aliases that are ALSO ordinary words. A headline about a martini (the drink), a patron (the
# customer) or Bombay (the city) is not evidence of the brand — but the string match is identical, so
# the match cannot be labelled DETERMINISTIC. These are matched and kept, and their provenance is
# downgraded to INFERENCE, which is the honest description of what that match actually is.
AMBIGUOUS_ALIASES = {"martini", "patron", "bombay", "banks", "legacy"}


def _mnorm(s):
    """One normalization for BOTH sides of a brand match. Applying it to the text but not the alias
    was a live defect: "DEWAR'S®" normalized to "dewar s" while the alias stayed "dewar's", so every
    Dewar's headline fell through to whatever brand appeared earlier in the line."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", s or "", flags=re.U).lower()).strip()


def match_brand(title, brands):
    """Match a headline against the vendor's declared brand roster: an exact, word-boundary match on
    a literal the vendor publishes, with the matched literal recorded — not a fuzzy guess. Longest
    alias first so "JOHN DEWAR & SONS" beats a bare vendor name earlier in the headline.

    Returns (brand, alias, ambiguous). `ambiguous` is True when the alias that matched is also a
    common word, and it flows into `field_provenance` as INFERENCE rather than DETERMINISTIC."""
    t = " %s " % _mnorm(title)
    cands = []
    for brand, aliases in brands.items():
        for a in aliases:
            na = _mnorm(a)
            if na:
                cands.append((len(na), brand, a, na))
    for _ln, brand, alias, na in sorted(cands, reverse=True):
        if re.search(r"(?<!\w)%s(?!\w)" % re.escape(na), t):
            return brand, alias, na in AMBIGUOUS_ALIASES
    return None, None, False


def classify_event(title):
    for etype, pat in _EVENT_TYPES:
        if re.search(pat, title or "", re.I):
            return etype
    return "other"


def find_market(title):
    for name, rx in _MARKET_RE:
        if rx.search(title or ""):
            return name
    return None


def find_price(title):
    m = _PRICE_RE.search(title or "")
    if not m:
        return None, None
    cur = _CURRENCY.get(m.group("cur").upper(), m.group("cur"))
    try:
        return float(m.group("amt").replace(",", "")), cur
    except ValueError:
        return None, None


def derive_events(rec, assets, brands, folder_years=None, log=print):
    """Derive `brand_events` rows from asset titles + folder metadata.

    Grain: one row per (brand, story, event year) — assets of the same story collapse into one event
    carrying `asset_count` and the contributing `source_asset_ids`.

    Provenance, per field:
      brand / hoodie_brand_id  DETERMINISTIC — exact match on a vendor-published brand literal,
                               downgraded to INFERENCE when the alias that matched is also a common
                               word (see AMBIGUOUS_ALIASES): "MARTINI" in a headline may be the brand
                               or the drink, and a string match cannot tell you which
      event_date               DETERMINISTIC — the folder's own year label, when the source uses one
      title / source_*         DETERMINISTIC — verbatim from the platform record
      price / currency         DETERMINISTIC — the currency literal as written in the headline
      event_type / market      INFERENCE     — a keyword classifier's read of free text
    """
    folder_years = folder_years or {}
    by_key = {}
    for a in assets:
        title = (a.get("title") or a.get("name") or "").strip()
        if not title:
            continue
        brand, alias, ambiguous = match_brand(title, brands)
        if not brand:
            continue
        year = folder_years.get(a.get("folder_id"))
        key = (brand, _norm_title(title), year)
        slot = by_key.setdefault(key, {"brand": brand, "alias": alias, "ambiguous": ambiguous,
                                       "title": title, "year": year, "ids": [], "urls": []})
        slot["ids"].append(a.get("asset_id"))
        if a.get("asset_url") and not slot["urls"]:
            slot["urls"].append(a["asset_url"])

    rows, fetched = [], _now()
    for (brand, nkey, year), s in by_key.items():
        etype = classify_event(s["title"])
        market = find_market(s["title"])
        price, currency = find_price(s["title"])
        # Price is only meaningful on a story about buying the thing; a "$1M donation" headline is
        # not a price point, so it is dropped rather than landed as one.
        if price is not None and etype not in ("launch", "availability", "limited_edition"):
            price, currency = None, None
        eid = "E-" + hashlib.sha1(
            ("%s|%s|%s|%s" % (rec.get("source_id"), brand, nkey, year)).encode("utf-8")).hexdigest()[:16]
        brand_prov = INFERENCE if s.get("ambiguous") else DETERMINISTIC
        prov = {"brand": brand_prov, "hoodie_brand_id": brand_prov, "brand_alias": s.get("alias"),
                "title": DETERMINISTIC,
                "source_asset_ids": DETERMINISTIC, "source_url": DETERMINISTIC,
                "event_date": DETERMINISTIC if year else None,
                "event_date_precision": DETERMINISTIC,
                "event_type": INFERENCE, "market": INFERENCE if market else None,
                "price": DETERMINISTIC if price is not None else None,
                "sku_id": None}
        rows.append({
            "event_id": eid,
            # hoodie_brand_id is the vendor-scoped brand slug until DAM brands are resolved against
            # dim_brand (P2). Naming it now and resolving later beats inventing a fake canon id.
            "hoodie_brand_id": "%s:%s" % (rec.get("source_id"), slug(brand)),
            # Canon columns start UNRESOLVED and are stamped by dam_canon.apply_to_events. Carrying
            # them from the start means an unresolved event is visibly unresolved, rather than a row
            # that silently lacks the column its resolved neighbours have.
            "brand_key": None, "canon_brand": None, "brand_resolution": "unresolved",
            "brand": brand, "sku_id": None, "abv": None,
            "event_type": etype,
            "event_date": ("%s-01-01" % year) if year else None,
            "event_date_precision": "year" if year else "unknown",
            "market": market, "price": price, "currency": currency,
            "title": display_title(s["title"])[:400], "asset_count": len(s["ids"]),
            "source": rec.get("source_id"), "source_id": rec.get("source_id"),
            "source_asset_ids": ",".join(str(i) for i in sorted(s["ids"])[:50]),
            "source_url": (s["urls"] or [None])[0],
            "rights_ref": rights.rights_ref(rec),
            "field_provenance": json.dumps({k: v for k, v in prov.items() if v}),
            "fetched_at": fetched,
        })
    return rows


def year_map(folders):
    """folder_id → 4-digit year, for sources that file assets under year folders. Only an exact
    4-digit folder name counts — that is what makes the derived date deterministic rather than a
    guess pulled out of a folder called 'Summer campaign'."""
    return {f["id"]: f["name"] for f in folders if _YEAR_FOLDER.match((f.get("name") or "").strip())}


def folder_paths(folders, root_id):
    """folder_id → 'Home/Images/2018' — the human-readable location, computed from parent links."""
    by_id = {f["id"]: f for f in folders}
    out = {}
    for fid in by_id:
        parts, cur, guard = [], fid, 0
        while cur in by_id and guard < 32:
            parts.append(by_id[cur].get("name") or str(cur))
            if cur == root_id:
                break
            cur = by_id[cur].get("parent_folder_id")
            guard += 1
        out[fid] = "/".join(reversed(parts))
    return out


# ── landing ───────────────────────────────────────────────────────────────────────────────────────
def assert_no_prose(events):
    """The other half of the fetch-for-facts bargain: FACTS may land, the PROSE they came from may
    not. `document_text` hands a whole press release to the extractor, so the failure mode to design
    against is a body paragraph quietly ending up in a column — at which point we would be storing
    the copyrightable expression while calling it a fact. Enforced at the landing boundary, on the
    data, rather than trusted to every future extractor."""
    for e in events:
        for k, v in e.items():
            if isinstance(v, str) and len(v) > MAX_LANDED_FIELD_CHARS:
                raise rights.RightsViolation(
                    "event %s field %r carries %d chars — over the %d-char prose ceiling. Facts may "
                    "land; the document body may not." % (e.get("event_id"), k, len(v),
                                                          MAX_LANDED_FIELD_CHARS))
    return True


def land(rec, assets, events, log=print):
    """Land both outputs + the rights ledger. Persistent catalogs, so `write_accumulate`
    ([[scraper-write-accumulate]]) — a re-pull of one drive must grow the book, never clobber it."""
    assert_no_prose(events)
    n_a = n_e = 0
    try:
        import warehouse
        if assets:
            warehouse.write_accumulate(ASSETS_TABLE, assets,
                                       key=lambda r: "%s|%s" % (r["source_id"], r["asset_id"]),
                                       fields=ASSET_FIELDS, coverage=False)
            n_a = len(assets)
            log("landed %s: %d asset pointers" % (ASSETS_TABLE, n_a))
        if events:
            warehouse.write_accumulate(EVENTS_TABLE, events, key="event_id",
                                       fields=EVENT_FIELDS, coverage=False)
            n_e = len(events)
            log("landed %s: %d events" % (EVENTS_TABLE, n_e))
    except Exception as e:
        log("dam land skipped: %s" % str(e)[:140])
    rights.land_record(rec, log=log)
    return n_a, n_e


def run_record(source_id, connid, started, total, new, status, warnings, extracts):
    return {"id": "R-DAM" + hashlib.sha1(("%s%s" % (source_id, total)).encode()).hexdigest()[:3].upper(),
            "connId": connid, "startedAt": started, "finishedAt": int(time.time() * 1000),
            "durationMs": 0, "status": status, "trigger": "manual", "total": total,
            "degraded": status != "success", "warnings": warnings, "healed": [],
            "extracts": extracts}


def snapshot_diff(keys, path):
    """new / dropped since the last run, from a JSON snapshot (same shape as the other connectors)."""
    cur = {str(k): 1 for k in keys}
    prev = {}
    if os.path.exists(path):
        try:
            prev = json.load(open(path)).get("cells", {})
        except Exception:
            prev = {}
    new = sum(1 for k in cur if k not in prev)
    dropped = sum(1 for k in prev if k not in cur)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump({"__ts__": int(time.time() * 1000), "cells": cur}, open(path, "w"))
    except Exception:
        pass
    return new, dropped
