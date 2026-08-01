#!/usr/bin/env python3
"""agent_exec.py — the EXECUTION SEAM for Hoodie Cockpit: turn a route from agent_router into a real
Claude Code run, on the SUBSCRIPTION rather than the metered API.

WHY CLAUDE CODE AND NOT THE ANTHROPIC SDK — this is the load-bearing decision:
The ~17 modules in unifyd/ that call Claude (self_heal, menu_ingest, label_vision, hi_analyst, …)
use `anthropic` + ANTHROPIC_API_KEY. That is the METERED API: every token is billed per-token to an
API account. There is no flag that makes the SDK bill a Pro/Max subscription instead — the
subscription is not an API credential.

What IS attached to the subscription is the `claude` CLI itself. Measured on this machine:
  ~/.claude.json carries `oauthAccount` (an OAuth login), and ANTHROPIC_API_KEY is unset.
So shelling out to `claude -p` runs on the subscription by construction. That makes the CLI the
engine and this module a thin, honest wrapper over it — no second billing rail, nothing to
reconcile. It also means this module deliberately does NOT import `anthropic`.

WHERE IT RUNS — and the standing rule it bumps into:
CLAUDE.md says NOTHING RUNS LOCALLY; all execution is on Fly. That rule is about scrapes, pulls,
geo passes and scheduled ticks — work that was duplicating and silently failing on the Mac. This is
a different class: an interactive operator cockpit whose credential (the OAuth login) exists only on
the Mac, in the same spirit as the accepted local visual-iteration loop. So this runs LOCAL by
design. Putting it on Fly would require minting a long-lived token and parking a personal
credential in a Fly secret — do not do that casually, and do not let this module be imported into a
scheduled path.

WHAT IT WRITES:
Every run appends one JSON line to agent_state/cockpit/ledger.jsonl — route in, result out, tokens,
duration, session id. That ledger is the entire point of the feedback loop: a routing policy you
can't measure is a routing policy you're guessing at. It is also the only place the burn is real
rather than estimated, which is why `burn_index` from the router is labelled an index and this
records actual `usage`.

    python3 unifyd/agent_exec.py --task "is the abc-fws scraper still enabled" --dry-run
    python3 unifyd/agent_exec.py --task "..." --go            # actually runs
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_router as router

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_state", "cockpit")
LEDGER = os.path.join(STATE, "ledger.jsonl")
THREADS = os.path.join(STATE, "threads.json")

# Model aliases the CLI accepts. The router speaks in tiers (haiku/sonnet/opus) on purpose: tiers are
# a stable policy vocabulary, while concrete ids churn every release. Mapping happens HERE, once, so
# a model launch is a one-line edit and never a policy rewrite.
CLI_MODEL = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus", "fable": "fable"}

# Tiers whose availability depends on the plan, mapped to what to fall back to. The CLI documents
# `fable` as a first-class alias (`--model` help lists 'fable', 'opus', 'sonnet'), but a documented
# alias is NOT proof of entitlement — and a `design` route is the worst possible place to discover
# that, because it's the one turn you most wanted to land. `--fallback-model` makes the CLI switch
# rather than fail, so an unentitled plan degrades to Opus instead of erroring out.
FALLBACK_MODEL = {"fable": "opus"}

# ── CAPABILITY PROFILE PER CLASS ─────────────────────────────────────────────────────────────────
# An allowlist is a two-edged thing and the first version of this only used one edge. Restricting a
# cheap lane to read-only is right — a `triage` lookup that can Write is a lookup that can surprise
# you. But the same list SUBTRACTS everything not named, and the original omitted web access from
# triage and review: "is this still true on the live site" then couldn't reach the live site, so the
# model either guessed or spent turns grepping for something that isn't in the repo at all.
#
# THE FREE-MONEY POINT, measured rather than assumed: a headless `claude -p` run on this machine has
# NO MCP servers configured and NO plugins installed (`claude mcp list` / `claude plugin list` are
# both empty). The connectors available in an interactive session come from the app's plugin layer and
# the subprocess does not inherit them. So the only capabilities the Cockpit can actually count on are
# the CLI's built-ins — which makes it worth naming them deliberately per lane instead of leaving a
# default set to chance. WebSearch/WebFetch in particular are already paid for and answer questions
# that would otherwise be re-derived, which is the same lever as the fact store.
#
# Deliberately NOT added: any per-call metered connector (Bright Data's per-GB tier and similar).
# "Maximize the tools available" does not extend to a tool that bills per use — that would trade the
# free-first rule for convenience.
READ = ["Read", "Grep", "Glob"]
GIT_READ = ["Bash(git log:*)", "Bash(git show:*)", "Bash(git status)", "Bash(git diff:*)"]
WEB = ["WebSearch", "WebFetch"]
EDIT = ["Edit", "Write"]

TOOLS_FOR = {
    # Lookups: read the repo, read git history, and check the live world — but never mutate.
    "triage":   READ + GIT_READ + WEB,
    # Docs may write, and needs the web to verify a link or a claim before it's committed to prose.
    "docs":     READ + GIT_READ + WEB + EDIT,
    # Review reads everything and reaches out for CVE/API/version facts, but does not fix in place —
    # a reviewer that edits stops being a reviewer.
    "review":   READ + GIT_READ + WEB,
    # Analysis needs the warehouse CLI plus reference lookups; still no writes.
    "analysis": READ + GIT_READ + WEB + ["Bash(python3:*)", "Bash(duckdb:*)"],
    # Design is reading and thinking, not building — it produces a plan, and letting it edit invites
    # it to start implementing before the structure has been agreed.
    "design":   READ + GIT_READ + WEB,
    # scrape / master / surface / ops intentionally have NO entry: they get the CLI's full default
    # tool set, because constraining a build lane to a list I guessed at is how you discover a missing
    # tool halfway through a long agentic run.
}


def _which_claude():
    """Locate the CLI. Explicit failure beats a confusing FileNotFoundError three frames deep."""
    p = os.environ.get("CLAUDE_BIN") or shutil.which("claude") \
        or os.path.expanduser("~/.local/bin/claude")
    return p if p and os.path.exists(p) else None


# ── metered opt-in — an explicit switch, never an ambient fallback ─────────────────────────────────
# ANTHROPIC_API_KEY takes precedence over an OAuth profile in every Anthropic client, so its MERE
# PRESENCE in the shell — set by some unrelated tool, a sourced .env, whatever — used to be enough to
# silently move spend onto the metered rail (or, in an earlier version of this module, to hard-refuse
# every run until the operator went and unset it by hand). Neither is right: the operator should never
# have to notice and clean up an ambient variable they didn't set for this purpose. So the variable's
# presence, by itself, now changes NOTHING — dispatch() strips it from the subprocess environment
# unless this flag is explicitly on. Metered billing is an action someone takes, not a state the
# environment can drift into.
EXEC_SETTINGS = os.path.join(STATE, "exec_settings.json")


def metered_allowed(path=None):
    """Defaults to False. Read fresh on every call (not cached) — a change should take effect on the
    very next dispatch, not after a restart."""
    p = path or EXEC_SETTINGS
    try:
        with open(p, encoding="utf-8") as fh:
            return bool(json.load(fh).get("metered_allowed", False))
    except Exception:
        return False


def set_metered_allowed(val, path=None):
    """Persist the switch. `path` lets tests point this at a tempfile instead of the real
    machine-local settings file — same isolation convention agent_tickets.py's `db=` uses."""
    p = path or EXEC_SETTINGS
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dict(metered_allowed=bool(val)), fh)
    os.replace(tmp, p)
    return bool(val)


