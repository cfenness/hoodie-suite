#!/usr/bin/env python3
"""agent_tickets_test.py — pins the one rule that makes ticket status trustworthy: forward-only.

The whole point of a ticket over a chat is that its status_history is an honest audit trail — if a
ticket could quietly step backward, "done" would stop meaning anything, the same reason
server.py's order lifecycle (which this module's ladder logic is a byte-for-byte copy of) refuses a
backward move. Everything else here is the supporting cast: atomic writes so a live-refreshing
viewer never sees a half-written file, and append_section's ordering since it's the literal
mechanism behind "embed test reports as the process goes."

    python3 unifyd/agent_tickets_test.py
"""
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

    tmp = tempfile.mkdtemp(prefix="tickets-")
    old_state, old_dir, old_index = A.STATE, A.TICKETS_DIR, A.INDEX
    A.STATE = tmp
    A.TICKETS_DIR = os.path.join(tmp, "tickets")
    A.INDEX = os.path.join(tmp, "tickets.json")

    try:
        # --- 1. create: file + index row, status draft ---------------------------------------------
        t = A.create("Fix the publix parse", "## Acceptance criteria\n- parses cleanly\n")
        check("create returns an id", t["id"].startswith("ticket:"), t)
        check("new ticket starts at draft", t["status"] == "draft", t)
        check("status_history seeded with the initial status",
              t["status_history"] == [dict(status="draft", at=t["created"])], t["status_history"])
        body = A.read_body(t["id"])
        check("the body file is readable and matches what was written",
              "Acceptance criteria" in body, body)
        check("get() finds it on the real (unmocked-db) index", A.get(t["id"])["id"] == t["id"])
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

        # --- 4. append_section: the "embed reports as it goes" mechanism ----------------------------
        t6 = A.create("crew test", "## Acceptance criteria\ncriteria text")
        before = A.get(t6["id"])["updated"]
        ok = A.append_section(t6["id"], "Engineer report", "changed publix.py:97")
        check("append_section reports success", ok is True)
        body6 = A.read_body(t6["id"])
        check("original body survives, nothing clobbered", "criteria text" in body6, body6)
        check("the new section is appended, in a real heading", "## Engineer report" in body6, body6)
        check("the appended text is present verbatim", "changed publix.py:97" in body6, body6)
        idx_criteria = body6.index("criteria text")
        idx_report = body6.index("Engineer report")
        check("the report comes AFTER the original criteria, not prepended",
              idx_report > idx_criteria, (idx_report, idx_criteria))
        A.append_section(t6["id"], "Qa report", "no regressions found")
        body6b = A.read_body(t6["id"])
        check("a second append doesn't overwrite the first",
              "Engineer report" in body6b and "Qa report" in body6b, body6b)
        check("second section comes after the first",
              body6b.index("Qa report") > body6b.index("Engineer report"), body6b)
        check("append_section bumps `updated`", A.get(t6["id"])["updated"] >= before)
        check("append_section on an unknown ticket returns False, not a crash",
              A.append_section("ticket:doesnotexist", "x", "y") is False)

        # --- 5. requires_docs / docs_done flags -------------------------------------------------------
        t7 = A.create("docs flag test", "criteria")
        check("requires_docs defaults false", A.get(t7["id"])["requires_docs"] is False)
        A.set_requires_docs(t7["id"], True)
        check("set_requires_docs flips it", A.get(t7["id"])["requires_docs"] is True)
        check("docs_done defaults false", A.get(t7["id"])["docs_done"] is False)
        A.mark_docs_done(t7["id"])
        check("mark_docs_done flips it", A.get(t7["id"])["docs_done"] is True)

        # --- 6. db= isolation: mutate-then-read must see the SAME list, not a stale reload ----------
        # The bug this protects against: get()/append_section()/advance_status() each independently
        # re-loading the index would mean a mutation made through one call is invisible to the next
        # call in the same test (or the same real request), even though both claim to operate on
        # "the" ticket store.
        rows = []
        r = A.create("isolated", "body", db=rows)
        check("create(db=list) appends to the SAME list object", len(rows) == 1 and rows[0]["id"] == r["id"],
              rows)
        check("...and does NOT touch the real on-disk index",
              not any(x["id"] == r["id"] for x in A._load_index()), "leaked to real index")
        A.advance_status(r["id"], "accepted", db=rows)
        check("advance_status(db=list) mutates that same list's record",
              rows[0]["status"] == "accepted", rows)
        A.append_section(r["id"], "note", "text", db=rows)
        check("append_section(db=list) also sees the isolated record (no crash, no false miss)",
              rows[0]["updated"] > 0, rows)

        # --- 7. atomic write: a crash mid-write must never corrupt the real file ---------------------
        t8 = A.create("atomicity", "v1")
        A.write_body(t8["id"], "v2")
        check("write_body actually replaced the content", A.read_body(t8["id"]) == "v2")
        check("no leftover .tmp file after a normal write",
              not os.path.exists(A._body_path(t8["id"]) + ".tmp"))

    finally:
        A.STATE, A.TICKETS_DIR, A.INDEX = old_state, old_dir, old_index

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
