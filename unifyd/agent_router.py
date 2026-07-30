#!/usr/bin/env python3
"""agent_router.py — the POLICY BRAIN for Hoodie Cockpit: given a task, decide the model, the
effort, the prompt tactic, and whether to continue / fork / start a thread.

This is a single-operator surface. The rules below encode how CHRIS works, not a general product:
the task classes are the real lanes in this repo (scraper, master-data, surface, review, ops, …),
and the standing rules are the ones already load-bearing in CLAUDE.md (no silent degrades, cite
evidence, no caps). It is meant to be TUNED from the Cockpit page, which is why every decision
carries a `why` string — a routing choice you can't read the reason for is one you can't correct.

WHY THIS IS DETERMINISTIC (and has no LLM in it):
A router that asks a model which model to use pays the very cost it exists to avoid, and its
decisions can't be tested or diffed. This module is pure stdlib and pure functions: same task in,
same route out. The Cockpit shows the route BEFORE anything runs, so the cheap moment to disagree
is before spend, not after.

THE COST MODEL IS SUBSCRIPTION, NOT API — this is the whole point and it changes the math:
  • On the API you pay per token, so "cheaper" means fewer dollars.
  • On a Claude subscription you pay a flat fee and are bounded by RATE LIMITS. The scarce
    resource is your usage budget inside a window, and Opus consumes it far faster than Sonnet,
    which consumes it faster than Haiku.
So the objective is NOT "minimize dollars" — it is "finish the work without burning the window on
tasks that didn't need the big model." That reframing is why `budget_pressure` is an input here:
the same task routes differently at the start of a window than at 85% consumed.

THREE DECISIONS, THREE SECTIONS:
  1. model + effort   — how much capability to spend
  2. tactic           — how to SHAPE the prompt (the 'caveman' family and its siblings)
  3. thread action    — continue / fork / new, i.e. what context to carry

    python3 unifyd/agent_router.py --task "fix the publix scraper" --class scrape
"""
import argparse
import json
import re
import sys

