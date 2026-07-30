#!/usr/bin/env python3
"""agent_exec_test.py — the CLI seam, tested without spending anything.

Every assertion here is about argv construction and policy, which are pure. Nothing dispatches: a test
suite that burns subscription budget is a test suite that gets run less often, and the interesting
failures in this module were never about the model's answer — they were about the command line.

The one that cost real time: `--allowedTools` is VARIADIC, so a trailing positional prompt was
swallowed as one more tool name and the CLI died with "Input must be provided either through stdin or
as a prompt argument" — an error naming the prompt while the actual cause was the flag before it.

    python3 unifyd/agent_exec_test.py
"""
import json
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
    print("agent_exec — the Claude Code seam")
    import agent_exec as X
    import agent_router as R

    if not X._which_claude():
        print("  claude CLI not found — argv tests need it to resolve a path")
        return 0

    route = R.route("fix the publix parse", cls="scrape")

    # --- 1. THE PROMPT IS NOT IN ARGV ------------------------------------------------------------
    argv = X.build_argv(route, session_id="s-1")
    check("prompt is NOT a positional arg (it goes on stdin)",
          route["prompt"] not in argv, argv[-3:])
    check("--allowedTools, if present, is not followed by the prompt",
          "--allowedTools" not in argv or argv[-1] != route["prompt"], argv[-2:])
    check("output format is json", "--output-format" in argv and "json" in argv)
    check("print mode is set", "-p" in argv)

    # --- 2. thread action -> the right CLI shape -------------------------------------------------
    cont = dict(route); cont["thread"] = dict(action="continue", why="", overlap=1.0)
    a = X.build_argv(cont, resume_id="abc")
    check("continue -> --resume with the id", "--resume" in a and "abc" in a, a)
    check("continue does NOT fork", "--fork-session" not in a, a)

    fork = dict(route); fork["thread"] = dict(action="fork", why="", overlap=0.5)
    a = X.build_argv(fork, resume_id="abc")
    check("fork -> --resume AND --fork-session", "--resume" in a and "--fork-session" in a, a)

    new = dict(route); new["thread"] = dict(action="new", why="", overlap=0.0)
    a = X.build_argv(new, session_id="fresh-id")
    check("new -> --session-id, no resume", "--session-id" in a and "--resume" not in a, a)
    # A fork with no parent cannot inherit anything; it must not silently produce a bare --resume.
    a = X.build_argv(fork, session_id="fresh-id", resume_id=None)
    check("fork without a parent falls back to a fresh session",
          "--session-id" in a and "--resume" not in a, a)

    # --- 3. model + effort + the entitlement fallback ---------------------------------------------
    for tier in R.LADDER:
        check("CLI knows how to name %s" % tier, tier in X.CLI_MODEL, sorted(X.CLI_MODEL))
    fab = dict(route); fab["model"] = "fable"; fab["effort"] = "max"
    a = X.build_argv(fab, session_id="s")
    check("fable is passed to --model", "fable" in a, a)
    check("fable carries --fallback-model (plan entitlement is unverified)",
          "--fallback-model" in a, a)
    check("...falling back to opus", a[a.index("--fallback-model") + 1] == "opus", a)
    op = dict(route); op["model"] = "opus"
    check("a tier that needs no fallback does not get one",
          "--fallback-model" not in X.build_argv(op, session_id="s"))
    check("effort is passed through",
          X.build_argv(fab, session_id="s")[X.build_argv(fab, session_id="s").index("--effort") + 1]
          == "max")

    # --- 4. capability profiles: restricting must not remove what a lane needs -------------------
    # The original allowlists omitted web access from triage/review, so "is this still true on the
    # live site" could not reach the live site.
    for lane in ("triage", "review", "analysis", "design", "docs"):
        tools = X.TOOLS_FOR.get(lane) or []
        check("%s can reach the web" % lane, any(t.startswith("Web") for t in tools), tools)
    for lane in ("triage", "review", "analysis", "design"):
        tools = X.TOOLS_FOR.get(lane) or []
        check("%s cannot write" % lane, not any(t in ("Write", "Edit") for t in tools), tools)
    check("docs CAN write (it is the lane that edits prose)",
          any(t in ("Write", "Edit") for t in X.TOOLS_FOR["docs"]))
    check("analysis can run python", any("python3" in t for t in X.TOOLS_FOR["analysis"]))
    for lane in ("scrape", "master", "ops", "surface"):
        check("%s is unrestricted (a long run must not hit a missing tool)" % lane,
              X.TOOLS_FOR.get(lane) is None, X.TOOLS_FOR.get(lane))

    # --- 5. the billing rail must be reported honestly -------------------------------------------
    a = X.auth_mode()
    for f in ("cli", "api_key_in_env", "mode"):
        check("auth_mode exposes %s" % f, f in a, sorted(a))
    check("auth_mode never returns a secret value",
          not any(isinstance(v, str) and v.startswith("sk-") for v in a.values()), "leaked")
    check("mode is labelled as unverified, not asserted live",
          "unverified" in a["mode"] or a["api_key_in_env"] or "no claude" in a["mode"], a["mode"])

    # A stray API key must WARN, because it silently moves spend onto the metered rail.
    had = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-not-a-real-key"
    try:
        a2 = X.auth_mode()
        check("a stray API key is detected", a2["api_key_in_env"] is True)
        check("...and reported as METERED", "METERED" in a2["mode"], a2["mode"])
        check("...with a warning naming the variable", "ANTHROPIC_API_KEY" in (a2["warning"] or ""),
              a2["warning"])
        rec = X.run("anything", dry_run=False)
        check("a run REFUSES rather than billing the metered rail", bool(rec.get("error")), rec)
    finally:
        if had is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = had

    # --- 6. dry run spends nothing ---------------------------------------------------------------
    rec = X.run("is abc-fws enabled", dry_run=True)
    check("dry run returns a route", bool(rec.get("route")), sorted(rec))
    check("dry run produces no result", rec.get("result") is None, rec.get("result"))
    check("dry run shows the command", bool(rec.get("argv_display")), rec.get("argv_display"))
    check("the displayed command hides the system prompt",
          "<system-prompt>" in rec["argv_display"], rec["argv_display"])
    check("the displayed command shows the prompt is on stdin",
          "stdin" in rec["argv_display"], rec["argv_display"])
    check("dry_run defaults to True (a function that spends by default spends by accident)",
          X.run("x").get("dry_run") is True)

    # --- 7. a full (mocked) dispatch — the ONLY path everything above deliberately never exercises --
    # Every check above avoids a real subprocess on purpose (that's what protects the test suite from
    # spending real budget). But that leaves the success path — result parsing, ledger write, thread
    # save — with zero coverage, and `dispatch()`'s split from `run()` moved code that referenced the
    # route dict's own `task` field; a plain rename slip there (`task` instead of `r["task"]`) would
    # NameError on every real, non-dry-run success and nothing above would catch it. Mock the one
    # thing that actually costs money (subprocess.run) and let everything else run for real.
    tmp_state = tempfile.mkdtemp(prefix="exec-state-")
    tmp_ledger, tmp_threads = os.path.join(tmp_state, "ledger.jsonl"), os.path.join(tmp_state, "threads.json")
    old_state, old_ledger, old_threads = X.STATE, X.LEDGER, X.THREADS
    X.STATE, X.LEDGER, X.THREADS = tmp_state, tmp_ledger, tmp_threads
    old_subprocess_run = X.subprocess.run

    class _FakeProc:
        returncode = 0
        stdout = json.dumps(dict(result="mocked answer", session_id="s-mocked",
                                 usage=dict(input_tokens=10, output_tokens=5),
                                 total_cost_usd=0.001, num_turns=1, is_error=False))
        stderr = ""

    def _fake_run(*a, **kw):
        return _FakeProc()

    X.subprocess.run = _fake_run
    try:
        rec = X.run("what does the mocked dispatch prove", dry_run=False)
    finally:
        X.subprocess.run = old_subprocess_run
        X.STATE, X.LEDGER, X.THREADS = old_state, old_ledger, old_threads

    check("a mocked success parses the result", rec.get("result") == "mocked answer", rec)
    check("...and the session id", rec.get("session_id") == "s-mocked", rec)
    check("...and usage", rec.get("usage", {}).get("input_tokens") == 10, rec)
    check("no error on a clean mocked success", not rec.get("error"), rec)

    with open(tmp_ledger, encoding="utf-8") as fh:
        ledger_lines = [json.loads(l) for l in fh if l.strip()]
    check("a real dispatch appends one ledger line", len(ledger_lines) == 1, ledger_lines)

    with open(tmp_threads, encoding="utf-8") as fh:
        saved = json.load(fh)
    check("the thread is saved under the mocked session id", "s-mocked" in saved, saved)
    if "s-mocked" in saved:
        # This is the exact line dispatch()'s split from run() nearly broke: it used to read the
        # bare local `task`, which only existed in run()'s own scope, not dispatch()'s.
        check("...with the ROUTE's task text as its subject (not truncated, not blank)",
              saved["s-mocked"]["subject"] == "what does the mocked dispatch prove", saved["s-mocked"])

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
