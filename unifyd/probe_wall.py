#!/usr/bin/env python3
"""probe_wall.py — find WHERE a session hits the wall, not just that it does.

Built as a tool rather than a throwaway script because this question recurs every time a target's
posture changes, and re-deriving the answer from a full fleet run costs hours. One machine, one session,
requests issued one at a time, each outcome classified — the cheapest experiment that separates the
hypotheses we cannot otherwise tell apart:

  challenged on request #1        -> the IDENTITY is burned before we ask anything: exit IP or TLS/
                                     header fingerprint. Rate and budget tuning cannot touch this.
  clean for N then challenged     -> a per-session QUOTA at N. Tune the session budget under N.
  clean throughout                -> the wall is aggregate, not per-session: concurrency or fleet rate.

Nothing about this is UberEats-specific except the fetcher it calls.

    python3 probe_wall.py --n 40
"""
import argparse
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def run(n=40, site="ubereats", log=print):
    import blocks
    import getstore
    import ue_catalog
    import warehouse
    # Reuse the loader that already knows the table and its shape — guessing a table name is how this
    # probe reported "no store universe" against a warehouse holding 502,212 of them.
    uni = ue_catalog.universe(site, log=lambda *_: None)
    ids = [u for (u, _name) in uni[:n]]
    if not ids:
        log("probe_wall: no store universe to sample")
        return 1
    # One thread, one session, sequential — so request ORDER is the only variable.
    os.environ["UE_SESSION_BUDGET"] = "0"          # never rotate: we want to see the session's own life
    seq = []
    for i, uid in enumerate(ids, 1):
        su = getstore.url_id_to_uuid(uid)
        if not su:
            continue
        data = getstore.fetch_store(su, site=site)
        cls = blocks.classify(status=200, has_payload=ue_catalog.has_catalog(data)) if data is not None \
            else blocks.SOFT_BLOCK
        t = blocks.get()
        seq.append((i, cls))
    firsts = [i for i, c in seq if c in (blocks.OK, blocks.EMPTY)]
    bad = [i for i, c in seq if blocks.is_throttle(c)]
    log("probe_wall: %d requests on ONE session" % len(seq))
    log("  sequence: %s" % "".join("." if c in (blocks.OK, blocks.EMPTY) else "X" for _, c in seq))
    log("  first usable at #%s | first block at #%s" % (firsts[0] if firsts else "never",
                                                        bad[0] if bad else "never"))
    if bad and bad[0] == 1:
        log("  => IDENTITY BURNED before the first request (exit IP or fingerprint).")
        log("     Rate limits and session budgets cannot fix this.")
    elif bad:
        log("  => PER-SESSION QUOTA at ~%d requests. Set the session budget below it." % bad[0])
    else:
        log("  => No per-session wall. The limit is aggregate: concurrency or fleet rate.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--site", default="ubereats")
    a = ap.parse_args()
    sys.exit(run(a.n, a.site))
