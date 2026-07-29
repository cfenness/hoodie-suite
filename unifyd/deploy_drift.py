#!/usr/bin/env python3
"""deploy_drift.py — is the code RUNNING in production the code we last deployed on purpose?

Twice in one day production silently stopped matching main. v623 shipped a merged feature; by v627
`/app/unifyd/locator_signal.py` was absent from the container. Four releases in between all read
`complete`. Nothing detected it — it was found by hand, by SSHing in and grepping, because someone
happened to wonder.

`flyctl releases` cannot see this class of problem: it reports that a deploy succeeded, not WHAT it
deployed. And the deploy guard only protects the machine it is installed on — a deploy from anywhere
else still lands.

So the check is: record what we deployed, then keep asking the live app what it is running.

  release_train.py deploy  ──▶ record(sha, fingerprint)   [after a VERIFIED origin/main deploy]
  hourly dispatcher        ──▶ check()  ──▶ health digest finding

A deploy made any other way does not update the expectation, so the live fingerprint stops matching
and the next hourly tick says so. That is the whole point: the thing we cannot prevent, we detect.

The fingerprint is computed over the code that actually ships, so it is independent of git — which
matters because the container has no `.git` and the repo is private, so the running app cannot ask
GitHub what main is. It only has to answer "am I still what was intended", and a recorded
expectation is enough for that.

    python3 unifyd/deploy_drift.py fingerprint [root]   # what this tree hashes to
    python3 unifyd/deploy_drift.py check                # compare live vs expected
"""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_KEY = "_deploy_expected.json"
LIVE_URL = os.environ.get("HOODIE_LIVE_URL", "https://hoodie-suite.fly.dev")

# What counts as "the code". Deliberately narrow and deterministic: the served apps and the engine.
# Runtime artifacts, caches and data are excluded — they change constantly and would make the
# fingerprint meaningless, which is the failure mode that turns a check into noise people ignore.
_DIRS = ("unifyd", "apps", "tools", "spine")
_EXT = (".py", ".html", ".js", ".css", ".json", ".sh", ".toml")
_SKIP_PARTS = ("__pycache__", "_archive", "agent_state", "/out/", "cola_out", "full_out",
               "specs_out", "walmart_raw", "full_pull_out", "fixtures", "node_modules", ".claude")
_SKIP_SUFFIX = ("_snapshot.json", ".local.json", ".pyc", ".log")


def _eligible(rel):
    if any(p in rel for p in _SKIP_PARTS):
        return False
    if rel.endswith(_SKIP_SUFFIX):
        return False
    return rel.endswith(_EXT)


def fingerprint(root=None):
    """A stable hash of the shipped code, plus the file count that produced it.

    Returns {"sha256": hex, "files": n}. The count travels with the hash on purpose: a fingerprint
    mismatch alone cannot tell you whether a file changed or 400 files vanished, and those want very
    different reactions.
    """
    root = root or ROOT
    h, n = hashlib.sha256(), 0
    for d in _DIRS:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames.sort()
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                if not _eligible(rel.replace(os.sep, "/")):
                    continue
                try:
                    with open(full, "rb") as f:
                        digest = hashlib.sha256(f.read()).hexdigest()
                except OSError:
                    continue
                h.update(rel.encode()); h.update(b"\0"); h.update(digest.encode()); h.update(b"\n")
                n += 1
    return {"sha256": h.hexdigest(), "files": n}


# --- the recorded expectation ------------------------------------------------------------------
def record(git_sha, fp=None, root=None, log=print):
    """Store what we just deployed, on purpose. Called ONLY after a verified origin/main deploy."""
    import warehouse
    fp = fp or fingerprint(root)
    payload = {"git_sha": git_sha, "fingerprint": fp["sha256"], "files": fp["files"],
               "recorded_at": int(time.time())}
    # The baseline is only useful if the CHECKER can read it, and the checker runs on Fly against
    # the shared warehouse. Writing to the local fallback "succeeds" and is useless — worse than
    # useless, because it logs success. This shipped once: release_train ran without warehouse
    # creds, wrote into its own throwaway deploy worktree, said "recorded", and the next check
    # reported no baseline at all.
    if not warehouse.remote():
        log("  [drift] NOT recorded — no shared warehouse configured (creds absent), so the "
            "baseline would go to a local path the hourly check cannot read. Drift detection "
            "stays OFF until this deploy runs somewhere with warehouse credentials.")
        return None
    try:
        warehouse.put_bytes(STATE_KEY, json.dumps(payload).encode())
        log("  [drift] recorded expected build %s (%d files, fp %s…) -> shared warehouse"
            % (git_sha[:8], fp["files"], fp["sha256"][:12]))
        return payload
    except Exception as e:                                    # noqa: BLE001
        log("  [drift] could NOT record the expected build (%s) — the next check will report "
            "'no expectation' rather than silently passing" % str(e)[:70])
        return None


