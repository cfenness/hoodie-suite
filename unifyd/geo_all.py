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
    # `backfill` runs FIRST and costs nothing: it carries geo fields an outlet is missing here but
    # that we already hold in a sibling table (measured: 42,585 DoorDash outlets had a city, an empty
    # state, and their state sitting in doordash_stores — so every pass below skipped them and they
    # were counted unreachable). Filling those first means fast-geo can place them on the same run.
    for name, fn in (("backfill", _backfill), ("fast", _fast), ("geocode", _geocode),
                     ("aggregator", _agg)):
        try:
            total[name] = fn(log)
        except Exception as e:
            log("[geo] %s pass failed: %s" % (name, str(e)[:140]))
            total[name] = 0
    log("[geo] done — backfill=%(backfill)s fast=%(fast)s geocode=%(geocode)s aggregator=%(aggregator)s" % {
        "backfill": total.get("backfill", 0), "fast": total.get("fast", 0),
        "geocode": total.get("geocode", 0), "aggregator": total.get("aggregator", 0)})
    return sum(total.values())


def _backfill(log):
    import mappability
    return mappability.run(log=log)


def _fast(log):
    import city_centroid
    return city_centroid.run(log=log)


def _geocode(log):
    import geocode
    return geocode.run(log=log)


def _agg(log):
    import aggregator_geo
    n = aggregator_geo.run(log=log)
    # Fold in whatever the SHARD FLEET staged since the last pass. Shards write disjoint parquet parts
    # and deliberately do not merge (concurrent write_accumulate is the clobber this all exists to
    # avoid); this is the single serialized point where the stage becomes src_outlets.
    try:
        aggregator_geo.merge_stage(log=log)
    except Exception as e:                                    # noqa: BLE001
        log("[geo] stage merge failed: %s" % str(e)[:120])
    return n


if __name__ == "__main__":
    print("geo total touched:", run())
