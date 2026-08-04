#!/usr/bin/env python3
"""Tail the UberEats fresh-IP run on the isolated Fly machine over ONE persistent
SSH session (remote-side while/sleep loop, not repeated flyctl invocations — see
the no-tight-loop-flyctl rule) and append a running trend row to a local doc every
~30s. Reads flyctl's stdout as it streams; never polls flyctl itself.

Each shard's line is EXPLICITLY tagged (__SHARD__ N) by the remote loop, and we keep
a running max per shard keyed by that tag — not by list position — because a dropped
or delayed read for one shard over a long-lived stream must never pull the reported
total backwards. The underlying scraper's own `done` checkpoint set is verified
strictly monotonic (grows via set.add() only, one process per shard, no restarts);
any apparent decrease is a stale/missing read for that tick, never real regression.
"""
import json
import re
import subprocess
import sys
import time

DOC = sys.argv[1] if len(sys.argv) > 1 else "progress.md"
MACHINE = sys.argv[2] if len(sys.argv) > 2 else "80205ef6412028"
N_SHARDS = 8

# The doc lives only on this Mac. SID (the first "-"-delimited token of the filename, e.g.
# "ubereats" out of "ubereats-fresh-ip-run-2026-07-30.md") is the key the deployed app's
# /api/scrape-progress/<sid> endpoint reads back — it's what carries this Mac's live progress log
# to a Fly-served page, which otherwise has no way to see this machine's filesystem.
import os as _os
SID = _os.path.basename(DOC).split("-")[0].split(".")[0]

REMOTE_LOOP = (
    "sh -c 'while true; do echo __TICK__ $(date -u +%s); "
    "for i in 0 1 2 3 4 5 6 7; do echo __SHARD__ $i; tail -n1 /tmp/ue_shard_$i.log 2>/dev/null; done; "
    "sleep 30; done'"
)

# Resolved, not hardcoded to one person's home directory. Same form as tools/repin_dispatcher.sh
# ("${FLYCTL:-$HOME/.fly/bin/flyctl}") and tools/poll_dd_regional_progress.py, so every tool in
# here finds flyctl the same way and an override works everywhere.
FLYCTL = _os.environ.get("FLYCTL") or _os.path.expanduser("~/.fly/bin/flyctl")

cmd = [
    FLYCTL, "ssh", "console",
    "-a", "hoodie-suite", "--machine", MACHINE, "-C", REMOTE_LOOP,
]

PROG_RE = re.compile(r"HOODIE_PROGRESS (\{.*\})")
STAGE_RE = re.compile(r"([\d,]+)/([\d,]+) stores")
SHARD_RE = re.compile(r"^__SHARD__ (\d+)$")

# running best-known state per shard index: {shard: {"rows": int, "done": int, "universe": int, "isp": float}}
best = {}

# "rows" (unlike "done") is a per-PROCESS in-memory counter in ue_catalog.py — it resets to 0 every
# time a shard restarts, because it's meant as "this run's own landed count", not a cumulative total.
# A restart is real and expected (we killed/relaunched shards mid-session more than once today), so the
# tracker must not just echo that raw reset as if progress went backwards — the whole point of this
# number is to answer "bigger or smaller than before", and a restart should never make it read smaller.
# _row_offset banks the peak seen before each detected restart (raw value drops below the last one) so
# the DISPLAYED total keeps climbing across restarts the same way "done" already does via its warehouse
# checkpoint — same signal, same guarantee.
#
# STATE IS PERSISTED TO DISK (not just this process's memory) — a real, confirmed bug: this poller
# itself has died and been restarted 3+ times in one session (dead SSH pipes, not scraper crashes), and
# every restart used to re-run _floor_applied=False from scratch, re-adding the ENTIRE historical floor
# on top of whatever the shards' current raw counts already were. That produced a fake "huge first
# reading" after every poller restart that had nothing to do with real scrape throughput — caught only
# because the reported number looked implausibly good compared to steady-state. Persisting the offset
# state means a poller restart resumes exactly where the last one left off instead of double-counting.
_STATE_FILE = DOC + ".rowstate.json"
HISTORICAL_ROWS_FLOOR = 864629


def _load_row_state():
    try:
        with open(_STATE_FILE) as f:
            d = json.load(f)
        return {int(k): v for k, v in d.get("offset", {}).items()}, \
               {int(k): v for k, v in d.get("last_raw", {}).items()}, \
               d.get("floor_applied", False)
    except Exception:
        return {}, {}, False


