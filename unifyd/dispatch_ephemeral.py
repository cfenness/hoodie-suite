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
    # .strip() — a secret pasted with a trailing newline/space breaks the Authorization header
    # ("Invalid header value"), which HTTP forbids; the token itself keeps its internal 'FlyV1 ' space.
    tok = os.environ.get("FLY_API_TOKEN", "").strip()
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


def _journal_id(sid):
    """A Hoodie Collect run id for a dispatcher-spawned run. Generated HERE (not on the pull machine) so
    the id exists before the machine does — otherwise a machine that dies during boot leaves no journal
    at all, which is exactly the blind spot the journal is meant to remove."""
    try:
        import run_journal
        return run_journal.new_id(sid)
    except Exception:
        return None


def current_image():
    """The image the SERVING app machine runs — ephemeral pulls use the exact same code.

    Select the `app` PROCESS GROUP explicitly. The old test ("any started machine that isn't an
    ephemeral pull") also matched THIS dispatcher machine, which is `started` while the tick runs —
    and the dispatcher is not updated by `flyctl deploy` (it has no process group), so on an
    unlucky API ordering it handed its own STALE image to every pull it spawned.
    """
    for m in _machines():
        cfg = m.get("config") or {}
        md = cfg.get("metadata") or {}
        if md.get("fly_process_group") == "app" and m.get("state") == "started":
            if cfg.get("image"):
                return cfg["image"]
    return None


def image_tag(ref):
    """Normalize a Fly image ref to the thing worth comparing — its release tag.

    The SAME release is reported in different shapes depending on how a machine was configured:
      registry.fly.io/app:deployment-XYZ                     (app machines, after a deploy)
      registry.fly.io/app:deployment-XYZ@sha256:abc...       (a machine pinned by `machine update`)
      registry.fly.io/app@sha256:abc...                      (pinned by digest only)
    Comparing these as raw strings reports a permanent false STALE, so pull out `deployment-XYZ`
    when a tag is present and fall back to the digest when it is not.
    """
    if not ref:
        return ""
    name = ref.rsplit("/", 1)[-1]                  # drop the registry host
    tag = name.split("@", 1)[0]                    # drop any @sha256:... suffix
    if ":" in tag:
        return tag.split(":", 1)[1]                # → deployment-XYZ
    return name.split("@", 1)[1] if "@" in name else name


def _warn_if_stale(app_image):
    """The dispatcher machine is pinned at creation and `flyctl deploy` does NOT touch it (no
    process group). Stale here is not cosmetic: due-ness is computed from THIS machine's copy of
    source_registry, so a newly added source stays invisible until the machine is re-pinned.
    Make that loud rather than silent — see tools/repin_dispatcher.sh."""
    mine = ""
    mid = os.environ.get("FLY_MACHINE_ID", "")
    if mid:                                        # read our OWN config from the same API as the
        try:                                       # app's, so both refs come back in one shape
            for m in _machines():
                if m.get("id") == mid:
                    mine = (m.get("config") or {}).get("image", "")
                    break
        except Exception:
            return
    if not mine:
        mine = os.environ.get("FLY_IMAGE_REF", "")
    if not mine or not app_image:
        return
    if image_tag(mine) != image_tag(app_image):
        print("dispatch: WARNING — dispatcher image is STALE (self=%s app=%s). Its source_registry "
              "is older than the deployed one, so newly added sources will NOT dispatch. Re-pin "
              "with tools/repin_dispatcher.sh." % (image_tag(mine), image_tag(app_image)))


def running_sources():
    """Sources that already have a live ephemeral machine (don't spawn a second)."""
    out = set()
    for m in _machines():
        md = ((m.get("config") or {}).get("metadata") or {})
        if md.get("role") == "ephemeral-pull" and m.get("state") in (
                "created", "starting", "started", "replacing"):
            out.add(md.get("source"))
    return out


