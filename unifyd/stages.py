"""stages.py — where the data is, how much is waiting, and whether the number can be trusted.

Step 4 of docs/PIPELINE-DESIGN.md. The directive's framing: inspection is the deliverable, not a
nice-to-have. Every stage must answer the same five questions for any source, from real landed data:

    how many rows arrived, and when          the landing signal, on the table ACTUALLY written
    how many are waiting to be promoted      the watermark gap, as a number
    what does a row look like here           real rows at this stage, not a summary
    what was dropped between stages, and why a fold that discards must say what and why
    which run/part produced this row         provenance per row, not per table

This module answers the first two for every table, and points at the existing `/api/source`
(`?name=<table>`) for the third. Four and five need per-row provenance the parts do not carry yet
(see fold.py: they have no timestamp either) and are deliberately absent rather than faked.

TWO RULES, BOTH LEARNED FROM FAILURES IN THIS REPO, AND BOTH ENFORCED HERE

  1. AN EMPTY BACKLOG MUST READ DIFFERENTLY FROM A STALL. `ok`/`current`/`empty` were collapsed once
     already, and that is what let `ubereats-enrich` report benignly while landing nothing for weeks.
     `state` here distinguishes: `flowing` (rows, fresh), `waiting` (a real backlog), `idle` (nothing
     waiting — success with no work), `stalled` (a backlog that is not moving), `never` (declared,
     nothing ever landed), `unknown` (could not be measured).

  2. A NUMBER THAT CANNOT BE COMPUTED IS WITHHELD, NOT ZEROED. A missing or unreadable table must
     never render as a low row count — `row_count` reads FOOTERS, so it will happily report 51.7M for
     a table no aggregate query can read. When a count fails, `rows` is None and `rows_error` says
     why. Rendering None as 0 is the specific lie this module exists to prevent, and the surface must
     show it as "—", never as a zero.

WHY THIS MATTERS OUTSIDE THE BUILDING. `tools/metro_deck.py` publishes per-metro market reports for
press and prospects, tagging every figure LANDED or DERIVED and stating that account counts are
OBSERVED COVERAGE — a floor. Those LANDED numbers come from `src_outlets` ⋈ `outlet_geography`, and
`src_outlets` is the worst table in the trust classification: eight writing modules doing unlocked
read-modify-write over 1.76M rows. If it quietly loses rows, a deck understates coverage in a press
release and nothing anywhere fails. This surface is what makes that floor provable before publishing.

The structural half (who writes each table, and the trust tier) comes from `tools/data_inventory.py`,
which is `ast`-only — no credentials, no network — so the shape of the pipeline is knowable even
where the live counts are not.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import table_spec

# Stage names, in flow order. Index IS the stage number used by table_spec.
STAGE_NAMES = ["discover", "capture", "consolidate", "normalize", "master", "facts"]

# How stale a table may be before a backlog counts as STALLED rather than merely waiting. Generous:
# a fold runs on a 6h interval, so a backlog younger than a day is normal operation, not a fault.
STALL_AFTER_S = 36 * 3600


def stage_of(table, registry_builds=None):
    """The pipeline stage a table belongs to, or None if it genuinely cannot be determined.

    Declared stages win — `table_spec` is the authority. Everything else is INFERRED from naming
    conventions that hold across the warehouse, and an inference that does not fire returns None so
    the caller can show "unknown" instead of quietly bucketing the table somewhere plausible. A
    wrong stage is worse than an absent one: it would put a table on a screen under a heading that
    implies a promotion path it does not have.
    """
    spec = table_spec.spec_for(table)
    if spec is not None:
        return spec.stage
    t = table.lower()
    if t.endswith("_parts"):
        return 1
    if t.endswith("_sitemap") or t in ("src_outlets", "outlet_geography") or t.endswith("_outlets"):
        return 0 if t.endswith("_sitemap") else 3
    if t.startswith("src_"):
        return 3
    if t.startswith("dim_") or t.endswith("_master"):
        return 4
    if t.startswith("fact_") or t.endswith("_observations"):
        return 5
    if t.endswith("_products") or t.endswith("_items") or t.endswith("_catalog"):
        return 2
    return None


def state_of(rows, pending, age_s, rows_error=None, now=None):
    """One word for what this table is doing — the distinction the old ok/current/empty collapse lost.

    `rows` None means the count could not be computed. That is NOT zero and NOT a stall; it is
    `unknown`, and it must stay visibly unknown all the way to the screen.
    """
    if rows_error or rows is None:
        return "unknown"
    if rows == 0:
        return "never"                       # declared, nothing ever landed — a real, loud signal
    if pending is None:
        return "flowing"                     # no promotion step, so no backlog to report
    if pending == 0:
        return "idle"                        # nothing waiting: success with no work, not a stall
    if age_s is not None and age_s > STALL_AFTER_S:
        return "stalled"                     # a backlog that is not moving
    return "waiting"


def _age_s(modified, now=None):
    if not modified:
        return None
    now = now if now is not None else time.time()
    try:
        return max(0.0, float(now) - float(modified))
    except (TypeError, ValueError):
        return None


def build(counts=None, watermarks=None, inventory=None, now=None):
    """The stage inventory.

    Every live read is INJECTED so the whole model is testable with no warehouse, no DuckDB and no
    network — the same pattern the overlay pipeline uses:
      * `counts`      table -> {"rows": int|None, "modified": epoch|None, "error": str|None}
      * `watermarks`  table -> pending part count (or None where the table has no promotion step)
      * `inventory`   tools/data_inventory.build() output (writers + registry)
    """
    inv = inventory or {}
    counts = counts or {}
    watermarks = watermarks or {}
    # data_inventory's shape: inv["tables"][t]["writers"] is a LIST of call-site dicts
    # ({module, line, writer, is_test, ...}); inv["writes"] is the same records flat. Read the
    # per-table map, and EXCLUDE test writers — a fixture seeding a table in a *_test.py is not a
    # production writer, and counting it would flag half the warehouse as multi-writer.
    tables_inv = inv.get("tables") or {}
    registry = inv.get("registry") or {}

    def _modules(t):
        recs = (tables_inv.get(t) or {}).get("writers") or []
        return sorted({r.get("module") for r in recs
                       if isinstance(r, dict) and not r.get("is_test") and r.get("module")})

    # table -> the sources that DECLARE it, so a table can be traced back to what is meant to fill it
    declared_by = {}
    for sid, s in registry.items():
        for t in (s.get("tables") or []):
            declared_by.setdefault(t, []).append(sid)

    tables = set(counts) | set(tables_inv) | set(declared_by) | set(table_spec.SPECS)
    out = []
    for t in sorted(tables):
        c = counts.get(t) or {}
        rows, err = c.get("rows"), c.get("error")
        pending = watermarks.get(t)
        age = _age_s(c.get("modified"), now)
        writers = _modules(t)
        st = stage_of(t)
        out.append({
            "table": t,
            "stage": st,
            "stage_name": STAGE_NAMES[st] if st is not None and st < len(STAGE_NAMES) else None,
            "declared": bool(table_spec.spec_for(t)),
            "rows": rows,                     # None = COULD NOT MEASURE. Never render as 0.
            "rows_error": err,
            "modified": c.get("modified"),
            "age_s": age,
            "pending": pending,               # None = no promotion step for this table
            "writers": writers,
            "writer_count": len(writers),
            # >1 unlocked writer on a merged table is the documented row-loss shape (src_outlets).
            "multi_writer": len(writers) > 1,
            "sources": sorted(declared_by.get(t) or []),
            "state": state_of(rows, pending, age, err, now),
        })
    return out


def summary(stages):
    """Counts by state — the headline. Deliberately reports `unknown` as its own bucket rather than
    folding it into a total, because an unmeasurable table is the thing you most need to see."""
    s = {}
    for r in stages:
        s[r["state"]] = s.get(r["state"], 0) + 1
    return {"by_state": s, "tables": len(stages),
            "unmeasured": s.get("unknown", 0),
            "multi_writer": sum(1 for r in stages if r["multi_writer"])}


# ---------------------------------------------------------------------------------------------
# Live adapters — the only part that touches the warehouse
# ---------------------------------------------------------------------------------------------
def live_counts(names=None):
    """table -> rows/modified/error, WITHHOLDING rather than zeroing on failure."""
    import warehouse
    out = {}
    try:
        ds = warehouse.list_datasets() or []
    except Exception as e:
        return {"_error": {"rows": None, "modified": None, "error": str(e)[:200]}}
    for d in ds:
        n = d.get("name")
        if not n or n.startswith("_") or (names and n not in names):
            continue
        rows, err = d.get("rows"), None
        if rows in (None, ""):
            rows, err = None, "row count unavailable"
        out[n] = {"rows": rows, "modified": d.get("modified"), "error": err}
    return out


def live_watermarks(tables=None):
    """table -> pending parts, for tables that have a fold watermark. Absent (None) means the table
    has no promotion step — which is different from a backlog of zero."""
    out = {}
    try:
        import fold
    except Exception:
        return out
    for t in (tables or [n for n in table_spec.SPECS if table_spec.SPECS[n].stage == 2]):
        try:
            out[t] = fold.pending(t)["pending"]
        except Exception:
            out[t] = None                     # unmeasurable, not zero
    return out


def live(now=None):
    """The stage inventory against the real warehouse."""
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "tools"))
        import data_inventory
        inv = data_inventory.build()
    except Exception:
        inv = {}
    counts = live_counts()
    return build(counts=counts, watermarks=live_watermarks(), inventory=inv, now=now)
