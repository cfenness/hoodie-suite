"""A throttled 200 is not a covered store.

The bug this pins down, measured live: the first catalog-only fleet marked 242,503 stores done and
landed items for 10,040 of them — 4%. Under load the BFF returns a well-formed 200 with no catalog in
it. That is not None, so `if not data` let it through, the store was marked done, and the CHECKPOINT
recorded coverage for a store never actually read. The next run would then SKIP those stores and report
high coverage over them — the lie compounding instead of correcting.

The discriminator has to be structural, not a row count: a real store carries the catalog container even
when it is legitimately empty, a throttled shell carries no catalog structure at all. Getting this
backwards in either direction is expensive — treat empty stores as failures and the sweep never
finishes; treat throttled shells as covered and the dataset is fiction.

Pure predicate — no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ue_catalog as U  # noqa: E402

fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s: got %r want %r" % (name, got, want))


# COVERED: the container is present, so the store answered — even with nothing in it.
check("store with items", U.has_catalog({"title": "A", "catalogSectionsMap": {"s": [{"uuid": "i"}]}}), True)
check("store with EMPTY catalog", U.has_catalog({"title": "A", "catalogSectionsMap": {}}), True)
check("nested container", U.has_catalog({"data": {"sections": []}}), True)
check("alternate key", U.has_catalog({"menuItems": []}), True)

# NOT COVERED: no catalog structure at all = we did not read this store.
check("throttled shell", U.has_catalog({"title": "A", "location": {}}), False)
check("empty dict", U.has_catalog({}), False)
check("None", U.has_catalog(None), False)
check("string junk", U.has_catalog("<html>blocked</html>"), False)
check("list", U.has_catalog([{"catalogSectionsMap": {}}]), False)

if fails:
    print("\n".join("  FAIL " + f for f in fails))
    print("-- %d failed" % len(fails))
    sys.exit(1)
print("-- coverage guard: a throttled 200 is not a covered store (9 checks)")