def auth_mode(settings_path=None):
    """Report which billing rail a run will actually use — never prints or returns secret values.

    Surfaced in the Cockpit because 'am I on the subscription or am I burning API credit' is exactly
    the question that must never be a guess. `settings_path` overrides where the metered_allowed()
    switch is read from — tests point this at a tempfile so exercising the METERED-enabled branch
    never touches (or persists) the real machine-local setting."""
    out = dict(cli=_which_claude(), api_key_in_env=bool(os.environ.get("ANTHROPIC_API_KEY")),
               metered_allowed=metered_allowed(settings_path), oauth=False, mode="unknown",
               warning=None, note=None)
    try:
        cfg = os.path.expanduser("~/.claude.json")
        if os.path.exists(cfg):
            with open(cfg, encoding="utf-8", errors="replace") as fh:
                out["oauth"] = bool(json.load(fh).get("oauthAccount"))
    except Exception:
        pass
    if out["api_key_in_env"] and out["metered_allowed"]:
        out["mode"] = "api-key (METERED — explicitly enabled)"
        out["warning"] = ("Metered billing is explicitly enabled and ANTHROPIC_API_KEY is set — "
                          "runs will bill the metered API, not your subscription. Turn it off in "
                          "Cockpit if that's no longer what you want.")
    elif out["api_key_in_env"]:
        # Ambient key, metered NOT opted into: dispatch() strips ANTHROPIC_API_KEY from the
        # subprocess environment before every run, so this can never silently bill the metered
        # rail. Deliberately does NOT set `warning` — the Cockpit surfaces `warning` as a "needs
        # your attention" item, and this is a handled, working-as-intended state, not a problem.
        # `note` carries the same information for a low-key hint instead.
        out["mode"] = "subscription (ANTHROPIC_API_KEY present but ignored)"
        out["note"] = ("ANTHROPIC_API_KEY is set in this environment, but metered billing is OFF, "
                       "so every dispatch ignores it and runs on your subscription.")
    elif out["oauth"]:
        # HONEST LABEL: this reads CONFIGURATION, not liveness. An `oauthAccount` in ~/.claude.json
        # only proves a login happened once — the access token expires, and a run then fails with
        # `401 OAuth access token has expired` while this function still cheerfully says
        # "subscription". Observed live. Liveness is only knowable by actually running, so the run
        # path classifies that 401 into `needs_auth` (below) instead of reporting a generic failure.
        #
        # `claude auth status` IS NOT PROOF EITHER, and this is the sharper lesson: measured on this
        # machine it returned {"loggedIn": true, "subscriptionType": "max"} at the same moment every
        # request was 401-ing on an expired token, and the keychain credential was five days stale.
        # It reports that a credential EXISTS, not that it works — which is also why `/login` quietly
        # did nothing (it saw one and skipped the flow). Recovery is `claude auth logout && claude
        # auth login`. Report what it says, clearly labelled, and never let it stand in for a run.
        out["mode"] = "subscription (OAuth, token liveness unverified)"
        try:
            claude = out["cli"]
            if claude:
                p = subprocess.run([claude, "auth", "status"], capture_output=True,
                                   text=True, timeout=20)
                st = json.loads((p.stdout or "").strip() or "{}")
                out["cli_reports"] = {k: st.get(k) for k in
                                      ("loggedIn", "authMethod", "subscriptionType")}
                out["cli_reports_note"] = ("self-reported: proves a credential exists, NOT that it "
                                           "is valid. If runs 401, use `claude auth logout && "
                                           "claude auth login` — a stale credential makes plain "
                                           "`/login` skip the flow.")
        except Exception:
            pass
    elif out["cli"]:
        out["mode"] = "cli present, not logged in"
        out["warning"] = "run `claude` once interactively to log in"
    else:
        out["mode"] = "no claude CLI found"
        out["warning"] = "install Claude Code, or set CLAUDE_BIN"
    return out


