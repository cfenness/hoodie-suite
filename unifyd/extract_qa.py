#!/usr/bin/env python3
"""extract_qa.py — watch the OUTPUT, not the connection. A 200 that is wrong is worse than a 403.

THE FAILURE THIS EXISTS FOR. Everything we instrumented today lives on one side of a line: did the
request succeed. `has_catalog` proves a response carried structure; `blocks` names why a fetch failed;
`pace` and `sessions` keep us under the target's limits. All of it is about getting the bytes.

None of it notices when a target quietly redesigns its payload and the parser starts returning nulls —
or worse, plausible-but-wrong values. Every fetch returns 200, every counter stays green, the run reports
`ok`, and the corpus rots silently. A block is loud and self-announcing. Parser drift is neither, which
is why it is usually found weeks later by whoever is consuming the data.

WHAT IT MEASURES. Per field, per source: how often the field is actually populated, compared against
that field's own recent history. "price is null 40% of the time today versus 2% for the last week" is a
finding even though nothing failed. The comparison is BASELINE-RELATIVE for the same reason the pacer's
trip threshold is: an absolute rule ("price must be 95% populated") encodes today's guess about a source
we may not understand, and every fixed constant in this system has been wrong within a day. A field that
has always been sparse is not drift; a field that was dense yesterday and is sparse now is.

WHAT IT DELIBERATELY DOES NOT DO. It does not judge correctness of individual values — that needs domain
rules and belongs in the dictionary/DQ layer. It answers the narrower question that catches the silent
killer: is this parser still extracting what it was extracting yesterday?

    stats = extract_qa.field_stats(rows)                  # cheap, on the batch we already have
    verdict = extract_qa.assess("ubereats", stats, baseline)
    extract_qa.record("ubereats", "ubereats_products", stats)   # append-only history
"""
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))

TABLE = "field_stats"
FIELDS = ["day", "source", "table_name", "field", "rows", "filled", "fill_pct"]

# A drop this large against the field's own history is drift, not noise. Expressed as RETENTION —
# "what fraction of its usual fill rate does this field still have" — because the natural phrasing
# ("dropped below half of baseline") silently misses the case that matters most: a field going from 98%
# to 50% populated is a parser losing half its values, and 50 <= 49 is false. Retention says it plainly.
RETAIN_MIN = float(os.environ.get("QA_RETAIN_MIN", "0.7"))   # kept less than 70% of its usual fill rate
DROP_ABS = float(os.environ.get("QA_DROP_ABS", "20.0"))      # ...and at least this many points lower
MIN_ROWS = int(os.environ.get("QA_MIN_ROWS", "200"))         # below this, a batch says nothing
MIN_BASELINE = float(os.environ.get("QA_MIN_BASELINE", "10.0"))  # a field always sparse cannot "drop"

OK = "ok"
DRIFT = "drift"                 # was populated, now much less so — the silent parser break
NEW = "new"                     # no history to compare against yet
THIN = "thin"                   # too few rows this batch to say anything


def _filled(v):
    """Populated means 'carries information', not merely 'not None' — a parser that breaks into empty
    strings or empty lists is exactly the case we are hunting, and `is not None` would call it healthy."""
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) > 0
    return True


def field_stats(rows, fields=None):
    """Fill rate per field for one batch. O(rows x fields) on data already in memory — cheap enough to
    run on every landed batch, which is the point: drift should surface within a run, not next week."""
    rows = list(rows or [])
    if not rows:
        return {}
    keys = list(fields) if fields else sorted({k for r in rows for k in r.keys()})
    n = len(rows)
    out = {}
    for k in keys:
        f = sum(1 for r in rows if _filled(r.get(k)))
        out[k] = {"rows": n, "filled": f, "fill_pct": round(100.0 * f / n, 2)}
    return out


def assess(stats, baseline, min_rows=MIN_ROWS):
    """Compare this batch against each field's own history. `baseline` is {field: fill_pct}.

    Conservative on purpose: no history means NEW (not a pass), too few rows means THIN (not a pass), and
    a field that was always sparse is never called drift. The alarm should be rare enough to be believed
    — a noisy detector gets muted, and a muted detector is worse than none.
    """
    verdicts = {}
    for field, s in (stats or {}).items():
        if s["rows"] < min_rows:
            verdicts[field] = THIN
            continue
        base = (baseline or {}).get(field)
        if base is None:
            verdicts[field] = NEW
            continue
        if base < MIN_BASELINE:
            verdicts[field] = OK          # never dense enough for a drop to mean anything
            continue
        now = s["fill_pct"]
        if now <= base * RETAIN_MIN and (base - now) >= DROP_ABS:
            verdicts[field] = DRIFT
            continue
        verdicts[field] = OK
    return verdicts


def drifted(verdicts):
    return sorted(f for f, v in (verdicts or {}).items() if v == DRIFT)


def summary(stats, verdicts):
    """One line naming the fields that moved, with the numbers, so an alert is actionable on sight."""
    bad = drifted(verdicts)
    if not bad:
        return ""
    return "; ".join("%s %.0f%% filled" % (f, stats[f]["fill_pct"]) for f in bad[:6])


def record(source, table_name, stats, day=None, log=print):
    """Append today's fill rates. Append-only, date-partitioned — a field's history IS the baseline, and
    a merged table could not answer 'what did this look like last Tuesday'."""
    if not stats:
        return 0
    day = day or time.strftime("%Y-%m-%d")
    rows = [{"day": day, "source": source, "table_name": table_name, "field": f,
             "rows": s["rows"], "filled": s["filled"], "fill_pct": s["fill_pct"]}
            for f, s in stats.items()]
    try:
        import warehouse
        warehouse.write_partition(TABLE, "%s_%s_%s" % (day, source, table_name), rows, fields=FIELDS)
        return len(rows)
    except Exception as e:
        log("  [qa] field stats not recorded: %s" % str(e)[:100])
        return 0


def baseline_for(source, table_name, days=7, exclude_day=None, log=print):
    """Each field's typical fill rate over the trailing window, as a MEDIAN.

    Median rather than mean because one already-broken day would drag a mean down and quietly raise the
    bar for calling the next day broken — a detector that adapts to its own failures stops detecting.
    Today is excluded so a batch is never compared against itself.
    """
    exclude_day = exclude_day or time.strftime("%Y-%m-%d")
    try:
        import warehouse
        rows = warehouse.query_parts(
            TABLE,
            "SELECT field, median(fill_pct) AS base FROM t "
            "WHERE source = '%s' AND table_name = '%s' AND day <> '%s' "
            "AND day >= strftime(now() - INTERVAL %d DAY, '%%Y-%%m-%%d') GROUP BY 1"
            % (source.replace("'", ""), table_name.replace("'", ""), exclude_day.replace("'", ""), days))
    except Exception as e:
        log("  [qa] no field history yet (%s)" % str(e)[:80])
        return {}
    return {r["field"]: float(r["base"]) for r in (rows or []) if r.get("field") is not None}