def expected(log=print):
    import warehouse
    try:
        return json.loads(warehouse.get_bytes(STATE_KEY).decode())
    except Exception:                                         # noqa: BLE001
        return None


# --- the check ---------------------------------------------------------------------------------
def _fetch_live(url=None, timeout=20):
    import urllib.request
    u = (url or LIVE_URL).rstrip("/") + "/api/version"
    with urllib.request.urlopen(u, timeout=timeout) as r:
        return json.loads(r.read().decode())


def check(live=None, exp=None, fetch=None, log=print):
    """[(code, severity, summary, evidence)] — empty means live matches the recorded expectation.

    Every path states what it concluded and why; none of them returns an empty list to mean
    "couldn't tell". A check that goes quiet when it fails to run is indistinguishable from a check
    that ran and found nothing, which is how two guards were silently inert earlier today.
    """
    exp = exp if exp is not None else expected(log=log)
    if not exp:
        return [("drift-no-baseline", "warn",
                 "no expected build recorded — cannot tell whether production matches main",
                 "deploy once with `python3 tools/release_train.py deploy` to set the baseline")]
    if live is None:
        try:
            live = (fetch or _fetch_live)()
        except Exception as e:                                # noqa: BLE001
            return [("drift-unreachable", "warn",
                     "could not read the live build stamp — drift is UNKNOWN, not absent",
                     "GET /api/version failed: %s" % str(e)[:90])]

    lf, ef = live.get("fingerprint"), exp.get("fingerprint")
    if not lf:
        return [("drift-unreachable", "warn",
                 "the live app returned no fingerprint — drift is UNKNOWN",
                 "payload: %s" % json.dumps(live)[:120])]
    if lf == ef:
        return []
    lost = (exp.get("files") or 0) - (live.get("files") or 0)
    detail = ("%d fewer code files than expected — this is what a deploy from a stale tree looks "
              "like" % lost) if lost > 0 else ("file count %s vs expected %s"
                                               % (live.get("files"), exp.get("files")))
    return [("deploy-drift", "critical",
             "PRODUCTION IS NOT THE BUILD WE DEPLOYED — %s" % detail,
             "live fp %s… (%s files) vs expected %s… (%s files, git %s, recorded %s ago); "
             "`flyctl releases` will still read 'complete'"
             % (lf[:12], live.get("files"), (ef or "")[:12], exp.get("files"),
                (exp.get("git_sha") or "?")[:8],
                _ago(time.time() - (exp.get("recorded_at") or time.time()))))]


def _ago(s):
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return "%.1f%s" % (s / n, unit)
    return "%ds" % max(0, int(s))


def findings(log=print):
    """health_digest-shaped findings, so the hourly digest can absorb these unchanged."""
    out = []
    for code, sev, summary, evidence in check(log=log):
        out.append({"code": code, "severity": sev, "source": "deploy", "summary": summary,
                    "evidence": evidence, "kind": "DETERMINISTIC"})
    return out


def main(argv):
    cmd = argv[0] if argv else "check"
    if cmd == "record":
        # Invoked as a SUBPROCESS by release_train under a pyarrow-capable interpreter. warehouse's
        # put_bytes goes through pyarrow.fs.S3FileSystem, which the system python does not have —
        # and the two interpreters on this machine have DISJOINT capabilities (venv 3.9: pyarrow +
        # duckdb, no tomllib; system 3.14: tomllib, no pyarrow), so "just use python3" is wrong
        # whichever one you mean.
        if len(argv) < 2:
            print("usage: deploy_drift.py record <git_sha> [root]", file=sys.stderr)
            return 1
        return 0 if record(argv[1], root=(argv[2] if len(argv) > 2 else None)) else 1
    if cmd == "fingerprint":
        fp = fingerprint(argv[1] if len(argv) > 1 else None)
        print("%s  (%d files)" % (fp["sha256"], fp["files"]))
        return 0
    res = check()
    if not res:
        exp = expected()
        print("deploy-drift: OK — live matches the expected build (git %s, %s files)"
              % ((exp or {}).get("git_sha", "?")[:8], (exp or {}).get("files")))
        return 0
    for code, sev, summary, evidence in res:
        print("[%s] %s: %s\n    %s" % (sev.upper(), code, summary, evidence))
    return 2 if any(r[1] == "critical" for r in res) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