# ── MODEL LADDER ─────────────────────────────────────────────────────────────────────────────────
# `weight` is RELATIVE SUBSCRIPTION BURN, not dollars — the ratio that matters on a flat-fee plan.
# It's an ordering knob for the router, deliberately coarse: the point is "opus costs you multiples
# of haiku for the same turn", not a precise multiplier. Tune from the Cockpit if your plan differs.
MODELS = {
    "haiku":  dict(id="haiku",  weight=1,  note="lookups, mechanical edits, classification"),
    "sonnet": dict(id="sonnet", weight=4,  note="most real work; near-Opus on coding/agentic"),
    "opus":   dict(id="opus",   weight=12, note="long-horizon, correctness-critical, deep reasoning"),
    # Fable is the top of the ladder and roughly 2x Opus per token, so it is reserved for ONE thing:
    # the up-front architecture/workflow design of something new, where the cost of a wrong structural
    # decision is paid back over every hour of building on it. It is a bad default for execution work
    # — the ladder demotes to opus under budget pressure, and `--fallback-model opus` covers the case
    # where the plan doesn't grant Fable at all (entitlement is unverified; the CLI documents the
    # alias, which is not the same as having access).
    "fable":  dict(id="fable",  weight=24, note="up-front architecture & workflow design; top tier"),
}
LADDER = ["haiku", "sonnet", "opus", "fable"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


# ── TASK CLASSES — the real lanes in this repo ────────────────────────────────────────────────────
# horizon:     how many turns/files this realistically spans (drives model + effort)
# correctness: what a wrong answer costs. `max` means a silent error is expensive and unrecoverable
#              — those lanes never route below sonnet regardless of budget pressure.
# tools:       tool-call intensity. Heavy tool use favors higher effort (measured: higher effort
#              produces substantially more tool usage in agentic/search work).
CLASSES = {
    # THE CLASS THIS SET WAS MISSING, and its absence was the worst routing bug in the file: designing
    # the architecture and workflow for something new is the most reasoning-intensive work here, and
    # with no lane for it the phrase "design the app" matched `surface` on the words app/page and
    # routed to sonnet/medium. The single highest-leverage decision in a project was going to a mid
    # tier at mid effort.
    #
    # `prefer` names the model/effort explicitly instead of deriving them, because this lane is not
    # "max correctness" in the same sense as a merge — a wrong merge is expensive to unwind, whereas a
    # wrong architecture is expensive FOREVER, paid back over every hour built on top of it. That
    # asymmetry justifies the top of the ladder for one up-front pass.
    "design": dict(
        horizon="long", correctness="max", tools="medium", prefer=("fable", "max"),
        note="up-front architecture / workflow / data-model design for something new. One expensive "
             "pass at the start, not a lane for the building that follows."),
    "scrape": dict(
        horizon="long", correctness="high", tools="heavy",
        note="scraper build/fix. The scrapers ARE the product — full-capture bar, no caps."),
    "master": dict(
        horizon="long", correctness="max", tools="medium",
        note="matching / identity / master-data. Wrong merges are expensive to unwind."),
    "review": dict(
        horizon="medium", correctness="max", tools="medium",
        note="code review / bug hunt. Recall matters more than brevity."),
    "analysis": dict(
        horizon="medium", correctness="high", tools="medium",
        note="data question over the warehouse. Deterministic vs inferred must stay separate."),
    "surface": dict(
        horizon="medium", correctness="med", tools="medium",
        note="app page / UI work. Visually verifiable, so errors surface fast and cheap."),
    "ops": dict(
        horizon="short", correctness="max", tools="light",
        note="deploy / dispatcher / infra. Short but hard to reverse — never cheap-route."),
    "docs": dict(
        horizon="short", correctness="med", tools="light",
        note="docs / handoff / roadmap upkeep."),
    "triage": dict(
        horizon="short", correctness="med", tools="light",
        note="quick lookup, 'is X still true', one-file check."),
}

# Keyword hints for classifying free text. First class with a hit wins, checked in this order.
#
# ORDER IS LOAD-BEARING, and `triage` leads for a non-obvious reason: it is a QUESTION SHAPE, not a
# topic. "is the abc-fws scraper still enabled" is a one-file lookup that happens to mention a
# scraper — with `scrape` checked first it routed to opus/xhigh, so every quick question about a
# scraper burned the expensive lane. Shape tells you the size of the work; topic only tells you what
# it's about. So the interrogative check runs first, and is kept deliberately narrow (explicit
# lookup phrasings only) so it can't swallow real work — note "how many stores does the warehouse
# cover" is a question too, but it's a DATA question and must stay `analysis`.
#
# After triage, `ops` and `master` lead because misrouting THOSE down is the expensive direction.
_CLASS_HINTS = [
    ("triage",   r"\bis\b.*\bstill\b|\bdoes\b.*\bexist\b|check whether|\bquick\b|look ?up"
                 r"|confirm that|\bstill (?:enabled|live|active|true|running|there)\b"),
    # Placed above `surface` and `ops` on purpose: "architect the workflow for a new app" contains
    # `app`, and "design the deploy pipeline" contains `deploy`. Kept narrow — an up-front structural
    # ask, not any sentence containing the word "design" (a design TWEAK is `surface`).
    ("design",   r"\barchitect(?:ure|ing)?\b|\bdesign the (?:system|architecture|workflow|schema|"
                 r"data model|pipeline|flow)\b|\bfrom scratch\b|\bgreenfield\b|"
                 r"\bhow should (?:we|i) (?:structure|model|architect|design)\b|"
                 r"\b(?:workflow|schema|data.?model) design\b|\bground.?up (?:re)?(?:build|design)\b"),
    ("ops",      r"deploy|dispatch|re-?pin|fly|flyctl|release|secret|launchd|cron|rate.?limit"),
    ("master",   r"match|identity|dim_|master|canon|dedup|upc|gtin|merge|entity|cluster"),
    ("review",   r"review|audit|bug|vulnerab|regress|security|smell|refactor.*safe"),
    ("scrape",   r"scrap|crawl|parse|selector|sitemap|proxy|anti.?bot|patchright|playwright|fetch"),
    ("analysis", r"analy[sz]|query|warehouse|duckdb|parquet|velocity|coverage|how many|what share"),
    ("surface",  r"\bpage\b|\bapp\b|\bui\b|html|css|render|sidebar|layout|chart|dashboard"),
    ("docs",     r"\bdocs?\b|readme|claude\.md|handoff|roadmap|changelog|comment"),
]


def classify(task):
    """Best-effort task class from free text. Returns (class, why). Defaults to `triage` — the
    CHEAP default is deliberate: an under-routed quick question costs one re-run, while an
    over-routed one silently burns the window on every stray message you type."""
    t = (task or "").lower()
    for name, pat in _CLASS_HINTS:
        m = re.search(pat, t)
        if m:
            return name, "matched %r -> %s" % (m.group(0), name)
    return "triage", "no class keyword matched; defaulting to the cheap lane"


# ── TACTICS — how the prompt is SHAPED ───────────────────────────────────────────────────────────
# `caveman` is the one you asked for by name: strip everything that isn't information. The others
# exist because prompt shaping cuts spend on BOTH sides of the wire — caveman trims what you send,
# `terse` trims what comes back, and on current models the response side is usually the bigger win.
#
# `system` is appended via `claude --append-system-prompt`, so a tactic is additive and never
# replaces the project's CLAUDE.md. `rewrite` transforms your task text before it's sent.
TACTICS = {
    "caveman": dict(
        label="Caveman",
        desc="Telegraphic input. Filler, hedging, and politeness stripped; imperative only.",
        rewrite="caveman",
        system=None,
        note="Cuts INPUT tokens. Best on mechanical, unambiguous asks. Do NOT use for long-horizon "
             "work — those need a full spec up front, and stripping context there costs more in "
             "re-derivation than it saves in tokens."),
    "spec": dict(
        label="Full spec",
        desc="Keeps the task verbatim and asks for the complete plan up front, in one turn.",
        rewrite=None,
        system="Give the complete task specification up front and work autonomously from it. "
               "Do not build the requirement up across turns.",
        note="The measured best shape for long-horizon agentic work — clear goal up front produces "
             "fewer, better turns. Deliberately the OPPOSITE of caveman; that's the tradeoff."),
    "terse": dict(
        label="Terse output",
        desc="Caps response verbosity without touching the reasoning.",
        rewrite=None,
        system="Keep responses focused, brief, and concise. Disclaimers and caveats are brief, "
               "with most of the response on the main answer. When asked to explain something, "
               "give a high-level summary unless an in-depth one is specifically requested.",
        note="Trims OUTPUT tokens ~20% in measurement. Note effort does NOT reliably shorten "
             "visible output — prompting is the lever that works."),
    "evidence": dict(
        label="Cite evidence",
        desc="Every claim must carry a file:line or a command output.",
        rewrite=None,
        system="Ground every factual claim in evidence you can point to: a file:line, a command's "
               "actual output, or a cited source. Label deterministic findings separately from "
               "inference. If you cannot ground a claim, say so instead of asserting it.",
        note="Your standing bar. Directly targets the 'unsupported paid-path claim' and "
             "'declared working from partial evidence' failure modes."),
    "scope": dict(
        label="Scope lock",
        desc="Deliver the ask at the scope intended; no adjacent tidying.",
        rewrite=None,
        system="Deliver what was asked, at the scope intended. Do not quietly narrow, widen, or "
               "transform it, and do not add refactors, abstractions, or error handling for cases "
               "that cannot happen. Finish the whole task; report completion only when fully done.",
        note="Measured to reduce scope expansion to near zero without extra clarifying questions."),
    "noverify": dict(
        label="Drop verify scaffolding",
        desc="Omits self-check instructions the current models no longer need.",
        rewrite=None,
        system=None,
        strip_verify=True,
        note="Current models self-verify unprompted; telling them to double-check causes "
             "OVER-verification and extra spend. This inverts the old best practice on purpose."),
}

# Which tactics each class gets by default. Composable — a route can carry several.
_CLASS_TACTICS = {
    "design":   ["spec", "evidence", "scope"],
    "scrape":   ["spec", "evidence", "scope"],
    "master":   ["spec", "evidence", "scope"],
    "review":   ["evidence", "terse"],
    "analysis": ["evidence", "terse"],
    "surface":  ["scope", "terse"],
    "ops":      ["evidence", "scope", "terse"],
    "docs":     ["caveman", "terse", "scope"],
    "triage":   ["caveman", "terse"],
}

# Filler that carries no information for the model. Ordered longest-first so multi-word phrases go
# before their fragments — otherwise "if you could" leaves a dangling "could".
_FILLER = [
    r"\bwhen you get a chance\b", r"\bif you (?:could|can|don'?t mind)\b", r"\bi was wondering\b",
    r"\bi(?:'d| would) (?:like|love) (?:you )?to\b", r"\bcould you (?:please )?\b",
    r"\bwould you (?:please )?\b", r"\bcan you (?:please )?\b", r"\bplease\b",
    r"\bi think (?:that )?(?:maybe )?\b", r"\bit (?:seems|looks) like\b", r"\bkind of\b",
    r"\bsort of\b", r"\bjust\b", r"\breally\b", r"\bactually\b", r"\bbasically\b",
    r"\bmaybe\b", r"\bperhaps\b", r"\ba bit\b", r"\bsomewhat\b", r"\bfor me\b",
    r"\bthanks(?: so much)?\b", r"\bthank you\b", r"\bi need you to\b", r"\bmake sure (?:to|you)\b",
    r"\bgo ahead and\b", r"\blet'?s\b", r"\bwe should\b", r"\bdo you mind\b",
]


# Spans that must survive caveman untouched: backticked code, then identifiers/paths (anything with
# an internal _ . / ) and anything containing a digit.
#
# ONE COMBINED PASS, deliberately. Protecting in two sequential re.sub calls is broken: the
# placeholder contains a digit, so the identifier pattern matched the FIRST pass's own placeholder,
# nested the holds, and silently destroyed the backticked content (caught by the test — a "quoted
# command" came back as a bare index). A single alternation can't re-scan what it already replaced.
_PROTECT = re.compile(r"`[^`]+`|\b[\w./-]*[_./][\w./-]*\b|\b\w*\d[\w./-]*\b")


def cavemanize(text):
    """Strip filler to telegraphic imperative. Returns (text, removed_words).

    LOAD-BEARING: identifiers are never touched. Code is information — a tactic that mangles
    `write_accumulate` or `apps/collect.html` to save four tokens has destroyed the only part of
    the message that mattered. Anything containing `_ / .` or a digit is preserved verbatim, and so
    is any backticked span."""
    if not text:
        return "", 0
    holds, out = [], text

    def _hold(m):
        holds.append(m.group(0))
        return "\x00%d\x00" % (len(holds) - 1)

    out = _PROTECT.sub(_hold, out)     # single pass — see _PROTECT above for why

    before = len(re.findall(r"\w+", out))
    for pat in _FILLER:
        out = re.sub(pat, " ", out, flags=re.I)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    # Stripping a trailing "Thanks!" leaves the "!" orphaned against the real terminator ("...?!").
    # Collapse runs of terminal punctuation to the first one.
    out = re.sub(r"([,.;:!?])[,.;:!?]+", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;")
    after = len(re.findall(r"\w+", out))

    for i, h in enumerate(holds):                     # restore protected spans
        out = out.replace("\x00%d\x00" % i, h)
    return out.strip(), max(0, before - after)


# ── THREAD LIFECYCLE — continue / fork / new ─────────────────────────────────────────────────────
# This is the "without losing the information when information matters" half of the problem, and
# the tradeoff is not symmetric:
#
#   continue → the whole history rides along. With prompt caching the prefix is cheap to re-read
#              (~0.1x), so continuing is NOT the expensive option people assume. What it costs is
#              context: the window fills, and eventually compaction starts summarizing away detail.
#   fork     → `--resume <id> --fork-session`. Inherits the parent's context but gets a NEW id, so
#              an experiment can't pollute the thread you care about. The right move for "try this
#              variant" and for anything you might want to throw away.
#   new      → fresh `--session-id`. Cheapest per turn and the only way to actually shed stale
#              context — but it pays a COLD CACHE WRITE and, if the prior context mattered, you pay
#              again in re-derivation. The expensive mistake, so it needs the strongest evidence.
#
# Hence the asymmetry in `thread_action`: `new` requires BOTH low topic overlap AND no declared
# dependency on prior context. Everything ambiguous continues, because a wrong `continue` wastes
# some context while a wrong `new` can lose a decision you already paid to make.
CONTEXT_FULL = 0.75        # fraction of window consumed before context bloat outweighs continuity
OVERLAP_NEW = 0.12         # topic overlap below this reads as a genuine subject change
_STOP = set("the a an and or but of to in on for with at by from is are was were be been it this "
            "that these those i we you he she they me my our your do does did done can could "
            "should would will now then than as if not no yes so up out off over under again".split())


def _bag(text):
    return {w for w in re.findall(r"[a-z0-9_./-]{3,}", (text or "").lower()) if w not in _STOP}


def topic_overlap(task, thread_subject):
    """Jaccard overlap of content words. 1.0 = same subject, 0.0 = unrelated."""
    a, b = _bag(task), _bag(thread_subject)
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def thread_action(task, thread=None, context_used=0.0, carries_context=None, branching=False):
    """Decide continue | fork | new. Returns dict(action, why, overlap).

    thread          — dict(id, subject) for the candidate thread, or None
    context_used    — 0..1 fraction of the context window already consumed
    carries_context — True if this task DEPENDS on the thread's prior work. When True, `new` is
                      never chosen: the whole point is not to throw away information that matters.
                      None means "unknown", which is treated as the safe True.
    branching       — True if this is an experiment/variant you may discard -> prefer fork
    """
    if not thread or not thread.get("id"):
        return dict(action="new", why="no live thread to continue", overlap=0.0)

    ov = topic_overlap(task, thread.get("subject", ""))
    depends = True if carries_context is None else bool(carries_context)

    if branching:
        return dict(action="fork", why="explicit branch — inherit context under a new id so the "
                                       "parent thread stays clean", overlap=ov)
    if depends and context_used >= CONTEXT_FULL:
        # Can't drop the context (it's needed) and can't keep growing it -> fork is the escape:
        # the parent stays intact while the child continues with a fresh id.
        return dict(action="fork", why="context %.0f%% full but the task depends on prior work — "
                                       "fork rather than lose it" % (context_used * 100), overlap=ov)
    if not depends and (ov < OVERLAP_NEW or context_used >= CONTEXT_FULL):
        reason = ("subject changed (overlap %.2f)" % ov) if ov < OVERLAP_NEW \
                 else ("context %.0f%% full" % (context_used * 100))
        return dict(action="new", why="%s and prior context is not needed — shed it" % reason,
                    overlap=ov)
    if depends and ov < OVERLAP_NEW:
        return dict(action="fork", why="subject changed (overlap %.2f) but prior context is "
                                       "declared load-bearing — fork, don't drop it" % ov, overlap=ov)
    return dict(action="continue", why="same subject (overlap %.2f), context %.0f%% — cached "
                                       "prefix makes continuing the cheap option"
                                       % (ov, context_used * 100), overlap=ov)


# ── THE ROUTE ────────────────────────────────────────────────────────────────────────────────────
def _model_for(cls, budget_pressure):
    """Pick model + effort. `budget_pressure` is 0..1 — how much of the usage window is gone.

    Pressure can demote by at most ONE rung, and never below the class floor. A router that
    collapses everything to haiku at 90% pressure doesn't save the window, it just produces work
    you have to redo — which costs more of the window than it saved."""
    spec = CLASSES[cls]
    horizon, correctness = spec["horizon"], spec["correctness"]

    if spec.get("prefer"):
        # An explicit lane. Still subject to budget demotion below — a preference is not an exemption.
        want, effort = spec["prefer"]
        floor = "opus" if want == "fable" else "sonnet"
    elif correctness == "max":
        want, effort, floor = "opus", ("xhigh" if horizon == "long" else "high"), "sonnet"
    elif horizon == "long":
        want, effort, floor = "opus", "xhigh", "sonnet"
    elif correctness == "high":
        want, effort, floor = "sonnet", "high", "sonnet"
    elif horizon == "medium":
        want, effort, floor = "sonnet", "medium", "haiku"
    else:
        want, effort, floor = "haiku", "low", "haiku"

    why = "%s: horizon=%s correctness=%s -> %s/%s" % (cls, horizon, correctness, want, effort)
    if budget_pressure >= 0.8 and LADDER.index(want) > LADDER.index(floor):
        demoted = LADDER[LADDER.index(want) - 1]
        why += "; budget %.0f%% consumed -> demoted to %s (floor %s)" % (
            budget_pressure * 100, demoted, floor)
        want = demoted
        if effort in ("xhigh", "max"):
            effort = "high"
    # Heavy tool use needs room to actually make the calls.
    if spec["tools"] == "heavy" and effort in ("low", "medium"):
        effort = "high"
        why += "; tool-heavy -> effort raised to high"
    return want, effort, why


def route(task, cls=None, thread=None, context_used=0.0, carries_context=None,
          branching=False, budget_pressure=0.0, tactics=None):
    """THE entry point. Returns a full, inspectable routing decision — nothing is implicit, because
    the Cockpit renders every field and you overrule it there before anything runs."""
    why = []
    if cls in (None, "", "auto"):
        cls, cwhy = classify(task)
        why.append(cwhy)
    else:
        why.append("class given explicitly: %s" % cls)
    if cls not in CLASSES:
        raise ValueError("unknown class %r (known: %s)" % (cls, ", ".join(sorted(CLASSES))))

    model, effort, mwhy = _model_for(cls, budget_pressure)
    why.append(mwhy)

    picked = list(tactics) if tactics else list(_CLASS_TACTICS[cls])
    # caveman and spec are mutually exclusive by construction — one strips context, the other
    # insists on all of it. Long-horizon always wins, because re-derivation costs more than tokens.
    if "caveman" in picked and "spec" in picked:
        picked.remove("caveman" if CLASSES[cls]["horizon"] == "long" else "spec")
        why.append("caveman and spec conflict -> kept the one matching horizon=%s"
                   % CLASSES[cls]["horizon"])
    for t in picked:
        if t not in TACTICS:
            raise ValueError("unknown tactic %r (known: %s)" % (t, ", ".join(sorted(TACTICS))))

    prompt, removed = (cavemanize(task) if "caveman" in picked else (task, 0))
    system = [TACTICS[t]["system"] for t in picked if TACTICS[t].get("system")]
    th = thread_action(task, thread, context_used, carries_context, branching)

    return dict(
        task=task, task_class=cls, model=model, model_weight=MODELS[model]["weight"],
        effort=effort, tactics=picked, prompt=prompt, filler_removed=removed,
        append_system="\n\n".join(system), thread=th, why=why,
        # Relative burn estimate for the Cockpit's budget gauge. Coarse and honestly labelled:
        # it orders routes against each other, it does not predict consumption.
        burn_index=MODELS[model]["weight"] * (1 + EFFORTS.index(effort) * 0.5),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="Route one task (prints the decision as JSON).")
    ap.add_argument("--task", required=True)
    ap.add_argument("--class", dest="cls", default="auto")
    ap.add_argument("--thread-id", default=None)
    ap.add_argument("--thread-subject", default="")
    ap.add_argument("--context-used", type=float, default=0.0)
    ap.add_argument("--budget-pressure", type=float, default=0.0)
    ap.add_argument("--branching", action="store_true")
    ap.add_argument("--no-context-needed", action="store_true",
                    help="prior thread context is NOT required for this task")
    a = ap.parse_args(argv)
    th = dict(id=a.thread_id, subject=a.thread_subject) if a.thread_id else None
    r = route(a.task, cls=a.cls, thread=th, context_used=a.context_used,
              carries_context=(False if a.no_context_needed else None),
              branching=a.branching, budget_pressure=a.budget_pressure)
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
