#!/usr/bin/env python3
"""rights.py — THE contract layer for harvested third-party content. ToS is not a README; it is the
gate, and it is enforced in code.

WHY THIS EXISTS
  Every other scraper in this engine harvests FACTS: prices, counts, addresses, UPCs. Facts are
  uncopyrightable, so "can we keep this?" never came up. DAM harvesting (supplier/brand media
  centres) is the first capability where the answer is sometimes NO — the assets are studio imagery
  and video owned by the vendor, and what we may do with them is set by that vendor's terms, not by
  what the HTTP server will hand us. A 200 OK is not a licence.

  So the permission is DATA, and the enforcement is a chokepoint. Every source carries a
  `rights_record`: a verbatim ToS snapshot + its sha256 + capture date, the robots status of the
  paths we actually touch, and a PARSED permission classification. Nothing in the DAM pipeline may
  fetch, retain, hash, embed, or serve an asset without going through `require()` / `emit()` against
  that record, and every emission (allowed OR denied) is logged against it.

THE TWO STANDING RULES (from the capability design, encoded below as `may()`)
  1. FACTS ALWAYS FLOW. Launch dates, markets, price points, product names — uncopyrightable, so
     `catalog_metadata` is ungated. A pointer (URL, filename, size, timestamps) is metadata about an
     asset, not the asset.
  2. IMAGES FLOW ONLY WITHIN GRANTED SCOPE, AND SILENCE IS NOT PERMISSION. `image_use="silent"`
     (terms say nothing about reuse) is a HOLD, identical in effect to `prohibited`. This is the
     asymmetry that matters: the failure mode of guessing "probably fine" is a licensing claim, and
     the failure mode of holding is a gap in a gallery.

WHY `needs_counsel` IS SET ON GRANTS, NOT ON HOLDS
  Counsel protects the AFFIRMATIVE act. A record that says "hold" and is enforced as a hold cannot
  hurt anyone, so it does not need a lawyer to bless it. A record that says "we may redistribute
  this commercially" is a decision to act on someone else's property, so it is flagged for review
  before it can widen scope — as is any classification the parser could not make decisively.

STALENESS IS A HOLD, NOT A WARNING
  `refresh()` re-fetches the terms and compares the sha256. If they moved, the record is `stale` and
  the gate DENIES every image action until a human re-reviews it. A permission parsed from terms that
  no longer exist is worse than no permission at all, because it reads as diligence.

  Corollary, learned on the first vendor: some ToS pages render client-side, so a stdlib fetch gets a
  heading and nothing else. `refresh()` will not treat a 94-character `<main>` as "the terms changed"
  OR as "the terms are fine" — it reports `unverifiable` and holds, because a fetch that cannot see
  the document proves nothing either way. The verbatim snapshot in the record is the reviewed
  artifact; the refresh is a change DETECTOR, not the source of truth.

API
    rec = rights.load("dam-bacardi")            # the committed, human-reviewed record
    rights.require(rec, "fetch_asset", url)     # raises RightsViolation unless in scope
    ok, why = rights.may(rec, "derive_embedding")
    rights.emit(rec, "serve_internal", asset_id, surface="cv-gallery")   # logs + enforces
    rights.classify(tos_text)                   # the deterministic ToS parser (pure, testable)

Records live in `unifyd/rights_records/<source_id>.json` — committed, diff-reviewable, one per
source. stdlib only.
"""
import hashlib
import json
import os
import re
import time
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse

import table_spec          # stdlib-only; the schema declaration for the tables this module lands

_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDS_DIR = os.path.join(_DIR, "rights_records")
_EMIT_LOG = os.path.join(_DIR, "agent_state", "rights", "emissions.jsonl")
EMISSIONS_TABLE = "dam_emissions"
RIGHTS_TABLE = "dam_rights"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# A ToS body shorter than this is not a document we can classify — it is a shell page whose text is
# injected client-side (measured: Bacardi's terms render 94 chars of <main> to a stdlib fetch and
# 23,833 in a browser). Below the floor we report `unverifiable`, never "unchanged".
MIN_TOS_CHARS = 800


# ── the schema counsel signs off (P6) ─────────────────────────────────────────────────────────────
# The permission MODEL — the vocabulary, the ladder, and the decision rules — is the thing a buyer's
# counsel accepts or rejects, and it is versioned separately from any individual record. A record
# carries the version it was authored under, so a later change to the model is visible as a version
# skew on every record rather than a silent reinterpretation of records already written.
#
# `SCHEMA_SIGNOFF` is deliberately unset in code. Nobody can mark the schema reviewed by editing a
# constant: sign-off is recorded per record (`schema_signoff`), by whoever actually reviewed it, and
# `queue()` reports every record still waiting. `docs/rights-counsel-review.md` is the packet a
# lawyer reads to make that decision.
SCHEMA_VERSION = "1.0"
SCHEMA_SIGNOFF = None          # set ONLY by a recorded human review, never in code


class RightsViolation(Exception):
    """Raised when the pipeline attempts something the source's rights record does not permit.
    Never catch this to continue — an out-of-scope emission is the one failure that must stop work."""


