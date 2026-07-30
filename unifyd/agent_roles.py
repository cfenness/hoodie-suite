#!/usr/bin/env python3
"""agent_roles.py — a CREW instead of one model doing four jobs badly.

The idea: a piece of work passes through roles — a PM who defines what "done" means, an engineer who
builds it, QA who tries to break it, and a lead engineer who reviews. Each role gets the model that
suits the role, not one model asked to switch hats mid-turn.

WHY ROLES BEAT ONE BIG PROMPT — and it isn't about capability:
A single model asked to build a thing and then review it is grading its own homework. It shares every
assumption it just made, so the errors it is most likely to miss are exactly the ones it introduced.
Separating the roles into separate turns with separate context is what makes the check real; the model
tier is secondary to the independence.

THE ONE HARD CONSTRAINT — a reviewer is never weaker than the author:
Haiku reviewing Opus output is theatre. It cannot find what it could not have written, so it produces
a confident approval that means nothing — worse than no review, because now there's a green check on
it. `crew_for()` therefore FLOORS every checking role at the author's tier. This mirrors the API's own
advisor-tool rule (the advisor model must be >= the executor model) and it is the reason this module
can't just be a table of role->model.

COST DISCIPLINE (subscription burn, per [[hoodie-cockpit]]):
Running a 4-role crew on a triage lookup would be absurd — most work does not need a crew at all. So:
  • the crew is OPT-IN per task, and `should_crew()` says when it's worth it
  • the engineer inherits the task's routed model from agent_router — roles do not re-decide it
  • PM and QA sit a tier BELOW the author where that's defensible; only the reviewer is floored up
  • `estimate_burn()` prices the crew against the solo route so the trade is visible before it runs

    python3 unifyd/agent_roles.py --task "rebuild the dispatcher on a generic registry"
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_router as R

PM, ENGINEER, QA, REVIEWER = "pm", "engineer", "qa", "reviewer"
STAGES = [PM, ENGINEER, QA, REVIEWER]

# Each role's brief is written to a standard already load-bearing in this repo: evidence over
# assertion, deterministic separated from inferred, no silent degrades. They are appended via
# `--append-system-prompt`, so they add to CLAUDE.md rather than replacing it.
ROLES = {
    PM: dict(
        label="PM",
        # Framing is short but decides everything downstream — a vague "done" makes all three later
        # roles argue about the target instead of hitting it. Worth a real tier, not the cheapest.
        tier="sonnet", effort="high", tools="read",
        produces="a gradeable definition of done",
        system=(
            "You are the PM for this task. Do NOT implement anything.\n"
            "Produce: (1) the outcome in one sentence, (2) a numbered list of acceptance criteria "
            "that are independently checkable — each one a thing someone could verify true or false "
            "without judgement, (3) explicitly out of scope, (4) the risk that would make this work "
            "worthless if it went wrong.\n"
            "Criteria like 'works well' or 'is clean' are not acceptance criteria. 'The parse returns "
            "13,900 rows against the live sitemap' is. If the request is ambiguous in a way that "
            "changes the work, say so in one line rather than picking silently."),
    ),
    ENGINEER: dict(
        label="Engineer",
        # No tier here on purpose: the engineer IS the routed task, so it inherits whatever
        # agent_router already decided for the work itself. A second opinion about the author's model
        # would just be two policies disagreeing.
        tier=None, effort=None, tools=None,
        produces="the change",
        system=(
            "You are the engineer. Build exactly what the acceptance criteria describe, at that "
            "scope — no adjacent tidying, no abstractions for cases that cannot happen.\n"
            "Report outcomes faithfully: if a test fails, say so with the output; if you skipped a "
            "criterion, say which and why. Never report completion for work that is not done."),
    ),
    QA: dict(
        label="QA",
        # QA is about RECALL, and recall is where a cheap model actually hurts — it finds fewer real
        # failures and you cannot tell the difference from a clean run. Floored to the author's tier.
        tier="sonnet", effort="high", tools="read+run",
        produces="a pass/fail per criterion, with evidence",
        system=(
            "You are QA. You did not write this code and you are not here to approve it.\n"
            "Walk the acceptance criteria one at a time and establish pass or fail for each, with the "
            "actual command output as evidence. A criterion you could not test is UNTESTED, not "
            "passed — say so explicitly.\n"
            "Report every failure you find, including ones you are unsure about or judge minor. Do "
            "NOT filter for severity or confidence; a separate step does that. Coverage is your job. "
            "For each finding give the evidence, your confidence, and an estimated severity.\n"
            "Actively try to break it: the empty input, the duplicate, the stale cache, the second "
            "run, the concurrent run. A guard that degrades quietly is indistinguishable from success "
            "— look for exactly that."),
    ),
    REVIEWER: dict(
        label="Lead engineer",
        # The adversarial check. Floored to >= the author's tier by crew_for(); see the module note.
        tier="opus", effort="high", tools="read",
        produces="a verdict with named blockers",
        system=(
            "You are the lead engineer reviewing this work. You did not write it.\n"
            "Judge it against the acceptance criteria, then against what the criteria missed. Cite "
            "file:line for every claim — a review comment without a location is an opinion.\n"
            "Separate what you verified from what you inferred, and label each. Do not restate the "
            "diff back; say what is wrong, what is risky, and what is fine.\n"
            "Give a verdict: ship, ship-with-followups, or blocked — and if blocked, the specific "
            "thing that must change. Do not approve work you could not verify; say what you could not "
            "check instead."),
    ),
}

# Tool profiles per role, resolved by agent_exec. Roles that must not mutate get no write tools —
# that's a mechanical guarantee of independence rather than a request in a prompt.
ROLE_TOOLS = {
    "read":     ["Read", "Grep", "Glob", "Bash(git log:*)", "Bash(git show:*)",
                 "Bash(git diff:*)", "Bash(git status)", "WebSearch", "WebFetch"],
    "read+run": ["Read", "Grep", "Glob", "Bash(git log:*)", "Bash(git show:*)",
                 "Bash(git diff:*)", "Bash(git status)", "Bash(python3:*)", "Bash(pytest:*)",
                 "WebSearch", "WebFetch"],
}


def _at_least(tier, floor):
    """Return whichever tier is higher on the ladder. The whole reviewer guarantee in one function."""
    li = R.LADDER
    if tier not in li:
        return floor
    if floor not in li:
        return tier
    return tier if li.index(tier) >= li.index(floor) else floor


def should_crew(task_class, cls_spec=None):
    """Is a crew worth it for this class? Returns (bool, why).

    Most work does not need four roles, and running them anyway is the kind of overhead that makes a
    good idea get switched off. The test is whether an undetected error is expensive: `max`-correctness
    lanes and long-horizon builds earn a crew; a lookup or a docs tweak does not, because the cost of
    being wrong is one re-run."""
    spec = cls_spec or R.CLASSES.get(task_class) or {}
    if spec.get("correctness") == "max":
        return True, "%s is max-correctness — an undetected error here is expensive" % task_class
    if spec.get("horizon") == "long":
        return True, "%s is long-horizon — errors compound over the run" % task_class
    return False, ("%s is short/recoverable — a crew costs more than the mistake it would catch"
                   % task_class)


def crew_for(task, cls=None, budget_pressure=0.0, roles=None, **route_kw):
    """Build the crew for one task. Returns the route plus an ordered list of concrete stages.

    The engineer's model comes from agent_router (one policy, not two). Every CHECKING role is then
    floored at the engineer's tier, so the reviewer can never be weaker than the author."""
    base = R.route(task, cls=cls, budget_pressure=budget_pressure, **route_kw)
    author_tier = base["model"]
    want = list(roles) if roles else STAGES
    for r in want:
        if r not in ROLES:
            raise ValueError("unknown role %r (known: %s)" % (r, ", ".join(STAGES)))

    stages, why = [], []
    for name in want:
        spec = ROLES[name]
        if name == ENGINEER:
            tier, effort = author_tier, base["effort"]
            note = "inherits the task's own route"
        else:
            tier = spec["tier"]
            floored = _at_least(tier, author_tier) if name in (QA, REVIEWER) else tier
            if floored != tier:
                why.append("%s floored %s -> %s: a checker below the author's tier cannot find what "
                           "the author could not have written" % (name, tier, floored))
                note = "floored up to the author's tier (%s)" % author_tier
            else:
                note = "role default"
            tier = floored
            effort = spec["effort"]
            # Budget pressure trims the ADVISORY roles only. The reviewer is the last line before a
            # bad change lands, so trimming it is trimming the thing you kept the crew for.
            if budget_pressure >= 0.8 and name in (PM,) and R.LADDER.index(tier) > 0:
                tier = R.LADDER[R.LADDER.index(tier) - 1]
                note += "; demoted under budget pressure"
        stages.append(dict(
            role=name, label=spec["label"], model=tier, effort=effort,
            tools=ROLE_TOOLS.get(spec["tools"]) if spec["tools"] else None,
            system=spec["system"], produces=spec["produces"], note=note,
            burn_index=R.MODELS[tier]["weight"] * (1 + R.EFFORTS.index(effort) * 0.5),
        ))

    worth, wwhy = should_crew(base["task_class"])
    solo = base["burn_index"]
    crew = sum(s["burn_index"] for s in stages)
    return dict(task=task, route=base, stages=stages, why=why,
                recommended=worth, recommendation_why=wwhy,
                solo_burn=round(solo, 1), crew_burn=round(crew, 1),
                multiple=round(crew / solo, 1) if solo else None)


