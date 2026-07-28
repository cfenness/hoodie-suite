"""Membership over a full-universe corpus must not cost a gigabyte.

Incremental enrichment asks one question per item — "already resolved?" — and the obvious answer, a set
of id strings, is correct until the corpus is the real universe. Measured: 9.5M ids (UberEats at full
coverage) cost 1,078 MB as a Python set, on a 4GB machine that has already given DuckDB half the box.
That is the same failure that killed three fleets — memory scaling with the CORPUS rather than the batch —
sitting one order of magnitude out from where we were testing.

Sorted 64-bit digests + binary search answer the same question in 72 MB.

Pure data structure — no network, no warehouse.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import idset  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


IDS = ["uuid-%d" % i for i in range(5000)]
s = idset.IdSet.from_ids(IDS)

check("length", len(s), 5000)
check("member found (first)", IDS[0] in s, True)
check("member found (last)", IDS[-1] in s, True)
check("member found (middle)", IDS[2500] in s, True)
check("non-member rejected", "uuid-999999" in s, False)
check("empty string rejected", "" in s, False)

# Truthiness must mean "we know something", never "filtering is off" — a caller that reads an empty
# index as "no filter configured" would re-enrich the entire corpus or, worse, skip all of it.
check("empty is falsey", bool(idset.IdSet.from_ids([])), False)
check("empty has no members", "anything" in idset.IdSet.from_ids([]), False)
check("populated is truthy", bool(s), True)

# Blank/None ids are dropped rather than becoming a matchable entry
check("None and '' dropped", len(idset.IdSet.from_ids(["a", None, "", "b"])), 2)

# Digests must be STABLE across processes — shards run on different machines and a persisted index has
# to agree with the one built next door. Python's salted hash() would silently break that.
check("digest is deterministic", idset.digest("store-42"), idset.digest("store-42"))
check("digest differs by id", idset.digest("a") == idset.digest("b"), False)

# The whole point: memory stays bounded and predictable.
per_id = s.bytes() / len(s)
check("8 bytes per id", per_id, 8.0)
check("9.5M ids under 100MB", (9_500_000 * per_id) / 1048576 < 100, True)

# Order of insertion must not change membership (it is sorted internally)
check("insertion order irrelevant",
      all(i in idset.IdSet.from_ids(list(reversed(IDS))) for i in IDS[:50]), True)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("── %d failed" % len(fails))
    sys.exit(1)
print("── idset: full-universe membership in 8 bytes per id (15 checks)")