# ── the vocabulary ────────────────────────────────────────────────────────────────────────────────
IMAGE_USE = ("permitted", "prohibited", "silent")

# Scope ladder, weakest → strongest. An action requires its scope to be AT MOST the granted scope.
SCOPES = ("none", "internal_only", "editorial_press", "commercial_redistribution")
_SCOPE_RANK = {s: i for i, s in enumerate(SCOPES)}

# Every action the DAM pipeline can take, and the scope it needs. `catalog_metadata` and
# `catalog_pointer` are 0-scope on purpose: they are facts and references, not the work.
ACTIONS = {
    "catalog_metadata":   "none",                       # filename, size, timestamps, folder path
    "catalog_pointer":    "none",                       # the asset URL as a reference (no bytes)
    # READ A TEXT DOCUMENT TO EXTRACT FACTS FROM IT — ungated, and narrow on purpose.
    #
    # Facts are uncopyrightable, and a press release exists to be read: the launch date, the market
    # and the price point are exactly what the supplier is publishing. Without this the fact feed can
    # only ever see filenames, which is why the ST-Germain × Glassette launch landed with a brand and
    # no date while its own press release sat unread in the same folder.
    #
    # What keeps it from being a hole in `fetch_asset`, all enforced in `dam.document_text()`:
    #   • TEXT-BEARING DOCUMENT TYPES ONLY (pdf/doc/docx/txt/rtf/odt). An image or a video is refused
    #     here, so this is not a second door to the pixels `fetch_asset` guards.
    #   • THE BYTES ARE TRANSIENT. Nothing is written to disk or to the warehouse; the function
    #     returns text and drops the payload.
    #   • THE PROSE NEVER LANDS. Only the extracted FACTS are persisted — `dam.land()` refuses any
    #     event field long enough to be body text, so the copyrightable expression cannot ride along
    #     inside a "fact" column.
    #   • IT IS LOGGED SEPARATELY, so "we only read documents, and only these" is auditable rather
    #     than asserted.
    # It remains staleness-sensitive (see `_STALE_SENSITIVE`): it touches the source's server, and a
    # ToS that moved may have changed the terms of access itself.
    "fetch_document_facts": "none",
    "fetch_asset":        "internal_only",              # pull the bytes at all
    "retain_asset":       "internal_only",              # store the bytes
    "derive_hash":        "internal_only",              # perceptual hash (a derivative work)
    "derive_embedding":   "internal_only",              # CV embedding (a derivative work)
    "serve_internal":     "internal_only",              # show inside Hoodie surfaces
    "serve_editorial":    "editorial_press",            # press/editorial contexts
    "redistribute":       "commercial_redistribution",  # resale / customer-facing redistribution
}

# Actions that reach out to the SOURCE'S SERVER, as opposed to operating on data we already hold.
# A stale record (terms moved since review) denies these even when they need no scope, because the
# terms that changed may be the terms of access. Cataloguing what we already have is unaffected.
_STALE_SENSITIVE = {"fetch_document_facts", "fetch_asset", "retain_asset", "derive_hash",
                    "derive_embedding", "serve_internal", "serve_editorial", "redistribute"}

# Text-bearing document types `fetch_document_facts` may touch. Enforced in dam.document_text().
DOCUMENT_EXTENSIONS = ("pdf", "doc", "docx", "txt", "rtf", "odt", "md")


def _sha(text):
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ── the deterministic ToS parser ──────────────────────────────────────────────────────────────────
# Each rule is (name, regex, weight). DECISIVE rules alone settle the classification; SUPPORTING
# rules add evidence and confidence but never settle it on their own. Every match is quoted verbatim
# into `evidence[]`, so a classification can always be audited back to the sentence that produced it.

_PROHIBIT = [
    ("no-commercial-use",
     r"(?:must|may) not use any part of the (?:materials|content)[^.]{0,120}?for commercial purposes", 2),
    ("no-use-outside-site",
     r"(?:are )?not permitted to use the (?:materials|content) outside of (?:the|our) site", 2),
    ("no-licence-granted",
     r"does not grant you any rights[^.]{0,80}?licen[cs]e", 2),
    ("personal-non-commercial-only",
     r"(?:for your )?(?:lawful,? )?personal[,\s]+non-?commercial use", 2),
    ("no-reproduction",
     r"no part of[^.]{0,80}?may be reproduced", 2),
    ("all-rights-reserved", r"all (?:such )?rights are reserved", 1),
    ("written-permission-required",
     r"(?:prior )?written (?:permission|consent) (?:is |of us )?(?:required|must be obtained)", 1),
]