def conflicting_machine(sid, shard=None, self_id=None):
    """The id of another live machine that a NEW launch of `sid` (this `shard`, if sharded) would
    collide with — or None if it's safe to proceed. This is the check `run_ephemeral.py` runs on EVERY
    launch, not just the dispatcher's own scheduled tick, because a manual UI click or a stray direct
    `flyctl machine run` bypasses running_sources() entirely — only a check enforced at the point a
    machine actually starts doing work can catch every launch path. Learned the hard way: 4 duplicate
    manual `ubereats` machines ran concurrently against the same 12-hour-rested proxy pool on
    2026-07-30, burning through it before the isolated run they were supposed to leave alone finished.

    An UNSHARDED launch (shard=None) reserves the WHOLE source — it conflicts with any other running
    machine for `sid`, sharded or not (a second full sweep would redundantly re-cover the same universe
    the sharded fleet is dividing). A SHARDED launch only reserves its own slice — it conflicts with
    another running machine for the same (sid, shard), or with an unsharded run of `sid` (which claims
    the whole source), but coexists fine with sibling shards at other indices."""
    for m in _machines():
        if self_id and m.get("id") == self_id:
            continue
        if m.get("state") not in ("created", "starting", "started", "replacing"):
            continue
        md = ((m.get("config") or {}).get("metadata") or {})
        if md.get("role") != "ephemeral-pull" or md.get("source") != sid:
            continue
        other_shard = md.get("shard")
        if shard is None or other_shard is None or other_shard == shard:
            return m.get("id")
    return None


