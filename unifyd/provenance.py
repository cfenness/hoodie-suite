"""provenance.py — WHOSE number is this?

Until a row reaches the master it is still the SOURCE's data. A value WE computed must never be
indistinguishable from one the retailer stated: a rep quoting a derived number back to a customer as
"what Binny's says" is how bad data reaches the field. Deriving is not the problem — precleanse, the
brand dictionary, the value dictionaries and the matchers all exist to derive, and matching does not
work without them. Claiming the derivation as the source's is the problem.

So this module carries ONE rule and makes it cheap enough that there is no reason to skip it:

    every field we calculate rather than transcribe is NAMED on the row that carries it.

Usage — set the value THROUGH derive() instead of assigning it:

    provenance.derive(row, "size_ml", _to_ml(name), "name-parse")   # instead of row["size_ml"] = ...

`how` is the rule that produced it ("name-parse", "dict", "propagated", "majority"), so the audit
answers not just WHICH fields we computed but by what means. Rows land with a `_derived` column:

    size_ml:name-parse,varietal:dict

Readers get `is_derived(row, field)` and `stated(row, field)`. `_derived` is `_`-prefixed, which is
this repo's existing convention for a provenance column that rides alongside the row and is NOT a
master field (resolve_hierarchy never shreds it as an attribute).

Nothing here rewrites or drops a landed value — the derived value still lands, exactly as before.
This only records who produced it, which is the difference between precleansing to help matching and
misrepresenting a retailer.
"""
import sys

COL = "_derived"


def derive(row, field, value, how=None):
    """Set row[field] to a value WE computed, and record that we computed it.

    No-ops on an empty value (nothing was derived, so nothing is claimed) and returns whatever the
    field already held, so it drops in where a plain `or` fallback used to sit.
    """
    if value is None or value == "":
        return row.get(field)
    row[field] = value
    mark(row, field, how)
    return value


def mark(row, field, how=None):
    """Record that `field` on this row is ours, for a value already assigned elsewhere."""
    tag = "%s:%s" % (field, how) if how else field
    cur = row.get(COL)
    if not cur:
        row[COL] = tag
        return
    if tag in _split(cur):
        return
    row[COL] = ",".join(sorted(_split(cur) | {tag}))


def freeze(row):
    """Intern the row's marker before it lands.

    Most rows in a batch derive the SAME fields by the SAME rules, so a 1.6M-row stage holds a few
    hundred distinct strings, not 1.6M. Call once per row before writing.
    """
    cur = row.get(COL)
    if cur:
        row[COL] = sys.intern(cur)
    return row


def fields(row):
    """The set of field names on this row that we derived (the `how` stripped off)."""
    return {t.split(":", 1)[0] for t in _split(row.get(COL))}


def is_derived(row, field):
    return field in fields(row)


def stated(row, field):
    """The value only if the SOURCE stated it — None when it is ours. Use where a value is about to
    be shown as the retailer's (a rep-facing surface, a sell sheet, an export we attribute)."""
    return None if is_derived(row, field) else row.get(field)


def how(row, field):
    """The rule that produced `field`, or None if the source stated it / we didn't record one."""
    for t in _split(row.get(COL)):
        if t == field:
            return ""
        if t.startswith(field + ":"):
            return t.split(":", 1)[1]
    return None


def summarize(rows):
    """{field: n} — how many rows carry each derived field. For the build log, so a derive rate that
    jumps (a source dropping a column) is visible in the run output instead of silently absorbed."""
    out = {}
    for r in rows:
        for f in fields(r):
            out[f] = out.get(f, 0) + 1
    return out


def _split(s):
    return {t for t in (s or "").split(",") if t}
