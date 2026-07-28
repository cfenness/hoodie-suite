#!/usr/bin/env python3
"""idset.py — membership over a corpus of ids too large to hold as Python strings.

WHY. Incremental enrichment needs one question answered per item: "have we already resolved this?" The
obvious answer is a `set` of id strings, and it is correct right up until the corpus is the real universe.
Measured here: 9.5M ids (UberEats at full coverage — ~502k stores x ~19 items) cost **1,078 MB** as a
Python set, on a 4GB machine that has already given DuckDB half the box. At 50M ids it is 5.6 GB. That is
the same failure that killed the fleet three times over — memory scaling with the CORPUS instead of the
batch — just waiting one order of magnitude out.

Store a sorted array of 64-bit digests instead and answer membership by binary search: **72 MB at 9.5M,
381 MB at 50M**. 15x smaller, and it keeps scaling.

THE TRADE, STATED PLAINLY. A 64-bit digest can collide, and a collision reads as "already resolved" — so
one item would keep the enrichment it already has instead of being re-fetched. At 9.5M ids the expected
number of collisions is ~2e-6; at 50M it is ~7e-5. That is far below the rate at which enrichment fails
for ordinary reasons (a timeout, a store going dark), and it fails in the safe direction: it never invents
data, it can only skip refreshing an item we already resolved. If that ever stops being acceptable, widen
to 128 bits with a second parallel array — the interface does not change.

    known = idset.IdSet.from_ids(rows)      # build once
    if item_uuid in known: ...              # O(log n), no per-item allocation
"""
import array
import bisect
import hashlib


def digest(s):
    """Stable 64-bit digest of an id string. md5 (not hash()) because Python's hash is salted per
    process — a persisted or cross-machine set must agree, and shards run on different machines."""
    return int.from_bytes(hashlib.md5(str(s).encode("utf-8", "replace")).digest()[:8], "big")


class IdSet:
    """Immutable, sorted, memory-bounded membership over id strings."""

    __slots__ = ("_a", "_n")

    def __init__(self, digests=()):
        a = array.array("Q", digests)
        # Sorting once buys O(log n) lookups forever. Building via a Python set first would defeat the
        # entire purpose — the peak allocation is what we are avoiding, not the steady state.
        self._a = array.array("Q", sorted(a))
        self._n = len(self._a)

    @classmethod
    def from_ids(cls, ids):
        return cls(digest(i) for i in ids if i)

    def __contains__(self, s):
        d = digest(s)
        i = bisect.bisect_left(self._a, d)
        return i < self._n and self._a[i] == d

    def __len__(self):
        return self._n

    def __bool__(self):
        # A cheap guard against the classic bug: `if known:` on an empty set must mean "nothing known",
        # never "no filtering configured". Callers rely on len() being honest.
        return self._n > 0

    def bytes(self):
        return self._a.buffer_info()[1] * self._a.itemsize
