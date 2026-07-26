#!/usr/bin/env python3
"""geo_all.py — run the whole geo pipeline in ONE process, in order, so the layers never race.

fast-geo, geocode, and aggregator-geo each write_accumulate the WHOLE src_outlets table (read → merge →
rewrite). Run concurrently they clobber each other — the last writer wins and silently drops the others' work.
So the daily schedule runs this single source instead of the three separately; they execute back-to-back on
one machine, each seeing the prior layer's writes:

  1. fast-geo     — city-centroid every un-geocoded outlet with a city+state (instant, $0)
  2. geocode      — Census street-address batch, upgrades city→exact where an address exists ($0)
  3. aggregator   — UberEats/Postmates store-page fetch, exact lat/lng for the no-address sitemap universe ($0)

The three remain in the registry (enabled=False) so they can still be spawned individually for a targeted
backfill — just never two-at-once against src_outlets.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run(log=print):
    total = {}
    for name, fn in (("fast", _fast), ("geocode", _geocode), ("aggregator", _agg)):
        try:
            total[name] = fn(log)
        except Exception as e:
            log("[geo] %s pass failed: %s" % (name, str(e)[:140]))
            total[name] = 0
    log("[geo] done — fast=%(fast)s geocode=%(geocode)s aggregator=%(aggregator)s" % {
        "fast": total.get("fast", 0), "geocode": total.get("geocode", 0), "aggregator": total.get("aggregator", 0)})
    return sum(total.values())


def _fast(log):
    import city_centroid
    return city_centroid.run(log=log)


def _geocode(log):
    import geocode
    return geocode.run(log=log)


def _agg(log):
    import aggregator_geo
    return aggregator_geo.run(log=log)


if __name__ == "__main__":
    print("geo total touched:", run())
