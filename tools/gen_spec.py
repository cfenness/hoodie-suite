#!/usr/bin/env python3
"""gen_spec.py — generate the engineering spec: one page per source, one page per table.

THE BRIEF THIS ANSWERS
  "Take a one-page Uber Eats document that an engineer can read and rebuild from if he chooses.
  All fields available, what is captured today, literally everything."

  So each source page has to carry the four things a rebuild needs and that are otherwise spread
  across the registry, the module, the warehouse and somebody's memory:
    1. THE CONTRACT — what it is, who runs it, on what cadence, what it costs, what gates it
    2. THE TRANSPORT — the endpoints, the identifiers, the anti-bot posture, the concurrency
    3. THE PAYLOAD — every field landed, with its real type, plus the raw fields deliberately dropped
    4. THE ARITHMETIC — universe size, what is covered today, and where the gap is

GENERATED, NOT HAND-KEPT — that is the whole point.
  Hand-written source docs drift the moment a scraper changes, and a drifted spec is worse than no
  spec because it is quoted with confidence. Every fact here is read at generation time from the
  same sources of truth the engine itself uses:

    source_registry.py  the roster: cadence, enabled, klass, cost_class, requires, mem, tables
    <module>.__doc__    the rebuild narrative — this repo's modules document their own recipe,
                        including the measurements behind the constants, so the docstring IS the
                        design note and is reproduced verbatim rather than paraphrased
    docs/spec/_live.json  the LANDED schema: real column names and types from the Parquet footers,
                        plus row counts (tools/spec_capture.py, runs on Fly)
    tools/data_inventory.py  who writes each table, with module:line and write mode
    unifyd/source_spec.py    the raw-field inventory where one exists (13 of 74 sources)
    unifyd/table_spec.py     the declared schema where one exists (6 of 171 tables)

  Where a fact is ABSENT the page says so in the place the fact would have been. A spec that quietly
  omits the 61 sources with no raw-field inventory reads as though the other 13 are the whole system.

    python3 tools/gen_spec.py                  # write docs/spec/**
    python3 tools/gen_spec.py --check          # report coverage, write nothing
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UNIFYD = os.path.join(ROOT, "unifyd")
SPECDIR = os.path.join(ROOT, "docs", "spec")
sys.path.insert(0, UNIFYD)


# ── inputs ────────────────────────────────────────────────────────────────────────────────────────
def live():
    p = os.path.join(SPECDIR, "_live.json")
    if not os.path.exists(p):
        return {"tables": {}, "captured_at": None, "n_landed": 0, "n_tables": 0}
    with open(p) as fh:
        return json.load(fh)


def inventory():
    raw = subprocess.check_output([sys.executable, os.path.join(HERE, "data_inventory.py"), "--json"],
                                  text=True)
    return json.loads(raw)


def registry():
    import source_registry as sr
    return list(sr.SOURCES), list(sr.BUILDS)


def raw_fields():
    try:
        import source_spec
        return source_spec.SPEC
    except Exception:
        return {}


def declared():
    try:
        import table_spec
        return table_spec.SPECS
    except Exception:
        return {}


# ── code introspection ────────────────────────────────────────────────────────────────────────────
_IMPORT = re.compile(r"import\s+([a-zA-Z_][\w]*)\s+as\s+m\b")


def module_of(src):
    """The module a registry row actually runs. The `code` field is a python one-liner, so the
    module is named in it rather than in a field of its own."""
    m = _IMPORT.search(src.get("code") or "")
    if m:
        return m.group(1)
    m = re.search(r"import\s+([a-zA-Z_][\w]*)", src.get("code") or "")
    return m.group(1) if m else None


def docstring(mod):
    """A module's own docstring, read with `ast` — no import, so a module needing pyarrow/duckdb
    still yields its documentation on a machine that has neither."""
    p = os.path.join(UNIFYD, "%s.py" % mod)
    if not mod or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return ast.get_docstring(ast.parse(fh.read()))
    except Exception:
        return None


def endpoints(mod):
    """URLs and API paths a module names literally. This is what an engineer needs first and it is
    never in the registry — it is a string constant three files away."""
    p = os.path.join(UNIFYD, "%s.py" % mod)
    if not mod or not os.path.exists(p):
        return []
    out = []
    try:
        with open(p, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except Exception:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            v = node.value.value
            if re.match(r"https?://", v) or v.startswith("/_p/api") or "/api/" in v:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        out.append((t.id, v))
    return out


def module_deps(mod):
    """Sibling modules it imports — the rebuild's dependency list."""
    p = os.path.join(UNIFYD, "%s.py" % mod)
    if not mod or not os.path.exists(p):
        return []
    names = set()
    try:
        with open(p, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except Exception:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                names.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    local = {f[:-3] for f in os.listdir(UNIFYD) if f.endswith(".py")}
    return sorted(n for n in names if n in local)


def loc(mod):
    p = os.path.join(UNIFYD, "%s.py" % mod)
    if not mod or not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def has_test(mod):
    return os.path.exists(os.path.join(UNIFYD, "%s_test.py" % mod))


# ── rendering ─────────────────────────────────────────────────────────────────────────────────────
def md_table(headers, rows):
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out) + "\n"


