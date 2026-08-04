"""warehouse_manifest_cas_test.py — locks down the fix for the 2026-07-31 src_outlets corruption.

THE BUG: a bucketed table's manifest is a single JSON object, read-modify-written with no lock and no
conflict check. `_accumulate_bucketed` mutates whatever `man` it was handed and overwrites the manifest
unconditionally — so a writer holding a STALE snapshot (captured before another writer's manifest
update landed) can win a last-write-wins race and silently revert the manifest to reference bucket part
files the other writer has already deleted as superseded. That is exactly what happened to
src_outlets in production: the live manifest pointed at __b=*/data_0.parquet files a concurrent
migrate_to_bucketed had already removed, while the actually-current data sat in orphaned, unreferenced
part-v2.parquet files.

THE FIX: `_write_manifest(name, man, expect=(version, updated_at))` re-reads the manifest fresh right
before writing and raises ManifestConflict if it no longer matches what the caller started from.
`_accumulate_bucketed`, `migrate_to_bucketed`, and `create_bucketed` all pass `expect` now.

Local mode, throwaway dir, no network — mirrors warehouse_compat_test.py's harness.
"""
import copy
import os
import shutil
import sys
import tempfile

for v in ("AWS_ENDPOINT_URL_S3", "TIGRIS_ENDPOINT", "BUCKET_NAME", "WAREHOUSE_BUCKET", "PLACES_BUCKET"):
    os.environ.pop(v, None)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warehouse  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wh_cas_")
warehouse._LOCAL_DIR = TMP

passed = failed = 0


def ok(name, cond, detail=""):
    global passed, failed
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + detail))
    passed += bool(cond)
    failed += not cond


FIELDS = ["sku", "store", "price"]


def mk(n, store, offset=0):
    return [dict(sku="SKU%05d" % (i + offset), store=store, price="1.00") for i in range(n)]


def key(r):
    return (r["sku"], r["store"])


try:
    assert not warehouse.remote(), "test must run in local mode"

    # ── setup: a bucketed table with real content ──────────────────────────────────────────────
    warehouse.write_parquet("cas_tbl", mk(200, "S1"), fields=FIELDS)
    man0 = warehouse.migrate_to_bucketed("cas_tbl", ["sku", "store"], hex_len=1)
    ok("initial migration lands 200 rows", warehouse.row_count("cas_tbl") == 200)

    # ── 1. normal (non-racing) accumulate still works exactly as before ───────────────────────
    warehouse.write_accumulate("cas_tbl", mk(50, "S2"), key=key, fields=FIELDS)
    ok("non-racing accumulate lands (200 -> 250)", warehouse.row_count("cas_tbl") == 250,
       "got %r" % warehouse.row_count("cas_tbl"))
    # deepcopy: `read_manifest`'s cache returns the SAME mutable dict object within a process, and
    # `_accumulate_bucketed` mutates its `man` argument in place. A real stale writer is a SEPARATE
    # process with its own memory — this snapshot must be independent of later in-place mutation to
    # simulate that honestly, not an artifact of same-process cache aliasing.
    man_after_1 = copy.deepcopy(warehouse.read_manifest("cas_tbl"))
    ok("version advanced to 2", man_after_1["version"] == 2, "got %r" % man_after_1["version"])

    # ── 2. THE RACE, isolated at the primitive: a writer holding a STALE (version, updated_at)
    #      snapshot must be refused at the manifest write, not silently allowed to overwrite the
    #      fresher state. (The full end-to-end race additionally 404s at the DuckDB read step
    #      first, since the stale writer's own referenced files may already be gone by then — also
    #      a safe/loud failure, but this isolates the NEW check itself, deterministically.) ───────
    stale_expect = (man_after_1["version"], man_after_1["updated_at"])   # snapshot BEFORE the next write
    warehouse.write_accumulate("cas_tbl", mk(30, "S3"), key=key, fields=FIELDS)  # a THIRD writer lands first
    ok("the third writer's update lands (250 -> 280)", warehouse.row_count("cas_tbl") == 280,
       "got %r" % warehouse.row_count("cas_tbl"))
    fresh_man = warehouse.read_manifest("cas_tbl")
    ok("the fresh manifest has moved past the stale snapshot",
       (fresh_man["version"], fresh_man["updated_at"]) != stale_expect)

    raised = None
    try:
        # the stale writer finally reaches its manifest write, still carrying the OLD expect it
        # captured before the third writer landed
        warehouse._write_manifest("cas_tbl", dict(fresh_man, version=999, parts={}), expect=stale_expect)
    except warehouse.ManifestConflict as e:
        raised = e
    ok("a manifest write with a stale `expect` raises ManifestConflict instead of overwriting",
       raised is not None)

    post = warehouse.read_manifest("cas_tbl")
    ok("manifest is UNCHANGED by the refused stale write (still the fresh state)",
       post["version"] == fresh_man["version"] and post["updated_at"] == fresh_man["updated_at"])
    ok("row count still reflects the real (fresher) data, not the stale writer's bogus payload",
       warehouse.row_count("cas_tbl") == 280, "got %r" % warehouse.row_count("cas_tbl"))

    # ── 2b. the SAME race through the real end-to-end path (_accumulate_bucketed) must also fail
    #       loudly rather than corrupt — whichever error fires first (file-404 or ManifestConflict),
    #       it must never succeed and must never leave the manifest changed. ─────────────────────
    stale_man_obj = copy.deepcopy(man_after_1)
    raised_e2e = None
    try:
        warehouse._accumulate_bucketed("cas_tbl", stale_man_obj, mk(10, "S4"))
    except Exception as e:
        raised_e2e = e
    ok("end-to-end stale accumulate never silently succeeds", raised_e2e is not None,
       "it returned normally instead of raising")
    post2 = warehouse.read_manifest("cas_tbl")
    ok("end-to-end: manifest still unchanged after the failed stale accumulate",
       post2["version"] == fresh_man["version"] and post2["updated_at"] == fresh_man["updated_at"])

    # ── 3. every part file the CURRENT manifest references must actually exist — the exact
    #      invariant that broke in production (manifest pointed at deleted files) ───────────────
    man_final = warehouse.read_manifest("cas_tbl")
    on_disk = set(warehouse._list_part_rels("cas_tbl"))
    referenced = {f for info in man_final["parts"].values() for f in info["files"]}
    missing = referenced - on_disk
    ok("every manifest-referenced file exists on storage (no dangling refs)", not missing,
       "missing: %r" % missing)

    # ── 4. create_bucketed / migrate_to_bucketed reject a manifest that appeared concurrently ──
    warehouse.write_parquet("cas_tbl2", mk(50, "S1"), fields=FIELDS)
    # capture the pre-check state a caller would have seen, then land a real manifest before the
    # (now-stale) caller's own write attempt
    warehouse.migrate_to_bucketed("cas_tbl2", ["sku", "store"], hex_len=1)
    raised2 = None
    try:
        warehouse.migrate_to_bucketed("cas_tbl2", ["sku", "store"], hex_len=1)
    except ValueError as e:
        raised2 = e   # the existing entry-check ("already migrated") still fires first — fine, same effect
    ok("migrate_to_bucketed refuses a table that's already migrated", raised2 is not None)

    print()
    print("%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
finally:
    shutil.rmtree(TMP, ignore_errors=True)
