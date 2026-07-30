#!/usr/bin/env python3
"""agent_router_test.py — pins the routing policy so it can be RETUNED without being broken.

The router is a set of judgment calls. That's fine — but a judgment call with no test is a judgment
call that silently drifts. Each check below states the reasoning it protects, so when you change a
rule from the Cockpit and a check fails, the failure tells you which tradeoff you just inverted and
you can decide deliberately instead of discovering it in a month of quietly-wrong routes.

The asymmetries these lock down (each one costs real money/time in the wrong direction):
  1. `ops` never cheap-routes — short, but hard to reverse.
  2. `new` requires BOTH low overlap AND no context dependency — a wrong `new` loses information.
  3. caveman never mangles identifiers — code is the information, not the filler around it.
  4. budget pressure demotes at most one rung and never below the class floor.

    python3 unifyd/agent_router_test.py
"""
import os
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
    print("agent_router — routing policy")
    import agent_router as R

    # --- 1. classification ------------------------------------------------------------------------
    for text, want in (
        ("deploy the fix to fly", "ops"),
        ("re-pin the dispatcher", "ops"),
        ("dedup the dim_item upc matches", "master"),
        ("review this diff for bugs", "review"),
        ("the publix selector drifted, fix the parse", "scrape"),
        ("how many stores does the warehouse cover", "analysis"),
        ("the sidebar layout is stacking on the collect page", "surface"),
        ("update CLAUDE.md for the new rule", "docs"),
        ("is the abc-fws scraper still enabled", "triage"),
    ):
        got, _ = R.classify(text)
        check("classify %-46r -> %s" % (text[:44], want), got == want, "got %s" % got)

    check("unknown text falls to the CHEAP lane, not the expensive one",
          R.classify("hey")[0] == "triage")

    # --- 2. model ladder: the expensive direction is the safe one ----------------------------------
    # `ops` is SHORT, so a naive horizon-only rule would route it to haiku. It must not: a wrong
    # deploy is the most expensive mistake in this repo (it silently reverts production).
    ops = R.route("deploy to fly", cls="ops")
    check("ops does not cheap-route despite short horizon", ops["model"] == "opus", ops["model"])
    check("triage DOES cheap-route", R.route("quick check", cls="triage")["model"] == "haiku")
    check("long-horizon scrape gets top-of-ladder + xhigh",
          (lambda r: r["model"] == "opus" and r["effort"] == "xhigh")(R.route("x", cls="scrape")))
    check("tool-heavy never runs at low/medium effort",
          R.route("x", cls="scrape")["effort"] in ("high", "xhigh", "max"))

    # --- 3. budget pressure: demote at most one rung, never below the floor ------------------------
    hi = R.route("dedup upc", cls="master", budget_pressure=0.95)
    check("max-correctness demotes at most one rung under pressure", hi["model"] == "sonnet",
          hi["model"])
    check("...and never below the class floor (not haiku)", hi["model"] != "haiku", hi["model"])
    check("no pressure -> no demotion",
          R.route("dedup upc", cls="master", budget_pressure=0.0)["model"] == "opus")
    check("pressure below the threshold does nothing",
          R.route("dedup upc", cls="master", budget_pressure=0.7)["model"] == "opus")
    check("demotion is recorded in `why`, not silent",
          any("demoted" in w for w in hi["why"]), hi["why"])

    # --- 4. caveman: strips filler, NEVER identifiers ----------------------------------------------
    out, removed = R.cavemanize(
        "Could you please just take a look at unifyd/publix.py and maybe fix write_accumulate? Thanks!")
    check("caveman removed filler words", removed >= 4, "removed=%d" % removed)
    for ident in ("unifyd/publix.py", "write_accumulate"):
        check("caveman preserved identifier %s" % ident, ident in out, out)
    for filler in ("please", "maybe", "Thanks"):
        check("caveman stripped %r" % filler, filler.lower() not in out.lower(), out)

    out2, _ = R.cavemanize("please run `git status` and check apps/collect.html line 42")
    check("caveman preserves a backticked span", "`git status`" in out2, out2)
    check("caveman preserves a path", "apps/collect.html" in out2, out2)
    check("caveman preserves a bare number token", "42" in out2, out2)
    check("caveman on empty input is safe", R.cavemanize("")[0] == "")
    check("caveman on None is safe", R.cavemanize(None)[0] == "")

    # --- 5. caveman vs spec are mutually exclusive -------------------------------------------------
    # They pull opposite ways by design: one strips context, the other insists on all of it.
    r = R.route("build the thing", cls="scrape", tactics=["caveman", "spec"])
    check("caveman+spec conflict is resolved, not both applied",
          not ("caveman" in r["tactics"] and "spec" in r["tactics"]), r["tactics"])
    check("long horizon keeps spec (re-derivation costs more than tokens)",
          "spec" in r["tactics"], r["tactics"])
    r2 = R.route("quick look", cls="triage", tactics=["caveman", "spec"])
    check("short horizon keeps caveman", "caveman" in r2["tactics"], r2["tactics"])

    # --- 6. thread lifecycle: the asymmetry is the point -------------------------------------------
    th = dict(id="abc", subject="publix scraper selector drift and the parse fix")

    a = R.thread_action("more on the publix selector parse", th, 0.2, carries_context=True)
    check("same subject + room -> continue", a["action"] == "continue", a)

    a = R.thread_action("unrelated: census tract geocoding", th, 0.2, carries_context=False)
    check("subject change + context NOT needed -> new", a["action"] == "new", a)

    # THE load-bearing one: same signals, but the context matters -> must NOT drop it.
    a = R.thread_action("unrelated: census tract geocoding", th, 0.2, carries_context=True)
    check("subject change but context IS needed -> fork, never new", a["action"] == "fork", a)

    a = R.thread_action("publix selector again", th, 0.9, carries_context=True)
    check("context full + needed -> fork (keep the info, fresh id)", a["action"] == "fork", a)

    a = R.thread_action("publix selector again", th, 0.9, carries_context=False)
    check("context full + not needed -> new (shed it)", a["action"] == "new", a)

    a = R.thread_action("anything", th, 0.2, branching=True)
    check("explicit branch -> fork", a["action"] == "fork", a)

    a = R.thread_action("anything", None, 0.0)
    check("no live thread -> new", a["action"] == "new", a)

    # Unknown dependency must behave like the SAFE case (True), not the cheap one.
    a = R.thread_action("totally different subject entirely", th, 0.2, carries_context=None)
    check("unknown context-dependency is treated as load-bearing (fork, not new)",
          a["action"] == "fork", a)

    check("topic_overlap is 1.0 for identical text",
          R.topic_overlap("publix selector", "publix selector") == 1.0)
    check("topic_overlap is 0.0 for disjoint text",
          R.topic_overlap("publix selector", "census tract geocode") == 0.0)
    check("topic_overlap handles empty input", R.topic_overlap("", "abc") == 0.0)

    # --- 7. every route is inspectable -------------------------------------------------------------
    r = R.route("fix the kroger parse", cls="auto")
    for field in ("task_class", "model", "effort", "tactics", "prompt", "append_system",
                  "thread", "why", "burn_index"):
        check("route exposes %s" % field, field in r, sorted(r))
    check("route carries a non-empty why (a reason you can read)", len(r["why"]) >= 2, r["why"])
    check("burn_index orders opus above haiku",
          R.route("x", cls="ops")["burn_index"] > R.route("x", cls="triage")["burn_index"])

    # --- 8. bad input fails loudly ----------------------------------------------------------------
    for bad, kw in ((dict(cls="nope"), "class"), (dict(tactics=["nope"]), "tactic")):
        try:
            R.route("x", **bad)
            check("rejects unknown %s" % kw, False, "no error raised")
        except ValueError:
            check("rejects unknown %s" % kw, True)

    # Every class must have a tactic set, or a route would silently get none.
    for cls in R.CLASSES:
        check("class %s has default tactics" % cls, bool(R._CLASS_TACTICS.get(cls)))
    for cls in R._CLASS_TACTICS:
        check("tactic-set class %s is a real class" % cls, cls in R.CLASSES)
    for name, t in R.TACTICS.items():
        check("tactic %s has a label and desc" % name, bool(t.get("label")) and bool(t.get("desc")))

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