_GRANT = [
    ("royalty-free", r"royalty[-\s]free", 2),
    ("editorial-grant",
     r"(?:free|may be (?:used|reproduced|downloaded))[^.]{0,80}?for editorial (?:use|purposes)", 2),
    # Tightened after a live false positive: the old pattern spanned 120 characters across sentence
    # boundaries and matched "…your right to use the Sites will immediately cease and Heaven Hill
    # Brands may, without…" as a press grant. A grant to the press must name what may be used.
    ("press-grant",
     r"(?:press|media|journalists?|accredited media)[^.]{0,60}?(?:may|are permitted to|are free to)"
     r"[^.]{0,30}?(?:use|download|reproduce|publish)[^.]{0,40}?"
     r"(?:image|photo|asset|material|content|logo|artwork)", 2),
    ("news-reporting-grant",
     r"may (?:be )?(?:use[d]?|reproduce[d]?|publish(?:ed)?)[^.]{0,80}?for (?:news|press) (?:reporting|use)", 2),
    ("permission-granted", r"(?:we )?(?:hereby )?grant (?:you )?a[^.]{0,60}?licen[cs]e", 1),
    # TRADE grants. A consumer media centre exists to protect brand assets; a TRADE platform exists to
    # distribute them — a sell sheet and a pack shot are published precisely so a distributor or
    # retailer can sell the product with them. That is a different licence in a different vocabulary,
    # and the press/editorial rules above cannot see it: nobody writes "royalty-free for editorial
    # use" on a distributor portal, they write "for use in connection with the sale of our products".
    #
    # Adding the vocabulary does NOT assert we have found such a grant — surveyed live across VIP
    # Brand Builder, SevenFifty/Provi, Salsify Sites, 1WorldSync and Syndigo, none publishes one as
    # web terms. It means the census will RECOGNISE one when it hits it, instead of reading a real
    # trade licence as silence.
    ("trade-partner-grant",
     r"(?:authoriz(?:ed|ised)|approved)\s+(?:retailer|distributor|wholesaler|trade partner|customer)s?"
     r"[^.]{0,80}?(?:may|are permitted to|are authoriz\w+ to)[^.]{0,40}?(?:use|download|display|reproduce)", 2),
    ("sale-purpose-grant",
     r"(?:may (?:be )?use[d]?|for use)[^.]{0,60}?in connection with the (?:sale|marketing|promotion|"
     r"advertis\w+|resale)\s+of[^.]{0,30}?(?:our|the|these)\s+products?", 2),
    ("retail-listing-grant",
     r"(?:may (?:be )?use[d]?|permitted)[^.]{0,70}?(?:e-?commerce|online|retail|product)\s+"
     r"(?:listing|catalog(?:ue)?|website|store)s?", 2),
]

# ── DIRECTIONALITY: whose licence is it? ──────────────────────────────────────────────────────────
# The single most dangerous misread in this whole capability, found live on THREE suppliers at once.
# Every corporate ToS contains a lavish, perpetual, ROYALTY-FREE licence — and it runs the wrong way:
# it is the licence YOU grant THEM over anything you upload.
#
#   AB InBev      "(b) grant ABI an unlimited, perpetual, royalty-free … license to use, modify…"
#   William Grant "…you grant the Company a perpetual, worldwide, royalty-free … right and license…"
#   Heaven Hill   "…you irrevocably grant Heaven Hill Brands … a … royalty-free license to: (i) display…"
#
# All three classified `permitted / editorial_press` at HIGH confidence, which would have authorised
# a CV gallery on assets nobody licensed to us. The negation guard could not catch it: there is no
# negation, the direction is simply inbound. So grant matches are now checked for an INBOUND context
# and, when found, recorded as evidence with ZERO weight — visible in the audit trail (a reviewer
# should see that the clause exists and why it was rejected), counted for nothing.
_INBOUND = re.compile(
    r"\byou\b[^.]{0,60}\b(?:grant|give|assign|licen[cs]e)\b"
    r"|\bgrant(?:s|ing)?\b[^.]{0,40}\b(?:us|we|our|the company|its affiliates)\b"
    r"|\bgrant\b\s+[A-Z][A-Za-z&.\- ]{2,40}\s+(?:an?|its)\b"
    r"|\b(?:your|user|consumer)\s+(?:content|submission|feedback|contribution|material)s?\b"
    r"|\bsubmissions?\b|\byou\s+(?:submit|upload|post|contribute|provide)\b"
    r"|\buser[-\s]generated\b", re.I)
_INBOUND_WINDOW = 420

# Scope hints — read only when a grant was found. Strongest hint wins.
_SCOPE_HINTS = [
    ("commercial_redistribution",
     r"(?:authoriz\w+ (?:retailer|distributor|trade partner)|in connection with the (?:sale|resale)"
     r"|e-?commerce listing|retail listing)"),
    ("commercial_redistribution",
     r"(?:commercial (?:use|purposes) (?:is|are) permitted|may be redistribut|for (?:any|all) purposes"
     r"|advertising and promotional use)"),
    ("editorial_press", r"editorial|press|news reporting|journalis"),
    ("internal_only", r"internal (?:use|purposes) only|for your own internal"),
]

_ATTRIBUTION = (r"(?:must|should) (?:credit|attribute|acknowledge)|attribution (?:is )?required"
                r"|credit (?:must|should) be given|photo credit|courtesy of")
_NO_ALTER = (r"(?:may|must) not (?:be )?(?:alter|modif|crop|edit|distort|manipulat)"
             r"|no (?:alteration|modification)s? (?:are |is )?(?:permitted|allowed)"
             r"|(?:images|materials) must not be (?:changed|altered)")
