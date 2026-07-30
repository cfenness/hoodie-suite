#!/usr/bin/env python3
"""agent_import_chat_test.py — the scope gate on a PERSONAL account.

This module reads a full claude.ai export, which on this account is an entire life: job applications,
medical questions, betting analysis, a children's storybook app. Only the hoodie / bev-alc material is
in scope, and the gate is a HARD one — out-of-scope conversations are never read, not merely
down-ranked, so nothing personal can reach a queryable store even as a low-scoring row.

That makes the gate's failure modes asymmetric, and the tests reflect it: letting personal content
through is the serious failure; missing a work thread just costs recall.

    python3 unifyd/agent_import_chat_test.py
"""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def conv(name, msgs):
    return dict(uuid="c", name=name, chat_messages=msgs)


def human(text, blocks=False):
    if blocks:
        return dict(sender="human", content=[dict(type="text", text=text)])
    return dict(sender="human", text=text)


def main():
    print("agent_import_chat — scoped intake of a personal export")
    import agent_import_chat as I

    # --- 1. THE HARD GATE: personal topics are excluded outright ---------------------------------
    # Each of these is a real conversation title from the export. An incidental work word inside a
    # personal thread must not pull it into scope.
    for title in ("Night sweats from medication or withdrawal",
                  "Tailoring resume and cover letter for senior product manager role",
                  "Argentina vs Cape Verde draw betting analysis",
                  "AI-enabled children's storybook subscription teaching DBT and CBT"):
        ok, why = I.in_scope(conv(title, [human("we should talk about wine and price")]))
        check("excluded: %s" % title[:44], ok is False, (ok, why))
        check("...and the reason is recorded", why == "excluded-topic", why)

    # --- 2. real work is admitted ----------------------------------------------------------------
    for title in ("HTML flashcard tester for hoodie suite",
                  "On-premise depletion insights for wine and spirits",
                  "Consolidating geography and census mapping for unifyd"):
        ok, why = I.in_scope(conv(title, [human("hello")]))
        check("in scope: %s" % title[:44], ok is True, (ok, why))

    # --- 3. ONE incidental keyword is not enough -------------------------------------------------
    # This is the tightening that mattered: "Preach's unwavering loyalty" and "How Toastmasters works"
    # both qualified off a single passing word like "price" and their mined clauses were autobiography.
    ok, why = I.in_scope(conv("Preach's unwavering loyalty",
                              [human("he never once mentioned the price of anything")]))
    check("a single incidental keyword does NOT admit a personal thread", ok is False, (ok, why))
    check("...and is reported as a weak signal", why in ("weak-signal", "no-signal"), why)

    ok, why = I.in_scope(conv("Some untitled thread",
                              [human("the scraper lands price and inventory per store")]))
    check("two distinct domain terms DO admit it", ok is True, (ok, why))
    check("...recorded as a content match", why == "content", why)

    ok, why = I.in_scope(conv("", [human("hi there")]))
    check("no signal at all is excluded", ok is False, (ok, why))

    # --- 4. message shapes: reading only one shape yields a silent empty import ------------------
    check("plain text message", I._text_of(dict(text="hello")) == "hello")
    check("content-block message",
          I._text_of(dict(content=[dict(type="text", text="hello")])) == "hello")
    check("multi-block message joins",
          "a" in I._text_of(dict(content=[dict(type="text", text="a"),
                                          dict(type="text", text="b")])))
    check("empty message is empty", I._text_of(dict()) == "")
    check("non-text blocks are ignored",
          I._text_of(dict(content=[dict(type="image", source="x")])) == "")
    check("human sender recognised", I._is_human(dict(sender="human")) is True)
    check("role=user recognised", I._is_human(dict(role="user")) is True)
    check("assistant is not human", I._is_human(dict(sender="assistant")) is False)

    # --- 5. the export loads from a zip without manual extraction --------------------------------
    tmp = tempfile.mkdtemp(prefix="import-")
    data = [conv("hoodie scraper price inventory", [human("we never delete a legacy attribute"),
                                                    dict(sender="assistant", text="understood"),
                                                    human("always store price each day", blocks=True)]),
            conv("Night sweats from medication", [human("wine and price and inventory")])]
    j = os.path.join(tmp, "conversations.json")
    json.dump(data, open(j, "w"))
    z = os.path.join(tmp, "export.zip")
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(j, "conversations.json")

    for path, label in ((j, "bare json"), (z, "zip")):
        res = I.scan_export(path)
        check("%s: only the in-scope conversation is read" % label,
              res["conversations"] == 1, res["conversations"])
        check("%s: the excluded one is counted, not silently dropped" % label,
              res["skipped"] == 1, res.get("skipped"))
        check("%s: exclusion reason is reported" % label,
              bool(res.get("skipped_reasons")), res.get("skipped_reasons"))
        check("%s: assistant turns are not mined" % label, res["turns_scanned"] == 2,
              res["turns_scanned"])

    # A zip with no conversations.json must fail loudly rather than report an empty history.
    bad = os.path.join(tmp, "empty.zip")
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("readme.txt", "nothing here")
    try:
        I.scan_export(bad)
        check("a zip without conversations.json errors loudly", False, "no error raised")
    except SystemExit:
        check("a zip without conversations.json errors loudly", True)
    try:
        I.scan_export(os.path.join(tmp, "does-not-exist.zip"))
        check("a missing file errors loudly", False, "no error raised")
    except SystemExit:
        check("a missing file errors loudly", True)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