def estimate_burn(task, cls=None, **kw):
    c = crew_for(task, cls=cls, **kw)
    return dict(solo=c["solo_burn"], crew=c["crew_burn"], multiple=c["multiple"],
                recommended=c["recommended"], why=c["recommendation_why"])


def _stage_prompt(task, role, prior):
    """What each stage actually reads. Pure and separately testable — the threading between stages is
    the one part of `run_crew()` a dry run can never exercise (a dry run never produces a `result` for
    any stage, so `prior` is always empty there).

    Only PM's and the ENGINEER's own text gets relayed forward; QA and the reviewer are pointed at the
    real working tree (they have `Bash(git diff:*)` etc.) rather than trusting a summary, because a
    report of what changed is not the same evidence as the change itself — this codebase's own
    verify-don't-guess rule applies here as much as anywhere else it's stated."""
    if role == PM:
        return task
    if role == ENGINEER:
        pm_out = prior.get(PM)
        if not pm_out:
            return task
        return ("Definition of done, from the PM review below — build to satisfy it, not just the "
                "one-line task:\n%s\n\nTask: %s" % (pm_out, task))
    if role == QA:
        parts = []
        if prior.get(PM):
            parts.append("Definition of done:\n%s" % prior[PM])
        if prior.get(ENGINEER):
            parts.append("The engineer reports:\n%s" % prior[ENGINEER])
        header = "\n\n".join(parts) + "\n\n" if parts else ""
        return ("%sTry to break what was just built for: %s\n\nDo not trust the report above — verify "
                "against the actual working tree (git diff, git log, run the tests) and say what you "
                "found, not what you were told." % (header, task))
    if role == REVIEWER:
        header = "QA found:\n%s\n\n" % prior[QA] if prior.get(QA) else ""
        return ("%sLead review of the changes made in this working tree for: %s\n\nConfirm or flag "
                "blockers, verified against git diff / git log yourself, not against what QA or the "
                "engineer reported." % (header, task))
    return task


