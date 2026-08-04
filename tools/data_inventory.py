#!/usr/bin/env python3
"""data_inventory.py — what data does this system define, and who writes it?

Answers the question that currently requires reading the registry, every scraper, warehouse.py and
the dispatcher to answer badly: WHAT TABLES EXIST, WHO WRITES EACH ONE, AND IN WHAT SHAPE.

STATIC ONLY, BY DESIGN. This reads the source tree with `ast` — no Tigris, no credentials, no
network. So it reports what the system is DEFINED to produce, never what is currently landed. Row
counts, byte sizes and freshness are a different question that requires the warehouse
(`tools/warehouse_egress.py inventory`). Keeping them apart is deliberate: a static map that is
always available beats a live one that needs credentials nobody has to hand.

    python3 tools/data_inventory.py           # markdown report to stdout
    python3 tools/data_inventory.py --json    # same data, machine-readable
"""
import argparse
import ast
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UNIFYD = os.path.join(ROOT, "unifyd")

# warehouse.py's write surface. The layout each implies is what makes a table's physical shape
# predictable from its writers alone.
WRITERS = {
    "write_parquet":      "flat (full overwrite)",
    "write_full_rebuild": "flat (full rebuild, layout-preserving)",
    "write_accumulate":   "accumulating (merge; bucketed if migrated)",
    "write_partition":    "partitioned (append-only parts)",
    "write_parquet_from_csv": "flat (from csv)",
}


def _tables_from_registry():
    """SOURCES + BUILDS from source_registry.py, read as a literal AST rather than imported — the
    module pulls in scraper deps that need not be installed to answer this question."""
    path = os.path.join(UNIFYD, "source_registry.py")
    tree = ast.parse(open(path).read())
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        which = next((n for n in names if n in ("SOURCES", "BUILDS")), None)
        if not which or not isinstance(node.value, ast.List):
            continue
        for el in node.value.elts:
            if not (isinstance(el, ast.Call) and getattr(el.func, "id", "") == "dict"):
                continue
            e = {}
            for kw in el.keywords:
                try:
                    e[kw.arg] = ast.literal_eval(kw.value)
                except Exception:
                    e[kw.arg] = "<expr>"
            if e.get("id"):
                e["_kind"] = which.lower().rstrip("s")
                out[e["id"]] = e
    return out