_MAY_ALTER = r"may (?:be )?(?:crop|resiz|scal)(?:ped|ed)?[^.]{0,40}?(?:permitted|allowed|as (?:needed|required))"
_TRADE_ONLY = (r"trade (?:use )?only|for (?:use by )?(?:licensed )?(?:trade|on-?trade|off-?trade)"
               r"|(?:licensed )?(?:retailers?|distributors?|wholesalers?) only")
_EXPIRY = (r"(?:expires?|valid (?:until|through)|embargo(?:ed)? until|licen[cs]e ends?)\s*:?\s*"
           r"(\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{1,2},? \d{4})")


# A GRANT phrase inside a negation is a PROHIBITION wearing the grant's words. Measured on the first
# vendor: "the use of this Site does not grant you any rights, title, interest or license to any
# Materials" matched the `permission-granted` rule and pulled a decisive `prohibited` down to
# `medium` confidence — i.e. the clause that most clearly forbids reuse was scored as evidence FOR
# it. Grant rules are therefore negation-guarded; prohibition rules are not (a double negative in the
# forbidding direction is rare, and guarding it would drop real prohibitions, which fails unsafe).
_NEG = re.compile(r"\b(?:not|never|no|nothing|except|without|neither|nor)\b", re.I)
_NEG_WINDOW = 70


def _inbound(text, start):
    """True when the licence around `start` runs FROM the reader TO the publisher (a UGC clause).
    Looks BACKWARD only: "you grant …" always precedes the "royalty-free" it qualifies."""
    return bool(_INBOUND.search(text[max(0, start - _INBOUND_WINDOW):start + 40]))


def _hits(text, rules, guard_negation=False, guard_direction=False):
    """Run a rule table over the text → [(name, weight, verbatim quote, side)]. Quotes are trimmed to
    a readable sentence-ish window so `evidence[]` is reviewable without re-reading the whole ToS.

    `guard_negation` skips a match sitting inside a negation and keeps scanning, so one negated
    occurrence neither counts as a grant nor suppresses a real one elsewhere.

    `guard_direction` skips a match whose licence runs INBOUND (the reader granting the publisher).
    The skipped match is still returned, with weight 0 and side "grant-inbound", so the audit trail
    shows the clause was seen and rejected — silently dropping it would leave a reviewer wondering
    whether we noticed the enormous royalty-free licence in the document."""
    out = []
    for name, pat, weight in rules:
        rejected = None
        matched = False
        for m in re.finditer(pat, text, re.I | re.S):
            if guard_negation and _NEG.search(text[max(0, m.start() - _NEG_WINDOW):m.start()]):
                continue
            s, e = max(0, m.start() - 60), min(len(text), m.end() + 60)
            quote = re.sub(r"\s+", " ", text[s:e]).strip()
            if guard_direction and _inbound(text, m.start()):
                rejected = rejected or (name, 0, quote, "grant-inbound")
                continue
            out.append((name, weight, quote, "grant" if guard_direction else "prohibit"))
            matched = True
            break
        if rejected and not matched:
            out.append(rejected)
    return out


def classify(tos_text):
    """Parse a verbatim ToS into a permission classification. PURE — no network, no state.

    Returns a dict: facts_use / image_use / scope / attribution_required / alteration_allowed /
    trade_partner_only /
    expiry / confidence / needs_counsel / evidence[] / rules_matched.

    The parser is deliberately conservative in one direction only: it will happily conclude
    `prohibited` from a single decisive clause, but it will not conclude `permitted` unless a grant
    outweighs every prohibition it found. A document that both grants and forbids is `prohibited`
    with `confidence="medium"` and `needs_counsel=True` — the contradiction is the finding."""
    text = tos_text or ""
    pro = _hits(text, _PROHIBIT)
    grant = _hits(text, _GRANT, guard_negation=True, guard_direction=True)
    pro_w = sum(w for _n, w, _q, _s in pro)
    grant_w = sum(w for _n, w, _q, _s in grant)
    # Only OUTBOUND grants count; inbound ones ride along as zero-weight evidence.
    real_grants = [g for g in grant if g[3] == "grant"]

    if not text.strip():
        image_use, confidence = "silent", "low"
    elif grant_w > pro_w:
        image_use, confidence = "permitted", ("high" if not pro else "medium")
    elif pro_w > 0:
        image_use, confidence = "prohibited", ("high" if not real_grants else "medium")
    else:
        image_use, confidence = "silent", "low"

    scope = "none"
    if image_use == "permitted":
        scope = "internal_only"          # a grant we can't scope is the narrowest grant
        for cand, pat in _SCOPE_HINTS:
            if re.search(pat, text, re.I):
                scope = cand
                break

    alter = None
    if re.search(_NO_ALTER, text, re.I):
        alter = False
    elif re.search(_MAY_ALTER, text, re.I):
        alter = True
    m_exp = re.search(_EXPIRY, text, re.I)

    # needs_counsel guards the AFFIRMATIVE act (see module docstring): any grant, or any
    # classification the parser could not make decisively.
    needs_counsel = bool(image_use == "permitted" or confidence != "high" or image_use == "silent")

    evidence = [{"rule": n, "weight": w, "quote": q, "side": "prohibit"} for n, w, q, _s in pro]
    evidence += [{"rule": n, "weight": w, "quote": q, "side": s} for n, w, q, s in grant]
    return {
        # `facts_use` is carried explicitly rather than left implicit. It is near-always "permitted"
        # (facts are uncopyrightable), and writing it down is the point: a reviewer reading a record
        # that says `image_use: prohibited` should see, in the same object, that the fact feed is
        # unaffected — otherwise "prohibited" reads as "this source is off", which it is not.
        "facts_use": "permitted",
        "image_use": image_use,
        "scope": scope,
        "attribution_required": bool(re.search(_ATTRIBUTION, text, re.I)),
        "alteration_allowed": alter,
        # A grant ADDRESSED TO a class is conditioned by construction, whether or not the word "only"
        # appears. "Authorized retailers may use these images" matched no "…only" phrasing, so the
        # most dangerous grant shape — one written for a class we may not belong to — arrived
        # UNCONDITIONED and the gate let it through. The rule that found it now sets the condition.
        "trade_partner_only": bool(re.search(_TRADE_ONLY, text, re.I))
                              or "trade-partner-grant" in [n for n, _w, _q, sd in grant if sd == "grant"],
        "expiry": m_exp.group(1) if m_exp else None,
        "confidence": confidence,
        "needs_counsel": needs_counsel,
        "evidence": evidence,
        "rules_matched": {"prohibit": [n for n, _w, _q, _s in pro],
                          "grant": [n for n, _w, _q, s in grant if s == "grant"],
                          "grant_inbound": [n for n, _w, _q, s in grant if s == "grant-inbound"],
                          "prohibit_weight": pro_w, "grant_weight": grant_w},
    }