def run_crew(task, cls=None, roles=None, budget_pressure=0.0, cwd=None, dry_run=True, timeout=900,
             **route_kw):
    """The scriptable counterpart to `crew_for()`'s plan: actually dispatch each stage, in order, in
    the given working tree. This is what turns "hand-run each stage yourself" into one call.

    Each stage is a FRESH session — separate context per role is what makes the check real (see the
    module docstring's whole argument against one model wearing four hats), so nothing here ever
    resumes or forks a prior stage's thread. A stage's model/effort/tools come from `crew_for()`'s
    plan, never agent_router's class-based policy, which is why this goes through agent_exec.dispatch()
    (an already-routed task) rather than agent_exec.run() (which would re-derive them from a class).

    Stops at the first stage that errors: a PM stage that failed to run leaves nothing for the
    engineer to build against, and an engineer stage that failed leaves nothing for QA to inspect —
    continuing past a broken stage would silently skip the role you were relying on.

    dry_run=True is the default for the same reason it is everywhere else in this module: a crew is
    3-4 dispatches, not one, so an accidental real run costs proportionally more."""
    import agent_exec as X
    plan = crew_for(task, cls=cls, roles=roles, budget_pressure=budget_pressure, **route_kw)
    results, prior = [], {}
    for stage in plan["stages"]:
        role = stage["role"]
        prompt = _stage_prompt(task, role, prior)
        r = dict(
            task=prompt, task_class="crew:%s" % role, model=stage["model"],
            model_weight=R.MODELS[stage["model"]]["weight"], effort=stage["effort"], tactics=[],
            prompt=prompt, filler_removed=0, append_system=stage["system"] or "",
            thread=R.thread_action(prompt), why=[stage["note"]], burn_index=stage["burn_index"],
        )
        rec = X.dispatch(r, cwd=cwd, dry_run=dry_run, timeout=timeout, allowed_tools=stage["tools"])
        rec["role"] = role
        results.append(rec)
        if rec.get("error"):
            break
        prior[role] = rec.get("result")
    ok = len(results) == len(plan["stages"]) and not any(x.get("error") for x in results)
    return dict(plan=plan, results=results, ok=ok)


# ── AGENT DEFINITIONS — the same roles, usable agentically ───────────────────────────────────────
# ORCHESTRATED vs AGENTIC is not either/or, and writing the roles as `.claude/agents/*.md` gets both:
#
#   orchestrated  `claude -p --agent hoodie-qa …`   — the Cockpit runs each stage itself. Deterministic
#                 order, genuinely separate context per role, and the cheap roles actually run cheap.
#   agentic       a lead agent delegates to `hoodie-qa` by name — it can loop (QA fails -> fix -> QA
#                 again) and fan out without any loop code here.
#
# The reason to write DEFINITIONS rather than pick one style: a plain agentic run loses the whole point
# of this module. Subagents inherit the parent's model unless their definition overrides it, so
# "assign a model to QA" quietly becomes "QA runs on whatever the lead is running". A definition file
# carries its own `model:`, so the per-role assignment survives either invocation style.
#
# Generated from ROLES rather than hand-written, because two copies of a role brief drift and the
# drift is invisible — you'd be reviewing with a prompt you no longer think you have.
AGENT_DIR = os.path.join(os.path.dirname(HERE), ".claude", "agents")
AGENT_PREFIX = "hoodie-"