def _threads():
    try:
        with open(THREADS, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_thread(tid, subject, meta=None):
    os.makedirs(STATE, exist_ok=True)
    th = _threads()
    rec = th.get(tid) or dict(id=tid, subject=subject, turns=0, created=time.time())
    rec.update(subject=subject or rec.get("subject", ""), last=time.time(),
               turns=rec.get("turns", 0) + 1)
    if meta:
        rec.update(meta)
    th[tid] = rec
    tmp = THREADS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(th, fh, indent=2)
    os.replace(tmp, THREADS)     # atomic — a half-written thread file loses every session id
    return rec


def build_argv(route_dict, cwd=None, session_id=None, resume_id=None, allowed_tools=None):
    """Assemble the exact `claude` argv for a route. Pure and returned to the caller so the Cockpit
    can SHOW the command before running it — the point of a dry run is that you can read what is
    about to happen, not that you're told it's fine.

    The three thread actions map to three concrete CLI shapes:
      continue → --resume <id>                    (same thread; cached prefix, cheap)
      fork     → --resume <id> --fork-session     (inherit context, NEW id, parent untouched)
      new      → --session-id <fresh uuid>        (no inherited context; cold cache)
    """
    claude = _which_claude()
    if not claude:
        raise RuntimeError("claude CLI not found (set CLAUDE_BIN)")

    action = route_dict["thread"]["action"]
    argv = [claude, "-p", "--output-format", "json"]
    argv += ["--model", CLI_MODEL[route_dict["model"]]]
    fb = FALLBACK_MODEL.get(route_dict["model"])
    if fb:
        argv += ["--fallback-model", CLI_MODEL[fb]]
    argv += ["--effort", route_dict["effort"]]

    if action == "continue" and resume_id:
        argv += ["--resume", resume_id]
    elif action == "fork" and resume_id:
        argv += ["--resume", resume_id, "--fork-session"]
    else:
        argv += ["--session-id", session_id or str(uuid.uuid4())]

    if route_dict.get("append_system"):
        argv += ["--append-system-prompt", route_dict["append_system"]]

    tools = allowed_tools if allowed_tools is not None else TOOLS_FOR.get(route_dict["task_class"])
    if tools:
        argv += ["--allowedTools"] + list(tools)

    # THE PROMPT GOES ON STDIN, NOT AS A POSITIONAL ARG — measured, not stylistic.
    # `--allowedTools <tools...>` is VARIADIC, so a trailing positional prompt is consumed as one
    # more tool name and the CLI then dies with "Input must be provided either through stdin or as a
    # prompt argument when using --print" — an error that names the prompt while the real cause is
    # the flag before it. stdin is immune to argv ordering, needs no shell quoting, and carries the
    # multi-line prompts the `spec` tactic produces. The caller pipes route_dict["prompt"] in.
    return argv


def run(task, cls="auto", thread_id=None, thread_subject="", context_used=0.0,
        carries_context=None, branching=False, budget_pressure=0.0, tactics=None,
        cwd=None, dry_run=True, timeout=900):
    """Route then execute. Returns a record that is ALSO the ledger line.

    dry_run=True is the default on purpose: this spends real subscription budget, and a function
    that runs by default is one that runs by accident."""
    known = _threads()
    if thread_id and not thread_subject:
        thread_subject = (known.get(thread_id) or {}).get("subject", "")

    th = dict(id=thread_id, subject=thread_subject) if thread_id else None
    r = router.route(task, cls=cls, thread=th, context_used=context_used,
                     carries_context=carries_context, branching=branching,
                     budget_pressure=budget_pressure, tactics=tactics)
    return dispatch(r, thread_id=thread_id, cwd=cwd, dry_run=dry_run, timeout=timeout)


def _dispatch_env(auth, base_env=None):
    """The subprocess environment a dispatch will actually run with. Pure function of `auth` (an
    auth_mode()-shaped dict) and `base_env` (defaults to the real os.environ, injectable for tests)
    — no I/O, no subprocess, safe to unit-test directly.

    Returns None ("inherit the caller's environment unchanged") except on the one path that must
    remove something: ANTHROPIC_API_KEY present AND metered billing not explicitly allowed. There,
    it returns a full copy of `base_env` with that one key removed — not unset in THIS process, just
    never handed to the child, so the CLI's own OAuth-vs-API-key precedence never even sees it and
    can't silently pick the metered rail because some unrelated tool happened to export the variable
    into this shell."""
    if auth.get("api_key_in_env") and not auth.get("metered_allowed"):
        src = base_env if base_env is not None else os.environ
        return {k: v for k, v in src.items() if k != "ANTHROPIC_API_KEY"}
    return None


def dispatch(r, thread_id=None, cwd=None, dry_run=True, timeout=900, allowed_tools=None):
    """Execute an ALREADY-ROUTED task. `run()` is this plus a `router.route()` call in front of it —
    split apart so a caller with its own routing decision can reuse the same argv-build / subprocess /
    ledger machinery instead of a second copy of it drifting alongside.

    That caller is agent_roles' crew stages: a stage's model/effort/tools come from `crew_for()`
    (author-tier floor, role-scoped tool lists), not from agent_router's class-based policy, so it
    can't go through `run()` — but it still needs everything `run()` does after routing: the
    auth-rail refusal, the argv shape, the JSON/non-JSON result handling, the ledger line."""
    rec = dict(ts=time.time(), route=r, auth=auth_mode(), dry_run=bool(dry_run),
               cwd=cwd or os.getcwd())
    action = r["thread"]["action"]
    new_id = str(uuid.uuid4()) if action == "new" else None
    try:
        rec["argv"] = build_argv(r, cwd=cwd, session_id=new_id, resume_id=thread_id,
                                 allowed_tools=allowed_tools)
    except RuntimeError as e:
        rec["error"] = str(e)
        return rec

    # Never show the system prompt in the displayed command line — it's long and already rendered
    # elsewhere in the UI. The task itself is no longer in argv at all (it's piped on stdin).
    rec["argv_display"] = " ".join(
        ("<system-prompt>" if i and rec["argv"][i - 1] == "--append-system-prompt" else a)
        for i, a in enumerate(rec["argv"])) + "  <<< <task on stdin>"

    if dry_run:
        rec["result"] = None
        return rec

    # Never let the ambient ANTHROPIC_API_KEY decide the billing rail — see _dispatch_env's own
    # docstring. Pulled out as its own pure function (env in, env out) specifically so this policy
    # is unit-testable without ever going near subprocess.run — this module's own standing rule
    # ("nothing dispatches" in tests) would otherwise make the metered-opt-in behavior untestable.
    env = _dispatch_env(rec["auth"])

    t0 = time.time()
    try:
        proc = subprocess.run(rec["argv"], capture_output=True, text=True,
                              input=r["prompt"], timeout=timeout, cwd=cwd or os.getcwd(), env=env)
        rec["exit"] = proc.returncode
        rec["seconds"] = round(time.time() - t0, 2)
        raw = (proc.stdout or "").strip()
        try:
            payload = json.loads(raw)
        except Exception:
            # A non-JSON body is a real failure mode (CLI error text, auth prompt). Keep it visible
            # and truncated rather than swallowing it into a generic "failed".
            payload = None
            rec["stdout_raw"] = raw[:4000]
        if payload:
            rec["result"] = payload.get("result")
            rec["session_id"] = payload.get("session_id") or new_id or thread_id
            rec["usage"] = payload.get("usage")
            rec["cli_cost_usd"] = payload.get("total_cost_usd")
            rec["num_turns"] = payload.get("num_turns")
            rec["is_error"] = payload.get("is_error")
        if proc.returncode != 0 and (proc.stderr or "").strip():
            rec["stderr"] = proc.stderr.strip()[:4000]
        # An expired OAuth token is the one failure with a specific, actionable fix, and it looks
        # nothing like a bad prompt — classify it so the Cockpit says "re-authenticate" rather than
        # burying a 401 in a generic error. Only the operator can fix this (interactive `claude`).
        blob = "%s %s" % (rec.get("stderr", ""), rec.get("stdout_raw", ""))
        if "401" in blob and ("oauth" in blob.lower() or "authenticate" in blob.lower()):
            rec["needs_auth"] = True
            rec["error"] = ("Claude Code's OAuth token has expired — run `claude` once "
                            "interactively (or /login) to re-authenticate. No budget was spent.")
        elif payload is None or proc.returncode != 0 or (payload or {}).get("is_error"):
            # A non-JSON body, a non-zero exit, or the CLI's own is_error flag are all real failures
            # — found live via a crew-stage test that made one stage fail non-JSON: with no `error`
            # set here, a caller that only checks `rec.get("error")` to decide whether a dispatch
            # succeeded (agent_roles.run_crew's "stop at the first failing stage", api_cockpit_chat's
            # status=answered/error) saw neither an error nor a result and treated it as a quiet,
            # ordinary miss — the crew kept running past a stage that had actually failed. A missing
            # error is worse than a wrong one: it looks like nothing went wrong at all.
            rec["error"] = (rec.get("stderr") or rec.get("stdout_raw")
                           or "dispatch failed (exit %s, no parseable result)" % proc.returncode)[:500]
    except subprocess.TimeoutExpired:
        rec["error"] = "timed out after %ss" % timeout
        rec["seconds"] = round(time.time() - t0, 2)

    sid = rec.get("session_id") or new_id
    if sid:
        _save_thread(sid, r["task"][:160], meta=dict(
            last_class=r["task_class"], last_model=r["model"], last_action=action))
    _append_ledger(rec)
    return rec


def _append_ledger(rec):
    os.makedirs(STATE, exist_ok=True)
    slim = {k: v for k, v in rec.items() if k != "argv"}   # argv holds the full prompt text
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(slim, default=str) + "\n")