def n(x):
    return format(int(x), ",") if isinstance(x, (int, float)) else str(x)


def source_page(src, L, INV, RAW, is_build=False):
    sid = src["id"]
    mod = module_of(src)
    doc = docstring(mod)
    eps = endpoints(mod)
    tabs = src.get("tables") or []
    o = []
    a = o.append

    a("# %s — `%s`\n" % (src.get("label") or sid, sid))
    a("> %s\n" % ("BUILD (derives from tables we already hold)" if is_build
                  else "SOURCE (acquires data from outside the system)"))

    # 1. the contract
    a("## 1. The contract\n")
    rows = [
        ("Registry id", "`%s`" % sid),
        ("Runs", "`%s`" % (src.get("code") or "").replace("`", "'")),
        ("Module", "`unifyd/%s.py`%s" % (mod, " — %d lines" % loc(mod) if loc(mod) else "")
         if mod else "_not resolvable from the registry `code`_"),
        ("Cadence", src.get("cadence") or ("every %sh" % src.get("interval_h") if src.get("interval_h") else "—")),
        ("Enabled", "**yes**" if src.get("enabled") else "no — does not run on a cadence"),
        ("Executor class", "`%s`" % (src.get("klass") or "headless")),
        ("Cost class", src.get("cost_class") or "—"),
        ("Memory / timeout", "%s MB / %s s" % (src.get("mem") or "4096", src.get("timeout") or "—")),
        ("Shards", src.get("shards") or 1),
        ("Credentials required", ", ".join("`%s`" % r for r in (src.get("requires") or [])) or "none"),
        ("Capabilities", ", ".join("`%s`" % c for c in (src.get("caps") or [])) or "none"),
        ("Unit test", "`unifyd/%s_test.py`" % mod if mod and has_test(mod) else "**none**"),
    ]
    a(md_table(["", ""], rows))
    if src.get("note"):
        a("\n**Registry note.** %s\n" % src["note"])

    # 2. transport
    a("\n## 2. Transport\n")
    if eps:
        a(md_table(["constant", "value"], [("`%s`" % k, "`%s`" % v) for k, v in eps]))
    else:
        a("_No literal endpoint constant in `%s.py`._ The transport is either inherited from a "
          "shared fetcher or built at run time — read the module.\n" % (mod or "?"))
    deps = module_deps(mod)
    if deps:
        a("\n**Depends on** %s\n" % ", ".join("`%s`" % d for d in deps))

    # 3. payload — the landed schema, which is the part a rebuild has to reproduce
    a("\n## 3. What it lands\n")
    if not tabs:
        a("_The registry declares no tables for this source._\n")
    for t in tabs:
        rec = L["tables"].get(t) or {}
        a("\n### `%s`\n" % t)
        if not rec:
            a("_Not in the live capture — the code writes it but the table was not scanned._\n")
            continue
        if not rec.get("landed"):
            a("**Has never landed.** %s\n\nThis is a registered source whose table does not exist in "
              "the warehouse — it has never completed a successful run, or it writes under a "
              "different name than the registry declares.\n" % ("`%s`" % rec.get("error", "")[:160]))
            continue
        bits = ["%s rows" % n(rec.get("rows", "?")), "%d columns" % len(rec["columns"])]
        if rec.get("layout") == "partitioned":
            bits.append("%s partitions" % n(rec.get("partitions", "?")))
            if (rec.get("schemas_sampled") or 1) > 1:
                bits.append("**%d different schemas in a 6-partition sample — this table has drifted**"
                            % rec["schemas_sampled"])
        a(" · ".join(bits) + "\n")
        a("\n" + md_table(["column", "type"],
                          [("`%s`" % c["name"], "`%s`" % c["type"]) for c in rec["columns"]]))
        w = (INV.get("tables", {}).get(t) or {}).get("writers") or []
        if w:
            a("\n**Written by** " + ", ".join(
                "`%s:%s` (%s)" % (x["module"], x["line"], x["writer"]) for x in w) + "\n")

    # (renumbered below) the module's own account of itself. Reproduced VERBATIM and last, because in this repo the
    # docstring is the design note — it carries the measurements behind the constants and the
    # reasoning behind the shape, which is precisely what a rebuild needs and what a summary loses.
    if doc:
        a("\n## 4. `%s.py` — the module's own account\n" % mod)
        a("> Verbatim from the source. This is the design note, not a summary of it.\n")
        a("\n```text\n%s\n```\n" % doc.rstrip())
    else:
        a("\n## 4. Module documentation\n")
        a("**`%s.py` has no module docstring.** Everywhere else in this engine the docstring carries "
          "the rebuild narrative — the measurements behind the constants, the failure modes, the "
          "reason for the shape. Without it this source is only as legible as its code.\n"
          % (mod or "?"))

    # 4. raw fields where we have them
    key = sid.replace("-", "_")
    rawspec = RAW.get(key) or RAW.get(sid)
    a("\n## 5. Raw source fields\n")
    if rawspec:
        a("Endpoint: `%s` · grain: %s\n\n" % (rawspec.get("endpoint", "—"), rawspec.get("grain", "—")))
        a(md_table(["raw field", "meaning", "maps to"],
                   [("`%s`" % f, m, ("`%s`" % t if t else "_raw_json only_")) for f, m, t in rawspec["raw"]]))
        if rawspec.get("notes"):
            a("\n%s\n" % rawspec["notes"])
    else:
        a("**No raw-field inventory exists for this source.** `unifyd/source_spec.py` documents the "
          "verbatim fields a source emits and which of them we promote — it covers %d of the %d "
          "sources. Until this one is added, the landed columns above are what we know we keep, and "
          "what the source offers that we DROP is unrecorded.\n" % (len(RAW), N_SOURCES))
    return "\n".join(o)