def write_agent_defs(outdir=None, roles=None):
    """Emit one markdown agent definition per checking role. Returns the paths written.

    The ENGINEER is deliberately excluded: its model is the task's own route, and a definition with a
    pinned model would fight agent_router for that decision. Omitting the file means an agentic lead
    stays on the routed model, which is the correct behaviour."""
    outdir = outdir or AGENT_DIR
    os.makedirs(outdir, exist_ok=True)
    written = []
    for name in (roles or [PM, QA, REVIEWER]):
        spec = ROLES[name]
        tools = ROLE_TOOLS.get(spec["tools"]) or []
        body = [
            "---",
            "name: %s%s" % (AGENT_PREFIX, name),
            "description: %s — produces %s. Use for the %s stage of a Hoodie Cockpit crew."
            % (spec["label"], spec["produces"], name),
            "model: %s" % spec["tier"],
            "effort: %s" % spec["effort"],
        ]
        if tools:
            body.append("tools: %s" % ", ".join(tools))
        body += [
            "---",
            "",
            "<!-- GENERATED from unifyd/agent_roles.py (ROLES[%r]). Edit there, then re-run"
            "\n     `python3 unifyd/agent_roles.py --write-agents`. Editing this file directly means"
            "\n     the module and the agent disagree, and nothing will tell you which one ran. -->" % name,
            "",
            spec["system"],
            "",
            "## Independence",
            "",
            "You did not write the work you are looking at, and you are not here to approve it. If you "
            "could not verify something, say that instead of assuming it holds — an unverified claim "
            "reported as checked is worse than an open question.",
        ]
        p = os.path.join(outdir, "%s%s.md" % (AGENT_PREFIX, name))
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("\n".join(body) + "\n")
        written.append(p)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description="Show the crew for one task.")
    ap.add_argument("--write-agents", action="store_true",
                    help="generate .claude/agents/hoodie-*.md from ROLES")
    ap.add_argument("--task", default=None)
    ap.add_argument("--class", dest="cls", default="auto")
    ap.add_argument("--budget-pressure", type=float, default=0.0)
    ap.add_argument("--roles", default=None, help="comma-separated subset (default: all four)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--run", action="store_true",
                    help="actually dispatch the crew (default is plan-only, like the rest of this CLI)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="with --run: show the argv per stage, dispatch nothing (default)")
    g.add_argument("--go", dest="dry_run", action="store_false",
                   help="with --run: actually dispatch (spends budget)")
    a = ap.parse_args(argv)
    if not a.write_agents and not a.task:
        ap.error("--task is required (or use --write-agents)")
    if a.write_agents:
        for path in write_agent_defs():
            print("wrote %s" % os.path.relpath(path, os.path.dirname(HERE)))
        return 0
    roles = a.roles.split(",") if a.roles else None
    if a.run:
        res = run_crew(a.task, cls=a.cls, budget_pressure=a.budget_pressure, roles=roles,
                       dry_run=a.dry_run)
        if a.json:
            print(json.dumps(res, indent=2))
            return 0
        for rec in res["results"]:
            print("  %-10s %-6s / %-5s  %s"
                  % (rec["role"], rec["route"]["model"], rec["route"]["effort"],
                     rec.get("error") or rec.get("argv_display") or rec.get("result", "")[:80]))
        print("\nok: %s" % res["ok"])
        return 0 if res["ok"] else 1
    c = crew_for(a.task, cls=a.cls, budget_pressure=a.budget_pressure, roles=roles)
    if a.json:
        print(json.dumps(c, indent=2))
        return 0
    print("task    : %s" % a.task)
    print("class   : %s   (author route %s/%s)"
          % (c["route"]["task_class"], c["route"]["model"], c["route"]["effort"]))
    print("crew    : %s  (%sx the solo route: %s vs %s burn index)"
          % ("recommended" if c["recommended"] else "NOT recommended",
             c["multiple"], c["crew_burn"], c["solo_burn"]))
    print("          %s" % c["recommendation_why"])
    print()
    for s in c["stages"]:
        print("  %-13s %-6s / %-5s  burn %5.1f   %s"
              % (s["label"], s["model"], s["effort"], s["burn_index"], s["note"]))
        print("  %-13s -> %s" % ("", s["produces"]))
    for w in c["why"]:
        print("\n  ! %s" % w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
