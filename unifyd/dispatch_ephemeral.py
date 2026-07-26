#!/usr/bin/env python3
"""dispatch_ephemeral.py — the pull scheduler. Spawns an EPHEMERAL Fly machine per DUE source.

Runs on a schedule (a Fly SCHEDULED MACHINE, hourly — no GitHub Actions) so pulls happen without anyone at a
keyboard. Each tick:
  1. reads the shared warehouse ledger → which enabled sources are past their interval (run_sources.due_sources),
  2. skips any source that already has a running ephemeral machine (no double-spawn),
  3. spawns a throwaway Fly machine per remaining due source (auto_destroy, running run_ephemeral.sh <source>),
     tagged metadata role=ephemeral-pull,source=<id>,
  4. refreshes the health digest off-Mac (used to be the workflow's second step).

Talks to Fly via the **Machines REST API** (api.machines.dev, stdlib urllib) — NOT the flyctl CLI — so it runs
from the plain app image (no binary) with only FLY_API_TOKEN in env. Isolation is inherited from the ephemeral
model: each pull is its own machine, deploys don't touch running ones, adding a source just makes it "due".

Env: FLY_API_TOKEN (a Fly SECRET, so app machines get it) + the Tigris warehouse creds (AWS_* / BUCKET_NAME,
already app secrets) so due_sources can read the ledger. Tunables: DISPATCH_MAX (per-tick cap, default 6),
DISPATCH_APP, DISPATCH_HEALTH=0 to skip the digest step.
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

APP = os.environ.get("DISPATCH_APP", "hoodie-suite")
MAX_SPAWN = int(os.environ.get("DISPATCH_MAX", "6"))          # cap the per-tick fan-out (image pulls + cost)
_HEADFUL = ("mac",)                                           # klass that drives headful Chrome → needs 8gb
_API = "https://api.machines.dev/v1"


def _api(method, path, body=None, timeout=60):
    tok = os.environ.get("FLY_API_TOKEN", "")
    req = urllib.request.Request(
        _API + path, method=method,
        data=(json.dumps(body).encode() if body is not None else None),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError("Fly API %s %s -> %s %s" % (method, path, e.code, detail))


def _machines():
    return _api("GET", "/apps/%s/machines" % APP)


def current_image():
    """The image the SERVING app machine runs — ephemeral pulls use the exact same code."""
    for m in _machines():
        md = ((m.get("config") or {}).get("metadata") or {})
        if md.get("role") != "ephemeral-pull" and m.get("state") == "started":
            img = (m.get("config") or {}).get("image")
            if img:
                return img
    return None


def running_sources():
    """Sources that already have a live ephemeral machine (don't spawn a second)."""
    out = set()
    for m in _machines():
        md = ((m.get("config") or {}).get("metadata") or {})
        if md.get("role") == "ephemeral-pull" and m.get("state") in (
                "created", "starting", "started", "replacing"):
            out.add(md.get("source"))
    return out


def spawn(sid, image, klass, mem_hint=None):
    # per-source `mem` override (a registry field) wins — a pass that accumulates into a huge table needs
    # headroom the 4GB headless default can't give (e.g. ttb-enrich). Headful (mac) klass → 8gb for Chrome.
    mem = int(mem_hint) if mem_hint else (8192 if klass in _HEADFUL else 4096)
    config = {
        "image": image,
        "auto_destroy": True,                                # == flyctl --rm: Fly removes it when the cmd exits
        "restart": {"policy": "no"},
        "guest": {"cpu_kind": "shared", "cpus": 4, "memory_mb": mem},
        "metadata": {"role": "ephemeral-pull", "source": sid},
        "init": {"cmd": ["bash", "/app/unifyd/run_ephemeral.sh", sid]},
    }
    try:
        r = _api("POST", "/apps/%s/machines" % APP, {"config": config})
        return bool(r.get("id"))
    except Exception as e:
        print("  spawn %s FAILED: %s" % (sid, str(e)[:180]))
        return False


def _refresh_health(log=print):
    """Fold in what the GitHub workflow's second step did: recompute the health digest off-Mac each tick."""
    if os.environ.get("DISPATCH_HEALTH") == "0":
        return
    try:
        import health_digest
        if hasattr(health_digest, "main"):
            health_digest.main([])
        else:
            health_digest.run()
        log("dispatch: health digest refreshed")
    except SystemExit:
        pass
    except Exception as e:
        log("dispatch: health digest skipped: %s" % str(e)[:100])


def main():
    if not os.environ.get("FLY_API_TOKEN"):
        print("dispatch: FLY_API_TOKEN not set (needs to be a Fly SECRET on the app)")
        return 1
    import run_sources
    image = current_image()
    if not image:
        print("dispatch: could not resolve current image via the Machines API")
        return 1
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
    _refresh_health()
    return 0


if __name__ == "__main__":
    sys.exit(main())