def _save_row_state():
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump({"offset": _row_offset, "last_raw": _row_last_raw,
                      "floor_applied": _floor_applied[0]}, f)
    except Exception:
        pass


_row_offset, _row_last_raw, _floor_applied_val = _load_row_state()
_floor_applied = [_floor_applied_val]


def _cumulative_rows(shard, raw_rows):
    if shard == 0 and not _floor_applied[0]:
        _row_offset[0] = _row_offset.get(0, 0) + HISTORICAL_ROWS_FLOOR
        _floor_applied[0] = True
    last = _row_last_raw.get(shard)
    if last is not None and raw_rows < last:
        _row_offset[shard] = _row_offset.get(shard, 0) + last   # bank the pre-restart peak
    _row_last_raw[shard] = raw_rows
    _save_row_state()
    return _row_offset.get(shard, 0) + raw_rows


def ingest(shard, line):
    m = PROG_RE.search(line)
    if not m:
        return
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return
    sm = STAGE_RE.search(d.get("stage", ""))
    done = int(sm.group(1).replace(",", "")) if sm else None
    universe = int(sm.group(2).replace(",", "")) if sm else None
    prev = best.get(shard)
    # never let a stale/short read regress a shard we've already seen further along
    if prev is not None and done is not None and done < prev.get("done", -1):
        return
    raw_rows = d.get("rows")
    cum_rows = _cumulative_rows(shard, raw_rows) if raw_rows is not None else (prev["rows"] if prev else 0)
    best[shard] = {
        "rows": cum_rows,
        "done": done if done is not None else (prev["done"] if prev else 0),
        "universe": universe if universe is not None else (prev["universe"] if prev else 0),
        "isp": d.get("isp.success_pct", prev.get("isp") if prev else None),
        "exits_burned": d.get("exits_burned", 0),
        "pace_backoffs": d.get("pace_backoffs", 0),
    }


LATEST_RE = re.compile(r"<!-- LATEST_START -->.*?<!-- LATEST_END -->", re.DOTALL)


def summarize(t_start):
    total_rows = sum(v["rows"] for v in best.values())
    total_done = sum(v["done"] for v in best.values())
    total_universe = sum(v["universe"] for v in best.values())
    isps = [v["isp"] for v in best.values() if v["isp"] is not None]
    avg_isp = sum(isps) / len(isps) if isps else float("nan")
    burned = sum(v["exits_burned"] for v in best.values())
    backoffs = sum(v["pace_backoffs"] for v in best.values())
    pct = 100.0 * total_done / total_universe if total_universe else 0.0
    elapsed_m = (time.time() - t_start) / 60.0
    now_s = time.strftime("%H:%M:%S", time.localtime())
    row = ("| %s | %.1f min | %d/%d shards seen | %d | %d/%d (%.2f%%) | "
           "%.1f%% | %d | %d |\n" % (
               now_s, elapsed_m, len(best), N_SHARDS, total_rows, total_done,
               total_universe, pct, avg_isp, burned, backoffs))
    latest = ("**Latest (%s, %.1f min in):** %d/%d shards seen — **%d rows** — "
              "**%d/%d stores done (%.2f%%)** — avg ISP success %.1f%% — "
              "exits burned %d — pace backoffs %d" % (
                  now_s, elapsed_m, len(best), N_SHARDS, total_rows, total_done,
                  total_universe, pct, avg_isp, burned, backoffs))
    return row, latest


def write_tick(t_start):
    if not best:
        return
    row, latest = summarize(t_start)
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
    lets a warehouse hiccup break the poller; the local file above is still written unconditionally
    and remains the source of truth."""
    try:
        sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "unifyd"))
        import warehouse
        warehouse.put_bytes("_scrape_progress/%s.md" % SID, content.encode())
    except Exception:
        pass


def main():
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, bufsize=1)
    cur_shard = None
    t_start = time.time()
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line.startswith("__TICK__"):
            write_tick(t_start)
            cur_shard = None
            continue
        sm = SHARD_RE.match(line)
        if sm:
            cur_shard = int(sm.group(1))
            continue
        if cur_shard is not None:
            ingest(cur_shard, line)


if __name__ == "__main__":
    main()
