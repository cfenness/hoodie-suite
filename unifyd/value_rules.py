#!/usr/bin/env python3
"""value_rules.py — catch values that are PLAUSIBLE BUT WRONG.

WHERE THIS SITS. `blocks` says why a fetch failed. `extract_qa` says a field stopped being populated.
Neither notices a parser that keeps filling every field with confident nonsense: a price column that
silently starts carrying the case price, an ABV of 900 because a decimal moved, a UPC missing its last
digit because a selector shifted by one. Fill rates stay perfect. Nothing 403s. The corpus quietly fills
with values that are the right TYPE and the wrong NUMBER — and that is the failure that survives all the
way to a customer, because everything upstream of it looks healthy.

WHY CONSTANTS ARE ALLOWED HERE, having been wrong five times elsewhere. Every fixed number that burned
us today described a SYSTEM WE DON'T CONTROL — a rate limit, a session quota, a dead-store background.
Those move. These constants describe the domain's physics: ethanol cannot exceed 100% of a volume, a
GS1 check digit is arithmetic, a retail price is not negative. They are not observations that go stale,
so pinning them is sound where pinning a throttle was not.

IT FLAGS, IT NEVER FIXES. Landed data is not rewritten — corrections belong in the translation layer,
not in the collector. A violation is recorded as a finding against the row, and the row still lands in
full. Silently "repairing" a value would destroy the evidence that the parser is broken, which is the
one thing we actually want to keep.

    v = value_rules.check_row(row)                 # [] when clean
    rates = value_rules.batch_rates(rows)          # violations per rule, per 1000 rows
"""
import os
import re

# Hard domain bounds. Physics and arithmetic, not observations of a moving system.
ABV_MAX = 100.0
PRICE_MAX_CENTS = int(os.environ.get("VR_PRICE_MAX_CENTS", "10000000"))   # $100,000 a bottle
SPIKE_MULT = float(os.environ.get("VR_SPIKE_MULT", "3.0"))   # violation rate this much over baseline
SPIKE_MIN = float(os.environ.get("VR_SPIKE_MIN", "5.0"))     # ...and at least this many per 1000
MIN_ROWS = int(os.environ.get("VR_MIN_ROWS", "200"))

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM.search(str(v))
    return float(m.group(0)) if m else None


def _rule_upc(row, out):
    """A GS1 identifier carries its own arithmetic proof. A failed check digit is not a judgement call —
    it means the string we captured is not the code the target published (truncated, shifted, OCR'd)."""
    for field in ("upc", "gtin"):
        raw = row.get(field)
        if not raw or not str(raw).strip():
            continue
        try:
            import upc
        except Exception:
            return
        code = str(raw).strip()
        if upc.is_placeholder(code):
            out.append((field + "_placeholder", code))
            continue
        digits = upc.only_digits(code)
        if len(digits) in (8, 12, 13, 14) and not upc.check_digit_ok(code):
            out.append((field + "_check_digit", code))


def _rule_abv(row, out):
    """Ethanol cannot exceed 100% of a volume. Above that the parser has grabbed proof, a serving size,
    or a decimal that moved — all of which look like a perfectly ordinary number in the column."""
    a = _num(row.get("abv"))
    if a is None:
        return
    if a < 0 or a > ABV_MAX:
        out.append(("abv_range", a))
        return
    # Category-conditional plausibility, where the existing dq_abv rules know the shape better than a
    # single global bound can (a 40% "beer" is a mis-parse even though 40 is a legal ABV).
    try:
        import dq_abv
        cat = row.get("category") or row.get("category_path") or ""
        res = dq_abv.check_abv(cat, a)
        if isinstance(res, dict) and res.get("ok") is False:
            out.append(("abv_category", "%s@%s" % (a, cat[:40])))
        elif isinstance(res, (list, tuple)) and res and res[0] is False:
            out.append(("abv_category", "%s@%s" % (a, cat[:40])))
    except Exception:
        pass                              # category rules are a bonus; the hard bound already fired


def _rule_price(row, out):
    """Retail prices are positive and finite. A zero is usually 'we failed to read it' wearing a number,
    and an absurd one is usually a case price or a cents/dollars confusion."""
    p = _num(row.get("price"))
    if p is not None:
        if p < 0:
            out.append(("price_negative", p))
        elif p == 0:
            out.append(("price_zero", p))
        elif p > PRICE_MAX_CENTS:
            out.append(("price_absurd", p))
    lp = _num(row.get("list_price"))
    if p is not None and lp is not None and lp > 0 and p > lp * 1.5:
        # A "sale" price half again above list means the two columns have been swapped or mismapped —
        # each value is individually plausible, which is exactly why only the relationship reveals it.
        out.append(("price_above_list", "%s>%s" % (p, lp)))


def check_row(row):
    """Every violation for one row, as (rule, offending_value). Empty list means clean."""
    out = []
    if not isinstance(row, dict):
        return out
    _rule_upc(row, out)
    _rule_abv(row, out)
    _rule_price(row, out)
    return out


def batch_rates(rows):
    """Violations per 1000 rows, per rule — the shape that survives a batch getting bigger or smaller.
    A rate, not a count, because a count doubles when a run gets faster and says nothing about health."""
    rows = list(rows or [])
    n = len(rows)
    if not n:
        return {}
    counts = {}
    for r in rows:
        for rule, _ in check_row(r):
            counts[rule] = counts.get(rule, 0) + 1
    return {rule: round(1000.0 * c / n, 2) for rule, c in counts.items()}


def assess(rates, baseline, rows=0, min_rows=MIN_ROWS):
    """Which rules are firing far above their own history. Same discipline as extract_qa: judged against
    this source's normal, because a real universe always carries some bad data and the question is
    whether TODAY is worse — not whether perfection was achieved."""
    if rows < min_rows:
        return {}
    bad = {}
    for rule, rate in (rates or {}).items():
        base = (baseline or {}).get(rule)
        if base is None:
            # No history. Only shout if it is already loud, so a first run doesn't cry wolf on
            # a rule that may simply be normal for this source.
            if rate >= SPIKE_MIN * 4:
                bad[rule] = {"rate": rate, "baseline": None}
            continue
        if rate >= max(SPIKE_MIN, base * SPIKE_MULT):
            bad[rule] = {"rate": rate, "baseline": base}
    return bad


def summary(bad):
    if not bad:
        return ""
    return "; ".join("%s %.1f/1k (was %s)" % (r, d["rate"], ("%.1f" % d["baseline"]) if d["baseline"] is not None else "n/a")
                     for r, d in sorted(bad.items())[:6])