def table_page(t, L, INV, DECL, owners):
    rec = L["tables"].get(t) or {}
    inv = INV.get("tables", {}).get(t) or {}
    o = []
    a = o.append
    a("# `%s`\n" % t)
    landed = rec.get("landed")
    a(md_table(["", ""], [
        ("Status", "landed" if landed else "**never landed**"),
        ("Rows", n(rec.get("rows", "—")) if landed else "—"),
        ("Columns", len(rec.get("columns") or []) if landed else "—"),
        ("Storage", rec.get("layout") or "—"),
        ("Partitions", n(rec["partitions"]) if rec.get("partitions") else "—"),
        ("Schema drift", ("**%d schemas in a 6-partition sample**" % rec["schemas_sampled"])
         if (rec.get("schemas_sampled") or 1) > 1 else ("uniform in sample" if rec.get("partitions") else "—")),
        ("Write mode", ", ".join(inv.get("layouts") or []) or "—"),
        ("Declared in `table_spec.py`", "yes" if t in DECL else "no — schema is whatever the writer emits"),
        ("Written by sources", ", ".join("`%s`" % s for s in owners.get(t, [])) or "—"),
        ("URI", "`%s`" % rec.get("uri", "—")),
    ]))
    if not landed and rec.get("error"):
        a("\n> The table does not exist in the warehouse: `%s`\n" % rec["error"][:200])
    if landed:
        a("\n## Columns\n")
        a(md_table(["column", "type"],
                   [("`%s`" % c["name"], "`%s`" % c["type"]) for c in rec["columns"]]))
    w = inv.get("writers") or []
    if w:
        a("\n## Writers\n")
        a(md_table(["module:line", "call", "layout", "pins dtypes"],
                   [("`%s:%s`" % (x["module"], x["line"]), "`%s`" % x["writer"], x["layout"],
                     "yes" if x.get("pins_dtypes") else "no") for x in w]))
    return "\n".join(o)


