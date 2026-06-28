#!/usr/bin/env python3
"""schedule_pull.py — run a Unifyd pull on a cadence, locally, before the backend exists.

Layer-3-lite: a tiny loop that POSTs /api/run to the local agent every N hours, so timed
runs land in run history and show up in Hoodie Pulls exactly like manual ones (trigger:
"scheduled"). Start the agent first (./dev.sh or `python unifyd/server.py`), then:

    python unifyd/schedule_pull.py abc-fws --every 24h
    python unifyd/schedule_pull.py abc-fws --every 12h --sample 60
    python unifyd/schedule_pull.py ttb-cola --every 24h --once    # run now, then exit

It runs once immediately, then sleeps. Ctrl-C to stop. For a hands-off daily pull without
keeping a process alive, use cron instead:

    0 6 * * *  curl -s -XPOST localhost:8765/api/run -H 'Content-Type: application/json' \
                 -d '{"connId":"abc-fws","trigger":"scheduled"}' >/dev/null
"""
import argparse, json, sys, time, urllib.request, urllib.error


def parse_every(s):
    s = s.strip().lower()
    mult = {"h": 3600, "m": 60, "d": 86400, "s": 1}
    if s and s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(float(s) * 3600)   # bare number = hours


def run_once(agent, conn_id, extra):
    body = dict(extra or {}); body.update(connId=conn_id, trigger="scheduled")
    req = urllib.request.Request(agent.rstrip("/") + "/api/run",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3600) as r:
        rec = json.load(r)
    extra_n = rec.get("extracts", [{}])
    delta = extra_n[0].get("delta") if extra_n else None
    return rec.get("status", "?"), rec.get("total", 0), delta


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a Unifyd pull on a cadence (local, pre-backend).")
    ap.add_argument("conn_id", help="connId to run, e.g. abc-fws / ttb-cola / fl-items")
    ap.add_argument("--every", default="24h", help="interval: 24h, 12h, 30m, 90s, or a bare number of hours")
    ap.add_argument("--agent", default="http://127.0.0.1:8765", help="agent base URL")
    ap.add_argument("--sample", type=int, help="abc-fws: how many SKUs to poll")
    ap.add_argument("--once", action="store_true", help="run a single pull and exit")
    a = ap.parse_args(argv)

    interval = parse_every(a.every)
    extra = {"sample": a.sample} if a.sample is not None else {}
    n = 0
    while True:
        n += 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            status, total, delta = run_once(a.agent, a.conn_id, extra)
            d = "" if delta is None else f", Δ{delta} since last"
            print(f"[{stamp}] run #{n} {a.conn_id}: {status} · {total} rows{d}", flush=True)
        except urllib.error.URLError as e:
            print(f"[{stamp}] run #{n} {a.conn_id}: agent unreachable ({e}). Is ./dev.sh up?", flush=True)
        if a.once:
            return 0
        print(f"    next run in {a.every} (Ctrl-C to stop)", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped."); sys.exit(0)