def _static_str(node, mod_consts):
    """Best-effort literal for a table-name argument. Handles the four forms actually used:
    a literal, a %-format, an f-string, and a module-level constant."""
    try:
        return ast.literal_eval(node)
    except Exception:
        pass
    if isinstance(node, ast.Name) and node.id in mod_consts:
        return mod_consts[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        left = _static_str(node.left, mod_consts)
        if isinstance(left, str):
            return left.replace("%s", "{}")            # e.g. "%s_products" -> "{}_products"
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            else:
                parts.append("{}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        l, r = _static_str(node.left, mod_consts), _static_str(node.right, mod_consts)
        if isinstance(l, str) and isinstance(r, str):
            return l + r
        if isinstance(l, str):
            return l + "{}"
    return None


def _scan_writes():
    """Every warehouse write call in the tree: (table, writer, module, line, pins_dtypes)."""
    rows = []
    for fn in sorted(os.listdir(UNIFYD)):
        if not fn.endswith(".py"):
            continue
        path = os.path.join(UNIFYD, fn)
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        # module-level string constants, so TABLE = "foo" resolves
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    v = ast.literal_eval(node.value)
                    if isinstance(v, str):
                        consts[node.targets[0].id] = v
                except Exception:
                    pass
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name not in WRITERS or not node.args:
                continue
            table = _static_str(node.args[0], consts)
            kwargs = {k.arg for k in node.keywords}
            rows.append({
                "table": table or "<dynamic>",
                "writer": name,
                "layout": WRITERS[name],
                "module": fn,
                "line": node.lineno,
                "is_test": fn.endswith("_test.py"),
                "pins_dtypes": ("dtypes" in kwargs) if name == "write_partition" else None,
                "declares_fields": "fields" in kwargs,
            })
    return rows


def build():
    reg = _tables_from_registry()
    writes = [w for w in _scan_writes() if not w["is_test"]]

    # table -> {writers, registry sources that declare it}
    tables = {}
    for w in writes:
        t = tables.setdefault(w["table"], {"writers": [], "declared_by": [], "layouts": set()})
        t["writers"].append(w)
        t["layouts"].add(w["layout"])
    for sid, e in reg.items():
        for t in (e.get("tables") or []):
            tables.setdefault(t, {"writers": [], "declared_by": [], "layouts": set()})
            tables[t]["declared_by"].append(sid)

    for t in tables.values():
        t["layouts"] = sorted(t["layouts"])
    return {"registry": reg, "tables": tables, "writes": writes}


# ── report ────────────────────────────────────────────────────────────────────────────────────

def _families(reg):
    """Group registry ids by their leading token, which is the only grouping the registry supports
    today — and the reason it is approximate is itself the finding."""
    fam = {}
    for sid in reg:
        root = re.split(r"[-_]", sid)[0]
        fam.setdefault(root, []).append(sid)
    return fam


def classify(tables, concrete, shared):
    """Bucket each concrete table by STRUCTURAL trust risk — mechanical, from the write path only.

    This says nothing about whether the values are right; it says whether the write path can lose
    or corrupt rows without saying so. That is the distinction that matters when the question is
    "what can I build on today": a table with one writer doing a full rebuild is reproducible by
    construction, whatever else is wrong upstream.
    """
    out = {"corruptible": [], "lossy": [], "unprovable": [], "unverifiable": [], "sound": []}
    for t in sorted(concrete):
        v = tables[t]
        ws = v["writers"]
        if not ws:
            out["unverifiable"].append((t, "declared by %s; no traceable writer"
                                        % ", ".join(v["declared_by"]) or "nothing"))
            continue
        mods = {w["module"] for w in ws}
        kinds = {w["writer"] for w in ws}
        unpinned = [w for w in ws if w["pins_dtypes"] is False]
        if unpinned:
            out["corruptible"].append(
                (t, "write_partition without dtypes at %s — batch-inferred schema; a union read "
                    "across partitions corrupts rather than fails"
                 % ", ".join(sorted("%s:%d" % (w["module"], w["line"]) for w in unpinned))))
        elif "write_accumulate" in kinds and len(mods) > 1:
            out["lossy"].append(
                (t, "write_accumulate (read-modify-write, no lock) from %d modules: %s — concurrent "
                    "writers silently drop each other's rows" % (len(mods), ", ".join(sorted(mods)))))
        elif "write_accumulate" in kinds:
            out["unprovable"].append((t, ", ".join(sorted(mods))))
        else:
            out["sound"].append(
                (t, "%s from %s — deterministic rebuild, reproducible from its inputs"
                 % ("/".join(sorted(kinds)), ", ".join(sorted(mods)))))
    return out


def trust_section(inv, tables, concrete, shared):
    c = classify(tables, concrete, shared)
    L = []
    A = L.append
    A("## Structural trust — what can be built on today\n")
    A("Mechanical classification from the WRITE PATH only. It does not judge whether values are")
    A("correct; it judges whether the write path can lose or corrupt rows without saying so. A")
    A("table can be structurally sound and still hold bad data from a broken scraper.\n")
    A("| tier | tables | meaning |")
    A("|---|---:|---|")
    A("| corruptible | %d | unpinned `write_partition` — the class that has already made two tables unreadable |" % len(c["corruptible"]))
    A("| lossy | %d | multiple `write_accumulate` modules — silent lost updates |" % len(c["lossy"]))
    A("| accumulating | %d | single-writer merge — WORKING AS DESIGNED; not rebuildable from scratch |" % len(c["unprovable"]))
    A("| unverifiable | %d | declared but no traceable writer — landing check is blind |" % len(c["unverifiable"]))
    A("| **sound** | **%d** | **single-writer full rebuild — reproducible by construction** |" % len(c["sound"]))
    A("")
    A("**The fix-first set is %d tables** (corruptible + lossy), not the whole warehouse. Those are"
      % (len(c["corruptible"]) + len(c["lossy"])))
    A("the paths that can lose or corrupt rows *without saying so*. Everything else is either")
    A("reproducible or working as intended.\n")
    for tier, hdr in (("corruptible", "Corruptible — fix before trusting"),
                      ("lossy", "Lossy — concurrent merge can silently drop rows")):
        if c[tier]:
            A("### %s\n" % hdr)
            for t, why in c[tier]:
                A("- `%s` — %s" % (t, why))
            A("")
    if c["unprovable"]:
        A("### Accumulating — working as designed\n")
        A("`write_accumulate` from a single module is the INTENDED pattern for a persistent catalog")
        A("(CLAUDE.md: \"Persistent catalogs use `write_accumulate` (merge)\"). These are not broken.")
        A("The one real limitation: a merge inherits every prior run, so the table is not a pure")
        A("function of current inputs — a row landed by a since-changed parser is indistinguishable")
        A("from one landed today. That matters for restatement, not for day-to-day correctness.\n")
        A(", ".join("`%s`" % t for t, _ in c["unprovable"]) + "\n")
    if c["sound"]:
        A("### Sound — deterministic, reproducible from inputs\n")
        A("These are rebuilt wholesale by one writer. A re-run reproduces them; there is no")
        A("accumulated history whose provenance cannot be stated.\n")
        A(", ".join("`%s`" % t for t, _ in c["sound"]) + "\n")
    return "\n".join(L)


def report(inv):
    reg, tables, writes = inv["registry"], inv["tables"], inv["writes"]
    L = []
    A = L.append
    A("# Data inventory — what this system defines\n")
    A("Static map from the source tree (`ast`, no warehouse access). Reports what is DEFINED to be")
    A("written, not what is currently landed — row counts and sizes need Tigris and are a separate")
    A("question (`tools/warehouse_egress.py inventory`).\n")
    A("- registry entries: **%d** (%d sources, %d builds)"
      % (len(reg), sum(1 for e in reg.values() if e["_kind"] == "source"),
         sum(1 for e in reg.values() if e["_kind"] == "build")))
    A("- distinct tables: **%d**" % len(tables))
    A("- production write call sites: **%d**\n" % len(writes))

    dynamic = [w for w in writes if w["table"] == "<dynamic>"]
    templates = sorted(t for t in tables if "{}" in t)
    declared = {t for t in tables if tables[t]["declared_by"]}
    concrete = {t for t in tables if "{}" not in t and t != "<dynamic>"}
    orphan_w = sorted(t for t in concrete if tables[t]["writers"] and not tables[t]["declared_by"])
    orphan_d = sorted(t for t in concrete if tables[t]["declared_by"] and not tables[t]["writers"])
    multi = sorted(t for t in concrete if len({w["module"] for w in tables[t]["writers"]}) > 1)
    unpinned = [w for w in writes if w["pins_dtypes"] is False]

    n_tpl = sum(1 for w in writes if "{}" in w["table"])
    n_opaque = len(dynamic) + n_tpl
    pct = round(100.0 * n_opaque / max(1, len(writes)))

    A("## The headline: this system is not statically inspectable\n")
    A("**%d of %d write call sites (%d%%) do not name their table in the source.** The name is"
      % (n_opaque, len(writes), pct))
    A("computed at run time — `\"%s_products\" % site`, an f-string, or a variable — so no tool and")
    A("no amount of reading can produce a complete table map. You have to RUN it to find out.\n")
    A("The only thing binding a computed name to a real table is a hand-typed `tables=[...]` on a")
    A("registry entry. Add a site and the code works while the map goes silently wrong — which is")
    A("why `ubereats_products` below is declared by four entries and written by nothing traceable.\n")
    A("- write sites with a fully opaque table name: **%d**" % len(dynamic))
    A("- write sites with a TEMPLATE name (`{}_products`): **%d**"
      % sum(1 for w in writes if "{}" in w["table"]))
    A("- tables only knowable from a registry declaration: **%d**\n" % len(orphan_d))

    A("## Where the map disagrees with itself\n")
    A("- concrete tables WRITTEN but declared by no registry entry: **%d**" % len(orphan_w))
    A("- concrete tables DECLARED but with no traceable writer: **%d**" % len(orphan_d))
    A("- concrete tables with writers in MORE THAN ONE module: **%d**" % len(multi))
    A("- `write_partition` call sites that do NOT pin dtypes: **%d of %d**\n"
      % (len(unpinned), sum(1 for w in writes if w["writer"] == "write_partition")))

    if templates:
        A("### Template table names, and what they probably resolve to\n")
        A("Matched against registry-declared names by pattern. This join is a GUESS — the registry")
        A("is hand-typed, so a site present in code but absent from `tables=[...]` is invisible here.\n")
        for t in templates:
            rx = re.compile("^" + re.escape(t).replace(r"\{\}", r"[a-z0-9_]+") + "$")
            hits = sorted(x for x in declared if rx.match(x))
            mods = ", ".join(sorted({"`%s:%d`" % (w["module"], w["line"]) for w in tables[t]["writers"]}))
            A("- `%s` — %s → %s" % (t, mods, ", ".join("`%s`" % h for h in hits) or "**no declared match**"))
        A("")

    if orphan_w:
        A("### Written, not declared\n")
        for t in orphan_w:
            src = ", ".join(sorted({"%s:%d" % (w["module"], w["line"]) for w in tables[t]["writers"]}))
            A("- `%s` — %s" % (t, src))
        A("")
    if orphan_d:
        A("### Declared, never written\n")
        for t in orphan_d:
            A("- `%s` — declared by %s" % (t, ", ".join(tables[t]["declared_by"])))
        A("")
    if multi:
        A("### Multiple writing modules (shared tables)\n")
        for t in multi:
            mods = sorted({w["module"] for w in tables[t]["writers"]})
            A("- `%s` — %s" % (t, ", ".join(mods)))
        A("")
    if unpinned:
        A("### write_partition without pinned dtypes\n")
        A("Each of these can land a batch-inferred schema; a union read across partitions then")
        A("reconciles incompatible types and corrupts rather than fails. Two live incidents so far.\n")
        for w in sorted(unpinned, key=lambda w: (w["module"], w["line"])):
            A("- `%s` — %s:%d" % (w["table"], w["module"], w["line"]))
        A("")

    # Landing verification sums the row counts of a source's DECLARED tables (run_sources:
    # b/a = sum(before/after.values()), delta = a - b; delta <= 0 with rows present => "current",
    # never "ok"). Two ways that instrument reads the wrong thing:
    shared = {t for t in concrete if len({w["module"] for w in tables[t]["writers"]}) > 1}
    blind, contaminated = [], []
    for sid, e in sorted(reg.items()):
        decl = [t for t in (e.get("tables") or []) if "{}" not in t]
        if not decl:
            continue
        if all(not tables.get(t, {}).get("writers") for t in decl):
            blind.append((sid, decl))
        elif all(t in shared for t in decl):
            contaminated.append((sid, decl))

    A("## Landing verification blind spots\n")
    A("A run is graded by the row-count delta across its DECLARED tables. If a source's declared")
    A("tables are not the tables it writes, the grade is measuring something else.\n")
    if blind:
        A("### Declares only tables with no traceable writer\n")
        A("These can never post a positive delta from their own work, so they report `current`")
        A("(or `empty`) however much they land — and `due_builds` only advances on `ok`.\n")
        A("**CANDIDATES, NOT CONFIRMED.** A writer using a computed table name is indistinguishable")
        A("here from no writer at all, so this list mixes real defects with the limits of static")
        A("analysis — which is itself the finding. Each needs the writing module read to settle.")
        A("Confirmed so far: `ubereats-enrich` (writes `ubereats_products_parts` via")
        A("`ue_enrich.py:97`, declares `ubereats_products`, so its delta is always 0).\n")
        for sid, decl in blind:
            A("- `%s` → declares %s" % (sid, ", ".join("`%s`" % t for t in decl)))
        A("")
    if contaminated:
        A("### Declares only SHARED tables\n")
        A("The delta includes every other source writing the same table, so it is not a per-source")
        A("signal at all.\n")
        for sid, decl in contaminated:
            A("- `%s` → declares %s" % (sid, ", ".join("`%s`" % t for t in decl)))
        A("")

    A(trust_section(inv, tables, concrete, shared))
    A("## Registry families\n")
    A("Grouping is by leading id token because the registry has no family field — which is why")
    A("`build-ue-catalog` does not group with `ubereats`.\n")
    A("| family | entries | ids |")
    A("|---|---:|---|")
    for root, ids in sorted(_families(reg).items(), key=lambda kv: -len(kv[1])):
        if len(ids) > 1:
            A("| `%s` | %d | %s |" % (root, len(ids), ", ".join("`%s`" % i for i in sorted(ids))))
    A("")

    A("## Every table\n")
    A("| table | layout | writers | declared by |")
    A("|---|---|---|---|")
    for t in sorted(tables):
        v = tables[t]
        lay = ", ".join(v["layouts"]) or "—"
        w = ", ".join(sorted({"`%s:%d`" % (x["module"], x["line"]) for x in v["writers"]})) or "—"
        d = ", ".join("`%s`" % s for s in sorted(v["declared_by"])) or "—"
        A("| `%s` | %s | %s | %s |" % (t, lay, w, d))
    A("")
    return "\n".join(L)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="emit the raw inventory as JSON")
    a = p.parse_args()
    inv = build()
    if a.json:
        out = {"registry": inv["registry"],
               "tables": {k: {**v, "writers": [dict(w) for w in v["writers"]]} for k, v in inv["tables"].items()},
               "writes": inv["writes"]}
        print(json.dumps(out, indent=2, default=str))
    else:
        print(report(inv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