# ── robots ────────────────────────────────────────────────────────────────────────────────────────
def robots_allows(robots_text, url, ua="*"):
    """Deterministic robots decision for a URL against a VERBATIM robots.txt body (stdlib
    RobotFileParser, so the longest-match Allow/Disallow precedence is the standard one and not
    something we reinvented). Text is passed in rather than fetched so the decision is reproducible
    from the snapshot in the rights record."""
    rp = urllib.robotparser.RobotFileParser()
    rp.parse((robots_text or "").splitlines())
    return bool(rp.can_fetch(ua, url))


def fetch_robots(host, timeout=25):
    """Fetch a host's robots.txt verbatim. Returns (text, error)."""
    url = "https://%s/robots.txt" % host
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), None
    except Exception as e:
        return "", str(e)[:160]


def fetch_tos_text(url, timeout=30):
    """Fetch a terms page and strip it to text. Returns (text, note). `note` is 'client-rendered'
    when the body came back below MIN_TOS_CHARS — the honest signal that this URL cannot be verified
    by a stdlib fetch and the committed snapshot is the only artifact we have."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        return "", "fetch-failed: %s" % str(e)[:140]
    html = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    m = re.search(r"<main[^>]*>(.*?)</main>", html, re.S | re.I)
    body = m.group(1) if m else html
    text = re.sub(r"<[^>]+>", "\n", body)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(l.strip() for l in text.split("\n") if l.strip())
    return text, (None if len(text) >= MIN_TOS_CHARS else "client-rendered")


# ── the records ───────────────────────────────────────────────────────────────────────────────────
def record_path(source_id):
    return os.path.join(RECORDS_DIR, "%s.json" % source_id)


def load(source_id):
    """Load a committed rights record. A DAM source with no record cannot run — that is the point:
    there is no 'harvest first, sort the rights out later' path."""
    p = record_path(source_id)
    if not os.path.exists(p):
        raise RightsViolation(
            "no rights record for %r (expected %s) — a DAM source may not run without one"
            % (source_id, os.path.relpath(p, _DIR)))
    with open(p, encoding="utf-8") as f:
        rec = json.load(f)
    rec.setdefault("source_id", source_id)
    return rec


def list_records():
    if not os.path.isdir(RECORDS_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(RECORDS_DIR) if f.endswith(".json"))


def rights_ref(rec):
    """The stable reference every emitted row carries back to the exact terms it was cleared under.
    Includes the ToS sha so a row can never be traced to a record that has since been re-reviewed."""
    return "%s@%s" % (rec.get("source_id", "?"), (rec.get("tos_sha256") or "nosha")[:12])


def refresh(rec, timeout=30):
    """Re-fetch the terms + robots and compare against the snapshot. Returns a status dict; does NOT
    mutate the record (re-review is a human act, and a machine that can bless its own terms change is
    not a control). Status is one of:
      current      — fetched text matches the snapshot sha
      changed      — fetched text differs → the gate holds until re-review
      unverifiable — the page is client-rendered / unfetchable → proves nothing, so the gate holds
    """
    out = {"checked_at": _now(), "tos_url": rec.get("tos_url")}
    text, note = fetch_tos_text(rec.get("tos_url") or "", timeout=timeout)
    if note:
        out.update(status="unverifiable", note=note, fetched_chars=len(text))
    elif _sha(text) == rec.get("tos_sha256"):
        out.update(status="current", fetched_chars=len(text))
    else:
        out.update(status="changed", fetched_chars=len(text), fetched_sha256=_sha(text))
    host = rec.get("host") or urlparse(rec.get("tos_url") or "").netloc
    rtext, rerr = fetch_robots(host, timeout=timeout)
    out["robots"] = {"host": host, "error": rerr,
                     "status": ("current" if _sha(rtext) == rec.get("robots_sha256")
                                else ("unfetchable" if rerr else "changed"))}
    return out


# ── the gate ──────────────────────────────────────────────────────────────────────────────────────
def may(rec, action):
    """(allowed, reason) for `action` under `rec`. This is the ONLY place the permission model is
    interpreted; everything else calls it. Unknown actions are denied — a typo'd action name must
    never read as permission."""
    if action not in ACTIONS:
        return False, "unknown action %r (deny-by-default)" % action
    need = ACTIONS[action]
    stale = rec.get("status") == "stale" or rec.get("review_state") == "stale"
    # Staleness is checked BEFORE the 0-scope shortcut, so a server-touching action that needs no
    # scope (fetch_document_facts) still stops when the terms have moved.
    if stale and action in _STALE_SENSITIVE:
        return False, "rights record is STALE — terms moved since review; re-review before %s" % action
    if _SCOPE_RANK[need] == 0:
        return True, ("reading a text document for FACTS is ungated (facts are uncopyrightable; "
                      "bytes are transient and prose never lands)"
                      if action == "fetch_document_facts" else "facts and pointers are ungated")

    perms = rec.get("permissions") or {}
    if stale:
        return False, "rights record is STALE — terms moved since review; re-review before any asset use"
    use = perms.get("image_use", "silent")
    if use == "silent":
        return False, "terms are SILENT on reuse — silence is not permission (default hold)"
    if use != "permitted":
        return False, "terms PROHIBIT reuse (%s)" % ", ".join(
            e["rule"] for e in (perms.get("evidence") or []) if e.get("side") == "prohibit") or "prohibited"
    granted = perms.get("scope", "none")
    if granted not in _SCOPE_RANK:
        return False, "unrecognized scope %r (deny-by-default)" % granted
    if _SCOPE_RANK[granted] < _SCOPE_RANK[need]:
        return False, "action needs scope %s, record grants %s" % (need, granted)
    # A TRADE-CONDITIONED grant is a grant to a class of people, not to whoever fetches the page.
    # "Authorized retailers may use these images to sell our products" is a real licence with a real
    # precondition, and reading it as ours would be the same error as reading a press grant addressed
    # to accredited media as ours. Denied until `trade_partner_verified` records who established it.
    if perms.get("trade_partner_only") and not rec.get("trade_partner_verified"):
        return False, ("grant is limited to authorized trade partners and no trade_partner_verified "
                       "sign-off records that we are one")
    exp = perms.get("expiry")
    if exp and str(exp) < time.strftime("%Y-%m-%d"):
        return False, "grant expired %s" % exp
    if perms.get("needs_counsel") and not rec.get("counsel_cleared"):
        return False, "grant is flagged needs_counsel and has no counsel_cleared sign-off"
    return True, "granted: scope=%s covers %s" % (granted, need)


def require(rec, action, subject=""):
    """Assert `action` is in scope, or raise. Use before ANY asset-touching work."""
    ok, why = may(rec, action)
    if not ok:
        _log_emission(rec, action, subject, allowed=False, reason=why)
        raise RightsViolation("%s: %s%s — %s" % (rights_ref(rec), action,
                                                (" on %s" % subject) if subject else "", why))
    return True


def emit(rec, action, subject="", surface="", land=False, log=None):
    """Record an emission of `subject` under `action`, enforcing scope. Every emission — allowed or
    denied — is written to the emission log, because "we never emitted anything out of scope" is a
    claim that needs an audit trail, not an assurance."""
    require(rec, action, subject)
    row = _log_emission(rec, action, subject, allowed=True, reason="in scope", surface=surface)
    if land:
        land_emissions([row], log=log or (lambda *_a: None))
    return row


def _log_emission(rec, action, subject, allowed, reason, surface=""):
    row = {"ts": _now(), "source_id": rec.get("source_id"), "rights_ref": rights_ref(rec),
           "action": action, "subject": str(subject)[:300], "surface": surface,
           "allowed": bool(allowed), "reason": reason[:300],
           "image_use": (rec.get("permissions") or {}).get("image_use"),
           "scope": (rec.get("permissions") or {}).get("scope")}
    try:
        os.makedirs(os.path.dirname(_EMIT_LOG), exist_ok=True)
        with open(_EMIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass          # the log is evidence, never a reason to fail the run
    return row


# The emission row's columns and their types are DECLARED IN table_spec (contract C2: schema belongs
# to the table, not to whoever writes it) and read back here, so this module and the warehouse cannot
# hold two versions of the same shape.
EMISSION_FIELDS = table_spec.fields_for(EMISSIONS_TABLE)
RIGHTS_FIELDS = ["source_id", "vendor", "host", "tos_url", "tos_sha256", "tos_captured_at",
                 "tos_capture_method", "robots_sha256", "robots_checked_at", "robots_allows_harvest",
                 "facts_use", "image_use", "scope", "attribution_required", "alteration_allowed",
                 "expiry", "confidence", "needs_counsel", "counsel_cleared", "review_state",
                 "schema_version", "schema_signoff", "reviewed_by", "reviewed_at", "escalation",
                 "evidence_json", "landed_at"]


def land_emissions(rows, log=print):
    """Append the emission log to the warehouse as a dated partition (emissions are EVENTS —
    append-only, never a column on a catalog)."""
    if not rows:
        return 0
    try:
        import warehouse
        # PIN THE SCHEMA, don't let the batch infer it. A run where every emission was denied lands
        # `allowed` all-False with `image_use`/`scope` all-None, and an inferred schema types those
        # columns from that batch alone — which is how a table ends up with per-file schemas that
        # union_by_name then corrupts on read. The types come from the declaration, never from here.
        dtypes = table_spec.arrow_dtypes(EMISSIONS_TABLE)
        if not EMISSION_FIELDS or dtypes is None:
            raise RuntimeError("%s has no table_spec declaration — declare it before landing, "
                               "an unpinned write is what makes the log unreadable" % EMISSIONS_TABLE)
        part = "%s_%s" % (time.strftime("%Y-%m-%d"), rows[0].get("source_id") or "dam")
        warehouse.write_partition(EMISSIONS_TABLE, part, rows, fields=EMISSION_FIELDS, dtypes=dtypes)
        return len(rows)
    except Exception as e:
        log("%s land skipped: %s" % (EMISSIONS_TABLE, str(e)[:120]))
        return 0


def land_record(rec, robots_ok=None, log=print):
    """Land the rights record itself, so the warehouse carries the permission ledger next to the data
    it governs. The full verbatim ToS stays in the repo (reviewable in diff); this is the index."""
    p = rec.get("permissions") or {}
    row = {"source_id": rec.get("source_id"), "vendor": rec.get("vendor"), "host": rec.get("host"),
           "tos_url": rec.get("tos_url"), "tos_sha256": rec.get("tos_sha256"),
           "tos_captured_at": rec.get("tos_captured_at"),
           "tos_capture_method": rec.get("tos_capture_method"),
           "robots_sha256": rec.get("robots_sha256"), "robots_checked_at": rec.get("robots_checked_at"),
           "robots_allows_harvest": robots_ok if robots_ok is not None else rec.get("robots_allows_harvest"),
           "facts_use": p.get("facts_use"), "image_use": p.get("image_use"), "scope": p.get("scope"),
           "attribution_required": p.get("attribution_required"),
           "alteration_allowed": p.get("alteration_allowed"),
           "trade_partner_only": p.get("trade_partner_only"),
           "expiry": p.get("expiry"), "confidence": p.get("confidence"),
           "needs_counsel": p.get("needs_counsel"), "counsel_cleared": rec.get("counsel_cleared", False),
           "review_state": rec.get("review_state"),
           "schema_version": rec.get("schema_version"), "schema_signoff": rec.get("schema_signoff"),
           "reviewed_by": rec.get("reviewed_by"),
           "reviewed_at": rec.get("reviewed_at"), "escalation": rec.get("escalation"),
           "evidence_json": json.dumps(p.get("evidence") or [])[:4000], "landed_at": _now()}
    try:
        import warehouse
        warehouse.write_accumulate(RIGHTS_TABLE, [row], key="source_id",
                                   fields=RIGHTS_FIELDS, coverage=False)
        return row
    except Exception as e:
        log("%s land skipped: %s" % (RIGHTS_TABLE, str(e)[:120]))
        return row


def build_record(source_id, vendor, host, tos_url, tos_text, tos_capture_method,
                 robots_text="", harvest_urls=(), reviewed_by="", escalation=""):
    """Assemble a rights record from a VERBATIM ToS + robots snapshot. Used to author a record for
    review; the result is written to rights_records/ and committed, never generated at runtime."""
    perms = classify(tos_text)
    checks = [{"url": u, "allowed": robots_allows(robots_text, u)} for u in harvest_urls]
    return {
        "source_id": source_id, "vendor": vendor, "host": host, "tos_url": tos_url,
        "tos_snapshot": tos_text, "tos_sha256": _sha(tos_text), "tos_captured_at": _now(),
        "tos_capture_method": tos_capture_method,
        "robots_snapshot": robots_text, "robots_sha256": _sha(robots_text),
        "robots_checked_at": _now(),
        "robots_allows_harvest": all(c["allowed"] for c in checks) if checks else None,
        "robots_checks": checks,
        "permissions": perms, "counsel_cleared": False, "review_state": "reviewed",
        "schema_version": SCHEMA_VERSION, "schema_signoff": None,
        "reviewed_by": reviewed_by, "reviewed_at": _now(), "escalation": escalation,
    }


# ── the counsel queue (P6) ────────────────────────────────────────────────────────────────────────
def queue():
    """Every record awaiting a human decision, and what each is BLOCKED ON.

    `needs_counsel` was a field nobody could act on: it said a review was owed but not what the
    reviewer had to decide or what would change if they decided it. This turns it into a work list —
    one row per record, naming the question, the consequence of leaving it, and the exact edit that
    resolves it. Blocked reasons, most consequential first:

      grant-uncleared   the terms GRANT reuse and we are not acting on it. This is the only entry
                        that costs capability — a decision here turns assets on.
      schema-unsigned   the record predates (or postdates) a signed-off schema version.
      undecided         the parser could not classify decisively; a human must read the terms.
      stale             the terms moved since review; every asset action is denied until re-read.
    """
    out = []
    for sid in list_records():
        try:
            rec = load(sid)
        except Exception as e:
            out.append({"source_id": sid, "blocked_on": "unreadable", "detail": str(e)[:120],
                        "unblocks": "fix or remove the record file", "costs_capability": True})
            continue
        p = rec.get("permissions") or {}
        reasons = []
        if rec.get("review_state") == "stale" or rec.get("status") == "stale":
            reasons.append(("stale", "terms moved since review — all asset actions denied",
                            "re-read the terms, re-snapshot, re-classify"))
        if p.get("image_use") == "permitted" and not rec.get("counsel_cleared"):
            reasons.append(("grant-uncleared",
                            "terms GRANT %s and the gate is holding it" % (p.get("scope") or "?"),
                            "review the grant, then set counsel_cleared=true"))
        if rec.get("schema_version") != SCHEMA_VERSION or not rec.get("schema_signoff"):
            reasons.append(("schema-unsigned",
                            "authored under schema %s; sign-off %s"
                            % (rec.get("schema_version") or "unversioned",
                               rec.get("schema_signoff") or "absent"),
                            "counsel accepts docs/rights-counsel-review.md, then record "
                            "schema_signoff on each record"))
        if p.get("confidence") != "high":
            reasons.append(("undecided",
                            "classification confidence=%s" % (p.get("confidence") or "?"),
                            "a human reads the terms and sets the classification by hand"))
        for why, detail, fix in reasons:
            out.append({"source_id": sid, "blocked_on": why, "detail": detail, "unblocks": fix,
                        "image_use": p.get("image_use"), "scope": p.get("scope"),
                        # Only an uncleared grant is costing us anything. A hold that is correctly
                        # held is not a queue item to clear, it is the system working — and mixing
                        # the two is how a review queue becomes noise nobody drains.
                        "costs_capability": why in ("grant-uncleared", "unreadable")})
    order = {"grant-uncleared": 0, "unreadable": 1, "stale": 2, "schema-unsigned": 3, "undecided": 4}
    return sorted(out, key=lambda r: (order.get(r["blocked_on"], 9), r["source_id"]))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Inspect and re-check rights records.")
    ap.add_argument("--queue", action="store_true",
                    help="list every record awaiting a human decision (the P6 counsel queue)")
    ap.add_argument("source_id", nargs="?", help="record to show; omit to list all")
    ap.add_argument("--refresh", action="store_true", help="re-fetch terms + robots and report drift")
    a = ap.parse_args(argv)
    if a.queue:
        q = queue()
        if not q:
            print("counsel queue: empty — every record is decided and signed off.")
            return
        costly = [r for r in q if r["costs_capability"]]
        print("counsel queue: %d item(s), %d costing capability\n" % (len(q), len(costly)))
        for r in q:
            print("  %-14s %-16s %s" % (r["source_id"], r["blocked_on"], r["detail"]))
            print("  %-14s %-16s -> %s" % ("", "", r["unblocks"]))
        print("\nOnly `grant-uncleared` and `unreadable` cost capability. A hold that is correctly "
              "held is the system working, not a queue item to clear.")
        return
    if not a.source_id:
        for sid in list_records():
            r = load(sid)
            p = r.get("permissions") or {}
            print("%-20s %-11s scope=%-26s confidence=%-6s counsel=%s"
                  % (sid, p.get("image_use"), p.get("scope"), p.get("confidence"),
                     "needed" if p.get("needs_counsel") and not r.get("counsel_cleared") else "-"))
        return
    rec = load(a.source_id)
    p = rec["permissions"]
    print("%s (%s)\n  terms: %s\n  sha:   %s (%s, %s)"
          % (rec["source_id"], rec["vendor"], rec["tos_url"], rec["tos_sha256"][:16],
             rec["tos_capture_method"], rec["tos_captured_at"]))
    print("  facts_use=%s image_use=%s scope=%s confidence=%s needs_counsel=%s trade_only=%s expiry=%s"
          % (p["facts_use"], p["image_use"], p["scope"], p["confidence"], p["needs_counsel"], p["trade_partner_only"], p["expiry"]))
    for e in p.get("evidence", []):
        print("   [%s] %s: “%s”" % (e["side"], e["rule"], e["quote"][:150]))
    print("  gate:")
    for act in ACTIONS:
        ok, why = may(rec, act)
        print("    %-18s %-6s %s" % (act, "ALLOW" if ok else "DENY", why))
    if a.refresh:
        print("  refresh:", json.dumps(refresh(rec), indent=2))


if __name__ == "__main__":
    main()