REG_IDS = set()
N_SOURCES = 0


def main(argv=None):
    global REG_IDS, N_SOURCES
    ap = argparse.ArgumentParser(description="Generate the engineering spec.")
    ap.add_argument("--check", action="store_true", help="report coverage only")
    a = ap.parse_args(argv)

    SOURCES, BUILDS = registry()
    REG_IDS = {s["id"] for s in SOURCES} | {b["id"] for b in BUILDS}
    N_SOURCES = len(SOURCES)
    L, INV, RAW, DECL = live(), inventory(), raw_fields(), declared()

    owners = {}
    for s in SOURCES + BUILDS:
        for t in (s.get("tables") or []):
            owners.setdefault(t, []).append(s["id"])
    all_tables = sorted(set(INV.get("tables", {})) | set(L.get("tables", {})) | set(owners))

    landed = [t for t in all_tables if (L["tables"].get(t) or {}).get("landed")]
    cov = {
        "sources": len(SOURCES), "builds": len(BUILDS),
        "sources_enabled": sum(1 for s in SOURCES if s.get("enabled")),
        "tables": len(all_tables), "tables_landed": len(landed),
        "tables_declared": len(DECL),
        "raw_field_specs": len(RAW),
        "columns": sum(len((L["tables"].get(t) or {}).get("columns") or []) for t in landed),
        "rows": sum(int((L["tables"].get(t) or {}).get("rows") or 0) for t in landed),
        "modules_resolved": sum(1 for s in SOURCES + BUILDS if module_of(s)),
        "with_docstring": sum(1 for s in SOURCES + BUILDS if docstring(module_of(s))),
        "with_test": sum(1 for s in SOURCES + BUILDS if module_of(s) and has_test(module_of(s))),
        "captured_at": L.get("captured_at"),
    }
    if a.check:
        print(json.dumps(cov, indent=1))
        return cov

    os.makedirs(os.path.join(SPECDIR, "sources"), exist_ok=True)
    os.makedirs(os.path.join(SPECDIR, "tables"), exist_ok=True)
    for s in SOURCES:
        with open(os.path.join(SPECDIR, "sources", "%s.md" % s["id"]), "w") as fh:
            fh.write(source_page(s, L, INV, RAW))
    for b in BUILDS:
        with open(os.path.join(SPECDIR, "sources", "%s.md" % b["id"]), "w") as fh:
            fh.write(source_page(b, L, INV, RAW, is_build=True))
    for t in all_tables:
        with open(os.path.join(SPECDIR, "tables", "%s.md" % t), "w") as fh:
            fh.write(table_page(t, L, INV, DECL, owners))

    write_index(SOURCES, BUILDS, all_tables, L, INV, RAW, DECL, owners, cov)
    print("wrote %d source pages + %d table pages" % (len(SOURCES) + len(BUILDS), len(all_tables)))
    print(json.dumps(cov, indent=1))
    return cov


