#!/usr/bin/env python3
"""table_spec_test.py — a table's schema must be DECLARED, not inferred. A RATCHET, not a unit test.

The break this locks down:

  `write_partition(name, part, rows, fields, dtypes)` builds each file's schema from THAT batch when
  `dtypes` is omitted. A column that is all-None in one run lands as `null`; the same column with
  values elsewhere lands int64/double/string. The `union_by_name` read across partitions then has to
  reconcile incompatible types for one column — and it does NOT fail cleanly. It corrupts the read
  with an invalid-UTF8 decode error (`'utf-8' codec can't decode byte 0xca`), which masks the real
  DuckDB message entirely.

Two tables have already been made unreadable this way:
  • ubereats_products_parts   5 schemas                                  (2026-07-30)
  • retail_observations      13 schemas / 3,622 partitions / 51.7M rows  (2026-08-03)

Why a mechanical guard rather than care: the write succeeds. Every file is individually valid. The
damage is only visible to an aggregate read, possibly weeks later — so nothing at write time, and no
test of the writing module, can see it. `write_partition`'s own docstring recorded this failure after
the first occurrence and it happened again anyway, in a different table, because "remember to pass
dtypes" is not enforcement.

The fix is that schema belongs to the TABLE (contract C2): declare it in `table_spec.py` and every
writer of that table inherits it, whether or not it remembers to ask.

    python3 unifyd/table_spec_test.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import table_spec  # noqa: E402

RAN, FAILED = [], []


def check(label, ok, detail=""):
    RAN.append(label)
    if not ok:
        FAILED.append(label)
    print("  %s %s%s" % ("PASS" if ok else "FAIL", label, ("  -- " + detail) if detail and not ok else ""))


def _module_consts(tree):
    """Module-level `NAME = "literal"` bindings, so a call that passes a constant instead of a
    literal is still resolvable. Many writers do exactly that (rights.py: EMISSIONS_TABLE), and
    treating those as 'unresolvable' made the ratchet demand dtypes from call sites whose table IS
    declared — noise that trains people to add baseline entries instead of specs."""
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) \
           and isinstance(n.value.value, str):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value.value
    return out


def _resolve_names(node, consts=None):
    """Every table name a `write_partition` first-argument could denote.

    Literal -> itself. `"%s_products_parts" % site` -> the name for each known site, because 30% of
    write call sites build their name at run time and a literal-only check would miss exactly the
    ones that broke. Unresolvable -> empty, which the caller treats as "must pass dtypes explicitly".
    """
    consts = consts or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name) and node.id in consts:
        return [consts[node.id]]
    # "<fmt>" % x  /  "<fmt>" % (x, y)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) \
       and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        fmt = node.left.value
        out = []
        for site in table_spec.SITES:
            try:
                out.append(fmt % site)
            except TypeError:
                pass                      # multi-arg format we cannot resolve statically
        # "x" + "_parts" style suffixing of a resolved name
        return out
    # <name> + "_parts"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _resolve_names(node.left, consts), _resolve_names(node.right, consts)
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, str) and left:
            return [l + node.right.value for l in left]
        if left and right:
            return [l + r for l in left for r in right]
    return []


def _write_partition_calls(path):
    """(lineno, first_arg_node, passes_dtypes) for every write_partition call in a file."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except SyntaxError:
        return []
    consts = _module_consts(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name != "write_partition" or not node.args:
            continue
        has_dtypes = any(k.arg == "dtypes" for k in node.keywords) or len(node.args) >= 5
        out.append((node.lineno, node.args[0], has_dtypes, consts))
    return out


def main():
    print("table_spec ratchet")

    # --- 1. the declaration must not drift from the module that owns the parse -------------------
    # table_spec declares the Uber product schema rather than importing it (so the module stays
    # dependency-free). That duplication is only safe if drift is caught, so catch it here.
    try:
        import ue_catalog
        lean = [f for f in ue_catalog.PRODUCT_FIELDS if f != "raw_json"]
        check("table_spec Uber product fields == ue_catalog.PRODUCT_FIELDS minus raw_json",
              lean == table_spec._UBER_PRODUCT_FIELDS,
              "spec has %d, ue_catalog lean has %d" % (len(table_spec._UBER_PRODUCT_FIELDS), len(lean)))
    except Exception as e:
        check("ue_catalog importable for drift check", False, str(e)[:120])

    # observe.py holds the other canonical copy — same reasoning.
    try:
        import observe
        spec = table_spec.spec_for("retail_observations")
        check("table_spec retail_observations fields == observe.OBS_FIELDS",
              list(observe.OBS_FIELDS) == spec.fields,
              "spec %d vs observe %d" % (len(spec.fields), len(observe.OBS_FIELDS)))
    except Exception as e:
        check("observe importable for drift check", False, str(e)[:120])

    # rights.py holds the other canonical copy of the emissions schema — same reasoning.
    try:
        import rights
        spec = table_spec.spec_for("dam_emissions")
        check("table_spec dam_emissions fields == rights.EMISSION_FIELDS",
              list(rights.EMISSION_FIELDS) == spec.fields,
              "spec %d vs rights %d" % (len(spec.fields), len(rights.EMISSION_FIELDS)))
    except Exception as e:
        check("rights importable for drift check", False, str(e)[:120])

    # --- 2. declared types must materialise ------------------------------------------------------
    # Needs pyarrow, which is a Fly-image dependency and is legitimately absent on a dev box. SKIP
    # honestly rather than fail: a check that cannot run must not read as a broken schema (the same
    # "withheld, not zeroed" rule the pipeline itself is being held to).
    try:
        import pyarrow  # noqa: F401
        have_arrow = True
    except ImportError:
        have_arrow = False
    if not have_arrow:
        print("  SKIP arrow_dtypes materialisation (pyarrow not installed here — runs on Fly/CI image)")
    else:
        for name in sorted(table_spec.SPECS):
            try:
                d = table_spec.arrow_dtypes(name)
                ok = d is not None and len(d) == len(table_spec.SPECS[name].fields)
            except Exception as e:
                ok, d = False, str(e)[:80]
            check("arrow_dtypes(%s) materialises every declared field" % name, ok)

    # --- 3. THE RATCHET: no unpinned, undeclared write_partition ---------------------------------
    # A call is acceptable if it passes dtypes explicitly OR every table it could name is declared.
    # Tests seed fixtures deliberately and are exempt.
    offenders = []
    for fn in sorted(os.listdir(HERE)):
        if not fn.endswith(".py") or fn.endswith("_test.py"):
            continue
        path = os.path.join(HERE, fn)
        for lineno, arg, has_dtypes, consts in _write_partition_calls(path):
            if has_dtypes:
                continue
            names = _resolve_names(arg, consts)
            if names and all(table_spec.spec_for(n) for n in names):
                continue                  # inherits its schema from the declaration
            offenders.append("%s:%d %s" % (fn, lineno, ("-> " + ", ".join(names)) if names
                                           else "(table name not statically resolvable)"))

    # A permanently-red check teaches people to ignore the colour — the same trust defect this work
    # exists to remove. So the pre-existing offenders are BASELINED by module, and the ratchet blocks
    # anything NEW. The baseline may only shrink: clearing a module means deleting its line here.
    # These are not "fine"; they are the remaining fix-first work, and this is the list of it.
    BASELINE = {
        "aggregator_geo.py": 1,   # name built from a variable source
        "coverage.py": 1,         # ditto
        "extract_qa.py": 1,       # ditto
        "facts.py": 2,            # fact_inventory / fact_price — stage-5, specs not yet declared
        "ladder.py": 1,           # name built from a variable
        "raw_capture.py": 1,      # append-only payload sink; name is per-source
        "run_sources.py": 1,      # source_runs_log
        "runlog.py": 1,           # scrape_runs
        "salsify.py": 1,          # per-site catalog name
        # rights.py deliberately ABSENT: dam_emissions is declared in table_spec, so its
        # write inherits the schema. This is what clearing a ratchet entry looks like.
        "ue_catalog.py": 1,       # secondary write; the primary land IS pinned
    }
    by_file = {}
    for o in offenders:
        by_file[o.split(":")[0]] = by_file.get(o.split(":")[0], 0) + 1

    regressions = []
    for fn, n in sorted(by_file.items()):
        allowed = BASELINE.get(fn, 0)
        if n > allowed:
            regressions.append("%s: %d unpinned, baseline allows %d" % (fn, n, allowed))
    check("no NEW unpinned write_partition (ratchet)", not regressions,
          "\n     " + "\n     ".join(regressions) + "\n     offenders:\n     "
          + "\n     ".join(offenders))

    cleared = [fn for fn, n in sorted(BASELINE.items()) if by_file.get(fn, 0) < n]
    check("baseline is still accurate (shrink it when a module is cleared)", not cleared,
          "now clean, remove from BASELINE: " + ", ".join(cleared))

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
