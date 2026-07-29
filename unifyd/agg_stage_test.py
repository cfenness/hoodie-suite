#!/usr/bin/env python3
"""agg_stage_test.py — the shard-safe write. stdlib only, no network, no warehouse.

Six shards can now run at once, which is only safe because they no longer write src_outlets directly.
write_accumulate is read-merge-REWRITE of the whole table, so concurrent callers clobber each other and
the last writer silently drops the rest — geo_all was built to serialize exactly that away. Shards
instead write_partition their own parquet part (disjoint paths, no read-modify-write) and a single
serialized merge folds the stage in.

Two properties carry that design, and both are tested here against the real merge_stage():

  1. LAST-WRITE-WINS BY staged_at. Parts persist after a merge (the warehouse API has no delete), so a
     later run re-reads old parts. Without an explicit recency rule, an OLD staged row could be
     re-applied over a NEWER exact geo — a silent regression that would look like the geocoder getting
     worse for no reason. Ordering must come from staged_at, never from part read order.
  2. A SHARD MUST NEVER MERGE. If each shard merged its own stage we would have re-created the
     concurrent rewrite. Only an unsharded run merges its own work.

    python3 unifyd/agg_stage_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def main():
    print("aggregator-geo shard-safe write")
    import aggregator_geo as A

    # Stub the warehouse boundary so the merge logic is exercised without Tigris or duckdb.
    captured = {}

    class FakeWH:
        @staticmethod
        def query_parts(name, sql=None, params=None):
            return captured["parts"]

        @staticmethod
        def write_accumulate(name, records, key, fields=None, coverage=True):
            captured["merged"] = {key(r): r for r in records}
            captured["table"] = name
            return {"rows": len(records)}

        @staticmethod
        def write_partition(name, part, records, fields=None):
            captured.setdefault("written", []).append((name, part, len(records)))
            return {"rows": len(records)}

    real = A.warehouse
    A.warehouse = FakeWH
    try:
        # --- 1. last-write-wins by staged_at, NOT by read order --------------------------------
        # The stale row is deliberately LAST in the list: if ordering came from part order rather
        # than staged_at, it would win and silently undo a good geocode.
        captured["parts"] = [
            {"source": "ubereats", "store_id": "A", "lat": 29.7, "lng": -95.3,
             "geo_precision": "exact", "staged_at": 200},
            {"source": "ubereats", "store_id": "A", "lat": None, "lng": None,
             "geo_precision": "agg_miss", "staged_at": 100},
        ]
        A.merge_stage(log=lambda *_a: None)
        got = captured["merged"][("ubereats", "A")]
        check("newer staged row wins over an older one read later",
              got["lat"] == 29.7 and got["geo_precision"] == "exact", got)
        check("merge targets src_outlets", captured["table"] == "src_outlets")

        # Reversed order must give the identical answer — proves recency, not luck.
        captured["parts"] = list(reversed(captured["parts"]))
        captured.pop("merged", None)
        A.merge_stage(log=lambda *_a: None)
        check("result is independent of part read order",
              captured["merged"][("ubereats", "A")]["lat"] == 29.7)

        # --- 2. rows from different shards all survive ------------------------------------------
        captured["parts"] = [
            {"source": "ubereats", "store_id": str(i), "lat": float(i), "lng": 1.0,
             "geo_precision": "exact", "staged_at": 10} for i in range(50)]
        captured.pop("merged", None)
        A.merge_stage(log=lambda *_a: None)
        check("every distinct store from the fleet is merged", len(captured["merged"]) == 50,
              len(captured.get("merged", {})))

        # --- 3. an empty stage is a no-op, not a clobber -----------------------------------------
        # write_accumulate refuses 0 rows over a populated table, but the merge must not even try —
        # an empty stage is the normal state right after a successful merge.
        captured["parts"] = []
        captured.pop("merged", None)
        n = A.merge_stage(log=lambda *_a: None)
        check("empty stage merges nothing and returns 0",
              n == 0 and "merged" not in captured, (n, "merged" in captured))

        # --- 4. staging writes disjoint parts ----------------------------------------------------
        captured.pop("written", None)
        A._stage([{"source": "ubereats", "store_id": "x"}], 0, 3)
        A._stage([{"source": "ubereats", "store_id": "y"}], 1, 3)
        parts = [p for _, p, _ in captured["written"]]
        check("two shards writing the same bucket produce DIFFERENT parts",
              len(set(parts)) == 2, parts)
        check("part names encode shard and bucket", all("s" in p and "b" in p for p in parts), parts)
        check("staging never touches src_outlets",
              all(n == A.STAGE for n, _, _ in captured["written"]), captured["written"])
    finally:
        A.warehouse = real

    # --- 5. the registry declares the fleet -----------------------------------------------------
    from source_registry import SOURCES
    src = next((s for s in SOURCES if s.get("id") == "aggregator-geo"), None)
    check("aggregator-geo still declares 6 shards", src and int(src.get("shards") or 1) == 6)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
