"""Offline test for run-doc id resolution: python run_docs_ids_test.py

A run doc's id is DECLARED in _RUN_DOCS, never inferred from its filename. Both places that used
to infer it split on the first dash, which collapsed `doordash-regional-run-*.md` onto `doordash`:
the regional run was unreachable, and asking for `doordash` served whichever doc sorted last —
alphabetically "regional" beats "full". One prefix, two runs, no error, wrong answer.

Pure string/dict logic pulled from server.py's module scope; no Flask, no network, no warehouse.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "server.py")).read()

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if not cond:
        FAILED.append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — %s" % detail) if not cond else ""))


# rebuild _RUN_DOCS + the two maps exactly as server.py declares them, without importing Flask
m = re.search(r"_RUN_DOCS = \[(.*?)\n\]", SRC, re.S)
RUN_DOCS = [{"id": i, "path": p} for i, p in
            re.findall(r'"id":\s*"([^"]+)",[^}]*?"path":\s*"([^"]+)"', m.group(1), re.S)]
BY_FILE = {os.path.basename(d["path"]): d["id"] for d in RUN_DOCS}
PATH_BY_ID = {d["id"]: os.path.basename(d["path"]) for d in RUN_DOCS}

print("the regional run is registered and distinct")
ids = [d["id"] for d in RUN_DOCS]
check("doordash-regional is a run doc", "doordash-regional" in ids, ids)
check("doordash is still its own run doc", "doordash" in ids, ids)
check("ids are unique", len(ids) == len(set(ids)), ids)
check("every id maps to a distinct file", len(set(PATH_BY_ID.values())) == len(PATH_BY_ID), PATH_BY_ID)

print("\nthe old first-dash inference is what broke; the declared map does not")
naive = {f.split("-")[0] for f in BY_FILE}
check("first-dash inference collapses the two doordash runs", len(naive) < len(BY_FILE), naive)
check("the declared map keeps them apart", len(set(BY_FILE.values())) == len(BY_FILE), BY_FILE)
check("the regional doc resolves to the regional id",
      BY_FILE.get("doordash-regional-run-2026-08-03.md") == "doordash-regional", BY_FILE)
check("the full-catalog doc resolves to the doordash id",
      BY_FILE.get("doordash-full-run-2026-07-30.md") == "doordash", BY_FILE)

print("\nlookups are exact-filename, not prefix globs")
check("scrape_progress_ep resolves via _DOC_PATH_BY_ID",
      "_DOC_PATH_BY_ID.get(key)" in SRC, "still globbing?")
check("the '<key>-*.md' glob is gone", '"%s-*.md" % key' not in SRC, "prefix glob still present")
check("runs_docs_ep maps files through _DOC_BY_FILE", "_DOC_BY_FILE[os.path.basename(p)]" in SRC)
check("runs_docs_ep no longer splits on the first dash",
      'os.path.basename(p).split("-")[0]' not in SRC)

print("\nthe declared paths are well-formed (the docs themselves are NOT in git, by design)")
# Run docs are written on the operator's Mac by the pollers and mirrored to the warehouse; they are
# deliberately not committed, which is exactly why scrape_progress_ep falls through to
# `_scrape_progress/<id>.md`. So assert the SHAPE of each declared path, not its presence on disk —
# asserting presence would fail in CI and in production for entirely correct reasons.
for d in RUN_DOCS:
    check("%s path points under docs/ and is .md" % d["id"],
          d["path"].startswith("../docs/") and d["path"].endswith(".md"), d["path"])
    check("%s warehouse mirror key is unambiguous" % d["id"],
          PATH_BY_ID[d["id"]].startswith(d["id"] + "-"), PATH_BY_ID[d["id"]])

print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
sys.exit(1 if FAILED else 0)
