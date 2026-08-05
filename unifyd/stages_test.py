#!/usr/bin/env python3
"""stages_test.py — the two rules the inspection surface exists to enforce.

Both come from failures already in this repo, and both are the kind that pass every other check:

  1. AN EMPTY BACKLOG MUST READ DIFFERENTLY FROM A STALL. Collapsing ok/current/empty is what let
     ubereats-enrich report benignly while landing zero rows for weeks.
  2. A NUMBER THAT CANNOT BE COMPUTED IS WITHHELD, NOT ZEROED. `row_count` reads footers, so it
     reported 51.7M rows for a retail_observations that no aggregate query could read. A surface
     that renders an unmeasurable table as 0 is stating a falsehood with total confidence.

Every live read is injected, so this runs with no warehouse, no DuckDB and no network.

    python3 unifyd/stages_test.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import stages  # noqa: E402
import table_spec  # noqa: E402

RAN, FAILED = [], []


def check(label, ok, detail=""):
    RAN.append(label)
    if not ok:
        FAILED.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("\n     " + detail) if detail and not ok else ""))


def main():
    print("stage inspection")
    NOW = 1_800_000_000

    # --- RULE 2: unmeasurable is not zero -------------------------------------------------------
    check("a failed count is 'unknown', never 'never'",
          stages.state_of(None, 0, 10, rows_error="IO Error") == "unknown")
    check("rows=None alone is enough to withhold", stages.state_of(None, None, None) == "unknown")
    check("a REAL zero still reads 'never' (declared, nothing landed)",
          stages.state_of(0, None, None) == "never")

    # --- RULE 1: empty backlog vs stall ---------------------------------------------------------
    check("no backlog reads 'idle', not 'stalled'", stages.state_of(100, 0, 60) == "idle")
    check("a fresh backlog reads 'waiting'", stages.state_of(100, 5, 60) == "waiting")
    check("an old backlog reads 'stalled'",
          stages.state_of(100, 5, stages.STALL_AFTER_S + 1) == "stalled")
    check("a table with no promotion step reads 'flowing', not 'idle'",
          stages.state_of(100, None, 60) == "flowing")
    # The distinction that matters most: zero-waiting and cannot-measure must never be the same word.
    check("idle and unknown are different states",
          stages.state_of(100, 0, 60) != stages.state_of(None, 0, 60))

    # --- stage assignment: declared wins, inference is conservative ------------------------------
    check("declared stage wins (table_spec is the authority)",
          stages.stage_of("ubereats_products") == 2 and stages.stage_of("retail_observations") == 5)
    check("parts infer to capture", stages.stage_of("acme_products_parts") == 1)
    check("dim_* infers to master", stages.stage_of("dim_sku") == 4)
    check("fact_* infers to facts", stages.stage_of("fact_price") == 5)
    check("src_* infers to normalize", stages.stage_of("src_outlets") == 3)
    check("an unrecognisable table is None, NOT guessed into a bucket",
          stages.stage_of("zzz_mystery_thing") is None)

    # --- build(): the join, fully injected ------------------------------------------------------
    inv = {"writes": {"src_outlets": ["a.py", "b.py", "c.py"], "binnys_products": ["binnys.py"]},
           "registry": {"binnys": {"id": "binnys", "tables": ["binnys_products"]}}}
    counts = {
        "binnys_products": {"rows": 1000, "modified": NOW - 60, "error": None},
        "src_outlets": {"rows": 1_760_000, "modified": NOW - 60, "error": None},
        "broken_tbl": {"rows": None, "modified": None, "error": "utf-8 codec can't decode byte 0xca"},
        "ubereats_products": {"rows": 2_160_806, "modified": NOW - 60, "error": None},
    }
    rows = stages.build(counts=counts, watermarks={"ubereats_products": 3432},
                        inventory=inv, now=NOW)
    by = {r["table"]: r for r in rows}

    check("the unreadable table is withheld, not zeroed",
          by["broken_tbl"]["rows"] is None and by["broken_tbl"]["state"] == "unknown"
          and "0xca" in (by["broken_tbl"]["rows_error"] or ""))
    check("a real backlog surfaces as a number",
          by["ubereats_products"]["pending"] == 3432 and by["ubereats_products"]["state"] == "waiting")
    check("multi-writer tables are flagged (the src_outlets row-loss shape)",
          by["src_outlets"]["multi_writer"] and by["src_outlets"]["writer_count"] == 3)
    check("single-writer tables are not flagged", not by["binnys_products"]["multi_writer"])
    check("a table is traced back to the source that declares it",
          by["binnys_products"]["sources"] == ["binnys"])
    check("declared tables are marked as declared",
          by["ubereats_products"]["declared"] and not by["broken_tbl"]["declared"])
    check("stage names accompany stage numbers",
          by["ubereats_products"]["stage_name"] == "consolidate"
          and by["src_outlets"]["stage_name"] == "normalize")

    # --- summary reports the unmeasurable rather than burying it --------------------------------
    s = stages.summary(rows)
    # A table DECLARED in table_spec but absent from the live counts is also unmeasured — we cannot
    # tell "does not exist yet" from "could not be read", and withholding is the safe reading of both.
    # Asserted against the derived number, not a hardcoded one, so adding a spec never breaks this.
    declared_uncounted = [t for t in table_spec.SPECS if t not in counts]
    expect_unknown = 1 + len(declared_uncounted)          # broken_tbl + every uncounted declaration
    check("declared-but-uncounted tables are unknown, not zero",
          all(by[t]["rows"] is None and by[t]["state"] == "unknown" for t in declared_uncounted),
          "offenders: %s" % [t for t in declared_uncounted if by[t]["state"] != "unknown"])
    check("summary counts unmeasured tables separately",
          s["unmeasured"] == expect_unknown, "%s vs expected %d" % (s, expect_unknown))
    check("summary counts multi-writer tables", s["multi_writer"] == 1, str(s))
    check("summary total matches the rows built", s["tables"] == len(rows))

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
