#!/usr/bin/env python3
"""platform_spec_test.py — the platform expansion must reproduce today's live entries EXACTLY.

This is a MIGRATION PROOF, not a behaviour test. Collapsing 8 hand-typed registry entries into one
declaration is only safe if the result is provably identical to what was running before — otherwise
the collapse silently changes what the scheduler does, on daily enabled sources, and nobody sees it
until data stops arriving.

`fixtures/uber_platform_registry.golden.json` is a frozen snapshot of the 8 entries as they stood
immediately before the migration, generated from the live registry rather than transcribed. This
test asserts `platform_spec.expand()` equals it key-for-key.

WHEN A DIFFERENCE IS INTENTIONAL, it goes in DELIBERATE_DEVIATIONS below with a reason, so an
intended change is a reviewable line in this file instead of a silent diff. The list is empty at
migration time, on purpose.

Known bug PRESERVED by this proof (see platform_spec.__doc__): `postmates` has no `shards` key, so
the dispatcher runs one machine and its code's `UE_SHARD` default of '0/8' caps it at ~12.5% of the
universe. Fixing it here would make "behaviour-neutral" unprovable, so it is preserved and named.
Removing that override in platform_spec is the fix — and this test will then fail loudly, which is
exactly what you want a coverage change to do.

    python3 unifyd/platform_spec_test.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import platform_spec  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "uber_platform_registry.golden.json")

# (entry_id, field) -> why this deviation is intended. Empty at migration time, deliberately.
DELIBERATE_DEVIATIONS = {}

RAN, FAILED = [], []


def check(label, ok, detail=""):
    RAN.append(label)
    if not ok:
        FAILED.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("\n     " + detail) if detail and not ok else ""))


def _diff(golden, got, kind):
    """Report per-entry, per-field differences — never just 'not equal'."""
    problems = []
    g_ids, n_ids = set(golden), {e["id"] for e in got}
    for missing in sorted(g_ids - n_ids):
        problems.append("%s %s: MISSING from expansion" % (kind, missing))
    for extra in sorted(n_ids - g_ids):
        problems.append("%s %s: EXTRA, not in golden" % (kind, extra))

    by_id = {e["id"]: e for e in got}
    for eid in sorted(g_ids & n_ids):
        g, n = golden[eid], by_id[eid]
        for field in sorted(set(g) | set(n)):
            if (eid, field) in DELIBERATE_DEVIATIONS:
                continue
            gv, nv = g.get(field, "<absent>"), n.get(field, "<absent>")
            if gv != nv:
                problems.append("%s %s.%s\n         golden: %r\n         expand: %r"
                                % (kind, eid, field, gv, nv))
    return problems


def main():
    print("platform_spec migration proof")
    golden = json.load(open(GOLDEN, encoding="utf-8"))
    out = platform_spec.expand()

    problems = _diff(golden["sources"], out["sources"], "source")
    problems += _diff(golden["builds"], out["builds"], "build")

    check("expansion reproduces the 8 live entries exactly",
          not problems, "%d difference(s):\n     %s" % (len(problems), "\n     ".join(problems)))

    # The collapse must not quietly drop a site or a phase.
    check("both sites present", set(platform_spec.SITES) == {"ubereats", "postmates"})
    check("expansion yields 7 sources + 1 build",
          len(out["sources"]) == 7 and len(out["builds"]) == 1,
          "got %d sources, %d builds" % (len(out["sources"]), len(out["builds"])))

    # Enrich exists for UberEats only, and that asymmetry must be declared rather than accidental.
    check("enrich is declared for ubereats only (postmates enriches inline)",
          set(platform_spec._ENRICH) == {"ubereats"}
          and platform_spec._CATALOG["postmates"]["inline_enrich"] is True
          and platform_spec._CATALOG["ubereats"]["inline_enrich"] is False)

    if DELIBERATE_DEVIATIONS:
        print("\n  %d deliberate deviation(s) from golden:" % len(DELIBERATE_DEVIATIONS))
        for (eid, field), why in sorted(DELIBERATE_DEVIATIONS.items()):
            print("     %s.%s — %s" % (eid, field, why))

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
