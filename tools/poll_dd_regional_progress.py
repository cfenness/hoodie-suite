#!/usr/bin/env python3
"""Tail the DoorDash Southeast-regional metro-scoped shards (2 machines, one shard each) over TWO
persistent SSH sessions (remote-side `tail -f`, not repeated flyctl invocations — see the
no-tight-loop-flyctl rule) and append a running trend row to a local doc every ~30s.

Unlike poll_ue_progress.py (one machine, 8 shards multiplexed over one SSH loop), each DoorDash shard
here lives on its OWN machine, so this opens one `tail -f` subprocess per machine and merges their
output on a shared timer. Store completions are counted by matching doordash_full.py's own
"store <id> — <n> items" log line (there is no "N/M stores" stage line like ue_catalog.py emits, so
"done" here is a running COUNT of completions seen this poller session, not a resumed total).
"""
import os as _os
import re
import subprocess
import sys
import threading
import time

DOC = sys.argv[1] if len(sys.argv) > 1 else "doordash-regional-run-2026-08-03.md"
SHARD_SPECS = [
    ("2862e79f34e3e8", "/tmp/dd_metro_shard0.log", 0),
    ("286d23f7416518", "/tmp/dd_metro_shard1.log", 1),
]

# The warehouse mirror key, and it must equal this run's id in server.py's _RUN_DOCS.
# It is DECLARED, not derived: the old `basename.split("-")[0]` gave "doordash" for
# "doordash-regional-run-*.md", so this poller mirrored regional ticks over the FULL-catalog run's
# key — whichever poller ticked last won, silently, under the other run's label. Any id containing
# a dash breaks that rule, and "doordash-regional" does. Override with argv[2] if the doc changes.
SID = sys.argv[2] if len(sys.argv) > 2 else "doordash-regional"

# Resolved, not hardcoded to one person's home directory — this file is in the repo now.
# poll_ue_progress.py still embeds the absolute path; left alone rather than touch a working
# tracked script, but new pollers should use this form.
FLYCTL = _os.environ.get("FLYCTL") or _os.path.expanduser("~/.fly/bin/flyctl")

STORE_RE = re.compile(r"store \S+ — (\d+) items")
ISP_RE = re.compile(r"'isp\.success_pct':\s*([\d.]+)")

lock = threading.Lock()
state = {i: {"done": 0, "items": 0, "isp": None} for _, _, i in SHARD_SPECS}


def _tail(machine, path, shard_i):
    cmd = [
        FLYCTL, "ssh", "console",
        "-a", "hoodie-suite", "--machine", machine,
        "-C", "tail -F -n0 %s" % path,
    ]
    while True:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1)
            for line in proc.stdout:
                m = STORE_RE.search(line)
                if m:
                    with lock:
                        state[shard_i]["done"] += 1
                        state[shard_i]["items"] += int(m.group(1))
                    continue
                m = ISP_RE.search(line)
                if m:
                    with lock:
                        state[shard_i]["isp"] = float(m.group(1))
        except Exception:
            pass
        time.sleep(10)   # dead SSH pipe — reconnect rather than give up (same as poll_ue_progress.py)


LATEST_RE = re.compile(r"<!-- LATEST_START -->.*?<!-- LATEST_END -->", re.DOTALL)


def write_tick(t_start):
    with lock:
        snap = {k: dict(v) for k, v in state.items()}
    total_done = sum(v["done"] for v in snap.values())
    total_items = sum(v["items"] for v in snap.values())
    isps = [v["isp"] for v in snap.values() if v["isp"] is not None]
    avg_isp = sum(isps) / len(isps) if isps else float("nan")
    elapsed_m = (time.time() - t_start) / 60.0
    now_s = time.strftime("%H:%M:%S", time.localtime())
    row = "| %s | %.1f min | %d | %d | %d | %.1f%% |\n" % (
        now_s, elapsed_m, snap[0]["done"], snap[1]["done"], total_items, avg_isp)
    latest = ("**Latest (%s, %.1f min in):** %d stores done (shard0=%d, shard1=%d) — "
              "**%d items landed** — avg ISP success %.1f%%" % (
                  now_s, elapsed_m, total_done, snap[0]["done"], snap[1]["done"],
                  total_items, avg_isp))
    with open(DOC, "r") as f:
        content = f.read()
    block = "<!-- LATEST_START -->\n%s\n<!-- LATEST_END -->" % latest
    content = LATEST_RE.sub(block, content, count=1)
    content += row
    with open(DOC, "w") as f:
        f.write(content)
    _push_to_warehouse(content)


def _push_to_warehouse(content):
    """Best-effort mirror to Tigris so the deployed app can read this Mac's live progress — never
    lets a warehouse hiccup break the poller; the local file above is still written unconditionally."""
    try:
        sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "unifyd"))
        import warehouse
        warehouse.put_bytes("_scrape_progress/%s.md" % SID, content.encode())
    except Exception:
        pass


def main():
    t_start = time.time()
    for machine, path, shard_i in SHARD_SPECS:
        t = threading.Thread(target=_tail, args=(machine, path, shard_i), daemon=True)
        t.start()
    while True:
        time.sleep(30)
        write_tick(t_start)


if __name__ == "__main__":
    main()