def spawn(sid, image, klass, mem_hint=None, run_id=None, days=None, want_all=False,
          trigger="scheduled", env=None):
    """Create the ephemeral machine for one source. `run_id` attaches a Hoodie Collect journal so the
    run is watchable while it executes; `days`/`want_all` set a time-bound source's window. Returns the
    machine id (truthy) rather than a bare bool, so a caller can correlate the machine with the run."""
    # per-source `mem` override (a registry field) wins — a pass that accumulates into a huge table needs
    # headroom the 4GB headless default can't give (e.g. ttb-enrich). Headful (mac) klass → 8gb for Chrome.
    #
    # THE REGISTRY IS CONSULTED HERE, not only by the caller. The dispatcher does pass s.get("mem") at
    # every call site, but any OTHER caller — a manual kick, a backfill script, a future tool — that
    # omitted the hint silently got the generic 4GB default even though the source declares 16384. Not a
    # theoretical gap: aggregator-geo (mem=16384) was hand-spawned without a hint, landed on 4GB, and was
    # OOM-killed by the kernel with delta=0 and no error the caller could see. Resolving the declared
    # value inside spawn() makes the registry authoritative on EVERY path, so sizing cannot depend on a
    # caller remembering to look it up.
    declared = None
    try:
        from source_registry import SOURCES
        declared = next((int(x["mem"]) for x in SOURCES
                         if x.get("id") == sid and x.get("mem")), None)
    except Exception:                                        # noqa: BLE001 — sizing must never block a spawn
        pass
    mem = int(mem_hint) if mem_hint else (declared or (8192 if klass in _HEADFUL else 4096))
    if declared and mem < declared:
        # An explicit hint still wins (a deliberately smaller run is legitimate), but say so — an
        # undersized machine dies as an OOM SIGKILL, which reads as "the source is broken".
        print("  spawn %s: mem %dMB is BELOW the registry's declared %dMB — expect an OOM kill if this "
              "source needs the headroom" % (sid, mem, declared))
    # Fly caps shared-CPU RAM at 2 GB × cpus, so a big accumulate (src_outlets is 1.76M rows → the whole-table
    # merge peaks past 8 GB) needs the cpu count scaled up to unlock the memory. 8 shared cpus → 16 GB ceiling.
    cpus = max(4, min(8, -(-mem // 2048)))
    argv = ["bash", "/app/unifyd/run_ephemeral.sh", sid]
    if run_id:
        argv += ["--run-id", run_id]
    if want_all:
        argv += ["--all"]
    elif days is not None:
        argv += ["--days", str(int(days))]
    argv += ["--trigger", trigger]
    meta = {"role": "ephemeral-pull", "source": sid}
    if run_id:
        meta["run_id"] = run_id                              # lets the bench find the machine for a run
    if env and env.get("UE_SHARD"):
        meta["shard"] = env["UE_SHARD"]                      # so a fleet is legible in the machine list
    config = {
        "image": image,
        "auto_destroy": True,                                # == flyctl --rm: Fly removes it when the cmd exits
        "restart": {"policy": "no"},
        "guest": {"cpu_kind": "shared", "cpus": cpus, "memory_mb": mem},
        "metadata": meta,
        "init": {"cmd": argv},
    }
    # Per-machine env is what makes a FLEET possible: identical image, identical command, one variable
    # (UE_SHARD=i/N) telling each machine which slice of the universe it owns. Shards need no
    # coordination because the split is a stable hash, so they cannot overlap or leave a gap.
    if env:
        config["env"] = {str(k): str(v) for k, v in env.items()}
    try:
        r = _api("POST", "/apps/%s/machines" % APP, {"config": config})
        return r.get("id")
    except Exception as e:
        print("  spawn %s FAILED: %s" % (sid, str(e)[:180]))
        return None


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
        print("dispatch: could not resolve the app machine's image via the Machines API")
        return 1
    _warn_if_stale(image)
    due = run_sources.due_sources()
    running = running_sources()
    todo = [s for s in due if s["id"] not in running]
    print("dispatch: %d due, %d already running, spawning up to %d | image=%s"
          % (len(due), len(due) - len(todo), MAX_SPAWN, image.rsplit(":", 1)[-1]))
    spawned = []
    for s in todo[:MAX_SPAWN]:
        # A SHARDED SOURCE MUST DISPATCH AS A FLEET, unattended. The registry can declare `shards: N`;
        # without this the scheduler spawned ONE machine for a source whose whole design is N machines
        # splitting the universe by a stable hash — so a hand-kicked run was 8x faster than the same
        # source running on its own schedule, and nobody would see why. Each shard is one machine with
        # one env var; they need no coordination because the split is deterministic.
        nsh = int(s.get("shards") or 1)
        if nsh > 1:
            ids = []
            for i in range(nsh):
                mid = spawn(s["id"], image, s.get("klass"), s.get("mem"),
                            run_id=_journal_id(s["id"]), trigger="scheduled",
                            env={"UE_SHARD": "%d/%d" % (i, nsh), "HOODIE_SHARD": "%d/%d" % (i, nsh)})
                if mid:
                    ids.append(mid)
            if ids:
                spawned.append("%s x%d" % (s["id"], len(ids)))
            continue
        # Every scheduled run gets a journal too, not just bench-triggered ones — so Hoodie Collect shows
        # the dispatcher's own work live, and an overnight failure is inspectable the next morning instead
        # of leaving only a one-line ledger verdict.
        if spawn(s["id"], image, s.get("klass"), s.get("mem"),
                 run_id=_journal_id(s["id"]), trigger="scheduled"):
            spawned.append(s["id"])
    deferred = [s["id"] for s in todo[MAX_SPAWN:]]
    print("dispatch: spawned=%s | skipped-running=%s | deferred-to-next-tick=%s"
          % (spawned, sorted(running & {s["id"] for s in due}), deferred))

    # Derived master builds (dim_sku chain, master_quality, item_identity, the canon head-to-head) run as
    # their OWN ephemeral machines too — the same isolation as a source, off the Mac (nothing local). Each
    # build is due only when its upstream landed + its interval passed (run_sources.due_builds, ledger + the
    # `after` chain), so they self-sequence; running_sources() dedup keeps a build's dim_* write single. Share
    # the per-tick cap with sources so one tick can't fan out unbounded.
    remaining = max(0, MAX_SPAWN - len(spawned))
    b_spawned = []
    if remaining:
        due_b = [b for b in run_sources.due_builds() if b["id"] not in running]
        for b in due_b[:remaining]:
            if spawn(b["id"], image, b.get("klass"), b.get("mem"),
                     run_id=_journal_id(b["id"]), trigger="scheduled"):
                b_spawned.append(b["id"])
        if due_b:
            print("dispatch: builds due=%s spawned=%s deferred=%s"
                  % ([b["id"] for b in due_b], b_spawned, [b["id"] for b in due_b[remaining:]]))
    # RECONCILE LIVENESS AGAINST MACHINES. A destroyed machine never closes its own journal, so the
    # bench would show dead runs as `running` until the staleness timer trips 15 minutes later. We are
    # already holding the authoritative machine list here — use it.
    try:
        import run_journal
        run_journal.reconcile_live([m.get("id") for m in _machines()
                                    if m.get("state") in ("created", "starting", "started", "replacing")])
    except Exception as e:
        print("dispatch: liveness reconcile skipped: %s" % str(e)[:100])
    _refresh_health()
    return 0


if __name__ == "__main__":
    sys.exit(main())
