"""The registry ratchet: every DAM source has a reviewed rights record, and every record is loadable,
internally consistent, and enforced.

This is the mechanical guard that makes "no harvest without terms" true rather than intended. Adding a
`source_class="dam"` row to the registry without its record fails HERE, at test time, instead of at
2am against someone's media server. Same shape as browser_driver_test.py — it names the offender."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import rights            # noqa: E402
import source_registry as sr  # noqa: E402

FAILS = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        FAILS.append(msg)


registered_ids = {x["id"] for x in sr.SOURCES}
dams = sr.dam_sources()
print("dam sources: %d" % len(dams))
check(dams, "at least one DAM source is registered")

for s in dams:
    sid = s["id"]
    print("\n%s:" % sid)
    check(s.get("rights_record"), "%s declares rights_record" % sid)
    # klass is execution placement — a DAM row with a novel klass would be dropped from every
    # scheduled pass by run_sources' `klass in ("headless","creds")` filter.
    check(s.get("klass") in ("headless", "creds", "mac", "build"),
          "%s uses a klass the dispatcher actually runs (%s)" % (sid, s.get("klass")))

    path = os.path.join(HERE, s.get("rights_record") or "")
    check(os.path.exists(path), "%s: the declared record file exists" % sid)
    if not os.path.exists(path):
        continue

    # Resolve the record by its DECLARED PATH, not by source id. A source may legitimately operate
    # under another source's terms — `dam-gallery` derives from `dam-bacardi`'s assets and is bound
    # by Bacardi's terms, so it must not need (or be allowed) a second record of its own that could
    # drift from the one the assets were collected under.
    rid = os.path.basename(path)[:-5]
    check(rid in registered_ids,
          "%s: its record (%s) belongs to a registered source" % (sid, rid))
    rec = rights.load(rid)
    p = rec.get("permissions") or {}
    check(rec.get("tos_url"), "%s: record names the governing terms URL" % sid)
    check(len(rec.get("tos_snapshot") or "") >= rights.MIN_TOS_CHARS,
          "%s: record carries a substantive verbatim ToS snapshot (%d chars)"
          % (sid, len(rec.get("tos_snapshot") or "")))
    check(rights._sha(rec["tos_snapshot"]) == rec.get("tos_sha256"),
          "%s: the snapshot's sha256 matches its text (record not hand-edited out of sync)" % sid)
    check(rec.get("robots_snapshot"), "%s: record carries a verbatim robots.txt snapshot" % sid)
    check(rec.get("robots_checks"), "%s: record records a per-URL robots decision" % sid)
    check(all(c["allowed"] for c in rec.get("robots_checks") or []),
          "%s: every URL the connector fetches is robots-ALLOWED" % sid)
    check(p.get("image_use") in rights.IMAGE_USE,
          "%s: image_use is one of %s (got %r)" % (sid, rights.IMAGE_USE, p.get("image_use")))
    check(p.get("scope") in rights.SCOPES,
          "%s: scope is one of %s (got %r)" % (sid, rights.SCOPES, p.get("scope")))
    check(p.get("evidence"), "%s: the classification cites verbatim evidence" % sid)
    # A grant that came from an INBOUND (user-content) clause is the misread that would authorise a
    # gallery on unlicensed assets — assert no record was ever built on one.
    check(not (p.get("image_use") == "permitted"
               and not (p.get("rules_matched") or {}).get("grant")),
          "%s: a `permitted` verdict rests on a real OUTBOUND grant rule" % sid)
    check(rec.get("reviewed_at"), "%s: the record records who/when it was reviewed" % sid)

    # The load-bearing invariant: anything short of an explicit, counsel-cleared grant must deny
    # every asset action. A record that permits image flow without sign-off is the failure this
    # capability exists to prevent, so assert the GATE, not the fields.
    granted = p.get("image_use") == "permitted" and rec.get("counsel_cleared")
    for act in ("fetch_asset", "retain_asset", "derive_hash", "derive_embedding",
                "serve_internal", "serve_editorial", "redistribute"):
        ok, _why = rights.may(rec, act)
        if not granted:
            check(not ok, "%s: denies %s (no counsel-cleared grant)" % (sid, act))
    check(rights.may(rec, "catalog_metadata")[0],
          "%s: facts and pointers still flow regardless of image rights" % sid)
    if p.get("image_use") != "permitted":
        check(rec.get("escalation"),
              "%s: a held source names its escalation path (how to ASK for permission)" % sid)

# Every record on disk belongs to a registered source — an orphan record is a permission floating free
# of anything that could enforce it.
print("\norphan records:")
registered = registered_ids
orphans = [r for r in rights.list_records() if r not in registered]
check(not orphans, "no rights record without a registered source (%s)" % (orphans or "clean"))

print("\n%s (%d failure%s)" % ("FAILED" if FAILS else "PASSED", len(FAILS), "" if len(FAILS) == 1 else "s"))
sys.exit(1 if FAILS else 0)