def write_index(SOURCES, BUILDS, all_tables, L, INV, RAW, DECL, owners, cov):
    o = []
    a = o.append
    a("# Hoodie data spec\n")
    a("Generated by `tools/gen_spec.py` from the registry, the module docstrings, the static write "
      "map and a live read of the warehouse. **Do not edit these pages by hand** — regenerate them. "
      "The one hand-authored page is `sources/ubereats-DEEP.md`, which is the worked example.\n")
    a("\n```bash\npython3 tools/spec_capture.py --counts   # on Fly: refresh the landed schemas\n"
      "python3 tools/gen_spec.py                # regenerate every page\n```\n")
    a("\nLive schema captured **%s**.\n" % (cov["captured_at"] or "never — pages are static-only"))

    a("\n## What exists\n")
    a(md_table(["", "count"], [
        ("Sources (acquire from outside)", cov["sources"]),
        ("...enabled on a cadence", cov["sources_enabled"]),
        ("Builds (derive from what we hold)", cov["builds"]),
        ("Tables the code writes", cov["tables"]),
        ("...that have actually landed", cov["tables_landed"]),
        ("Columns described", n(cov["columns"])),
        ("Rows landed", n(cov["rows"])),
    ]))

    a("\n## Where the documentation is thin\n")
    a("Stated rather than hidden, because a spec that only shows what is covered reads as complete.\n\n")
    a(md_table(["gap", "state", "consequence"], [
        ("Declared schemas", "%d of %d tables in `table_spec.py`" % (cov["tables_declared"], cov["tables"]),
         "for the rest the schema is whatever the last writer emitted, and two tables have already "
         "been corrupted by per-partition schema drift"),
        ("Raw-field inventories", "%d of %d sources in `source_spec.py`" % (cov["raw_field_specs"], cov["sources"]),
         "for the rest we know what we KEEP but have not recorded what the source offers and we drop"),
        ("Never-landed tables", "%d of %d" % (cov["tables"] - cov["tables_landed"], cov["tables"]),
         "the code writes them; the warehouse has no such object"),
        ("Module docstrings", "%d of %d sources" % (cov["with_docstring"], cov["sources"] + cov["builds"]),
         "the rebuild narrative is absent for the remainder"),
        ("Unit tests", "%d of %d sources" % (cov["with_test"], cov["sources"] + cov["builds"]),
         "a parser change in the rest is caught only in production"),
    ]))

    a("\n## Sources\n")
    a(md_table(["id", "label", "cadence", "on", "class", "cost", "tables"],
               [("[`%s`](sources/%s.md)" % (s["id"], s["id"]), s.get("label", ""),
                 s.get("cadence", "—"), "yes" if s.get("enabled") else "no",
                 s.get("klass", "headless"), s.get("cost_class", "—"),
                 ", ".join("`%s`" % t for t in (s.get("tables") or [])) or "—")
                for s in sorted(SOURCES, key=lambda x: x["id"])]))

    a("\n## Builds\n")
    a(md_table(["id", "label", "every", "on", "tables"],
               [("[`%s`](sources/%s.md)" % (b["id"], b["id"]), b.get("label", ""),
                 "%sh" % b.get("interval_h", "—"), "yes" if b.get("enabled") else "no",
                 ", ".join("`%s`" % t for t in (b.get("tables") or [])) or "—")
                for b in sorted(BUILDS, key=lambda x: x["id"])]))

    a("\n## Tables\n")
    rows = []
    for t in all_tables:
        rec = L["tables"].get(t) or {}
        rows.append(("[`%s`](tables/%s.md)" % (t, t),
                     n(rec.get("rows", 0)) if rec.get("landed") else "—",
                     len(rec.get("columns") or []) if rec.get("landed") else "—",
                     "yes" if rec.get("landed") else "**never landed**",
                     ", ".join("`%s`" % s for s in owners.get(t, [])) or "—"))
    a(md_table(["table", "rows", "cols", "landed", "written by"], rows))
    with open(os.path.join(SPECDIR, "README.md"), "w") as fh:
        fh.write("\n".join(o))


if __name__ == "__main__":
    main()
