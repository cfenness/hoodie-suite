#!/usr/bin/env python3
"""agent_tickets_test.py — pins the one rule that makes ticket status trustworthy: forward-only.

The whole point of a ticket over a chat is that its status_history is an honest audit trail — if a
ticket could quietly step backward, "done" would stop meaning anything, the same reason
server.py's order lifecycle (which this module's ladder logic is a byte-for-byte copy of) refuses a
backward move. Everything else here is the supporting cast: atomic per-ticket writes so a
live-refreshing viewer never sees a half-written file, append_section's ordering since it's the
literal mechanism behind "embed test reports as the process goes", db= isolation now that storage
is a dict-of-records rather than a list, and the Jira-parity surface (issue_type/priority/labels/
story_points/epic_id, set_pr/set_jira, epics + rollup, jira_csv) added in the durable-storage
rewrite.

    python3 unifyd/agent_tickets_test.py
"""
import csv
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    print("agent_tickets — forward-only lifecycle for real acceptance criteria")
    import agent_tickets as A

    # --- 0. derive_title: UNCHANGED by the rewrite, same cases as before ---------------------------
    real_draft = (
        "## Acceptance criteria\n\n"
        "## Outcome\n\n"
        "Add a standalone `unifyd/browser_warm_test.py` unit-test suite covering the module's pure "
        "logic.\n\n"
        "## Acceptance criteria\n\n"
        "1. it exists\n2. it passes\n")
    t0 = A.derive_title(real_draft, "fallback")
    check("finds the sentence under a LATER '## Outcome' heading, not the first heading it sees",
          t0.startswith("Add a standalone"), t0)
    check("strips the backtick-code markup from the title", "`" not in t0, t0)
    check("title is never just the word 'Outcome' (the bug this exists to fix)",
          t0.strip().lower() != "outcome", t0)

    check("plain prose with no headings at all just uses the first line",
          A.derive_title("Fix the publix parse.\n\nMore detail below.", "fb") == "Fix the publix parse.")
    check("a heading is skipped even when it's the very first line — the prose under it wins",
          A.derive_title("# Ship the new connector\n\nDetails.", "fb") == "Details.")
    check("**bold** markup is stripped from the title",
          A.derive_title("**Fix the retry loop**\n\nrest", "fb") == "Fix the retry loop")

    real_draft_2 = ("## Acceptance criteria\n\n"
                    "**Outcome:** Add a test suite for `unifyd/browser_warm.py` that would have "
                    "caught each of the 7 prior breaks.\n\n"
                    "**Acceptance criteria:**\n1. it exists\n")
    t2 = A.derive_title(real_draft_2, "fb")
    check("an inline '**Outcome:**' label (no heading) is stripped, not left as a prefix",
          t2.startswith("Add a test suite"), t2)
    check("...and the label word 'Outcome' doesn't survive anywhere in the title",
          "outcome" not in t2.lower(), t2)
    check("blank/empty draft falls back rather than returning an empty title",
          A.derive_title("", "fallback title") == "fallback title")
    check("a draft that's ONLY headings, no prose anywhere, falls back rather than returning ''",
          A.derive_title("## Acceptance criteria\n## Outcome\n## Scope", "fallback title")
          == "fallback title")
    long_line = "x" * 200
    check("title is capped at 120 chars, not left to grow unbounded",
          len(A.derive_title(long_line, "fb")) == 120, A.derive_title(long_line, "fb"))

    # --- 0b. extract_verdict_json: pure function, finds the LAST fenced ```json block --------------
    qa_report = (
        "I walked each acceptance criterion and tried to break it.\n\n"
        "1. Parses cleanly on a fresh input — PASS, ran `python3 publix_test.py`, 12/12 checks ok.\n"
        "2. Handles the empty-cart edge case — FAIL, raises KeyError on empty rows.\n\n"
        "```json\n"
        '{"criteria":[{"text":"Parses cleanly on a fresh input","status":"pass",'
        '"evidence":"publix_test.py:12/12 checks ok","severity":null},'
        '{"text":"Handles the empty-cart edge case","status":"fail",'
        '"evidence":"KeyError at publix.py:97 on empty rows","severity":"major"}],'
        '"verdict":"fail"}\n'
        "```\n")
    qa_verdict = A.extract_verdict_json(qa_report)
    check("extract_verdict_json parses a realistic QA-shaped report to the exact expected dict",
          qa_verdict == dict(criteria=[
              dict(text="Parses cleanly on a fresh input", status="pass",
                   evidence="publix_test.py:12/12 checks ok", severity=None),
              dict(text="Handles the empty-cart edge case", status="fail",
                   evidence="KeyError at publix.py:97 on empty rows", severity="major"),
          ], verdict="fail"), qa_verdict)

    reviewer_report = (
        "I reviewed the diff against the acceptance criteria and against what they missed.\n\n"
        "The parser change at publix.py:97 is correct and covered by a test. The empty-cart path "
        "is not handled and there is no test for it — that is a real gap.\n\n"
        "```json\n"
        '{"verdict":"ship-with-followups","blockers":[],'
        '"summary":"Core parse fix is solid; empty-cart handling should follow up separately."}\n'
        "```\n")
    reviewer_verdict = A.extract_verdict_json(reviewer_report)
    check("extract_verdict_json parses a realistic REVIEWER-shaped report to the exact expected dict",
          reviewer_verdict == dict(
              verdict="ship-with-followups", blockers=[],
              summary="Core parse fix is solid; empty-cart handling should follow up separately."),
          reviewer_verdict)

    check("no fenced json block at all returns None",
          A.extract_verdict_json("Just prose, no code block here.") is None)

    invalid_block = (
        "Some reasoning first.\n\n"
        "```json\n"
        '{"verdict": "ship", blockers: [],}\n'
        "```\n")
    check("a fenced block with invalid JSON (unquoted key, trailing comma) returns None, not a raise",
          A.extract_verdict_json(invalid_block) is None)

    two_blocks = (
        "Here's the shape I'll fill in, for reference:\n\n"
        "```json\n"
        '{"verdict":"ship","blockers":[],"summary":"example shape, not the real answer"}\n'
        "```\n\n"
        "Having now actually reviewed it, here's my real verdict.\n\n"
        "```json\n"
        '{"verdict":"blocked","blockers":["publix.py:97 empty-cart KeyError"],'
        '"summary":"Blocked on the empty-cart crash."}\n'
        "```\n")
    two_blocks_verdict = A.extract_verdict_json(two_blocks)
    check("two fenced json blocks: parses the LAST one, not the first",
          two_blocks_verdict == dict(verdict="blocked",
                                      blockers=["publix.py:97 empty-cart KeyError"],
                                      summary="Blocked on the empty-cart crash."),
          two_blocks_verdict)

    check("empty string input returns None", A.extract_verdict_json("") is None)
    check("None input returns None", A.extract_verdict_json(None) is None)

    tmp = tempfile.mkdtemp(prefix="tickets-")
    old_state, old_tdir, old_edir = A.STATE, A.TICKETS_DIR, A.EPICS_DIR
    A.STATE = tmp
    A.TICKETS_DIR = os.path.join(tmp, "tickets")
    A.EPICS_DIR = os.path.join(tmp, "epics")

    try:
        # --- 1. create: file-per-ticket, status draft ------------------------------------------------
        t = A.create("Fix the publix parse", "## Acceptance criteria\n- parses cleanly\n")
        check("create returns an id starting ticket:", t["id"].startswith("ticket:"), t)
        check("new ticket starts at draft", t["status"] == "draft", t)
        check("status_history seeded with the initial status",
              t["status_history"] == [dict(status="draft", at=t["created"])], t["status_history"])
        body = A.read_body(t["id"])
        check("read_body/render_markdown produces a real markdown string with description_md",
              "Acceptance criteria" in body and "parses cleanly" in body, body)
        check("get() finds it on the real (unmocked-db) store", A.get(t["id"])["id"] == t["id"])
        check("list_tickets() surfaces it", any(x["id"] == t["id"] for x in A.list_tickets()))

        # --- 2. forward-only: the rule this module exists to protect --------------------------------
        for step in ("accepted", "in_progress", "testing", "done"):
            r = A.advance_status(t["id"], step)
            check("advances cleanly to %s" % step, r["ok"] is True, r)
        check("done is a closed ticket", A.advance_status(t["id"], "in_progress")["ok"] is False)
        check("...with a specific 'closed' reason, not a generic one",
              "closed" in A.advance_status(t["id"], "in_progress")["error"])

        t2 = A.create("another ticket", "criteria")
        A.advance_status(t2["id"], "in_progress")
        back = A.advance_status(t2["id"], "accepted")
        check("stepping backward within the flow is rejected", back["ok"] is False, back)
        check("...and says which direction, not just 'no'",
              "in_progress -> accepted" in back["error"] or "backward" in back["error"], back)
        fwd = A.advance_status(t2["id"], "testing")
        check("but forward still works after a rejected backward attempt", fwd["ok"] is True, fwd)

        check("unknown status is rejected, not silently accepted",
              A.advance_status(t2["id"], "not-a-real-status")["ok"] is False)

        # --- 3. sinks: blocked/cancelled reachable from any LIVE status, not from done ---------------
        t3 = A.create("sink test", "criteria")
        check("blocked is reachable from draft", A.advance_status(t3["id"], "blocked")["ok"] is True)
        t4 = A.create("sink test 2", "criteria")
        A.advance_status(t4["id"], "accepted")
        A.advance_status(t4["id"], "in_progress")
        check("cancelled is reachable mid-flow", A.advance_status(t4["id"], "cancelled")["ok"] is True)
        t5 = A.create("closed sink test", "criteria")
        for step in ("accepted", "in_progress", "testing", "done"):
            A.advance_status(t5["id"], step)
        check("a done ticket cannot be blocked/cancelled either — it is CLOSED, not just forward-only",
              A.advance_status(t5["id"], "blocked")["ok"] is False)

        # --- 4. add_activity / append_section: the "embed reports as it goes" mechanism ---------------
        t6 = A.create("crew test", "## Acceptance criteria\ncriteria text")
        before = A.get(t6["id"])["updated"]
        ok = A.append_section(t6["id"], "Engineer report", "changed publix.py:97")
        check("append_section reports success", ok is True)
        rec6 = A.get(t6["id"])
        check("description_md is untouched, nothing clobbered",
              rec6["description_md"] == "## Acceptance criteria\ncriteria text", rec6["description_md"])
        check("activity got a new structured entry", len(rec6["activity"]) == 1, rec6["activity"])
        body6 = A.render_markdown(rec6)
        check("original body survives in rendered markdown, nothing clobbered",
              "criteria text" in body6, body6)
        check("the new section is appended, in a real heading", "## Engineer report" in body6, body6)
        check("the appended text is present verbatim", "changed publix.py:97" in body6, body6)
        check("append_section (3-arg compat wrapper) still stores verdict=None, wasn't broken by "
              "the new verdict param",
              "verdict" in rec6["activity"][0] and rec6["activity"][0]["verdict"] is None,
              rec6["activity"][0])
        idx_criteria = body6.index("criteria text")
        idx_report = body6.index("Engineer report")
        check("the report comes AFTER the original criteria, not prepended",
              idx_report > idx_criteria, (idx_report, idx_criteria))
        A.append_section(t6["id"], "Qa report", "no regressions found")
        body6b = A.render_markdown(A.get(t6["id"]))
        check("a second append doesn't overwrite the first",
              "Engineer report" in body6b and "Qa report" in body6b, body6b)
        check("second section comes after the first",
              body6b.index("Qa report") > body6b.index("Engineer report"), body6b)
        check("append_section bumps `updated`", A.get(t6["id"])["updated"] >= before)
        check("append_section on an unknown ticket returns False, not a crash",
              A.append_section("ticket:doesnotexist", "x", "y") is False)

        # usage -> cost rollup
        t6b = A.create("cost rollup test", "criteria")
        A.add_activity(t6b["id"], "engineer", "Engineer", "did stuff",
                        usage=dict(input_tokens=100, output_tokens=40))
        A.add_activity(t6b["id"], "qa", "QA", "checked stuff",
                        usage=dict(input_tokens=30, output_tokens=10))
        cost6b = A.get(t6b["id"])["cost"]
        check("usage input_tokens rolls into ticket cost total",
              cost6b["input_tokens"] == 130, cost6b)
        check("usage output_tokens rolls into ticket cost total",
              cost6b["output_tokens"] == 50, cost6b)
        check("cost.by_stage records the per-kind usage",
              cost6b["by_stage"].get("engineer", {}).get("input_tokens") == 100, cost6b)

        # verdict storage: add_activity's new keyword param, default vs explicit
        act6b0 = A.get(t6b["id"])["activity"][0]
        check("add_activity with no verdict arg stores verdict=None on the entry "
              "(key present, not omitted)",
              "verdict" in act6b0 and act6b0["verdict"] is None, act6b0)

        qa_verdict_payload = dict(criteria=[dict(text="parses cleanly", status="pass",
                                                   evidence="ok", severity=None)], verdict="pass")
        A.add_activity(t6b["id"], "qa", "QA", "final check passed", verdict=qa_verdict_payload)
        rec6b_v = A.get(t6b["id"])
        check("add_activity(verdict=...) stores the exact dict passed, verbatim",
              rec6b_v["activity"][-1]["verdict"] == qa_verdict_payload, rec6b_v["activity"][-1])

        # --- 4b. _route_receipt / add_activity(route=) / ticket_receipt: the cost-receipt layer -------
        route_full = dict(model="claude-sonnet-5", effort="high", tactics=["tdd", "subagent"],
                           task_class="engineer", burn_index=7,
                           prompt="do the thing", append_system="extra sys instructions",
                           why="because the criteria said so", thread=[dict(role="user", text="hi")])
        trimmed = A._route_receipt(route_full)
        # route_full has no filler_removed key, so it trims to None for that field — a route missing
        # the key (e.g. a crew stage, which never carries filler_removed at all) is exactly this case.
        check("_route_receipt trims a full route dict to exactly the expected keys/values",
              trimmed == dict(model="claude-sonnet-5", effort="high", tactics=["tdd", "subagent"],
                               task_class="engineer", burn_index=7, filler_removed=None), trimmed)
        check("_route_receipt result has EXACTLY A._ROUTE_FIELDS's keys, nothing more",
              set(trimmed.keys()) == set(A._ROUTE_FIELDS), trimmed.keys())

        route_caveman = dict(model="claude-haiku-4-5", effort="low", tactics=["caveman"],
                             task_class="triage", burn_index=1, filler_removed=9,
                             prompt="stripped prompt")
        trimmed_cm = A._route_receipt(route_caveman)
        check("_route_receipt carries a real filler_removed count through when the route has one",
              trimmed_cm["filler_removed"] == 9, trimmed_cm)
        for dropped in ("prompt", "append_system", "why", "thread"):
            check("_route_receipt genuinely drops '%s', not just leaves it unchecked" % dropped,
                  dropped not in trimmed, trimmed)
        check("_route_receipt(None) returns None", A._route_receipt(None) is None)
        check("_route_receipt({}) returns None", A._route_receipt({}) is None)

        t7 = A.create("route test", "criteria")
        route_payload = dict(model="claude-opus-5", effort="med", tactics=["review"],
                              task_class="qa", burn_index=3,
                              prompt="secret prompt text", why="rationale that shouldn't be stored")
        A.add_activity(t7["id"], "qa", "QA", "checked stuff", route=route_payload)
        act7 = A.get(t7["id"])["activity"][-1]
        check("add_activity(route=...) stores the TRIMMED dict on the entry, not the raw input",
              act7["route"] == dict(model="claude-opus-5", effort="med", tactics=["review"],
                                     task_class="qa", burn_index=3, filler_removed=None), act7["route"])
        check("...proving trimming happened at the storage layer: 'prompt' didn't survive",
              "prompt" not in act7["route"], act7["route"])
        check("...and 'why' didn't survive either",
              "why" not in act7["route"], act7["route"])

        A.add_activity(t7["id"], "note", "Note", "plain note, no route given")
        act7b = A.get(t7["id"])["activity"][-1]
        check("add_activity with route omitted stores the key 'route' present and None",
              "route" in act7b and act7b["route"] is None, act7b)

        # ticket_receipt: build a ticket with a route+usage stage, a usage-only stage, and a
        # route/usage-less append_section note, then check the itemized receipt
        t12 = A.create("receipt test", "criteria")
        route_engineer = dict(model="claude-sonnet-5", effort="high", tactics=["tdd"],
                               task_class="engineer", burn_index=5, filler_removed=12,
                               prompt="engineer prompt", why="engineer why")
        A.add_activity(t12["id"], "engineer", "Engineer", "did the engineering work",
                        usage=dict(input_tokens=200, output_tokens=80), route=route_engineer)
        A.add_activity(t12["id"], "qa", "QA", "qa checked it",
                        usage=dict(input_tokens=50, output_tokens=20), route=None)
        A.append_section(t12["id"], "Manual note", "just a manual note, nothing structured")

        rec12 = A.get(t12["id"])
        check("ticket has all 3 activity entries before receipting",
              len(rec12["activity"]) == 3, rec12["activity"])
        receipt12 = A.ticket_receipt(rec12)
        check("ticket_receipt skips the route-less/usage-less append_section entry: 2 stages, not 3",
              len(receipt12["stages"]) == 2, receipt12["stages"])

        stage_eng = receipt12["stages"][0]
        check("stage 1 (route+usage) role/kind carried through",
              stage_eng["role"] == "Engineer" and stage_eng["kind"] == "engineer", stage_eng)
        check("stage 1 model/effort/tactics/burn_index come from its route",
              stage_eng["model"] == "claude-sonnet-5" and stage_eng["effort"] == "high" and
              stage_eng["tactics"] == ["tdd"] and stage_eng["burn_index"] == 5, stage_eng)
        check("stage 1 input/output tokens come from its usage",
              stage_eng["input_tokens"] == 200 and stage_eng["output_tokens"] == 80, stage_eng)

        stage_qa = receipt12["stages"][1]
        check("stage 2 (usage but route=None) is INCLUDED, not skipped, since usage alone qualifies it",
              stage_qa["role"] == "QA" and stage_qa["kind"] == "qa", stage_qa)
        check("stage 2 with no route defaults model/effort/tactics/burn_index to None",
              stage_qa["model"] is None and stage_qa["effort"] is None and
              stage_qa["tactics"] is None and stage_qa["burn_index"] is None, stage_qa)
        check("stage 2 input/output tokens still come from its usage despite no route",
              stage_qa["input_tokens"] == 50 and stage_qa["output_tokens"] == 20, stage_qa)

        check("ticket_receipt input_tokens matches the ticket's own accumulated cost, read off the record",
              receipt12["input_tokens"] == rec12["cost"]["input_tokens"],
              (receipt12["input_tokens"], rec12["cost"]))
        check("ticket_receipt output_tokens matches the ticket's own accumulated cost, read off the record",
              receipt12["output_tokens"] == rec12["cost"]["output_tokens"],
              (receipt12["output_tokens"], rec12["cost"]))
        check("burn_index_total sums the stages' burn_index, treating the route-less stage's None as 0",
              receipt12["burn_index_total"] == 5, receipt12)
        check("stage 1's filler_removed comes through from its route",
              stage_eng["filler_removed"] == 12, stage_eng)
        check("stage 2 (route=None) defaults filler_removed to None too",
              stage_qa["filler_removed"] is None, stage_qa)
        check("filler_words_removed_total sums the stages' filler_removed, treating None as 0 "
              "(exact, since cavemanize()'s count is deterministic, never an estimate)",
              receipt12["filler_words_removed_total"] == 12, receipt12)

        # edge cases: no crash, sane empty-ish shape
        receipt_none = A.ticket_receipt(None)
        check("ticket_receipt(None) doesn't crash and returns an empty-ish shape",
              receipt_none == dict(stages=[], input_tokens=0, output_tokens=0, burn_index_total=0,
                                    filler_words_removed_total=0),
              receipt_none)
        receipt_empty = A.ticket_receipt({})
        check("ticket_receipt({}) (no 'activity' key at all) also returns an empty-ish shape",
              receipt_empty == dict(stages=[], input_tokens=0, output_tokens=0, burn_index_total=0,
                                     filler_words_removed_total=0),
              receipt_empty)

        # --- 5. edit_body: REPLACES, unlike append_section which only ever adds ------------------------
        t9 = A.create("edit test", "original criteria")
        before9 = A.get(t9["id"])["updated"]
        ok9 = A.edit_body(t9["id"], "revised criteria")
        check("edit_body reports success", ok9 is True)
        check("edit_body replaces description_md outright",
              A.get(t9["id"])["description_md"] == "revised criteria")
        check("...the original text is gone, not appended alongside",
              "original" not in A.get(t9["id"])["description_md"])
        check("edit_body bumps `updated` like every other mutator", A.get(t9["id"])["updated"] >= before9)
        check("edit_body on an unknown ticket returns False, not a crash",
              A.edit_body("ticket:doesnotexist", "x") is False)

        # --- 6. db= isolation: now a DICT keyed by ticket id, not a list --------------------------------
        rows = {}
        r = A.create("isolated", "body", db=rows)
        check("create(db=dict) populates the SAME dict, keyed by id",
              len(rows) == 1 and rows.get(r["id"], {}).get("id") == r["id"], rows)
        check("...and does NOT touch the real on-disk store",
              A.get(r["id"], db=None) is None, "leaked to real store")
        A.advance_status(r["id"], "accepted", db=rows)
        check("advance_status(db=dict) mutates that same dict's record",
              rows[r["id"]]["status"] == "accepted", rows)
        A.add_activity(r["id"], "note", "Note", "text", db=rows)
        check("add_activity(db=dict) also sees the isolated record (no crash, no false miss)",
              rows[r["id"]]["updated"] > 0, rows)

        # --- 7. atomic write: no leftover .tmp file after a normal save ---------------------------------
        t8 = A.create("atomicity", "v1")
        A.edit_body(t8["id"], "v2")
        check("edit_body actually replaced the content", A.get(t8["id"])["description_md"] == "v2")
        check("no leftover .tmp file after a normal create/edit",
              not os.path.exists(A._tpath(t8["id"]) + ".tmp"))

        # --- 8. set_fields: Jira-parity fields, invalid enums rejected, labels=[] clears ---------------
        t10 = A.create("fields test", "criteria")
        e10 = A.create_epic("some epic")
        ok10 = A.set_fields(t10["id"], epic_id=e10["id"], issue_type="bug", priority="high",
                             labels=["a", "b"], story_points=5)
        check("set_fields reports success", ok10 is True)
        rec10 = A.get(t10["id"])
        check("set_fields patches epic_id", rec10["epic_id"] == e10["id"], rec10)
        check("set_fields patches issue_type", rec10["issue_type"] == "bug", rec10)
        check("set_fields patches priority", rec10["priority"] == "high", rec10)
        check("set_fields patches labels", rec10["labels"] == ["a", "b"], rec10)
        check("set_fields patches story_points", rec10["story_points"] == 5, rec10)

        A.set_fields(t10["id"], issue_type="not-a-real-type", priority="not-a-real-priority")
        rec10b = A.get(t10["id"])
        check("an invalid issue_type is rejected/ignored, not corrupting the record",
              rec10b["issue_type"] == "bug", rec10b["issue_type"])
        check("an invalid priority is rejected/ignored, not corrupting the record",
              rec10b["priority"] == "high", rec10b["priority"])

        A.set_fields(t10["id"], labels=[])
        rec10c = A.get(t10["id"])
        check("an explicit empty labels=[] actually clears labels (not-provided vs provided-empty)",
              rec10c["labels"] == [], rec10c["labels"])

        check("set_fields on an unknown ticket returns False",
              A.set_fields("ticket:doesnotexist", priority="high") is False)

        # --- 9. set_pr / set_jira: partial updates only touch given fields ------------------------------
        t11 = A.create("pr/jira test", "criteria")
        A.set_pr(t11["id"], number=42, url="https://example.com/pr/42")
        rec11 = A.get(t11["id"])
        check("set_pr sets number", rec11["pr"]["number"] == 42, rec11["pr"])
        check("set_pr sets url", rec11["pr"]["url"] == "https://example.com/pr/42", rec11["pr"])
        check("set_pr leaves untouched fields alone (branch still None)",
              rec11["pr"]["branch"] is None, rec11["pr"])
        A.set_pr(t11["id"], branch="feat/x")
        rec11b = A.get(t11["id"])
        check("a second, partial set_pr call only touches the field given",
              rec11b["pr"]["branch"] == "feat/x" and rec11b["pr"]["number"] == 42, rec11b["pr"])
        check("set_pr on an unknown ticket returns False", A.set_pr("ticket:nope", number=1) is False)

        A.set_jira(t11["id"], key="ABC-1")
        rec11c = A.get(t11["id"])
        check("set_jira sets key", rec11c["jira"]["key"] == "ABC-1", rec11c["jira"])
        check("set_jira leaves url untouched when not given", rec11c["jira"]["url"] is None, rec11c["jira"])
        A.set_jira(t11["id"], url="https://jira.example.com/ABC-1")
        rec11d = A.get(t11["id"])
        check("a second, partial set_jira call only touches the field given",
              rec11d["jira"]["url"] == "https://jira.example.com/ABC-1" and
              rec11d["jira"]["key"] == "ABC-1", rec11d["jira"])
        check("set_jira on an unknown ticket returns False", A.set_jira("ticket:nope", key="X-1") is False)

        # --- 10. epics: create/list/edit/rollup ----------------------------------------------------------
        epic = A.create_epic("Rollup epic")
        check("create_epic returns an id starting epic:", epic["id"].startswith("epic:"), epic)
        check("new epic starts at status open", epic["status"] == "open", epic)

        import time as _time
        epic_old = A.create_epic("older epic")
        _time.sleep(0.01)
        A.edit_epic(epic_old["id"], title="older epic touched")
        epics_sorted = A.list_epics()
        check("list_epics sorts newest-updated first",
              epics_sorted[0]["id"] == epic_old["id"], [e["id"] for e in epics_sorted])

        ok_edit = A.edit_epic(epic["id"], title="Rollup epic v2", status="in_progress",
                               story_points_planned=13)
        check("edit_epic reports success", ok_edit is True)
        recE = A.get_epic(epic["id"])
        check("edit_epic patches title", recE["title"] == "Rollup epic v2", recE)
        check("edit_epic patches status", recE["status"] == "in_progress", recE)
        check("edit_epic patches story_points_planned", recE["story_points_planned"] == 13, recE)

        A.edit_epic(epic["id"], status="not-a-real-status")
        recE2 = A.get_epic(epic["id"])
        check("edit_epic rejects an invalid status the same way set_fields rejects invalid enums",
              recE2["status"] == "in_progress", recE2["status"])
        check("edit_epic on an unknown epic returns False", A.edit_epic("epic:nope", title="x") is False)

        ra = A.create("rollup a", "c")
        rb = A.create("rollup b", "c")
        rc = A.create("rollup c", "c")
        A.set_fields(ra["id"], epic_id=epic["id"], story_points=3)
        A.set_fields(rb["id"], epic_id=epic["id"], story_points=5)
        A.set_fields(rc["id"], epic_id=epic["id"], story_points=2)
        A.advance_status(ra["id"], "accepted")
        A.advance_status(rb["id"], "accepted")
        A.advance_status(rb["id"], "in_progress")
        roll = A.epic_rollup(epic["id"])
        check("epic_rollup ticket_count matches the tickets assigned to it",
              roll["ticket_count"] == 3, roll)
        check("epic_rollup counts-by-status is correct",
              roll["counts"] == dict(accepted=1, in_progress=1, draft=1), roll["counts"])
        check("epic_rollup story_points sums the assigned tickets",
              roll["story_points"] == 10, roll)

        # --- 11. list_tickets(epic_id=...) filter, alone and combined with status= ----------------------
        by_epic = A.list_tickets(epic_id=epic["id"])
        check("list_tickets(epic_id=) filters to only that epic's tickets",
              {x["id"] for x in by_epic} == {ra["id"], rb["id"], rc["id"]}, by_epic)
        by_epic_and_status = A.list_tickets(epic_id=epic["id"], status="accepted")
        check("list_tickets(epic_id=, status=) combines both filters",
              {x["id"] for x in by_epic_and_status} == {ra["id"]}, by_epic_and_status)

        # --- 12. jira_csv_rows / jira_csv -----------------------------------------------------------------
        jt1 = A.create("Jira export ticket one", "desc one")
        A.set_fields(jt1["id"], issue_type="story", priority="highest", labels=["urgent", "web"],
                     story_points=8, epic_id=epic["id"])
        jt2 = A.create("Jira export ticket two", "desc two")
        A.set_fields(jt2["id"], issue_type="chore", priority="lowest")  # no epic assigned

        epics_by_id = {epic["id"]: A.get_epic(epic["id"])}
        rows_out = A.jira_csv_rows([A.get(jt1["id"]), A.get(jt2["id"])], epics_by_id=epics_by_id)
        check("jira_csv_rows header has exactly 8 columns in order",
              rows_out[0] == ["Summary", "Issue Type", "Priority", "Labels", "Epic Link",
                              "Story Points", "Description", "Status"], rows_out[0])
        check("jira_csv_rows produces one data row per ticket", len(rows_out) == 3, rows_out)

        row1 = rows_out[1]
        check("Issue Type is Title-cased via _JIRA_ISSUE_TYPE ('story' -> 'Story')",
              row1[1] == A._JIRA_ISSUE_TYPE["story"] == "Story", row1)
        check("Priority is Title-cased via _JIRA_PRIORITY ('highest' -> 'Highest')",
              row1[2] == A._JIRA_PRIORITY["highest"] == "Highest", row1)
        check("Labels are space-joined", row1[3] == "urgent web", row1)
        check("Epic Link resolves to the epic's title", row1[4] == "Rollup epic v2", row1)
        check("Story Points is stringified", row1[5] == "8", row1)

        row2 = rows_out[2]
        check("a ticket with no epic assigned gets an empty Epic Link, not a crash", row2[4] == "", row2)
        check("chore maps to Task, lowest maps to Lowest",
              row2[1] == "Task" and row2[2] == "Lowest", row2)

        csv_text = A.jira_csv([A.get(jt1["id"]), A.get(jt2["id"])], epics_by_id=epics_by_id)
        parsed = list(csv.reader(io.StringIO(csv_text)))
        check("jira_csv produces valid, round-trippable CSV text (row count matches)",
              len(parsed) == len(rows_out), (len(parsed), len(rows_out)))
        check("...and column count matches too",
              all(len(r) == 8 for r in parsed), parsed)

        # --- 8. add_activity(attachment=...): evidence linkage (T-2.2) --------------------------------
        t13 = A.create("attachment test", "criteria")
        snap = dict(key="shot:abc123:1700000000", ts=1700000000, url="https://example.com/x")
        A.add_activity(t13["id"], "snapshot", "Snapshot", "https://example.com/x", attachment=snap)
        act13 = A.get(t13["id"])["activity"][-1]
        check("add_activity(attachment=...) stores the dict verbatim, no transformation",
              act13["attachment"] == snap, act13["attachment"])
        check("attachment is independent of verdict/route — both stay their own defaults",
              act13["verdict"] is None and act13["route"] is None, act13)

        A.add_activity(t13["id"], "note", "Note", "no attachment given")
        act13b = A.get(t13["id"])["activity"][-1]
        check("add_activity with attachment omitted stores the key present and None",
              "attachment" in act13b and act13b["attachment"] is None, act13b)

        diffatt = dict(a_key="shot:aaa:1", b_key="shot:bbb:2", diff_pct=12.5, url="https://example.com/x")
        A.add_activity(t13["id"], "visual_diff", "Visual diff", "12.5% changed", attachment=diffatt)
        act13c = A.get(t13["id"])["activity"][-1]
        check("a visual_diff attachment (two keys + diff_pct) round-trips exactly too",
              act13c["attachment"] == diffatt, act13c["attachment"])

        # --- 9. velocity / velocity_by_epic: story points DONE in a trailing window (T-3.2) -----------
        vdb, vedb = {}, {}
        ve = A.create_epic("Velocity Epic", db=vedb)
        v1 = A.create("v1", "crit", story_points=5, epic_id=ve["id"], db=vdb)
        v2 = A.create("v2", "crit", story_points=3, epic_id=ve["id"], db=vdb)
        v3 = A.create("v3", "crit", story_points=8, db=vdb)          # no epic
        v4 = A.create("v4", "crit", story_points=13, epic_id=ve["id"], db=vdb)   # left in draft

        for vid in (v1["id"], v2["id"], v3["id"]):
            for step in ("accepted", "in_progress", "testing", "done"):
                A.advance_status(vid, step, db=vdb)

        check("a ticket never moved to done contributes nothing to velocity",
              A.get(v4["id"], db=vdb)["status"] == "draft")

        vel = A.velocity(window_days=7, db=vdb)
        check("velocity(): tickets_done counts every ticket done within the window, any epic or none",
              vel["tickets_done"] == 3, vel)
        check("velocity(): story_points sums only the done tickets' points (5+3+8, not the draft's 13)",
              vel["story_points"] == 16, vel)
        check("velocity() carries the window_days and epic_id it was asked for",
              vel["window_days"] == 7 and vel["epic_id"] is None, vel)

        vel_epic = A.velocity(window_days=7, epic_id=ve["id"], db=vdb)
        check("velocity(epic_id=...) scopes to just that epic's done tickets (v1+v2, not v3)",
              vel_epic["tickets_done"] == 2 and vel_epic["story_points"] == 8, vel_epic)

        # backdate v3's done transition outside the window — proves the cutoff is real, not a no-op
        old_done_at = A.get(v3["id"], db=vdb)["status_history"][-1]["at"]
        A.get(v3["id"], db=vdb)["status_history"][-1]["at"] = old_done_at - int(30 * 86400 * 1000)
        vel_after_backdate = A.velocity(window_days=7, db=vdb)
        check("backdating a ticket's done transition outside the window drops it from velocity",
              vel_after_backdate["tickets_done"] == 2 and vel_after_backdate["story_points"] == 8,
              vel_after_backdate)
        vel_wide = A.velocity(window_days=60, db=vdb)
        check("...but a wide-enough window still counts it",
              vel_wide["tickets_done"] == 3 and vel_wide["story_points"] == 16, vel_wide)

        vbe = A.velocity_by_epic(window_days=7, db=vdb, epic_db=vedb)
        check("velocity_by_epic() returns one row per real epic", len(vbe) == 1, vbe)
        check("velocity_by_epic() row carries the full epic object plus its own done-count/points",
              vbe[0]["epic"]["id"] == ve["id"] and vbe[0]["tickets_done"] == 2
              and vbe[0]["story_points"] == 8, vbe[0])

        vel_empty = A.velocity(window_days=7, db={})
        check("velocity() on a ticket store with nothing done yet returns zeros, not a crash",
              vel_empty["tickets_done"] == 0 and vel_empty["story_points"] == 0
              and vel_empty["window_days"] == 7 and vel_empty["epic_id"] is None, vel_empty)

        # --- 10. parse_link_directives: fixed phrasing, not fuzzy (auto-link + manual override) -------
        d1 = A.parse_link_directives("This blocks T-9 and is blocked by T-3.")
        check("'blocks T-9' parses as ('blocks', 'T-9')", ("blocks", "T-9") in d1, d1)
        check("'blocked by T-3' parses as ('blocked_by', 'T-3'), NOT also matched by the blocks pattern",
              ("blocked_by", "T-3") in d1 and ("blocks", "T-3") not in d1, d1)
        d2 = A.parse_link_directives("Depends on T-5. Also relates to T-6 — see also T-7.")
        check("'depends on' maps to blocked_by",
              ("blocked_by", "T-5") in d2, d2)
        check("'relates to' and 'see also' both map to relates_to",
              ("relates_to", "T-6") in d2 and ("relates_to", "T-7") in d2, d2)
        d3 = A.parse_link_directives("blocks T-1 blocks T-1 blocks T-1")
        check("repeated identical directives dedupe to one entry",
              d3.count(("blocks", "T-1")) == 1, d3)
        d4 = A.parse_link_directives("this is unblocked and works fine, no directive here")
        check("plain prose with no exact phrasing produces nothing — not fuzzy-matched",
              d4 == [], d4)

        ldb = {}
        l1 = A.create("link ticket one", "first", db=ldb)
        l2 = A.create("link ticket two", "second", db=ldb)
        check("create() assigns sequential display keys", l1["key"] == "T-1" and l2["key"] == "T-2",
              (l1["key"], l2["key"]))

        # --- 11. auto-link on create(): description text alone links two real tickets ------------------
        l3 = A.create("link ticket three", "this blocks %s" % l1["key"], db=ldb)
        l1_after = A.get(l1["id"], db=ldb)
        check("auto-link on create(): the NEW ticket got a 'blocks' link to the referenced key",
              A.get(l3["id"], db=ldb)["links"] == [dict(type="blocks", ticket_id=l1["id"],
                    key=l1["key"], title=l1["title"], source="auto",
                    at=A.get(l3["id"], db=ldb)["links"][0]["at"])])
        check("...and the mirror ('blocked_by') landed on the REFERENCED ticket automatically",
              any(l["type"] == "blocked_by" and l["ticket_id"] == l3["id"] and l["source"] == "auto"
                  for l in l1_after["links"]), l1_after["links"])

        # --- 12. auto-link on add_activity()/edit_body(): not just create() ----------------------------
        A.add_activity(l2["id"], "note", "Note", "actually this relates to %s" % l1["key"], db=ldb)
        check("auto-link fires from add_activity() text too",
              any(l["type"] == "relates_to" and l["ticket_id"] == l1["id"]
                  for l in A.get(l2["id"], db=ldb)["links"]), A.get(l2["id"], db=ldb)["links"])
        A.edit_body(l2["id"], "revised criteria — blocked by %s" % l3["key"], db=ldb)
        check("auto-link fires from edit_body() text too",
              any(l["type"] == "blocked_by" and l["ticket_id"] == l3["id"]
                  for l in A.get(l2["id"], db=ldb)["links"]), A.get(l2["id"], db=ldb)["links"])

        # --- 13. manual add_link/remove_link: symmetric, self/contradiction refused --------------------
        mdb = {}
        m1 = A.create("m1", "c", db=mdb)
        m2 = A.create("m2", "c", db=mdb)
        r_self = A.add_link(m1["id"], m1["key"], "blocks", db=mdb)
        check("a ticket cannot link to itself", not r_self["ok"], r_self)
        r_bad_type = A.add_link(m1["id"], m2["key"], "nope", db=mdb)
        check("an unknown link type is refused", not r_bad_type["ok"], r_bad_type)
        r_add = A.add_link(m1["id"], m2["key"], "blocks", source="manual", db=mdb)
        check("manual add_link succeeds", r_add["ok"], r_add)
        check("manual link is tagged source=manual (distinct from auto)",
              A.get(m1["id"], db=mdb)["links"][0]["source"] == "manual",
              A.get(m1["id"], db=mdb)["links"])
        r_contra = A.add_link(m2["id"], m1["key"], "blocks", db=mdb)
        check("the direct 2-ticket contradiction (B blocks A when A already blocks B) is refused",
              not r_contra["ok"], r_contra)
        r_relates_both_ways = A.add_link(m2["id"], m1["key"], "relates_to", db=mdb)
        check("relates_to has no directional contradiction — both tickets can relate to each other",
              r_relates_both_ways["ok"], r_relates_both_ways)

        r_rm = A.remove_link(m1["id"], m2["key"], "blocks", db=mdb)
        check("remove_link succeeds and reports something was actually removed", r_rm["ok"] and r_rm["removed"], r_rm)
        check("removal cleared the link on BOTH sides",
              not A.get(m1["id"], db=mdb)["links"] or all(l["type"] != "blocks" for l in A.get(m1["id"], db=mdb)["links"])
              , A.get(m1["id"], db=mdb)["links"])
        check("...and the mirrored 'blocked_by' is gone from the other ticket too",
              all(l["type"] != "blocked_by" for l in A.get(m2["id"], db=mdb)["links"]),
              A.get(m2["id"], db=mdb)["links"])

        # --- 14. dismissal sticks: a manual removal is not silently re-added by a later auto-scan ------
        A.edit_body(m1["id"], "reminder: this blocks %s" % m2["key"], db=mdb)
        check("re-editing the body with the SAME directive text does NOT resurrect the manually "
              "removed 'blocks' link (the unrelated relates_to link from the contradiction test above "
              "is still legitimately there, so this checks the specific dismissed type, not 'any link')",
              all(not (l["type"] == "blocks" and l["ticket_id"] == m2["id"])
                  for l in A.get(m1["id"], db=mdb)["links"]),
              A.get(m1["id"], db=mdb)["links"])

        # --- 15. blocking_status + advance_status's blocking gate (with manual override) ---------------
        bdb = {}
        blocker = A.create("blocker ticket", "c", db=bdb)
        blocked = A.create("blocked ticket", "c", db=bdb)
        A.add_link(blocked["id"], blocker["key"], "blocked_by", db=bdb)
        bs = A.blocking_status(blocked["id"], db=bdb)
        check("blocking_status reports blocked while the blocker is open",
              bs["blocked"] and bs["by"][0]["id"] == blocker["id"], bs)

        adv_refused = A.advance_status(blocked["id"], "accepted", db=bdb)
        check("advance_status refuses to move a blocked ticket forward without override",
              not adv_refused["ok"] and adv_refused.get("blocked_by"), adv_refused)
        check("...and the still-draft ticket's status did not change",
              A.get(blocked["id"], db=bdb)["status"] == "draft")

        adv_override = A.advance_status(blocked["id"], "accepted", db=bdb, override_block=True)
        check("override_block=True lets the SAME move through",
              adv_override["ok"] and adv_override["status"] == "accepted", adv_override)
        hist = A.get(blocked["id"], db=bdb)["status_history"][-1]
        check("the override is recorded in status_history, not silent",
              hist.get("override_block") is True and hist.get("blocked_by"), hist)

        # once the blocker resolves, blocking_status clears and a NEW ticket's forward move needs no override
        A.advance_status(blocker["id"], "accepted", db=bdb)
        A.advance_status(blocker["id"], "in_progress", db=bdb)
        A.advance_status(blocker["id"], "testing", db=bdb)
        A.advance_status(blocker["id"], "done", db=bdb)
        bs_after = A.blocking_status(blocked["id"], db=bdb)
        check("a done blocker stops counting — blocking_status clears with no cleanup on the blocked side",
              not bs_after["blocked"], bs_after)
        adv_clear = A.advance_status(blocked["id"], "in_progress", db=bdb)
        check("advance_status now succeeds without override once the blocker is done",
              adv_clear["ok"], adv_clear)

        # find_by_key: case/whitespace-insensitive, garbage-safe
        check("find_by_key resolves lowercase/whitespace variants",
              A.find_by_key(" t-1 ", db=ldb) is not None
              and A.find_by_key(" t-1 ", db=ldb)["id"] == l1["id"])
        check("find_by_key returns None for garbage, never raises",
              A.find_by_key("not-a-key", db=ldb) is None and A.find_by_key("", db=ldb) is None
              and A.find_by_key(None, db=ldb) is None)

    finally:
        A.STATE, A.TICKETS_DIR, A.EPICS_DIR = old_state, old_tdir, old_edir

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
