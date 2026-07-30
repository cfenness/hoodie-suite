#!/usr/bin/env python3
"""agent_roles_test.py — pins the crew rules, above all that a checker is never weaker than the author.

The rule this exists to protect: haiku reviewing opus output cannot find what it could not have
written, so it returns a confident approval that means nothing. That is worse than skipping review,
because now there's a green check on the work — the same "don't trust green" failure the rest of this
codebase is built to avoid. It is also the easiest rule to break by accident, since the cheap thing to
write is a flat table of role -> model.

    python3 unifyd/agent_roles_test.py
"""
import os
import re
import sys

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
    print("agent_roles — a crew, not one model wearing four hats")
    import agent_roles as A
    import agent_router as R

    # --- 1. THE GUARANTEE: no checker below the author ------------------------------------------
    # `master` routes the author to opus, while QA's role default is sonnet. QA must be lifted.
    c = A.crew_for("dedup the dim_item upc matches")
    author = c["route"]["model"]
    check("author tier comes from the router", author == "opus", author)
    by = {s["role"]: s for s in c["stages"]}
    for role in (A.QA, A.REVIEWER):
        ai, ri = R.LADDER.index(author), R.LADDER.index(by[role]["model"])
        check("%s is never below the author (%s vs %s)" % (role, by[role]["model"], author),
              ri >= ai, by[role])
    check("the QA lift is explained, not silent",
          any("floored" in w for w in c["why"]), c["why"])
    check("the lift is recorded on the stage too", "floored" in by[A.QA]["note"], by[A.QA]["note"])

    # And it must hold at the TOP of the ladder, where there is nothing above to lift to.
    d = A.crew_for("design the architecture for a new ordering app")
    check("design routes the author to fable", d["route"]["model"] == "fable", d["route"]["model"])
    dby = {s["role"]: s for s in d["stages"]}
    for role in (A.QA, A.REVIEWER):
        check("%s is lifted to fable when the author is fable" % role,
              dby[role]["model"] == "fable", dby[role]["model"])

    # The reverse must NOT happen: a cheap author must not drag the reviewer down below its default.
    t = A.crew_for("quick check on something", cls="triage")
    tby = {s["role"]: s for s in t["stages"]}
    check("a haiku author does not drag the reviewer down",
          R.LADDER.index(tby[A.REVIEWER]["model"]) >= R.LADDER.index("opus"),
          tby[A.REVIEWER]["model"])

    # --- 2. the engineer inherits the route, it does not re-decide ------------------------------
    check("engineer model == the routed model", by[A.ENGINEER]["model"] == author, by[A.ENGINEER])
    check("engineer effort == the routed effort",
          by[A.ENGINEER]["effort"] == c["route"]["effort"], by[A.ENGINEER])
    check("engineer note says it inherits", "inherit" in by[A.ENGINEER]["note"], by[A.ENGINEER])

    # --- 3. is a crew even worth it? -------------------------------------------------------------
    for cls in ("master", "ops", "review", "design", "scrape"):
        ok, why = A.should_crew(cls)
        check("crew recommended for %s" % cls, ok is True, why)
    for cls in ("triage", "docs"):
        ok, why = A.should_crew(cls)
        check("crew NOT recommended for %s (cheaper to re-run)" % cls, ok is False, why)
    check("the recommendation carries a reason", bool(A.should_crew("triage")[1]))

    # A crew on a lookup is absurdly expensive relative to the mistake — the number must show that.
    cheap = A.crew_for("quick check", cls="triage")
    check("crew-vs-solo multiple is reported", cheap["multiple"] is not None, cheap["multiple"])
    check("a crew on triage is >10x the solo route", cheap["multiple"] > 10, cheap["multiple"])
    check("...and is flagged not-recommended", cheap["recommended"] is False)
    heavy = A.crew_for("dedup upc", cls="master")
    check("a crew on master is a modest multiple, not absurd",
          2 <= heavy["multiple"] <= 6, heavy["multiple"])

    # --- 4. budget pressure trims the advisory role, never the last line of defence -------------
    hp = A.crew_for("dedup upc", cls="master", budget_pressure=0.95)
    hby = {s["role"]: s for s in hp["stages"]}
    check("PM is demoted under pressure", "demoted" in hby[A.PM]["note"], hby[A.PM]["note"])
    check("the REVIEWER is NOT demoted under pressure — it's what you kept the crew for",
          "demoted" not in hby[A.REVIEWER]["note"], hby[A.REVIEWER]["note"])
    check("QA still meets the author floor under pressure",
          R.LADDER.index(hby[A.QA]["model"]) >= R.LADDER.index(hp["route"]["model"]), hby[A.QA])

    # --- 5. shape / safety ----------------------------------------------------------------------
    check("all four stages by default", [s["role"] for s in c["stages"]] == A.STAGES,
          [s["role"] for s in c["stages"]])
    sub = A.crew_for("dedup upc", cls="master", roles=[A.ENGINEER, A.REVIEWER])
    check("a subset crew is allowed", [s["role"] for s in sub["stages"]] == ["engineer", "reviewer"])
    try:
        A.crew_for("x", roles=["architect"])
        check("unknown role is rejected", False, "no error")
    except ValueError:
        check("unknown role is rejected", True)
    for s in c["stages"]:
        for f in ("role", "label", "model", "effort", "system", "produces", "note", "burn_index"):
            check("stage %s exposes %s" % (s["role"], f), f in s, sorted(s))
        break   # shape is uniform; one stage is enough to pin it

    # Non-mutating roles must have NO write tools — independence enforced mechanically, not requested.
    for role in (A.PM, A.QA, A.REVIEWER):
        tools = by[role]["tools"] or []
        check("%s has no write/edit tool" % role,
              not any(t in ("Write", "Edit", "NotebookEdit") for t in tools), tools)
    check("QA can actually run things (else it cannot test)",
          any("python3" in t or "pytest" in t for t in (by[A.QA]["tools"] or [])), by[A.QA]["tools"])
    check("reviewer can read git history",
          any("git" in t for t in (by[A.REVIEWER]["tools"] or [])), by[A.REVIEWER]["tools"])
    for role in (A.PM, A.QA, A.REVIEWER):
        check("%s brief states it did not write the work" % role,
              "did not write" in A.ROLES[role]["system"] or "Do NOT implement" in A.ROLES[role]["system"],
              A.ROLES[role]["system"][:60])

    # --- 6. generated agent defs must not drift from ROLES --------------------------------------
    # Two copies of a role brief drift, and the drift is invisible: you'd review with a prompt you no
    # longer think you have. So the files are generated, and this asserts they still match.
    import tempfile
    out = tempfile.mkdtemp(prefix="roles-agents-")
    paths = A.write_agent_defs(outdir=out)
    check("one definition per checking role", len(paths) == 3, paths)
    names = {os.path.basename(p) for p in paths}
    check("engineer gets NO definition (its model is the task's route)",
          "hoodie-engineer.md" not in names, names)
    for p in paths:
        txt = open(p, encoding="utf-8").read()
        role = os.path.basename(p)[len("hoodie-"):-3]
        spec = A.ROLES[role]
        check("%s declares its model" % role,
              re.search(r"^model:\s*%s$" % re.escape(spec["tier"]), txt, re.M) is not None, role)
        check("%s declares its effort" % role,
              re.search(r"^effort:\s*%s$" % re.escape(spec["effort"]), txt, re.M) is not None, role)
        check("%s carries the brief verbatim" % role, spec["system"].split("\n")[0] in txt, role)
        check("%s is marked generated" % role, "GENERATED from" in txt, role)
        check("%s declares no write tool" % role,
              not re.search(r"^tools:.*\b(Write|Edit)\b", txt, re.M), role)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
