#!/usr/bin/env python3
"""monitor_footer_cache_test.py — the footer cache survives process death. No network, no creds.

Pins the live finding (2026-08-04). `_FOOTER_CACHE` was a module-level dict, and the health digest's
biggest caller — `dispatch_ephemeral` — runs on an EPHEMERAL Fly machine that is destroyed every
hourly tick. So the incremental-footer optimisation was always cold in production: measured on the
serving box against 41,559 parquet objects, cold `_list_datasets_fast` = 112.3s vs warm = 4.9s (23x).
That cold sweep is what pushed the digest past its hourly budget; the 17:50 tick was skipped because
the 16:50 run had not finished.

These checks are about the PERSISTENCE contract only — the mtime check that decides whether a cached
entry may be USED is unchanged and remains authoritative.

    python3 unifyd/monitor_footer_cache_test.py
"""
import gzip
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


class FakeWarehouse(types.ModuleType):
    """Stands in for the real warehouse so the test needs no bucket, no creds and no pyarrow."""

    def __init__(self):
        super().__init__("warehouse")
        self.blobs = {}
        self.puts = 0

    def get_bytes(self, key):
        return self.blobs.get(key)

    def put_bytes(self, key, data):
        self.puts += 1
        self.blobs[key] = data
        return key


def fresh(fake):
    """Import monitor against the fake warehouse, with the cache reset to a cold process."""
    sys.modules["warehouse"] = fake
    for m in ("monitor",):
        sys.modules.pop(m, None)
    import monitor
    monitor._FOOTER_CACHE.clear()
    monitor._footer_cache_state["loaded"] = False
    monitor._footer_cache_state["dirty"] = False
    return monitor


def main():
    fake = FakeWarehouse()
    mon = fresh(fake)

    print("a saved cache is reloaded by a COLD process — the whole point")
    mon._FOOTER_CACHE["a.parquet"] = (100.0, 7, ["x", "y"])
    mon._FOOTER_CACHE["t/2026-01-01_s.parquet"] = (200.0, 9, ["x", "y"])
    mon._footer_cache_state["dirty"] = True
    mon._footer_cache_save({"a.parquet", "t/2026-01-01_s.parquet"})
    check("save wrote one blob", fake.puts == 1, fake.puts)

    mon2 = fresh(fake)                                   # simulates the next ephemeral machine
    check("cold process starts empty", len(mon2._FOOTER_CACHE) == 0, mon2._FOOTER_CACHE)
    mon2._footer_cache_load()
    check("hydrated both entries", len(mon2._FOOTER_CACHE) == 2, mon2._FOOTER_CACHE)
    check("mtime+rows survive round-trip", mon2._FOOTER_CACHE["a.parquet"][:2] == (100.0, 7),
          mon2._FOOTER_CACHE["a.parquet"])
    check("fields survive round-trip", mon2._FOOTER_CACHE["a.parquet"][2] == ["x", "y"],
          mon2._FOOTER_CACHE["a.parquet"])

    print("\nschemas are INTERNED — a shared schema is stored once, not once per part")
    blob = json.loads(gzip.decompress(fake.blobs[mon._FOOTER_CACHE_KEY]).decode("utf-8"))
    check("both files stored", len(blob["files"]) == 2, blob["files"])
    check("only ONE schema stored for two files sharing it", len(blob["schemas"]) == 1, blob["schemas"])
    check("files reference the schema by index", blob["files"]["a.parquet"][2] == 0, blob["files"])

    print("\npruning: a file that no longer exists is dropped, so the blob can't grow forever")
    mon2._FOOTER_CACHE["gone.parquet"] = (1.0, 1, ["z"])
    mon2._footer_cache_state["dirty"] = True
    mon2._footer_cache_save({"a.parquet"})               # listing now proves only a.parquet exists
    blob2 = json.loads(gzip.decompress(fake.blobs[mon._FOOTER_CACHE_KEY]).decode("utf-8"))
    check("pruned to the live listing", set(blob2["files"]) == {"a.parquet"}, blob2["files"])

    print("\na clean sweep does NOT rewrite the blob (no dirty flag = nothing was re-read)")
    before = fake.puts
    mon2._footer_cache_state["dirty"] = False
    mon2._footer_cache_save({"a.parquet"})
    check("no write when nothing changed", fake.puts == before, (before, fake.puts))

    print("\na CORRUPT blob clears the cache rather than half-hydrating it")
    fake.blobs[mon._FOOTER_CACHE_KEY] = b"not gzip at all"
    mon3 = fresh(fake)
    mon3._footer_cache_load()
    check("corrupt blob leaves an EMPTY cache, not a partial one", len(mon3._FOOTER_CACHE) == 0,
          mon3._FOOTER_CACHE)

    print("\nan absent blob is normal (first ever run), not an error")
    fake.blobs.clear()
    mon4 = fresh(fake)
    mon4._footer_cache_load()
    check("missing blob hydrates to empty without raising", len(mon4._FOOTER_CACHE) == 0, mon4._FOOTER_CACHE)

    print("\na warehouse that raises must never break the sweep")
    class Boom(FakeWarehouse):
        def get_bytes(self, key):
            raise RuntimeError("tigris down")

        def put_bytes(self, key, data):
            raise RuntimeError("tigris down")

    mon5 = fresh(Boom())
    try:
        mon5._footer_cache_load()
        mon5._FOOTER_CACHE["a.parquet"] = (1.0, 1, ["z"])
        mon5._footer_cache_state["dirty"] = True
        mon5._footer_cache_save({"a.parquet"})
        check("load+save swallow storage failure", True)
    except Exception as e:
        check("load+save swallow storage failure", False, e)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
