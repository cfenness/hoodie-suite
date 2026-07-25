#!/usr/bin/env python3
"""dispatch_ephemeral.py — the pull scheduler. Spawns an EPHEMERAL Fly machine per DUE source.

Runs on a cron (see .github/workflows/dispatch.yml, every 30 min) so pulls happen without anyone at a
keyboard. Each tick:
  1. reads the shared warehouse ledger → which enabled sources are past their interval (run_sources.due_sources),
  2. skips any source that already has a running ephemeral machine (no double-spawn),
  3. spawns a throwaway Fly machine per remaining due source (`flyctl machine run --rm --detach ...
     bash run_ephemeral.sh <source>`), tagged metadata role=ephemeral-pull,source=<id>.

Isolation is inherited from the ephemeral model: each pull is its own machine, deploys don't touch running
ones, and adding a source just makes it show up as "due" next tick — no redeploy, nothing else disturbed.

Env: FLY_API_TOKEN (flyctl auth) + the Tigris warehouse creds (AWS_* / BUCKET_NAME) so due_sources can read
the ledger. Tunables: DISPATCH_MAX (max machines to spawn per tick, default 6), DISPATCH_APP.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

APP = os.environ.get("DISPATCH_APP", "hoodie-suite")
MAX_SPAWN = int(os.environ.get("DISPATCH_MAX", "6"))          # cap the per-tick fan-out (image pulls + cost)
_HEADFUL = ("mac",)                                           # klass that drives headful Chrome → needs 8gb


def _fly(args, timeout=120):
    return subprocess.run(["flyctl"] + args, capture_output=True, text=True, timeout=timeout)


def current_image():
    """The image the serving app is running — ephemeral machines use the same code."""
    r = _fly(["releases", "--image", "-a", APP])
    for line in r.stdout.splitlines():
        for tok in line.split():
            if tok.startswith("registry.fly.io/"):
                return tok
    return None


def running_sources():
    """Sources that already have a live ephemeral machine (don't spawn a second)."""
    r = _fly(["machines", "list", "-a", APP, "--json"])
    out = set()
    try:
        for m in json.loads(r.stdout or "[]"):
            md = ((m.get("config") or {}).get("metadata") or {})
            if md.get("role") == "ephemeral-pull" and m.get("state") in (
                    "created", "starting", "started", "replacing"):
                out.add(md.get("source"))
    except Exception:
        pass
    return out


def spawn(sid, image, klass, mem_hint=None):
    # per-source `mem` override (a registry field) wins — a derived pass that accumulates into a huge table
    # (e.g. ttb-enrich merging the 1.86M ttb_cola_detail) needs headroom the 4GB headless default can't give.
    mem = str(mem_hint) if mem_hint else ("8192" if klass in _HEADFUL else "4096")
    r = _fly(["machine", "run", image, "-a", APP, "--rm", "--detach", "--restart", "no",
              "--vm-memory", mem, "--vm-cpu-kind", "shared", "--vm-cpus", "4",
              "--metadata", "role=ephemeral-pull", "--metadata", "source=" + sid,
              "bash", "/app/unifyd/run_ephemeral.sh", sid])
    ok = r.returncode == 0 or "Machine ID" in (r.stdout + r.stderr)
    if not ok:
        print("  spawn %s FAILED: %s" % (sid, (r.stderr or r.stdout).strip()[:160]))
    return ok


def main():
    if not os.environ.get("FLY_API_TOKEN"):
        print("dispatch: FLY_API_TOKEN not set"); return 1
    import run_sources
    image = current_image()
    if not image:
        print("dispatch: could not resolve current image"); return 1
    due = run_sources.due_sources()
    running = running_sources()
    todo = [s for s in due if s["id"] not in running]
    print("dispatch: %d due, %d already running, spawning up to %d | image=%s"
          % (len(due), len(due) - len(todo), MAX_SPAWN, image.rsplit(":", 1)[-1]))
    spawned = []
    for s in todo[:MAX_SPAWN]:
        if spawn(s["id"], image, s.get("klass"), s.get("mem")):
            spawned.append(s["id"])
    deferred = [s["id"] for s in todo[MAX_SPAWN:]]
    print("dispatch: spawned=%s | skipped-running=%s | deferred-to-next-tick=%s"
          % (spawned, sorted(running & {s["id"] for s in due}), deferred))
    return 0


if __name__ == "__main__":
    sys.exit(main())
