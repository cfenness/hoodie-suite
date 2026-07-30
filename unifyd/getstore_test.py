#!/usr/bin/env python3
"""getstore_test.py — disjoint exit-slice partitioning (COLLECT-HANDOFF.md §1e/§9a), pure logic, no
network. `set_shard`/`_shard_pool` is the fix for the 8-shard collision: every shard process must see a
different slice of the pool, never the whole thing, never an empty one.

    python3 getstore_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import getstore as G

fails = []


def check(name, got, want=True):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r want %r" % (name, got, want))
        fails.append(name)


def pool(n):
    return ["user:pass@10.0.0.%d:8080" % i for i in range(n)]


print("default (never sharded) returns the full pool unchanged")
G.set_shard(0, 1)
p = pool(50)
check("full pool, unmodified", G._shard_pool(p), p)

print("sharded slices are disjoint and cover the whole pool")
p = pool(50)
G.set_shard(0, 1)
nshard = 8
slices = []
for i in range(nshard):
    G.set_shard(i, nshard)
    slices.append(G._shard_pool(p))
flat = [e for s in slices for e in s]
check("every entry assigned to exactly one shard", sorted(flat), sorted(p))
for i, s in enumerate(slices):
    check("shard %d got at least one exit (50 exits / 8 shards)" % i, len(s) >= 1)
overlap = set(slices[0]) & set(slices[1])
check("shard 0 and shard 1 share no exits", overlap, set())

print("pairwise disjoint across every shard, not just 0 vs 1")
all_disjoint = True
for i in range(nshard):
    for j in range(i + 1, nshard):
        if set(slices[i]) & set(slices[j]):
            all_disjoint = False
check("no two shards' slices intersect", all_disjoint)

print("a pool too small for the shard count degrades to sharing, not to an empty slice")
G.set_shard(5, 8)
small = pool(3)
check("degenerate case returns the full (small) pool rather than []", G._shard_pool(small), small)

print("UE_PARTITION_POOL=0 disables partitioning entirely")
G.set_shard(0, 8)
os.environ["UE_PARTITION_POOL"] = "0"
import importlib
importlib.reload(G)
G.set_shard(0, 8)
p = pool(50)
check("partitioning off -> full pool even when sharded", G._shard_pool(p), p)
del os.environ["UE_PARTITION_POOL"]
importlib.reload(G)          # restore default for anything imported after this module

if fails:
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- getstore: disjoint exit-slice partitioning removes cross-shard collision structurally")