def ledger(limit=100):
    """Newest-first run history. The measured counterpart to the router's estimates."""
    try:
        with open(LEDGER, encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
    except Exception:
        return []
    return rows[-limit:][::-1]


def stats():
    """Roll the ledger into the numbers that tell you whether the policy is working: where the burn
    actually went, by class and by model."""
    rows = [r for r in ledger(10000) if not r.get("dry_run")]
    by_class, by_model = {}, {}
    tok = 0
    for r in rows:
        rt = r.get("route") or {}
        u = r.get("usage") or {}
        n = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
        tok += n
        for bucket, key in ((by_class, rt.get("task_class")), (by_model, rt.get("model"))):
            if key:
                b = bucket.setdefault(key, dict(runs=0, tokens=0))
                b["runs"] += 1
                b["tokens"] += n
    return dict(runs=len(rows), tokens=tok, by_class=by_class, by_model=by_model,
                threads=len(_threads()))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Route and optionally run one task via Claude Code.")
    ap.add_argument("--task", required=True)
    ap.add_argument("--class", dest="cls", default="auto")
    ap.add_argument("--thread-id", default=None)
    ap.add_argument("--context-used", type=float, default=0.0)
    ap.add_argument("--budget-pressure", type=float, default=0.0)
    ap.add_argument("--branching", action="store_true")
    ap.add_argument("--no-context-needed", action="store_true")
    ap.add_argument("--cwd", default=None)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True)
    g.add_argument("--go", dest="dry_run", action="store_false", help="actually run (spends budget)")
    a = ap.parse_args(argv)

    rec = run(a.task, cls=a.cls, thread_id=a.thread_id, context_used=a.context_used,
              carries_context=(False if a.no_context_needed else None), branching=a.branching,
              budget_pressure=a.budget_pressure, cwd=a.cwd, dry_run=a.dry_run)
    r = rec["route"]
    print("auth      : %s%s" % (rec["auth"]["mode"],
                                "  ⚠ " + rec["auth"]["warning"] if rec["auth"]["warning"] else ""))
    print("class     : %s" % r["task_class"])
    print("model     : %s / %s   (burn index %.1f)" % (r["model"], r["effort"], r["burn_index"]))
    print("tactics   : %s" % ", ".join(r["tactics"]))
    print("thread    : %s — %s" % (r["thread"]["action"], r["thread"]["why"]))
    if r["filler_removed"]:
        print("caveman   : -%d filler words" % r["filler_removed"])
        print("prompt    : %s" % r["prompt"])
    for w in r["why"]:
        print("  why     : %s" % w)
    print("command   : %s" % rec.get("argv_display", "(none)"))
    if rec.get("error"):
        print("ERROR     : %s" % rec["error"])
        return 1
    if not a.dry_run:
        print("exit      : %s in %ss" % (rec.get("exit"), rec.get("seconds")))
        print("session   : %s" % rec.get("session_id"))
        if rec.get("usage"):
            print("usage     : %s" % json.dumps(rec["usage"]))
        print("\n%s" % (rec.get("result") or rec.get("stdout_raw") or "(no result)"))
    else:
        print("\n(dry run — pass --go to execute)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
