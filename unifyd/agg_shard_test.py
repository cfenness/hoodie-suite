#!/usr/bin/env python3
"""agg_shard_test.py — the shard split must cover the universe exactly once. stdlib only, no network.

aggregator-geo is now `shards: 6`. Six machines split a ~770k backlog with NO coordination, which is
only safe if the split is a total partition: every row claimed by exactly one shard, none by two, none
by none. A gap is silent under-delivery (rows nobody fetches, reported as a completed run); an overlap
is duplicated cost and duplicated writes.

The split is a SQL predicate, so it is verified here against DuckDB's own hash — the same function the
query uses — rather than against a Python re-implementation that could agree with itself while
disagreeing with production.

Also pinned: the shard salt must DIFFER from the bucket salt. Both are `hash(key) % n` over the same
key; sharing an expression would correlate them, so a shard could draw its rows from a few buckets and
leave others empty — doing a fraction of the work while reporting a full pass.

    python3 unifyd/agg_shard_test.py          (skips cleanly without duckdb)
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
    print("aggregator-geo shard split")
    try:
        import duckdb
    except ImportError:
        print("  duckdb not installed — skipping (this suite needs the real hash)")
        return 0

    con = duckdb.connect()
    N = 6
    KEY = "CAST(source AS VARCHAR) || '|' || CAST(store_id AS VARCHAR)"
    con.execute("CREATE TABLE t AS SELECT 'ubereats' AS source, CAST(i AS VARCHAR) AS store_id "
                "FROM range(20000) tbl(i)")
    total = con.execute("SELECT count(*) FROM t").fetchone()[0]

    # --- a total partition: every row exactly once -------------------------------------------------
    counts = []
    for i in range(N):
        counts.append(con.execute(
            "SELECT count(*) FROM t WHERE (hash(%s || '#shard') %% %d) = %d" % (KEY, N, i)).fetchone()[0])
    check("shards sum to the whole universe (no gap, no overlap)", sum(counts) == total,
          (sum(counts), total))
    check("no shard is empty", all(c > 0 for c in counts), counts)

    # Disjointness proved directly rather than inferred from the sum.
    dupes = con.execute(
        "SELECT count(*) FROM (SELECT store_id, count(*) c FROM ("
        + " UNION ALL ".join(
            "SELECT store_id FROM t WHERE (hash(%s || '#shard') %% %d) = %d" % (KEY, N, i)
            for i in range(N))
        + ") GROUP BY store_id HAVING c > 1)").fetchone()[0]
    check("no row is claimed by two shards", dupes == 0, dupes)

    # --- balance: a wildly skewed split wastes the fleet -------------------------------------------
    lo, hi, exp = min(counts), max(counts), total / N
    check("shards are within 10%% of even (%d..%d, expect ~%d)" % (lo, hi, exp),
          lo > exp * 0.9 and hi < exp * 1.1, counts)

    # --- THE SALT: shard and bucket splits must not correlate --------------------------------------
    # Shard 0's rows, bucketed. If the two hashes were the same expression, shard 0 would land in only
    # every Nth bucket and the rest would come back empty.
    NB = 12
    occupied = con.execute(
        "SELECT count(DISTINCT (hash(%s) %% %d)) FROM t WHERE (hash(%s || '#shard') %% %d) = 0"
        % (KEY, NB, KEY, N)).fetchone()[0]
    check("one shard's rows spread across ALL buckets (salt de-correlates)", occupied == NB,
          (occupied, NB))

    # Same key, same modulus, different salt must not be the identity mapping.
    same = con.execute(
        "SELECT count(*) FROM t WHERE (hash(%s || '#shard') %% %d) = (hash(%s) %% %d)"
        % (KEY, N, KEY, N)).fetchone()[0]
    check("salted and unsalted splits disagree on most rows", same < total * 0.35, (same, total))

    # --- the registry actually declares it ---------------------------------------------------------
    from source_registry import SOURCES
    src = next((s for s in SOURCES if s.get("id") == "aggregator-geo"), None)
    check("aggregator-geo declares shards", src and int(src.get("shards") or 1) == N,
          src and src.get("shards"))
    check("it still declares the mem that stopped the OOM", src and src.get("mem") == 16384)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
