"""Exercise the rights gate — the control this whole capability rests on.

These assert the PROPERTIES, not the implementation: silence holds like a prohibition, a negated
grant is not a grant, an unknown action is denied, a stale record shuts image flow off, and facts
flow no matter what the terms say. Pure stdlib; no network, no warehouse."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rights  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


def rec_for(perms, **kw):
    r = {"source_id": "test", "tos_sha256": "a" * 64, "permissions": perms}
    r.update(kw)
    return r


# ── the classifier ────────────────────────────────────────────────────────────────────────────────
print("classify:")
PROHIBIT_TOS = ("All such rights are reserved. You must not use any part of the Materials on our "
                "Site for commercial purposes unless expressly permitted by us.")
c = rights.classify(PROHIBIT_TOS)
check(c["image_use"] == "prohibited", "an explicit commercial-use ban classifies as prohibited")
check(c["scope"] == "none", "a prohibition grants no scope")
check(c["confidence"] == "high" and not c["needs_counsel"],
      "an unambiguous hold is high-confidence and needs no counsel (counsel guards the grant)")
check(any(e["quote"] for e in c["evidence"]), "every classification carries verbatim evidence")

c = rights.classify("Welcome to our media centre. Downloads are available to registered users.")
check(c["image_use"] == "silent" and c["scope"] == "none",
      "terms that say nothing about reuse classify as SILENT, not permitted")
check(c["needs_counsel"], "a silent classification is flagged for counsel")

# The regression this rule exists for: "does not grant you any rights ... or license" is the
# clearest possible prohibition and used to score as a GRANT, dropping a decisive hold to medium.
NEGATED = ("Except as provided herein, the use of this Site does not grant you any rights, title, "
           "interest or license to any Materials you may access on this Site. All rights reserved.")
c = rights.classify(NEGATED)
check(c["image_use"] == "prohibited", "a NEGATED grant phrase is not a grant")
check(not c["rules_matched"]["grant"], "the negated clause contributes zero grant weight")
check(c["confidence"] == "high", "with the false grant gone, the hold is decisive again")

GRANT_TOS = ("Images in this press room are made available royalty-free for editorial use by "
             "accredited media. Photo credit must be given to the brand. Images may not be altered "
             "or cropped.")
c = rights.classify(GRANT_TOS)
check(c["image_use"] == "permitted", "an explicit royalty-free editorial grant classifies as permitted")
check(c["scope"] == "editorial_press", "an editorial grant scopes to editorial_press, not commercial")
check(c["attribution_required"] is True, "an attribution clause is picked up")
check(c["facts_use"] == "permitted", "facts_use is carried explicitly on every classification")
check(c["alteration_allowed"] is False, "a no-alteration clause is picked up")
check(c["needs_counsel"], "a GRANT is flagged for counsel — counsel guards the affirmative act")

MIXED = GRANT_TOS + " You must not use any part of the Materials on our Site for commercial purposes."
c = rights.classify(MIXED)
check(c["confidence"] == "medium" and c["needs_counsel"],
      "a document that both grants and forbids is medium-confidence and flagged")

c = rights.classify("Assets are licensed for trade use only and this licence expires 2020-01-01.")
check(c["trade_partner_only"] is True, "trade-partner-only restriction is picked up")
check(c["expiry"] == "2020-01-01", "an expiry date is picked up")

# ── the gate ──────────────────────────────────────────────────────────────────────────────────────
print("\ngate:")
PROHIBITED = rec_for(rights.classify(PROHIBIT_TOS))
SILENT = rec_for(rights.classify("Nothing here mentions reuse at all."))
EDITORIAL = rec_for(rights.classify(GRANT_TOS), counsel_cleared=True)

for name, rec in (("prohibited", PROHIBITED), ("silent", SILENT)):
    for act in ("fetch_asset", "retain_asset", "derive_hash", "derive_embedding",
                "serve_internal", "serve_editorial", "redistribute"):
        ok, _why = rights.may(rec, act)
        check(not ok, "%s record denies %s" % (name, act))
    for act in ("catalog_metadata", "catalog_pointer"):
        ok, _why = rights.may(rec, act)
        check(ok, "%s record still allows %s (facts always flow)" % (name, act))

check(rights.may(EDITORIAL, "serve_editorial")[0], "an editorial grant permits serve_editorial")
check(rights.may(EDITORIAL, "derive_embedding")[0],
      "an editorial grant covers the weaker internal_only actions")
check(not rights.may(EDITORIAL, "redistribute")[0],
      "an editorial grant does NOT reach commercial redistribution")

uncleared = rec_for(rights.classify(GRANT_TOS))
check(not rights.may(uncleared, "serve_editorial")[0],
      "a grant flagged needs_counsel is inert until counsel_cleared is set")

check(not rights.may(EDITORIAL, "sell_to_third_party")[0],
      "an unknown action is DENIED — a typo must never read as permission")

stale = dict(EDITORIAL, review_state="stale")
check(not rights.may(stale, "serve_internal")[0], "a STALE record shuts off every image action")
check(rights.may(stale, "catalog_metadata")[0], "a stale record still permits facts")

expired = rec_for(dict(rights.classify(GRANT_TOS), expiry="2020-01-01"), counsel_cleared=True)
check(not rights.may(expired, "serve_editorial")[0], "an expired grant no longer permits anything")

bad_scope = rec_for({"image_use": "permitted", "scope": "whatever-we-like"}, counsel_cleared=True)
check(not rights.may(bad_scope, "serve_internal")[0], "an unrecognized scope is denied, not coerced")

# require() raises and logs
try:
    rights.require(PROHIBITED, "retain_asset", "asset-1")
    check(False, "require() raises RightsViolation on a denied action")
except rights.RightsViolation as e:
    check("retain_asset" in str(e) and "PROHIBIT" in str(e).upper(),
          "require() raises RightsViolation naming the action and the reason")

row = rights.emit(EDITORIAL, "serve_editorial", "asset-9", surface="press-kit")
check(row["allowed"] and row["action"] == "serve_editorial", "emit() logs an allowed emission")

# ── robots ────────────────────────────────────────────────────────────────────────────────────────
print("\nrobots:")
ROBOTS = "User-agent: *\nDisallow: /api/\nDisallow: /login\n"
check(rights.robots_allows(ROBOTS, "https://h/drives/get-tree/42?folder_id=1"),
      "a path outside every Disallow prefix is allowed")
check(not rights.robots_allows(ROBOTS, "https://h/api/files"), "a disallowed prefix is refused")
check(not rights.robots_allows(ROBOTS, "https://h/login"), "an exact disallowed path is refused")

# ── the committed record for the first vendor ─────────────────────────────────────────────────────
print("\ndam-bacardi record:")
if os.path.exists(rights.record_path("dam-bacardi")):
    rec = rights.load("dam-bacardi")
    p = rec["permissions"]
    check(len(rec.get("tos_snapshot") or "") > rights.MIN_TOS_CHARS,
          "the record carries a full verbatim ToS snapshot, not a stub")
    check(rights._sha(rec["tos_snapshot"]) == rec["tos_sha256"], "the snapshot sha matches its text")
    check(p["image_use"] == "prohibited" and p["scope"] == "none",
          "Bacardi's terms classify prohibited/none (no press or editorial carve-out exists)")
    check(rec["robots_allows_harvest"] is True,
          "every URL this connector fetches is robots-permitted on the vendor's own robots.txt")
    check(rec.get("escalation"), "the record names the escalation path for widening scope")
    for act in ("fetch_asset", "derive_hash", "derive_embedding", "serve_internal", "redistribute"):
        check(not rights.may(rec, act)[0], "dam-bacardi denies %s" % act)
    check(rights.may(rec, "catalog_metadata")[0], "dam-bacardi still allows catalog_metadata")
else:
    check(False, "rights_records/dam-bacardi.json is missing")

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
