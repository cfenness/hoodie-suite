"""sql_console.py — arbitrary read-only SQL over the whole warehouse.

WHY THIS EXISTS. Every other data surface in this suite shows a CURATED view: the monitor shows counts,
the console shows a sample, a deck shows the figures someone chose to put on it. That is fine until you
want to check something nobody built a screen for — and then the only way to see the data is to ask an
agent, which means you are reading what it chose to show you rather than what is there. This is the
escape hatch: type SQL, get rows, against the real Parquet.

WHAT IT DOES. Names resolve themselves. `SELECT * FROM binnys_products LIMIT 10` works with no setup —
the runner finds every warehouse table named in the statement (including PARTITIONED ones, which are a
directory of parts, not a file) and creates a view for each before running it. So joins across tables
that live in different physical layouts just work:

    SELECT o.source, count(*) FROM retail_observations o
    JOIN src_outlets s USING (store_id) GROUP BY 1 ORDER BY 2 DESC

SAFETY — the warehouse is the product, so this path is READ-ONLY, enforced three ways:
  1. ONE statement, and it must start with SELECT / WITH / DESCRIBE / SUMMARIZE / EXPLAIN / SHOW / PIVOT
     / TABLE / FROM. Anything else is refused before DuckDB ever sees it.
  2. A denylist of the verbs that can reach storage or the host regardless of position (COPY / EXPORT /
     ATTACH / INSTALL / LOAD / CREATE / INSERT / UPDATE / DELETE / DROP / ALTER / read_csv-into-write /
     the filesystem + shell functions). DuckDB can write S3 from inside a plain SELECT via COPY, which is
     exactly how a "query" clobbers a catalog, so the check is on TOKENS, not on the leading keyword.
  3. A row cap and a hard wall-clock timeout via `interrupt()` — object storage is high-latency and an
     unbounded scan of a 100M-row partitioned table would otherwise pin the server.

The guard is deliberately a denylist ON TOP of an allowlist of openers, not either alone: the allowlist
stops the obvious, the denylist stops the clever, and a new DuckDB verb that slips both still cannot
write because it never gets a second statement to run in.
"""
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROW_CAP = int(os.environ.get("SQL_ROW_CAP", "5000"))       # rows returned to the browser
TIMEOUT_S = float(os.environ.get("SQL_TIMEOUT_S", "120"))  # wall clock per statement

# Statements we will run. Everything else is refused — including a bare identifier, which DuckDB would
# happily accept as a table read but which is also how a typo becomes a surprise.
_OPENERS = ("select", "with", "describe", "desc", "summarize", "explain", "show", "pivot", "unpivot",
            "table", "from", "values", "call")

# Verbs that can write, mount, or shell out — refused ANYWHERE in the statement, not just at the front.
# COPY is the important one: `COPY (SELECT …) TO 's3://…'` is a write hiding inside a read.
_BANNED = {"copy", "export", "import", "attach", "detach", "install", "load", "create", "insert",
           "update", "delete", "drop", "alter", "truncate", "vacuum", "checkpoint", "begin", "commit",
           "rollback", "set", "reset", "pragma", "prepare", "execute", "deallocate", "grant", "revoke",
           "use", "force"}
# Functions that read or write the host filesystem / spawn work outside the query.
_BANNED_FN = {"read_text", "read_blob", "write_file", "shell", "system", "getenv", "sniff_csv"}

_CON = {"con": None}
_LOCK = threading.Lock()


class SqlRefused(Exception):
    """The statement was refused by the guard. The message is shown to the user verbatim."""


def _strip(sql):
    """SQL with string literals, comments and quoted identifiers blanked out.

    Tokens are only meaningful outside literals: a product name really can contain the word "drop"
    (`SELECT * FROM t WHERE name LIKE '%drop%'`), and refusing that would make the console useless for
    the text searches it is most often used for. Blanking preserves offsets so nothing else shifts.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql[i:i + 2] == "--":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif ch == "/" and sql[i:i + 2] == "/*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i)); i = j
        elif ch in "'\"":
            j, q = i + 1, ch
            while j < n:
                if sql[j] == q:
                    if sql[j:j + 2] == q * 2:            # doubled quote = an escaped quote, not the end
                        j += 2; continue
                    j += 1; break
                if sql[j] == "\\" and q == "'":
                    j += 2; continue
                j += 1
            out.append(" " * (j - i)); i = j
        else:
            out.append(ch); i += 1
    return "".join(out)


def _strip_idents(sql):
    """Like _strip, but KEEPS what is inside double quotes.

    In SQL a double-quoted token is an IDENTIFIER, not a literal — `"src_outlets"` is a table name.
    _strip blanks it (correct for the keyword guard: a quoted token can never be a banned verb), but
    using that same blanked text to find table names means a quoted name NEVER BINDS. Every statement
    the visual join builder writes quotes its tables, so this made the whole builder fail with "Table
    with name src_outlets does not exist" against a table holding 1.9M rows.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql[i:i + 2] == "--":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif ch == "/" and sql[i:i + 2] == "/*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i)); i = j
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if sql[j:j + 2] == "''":
                        j += 2; continue
                    j += 1; break
                if sql[j] == "\\":
                    j += 2; continue
                j += 1
            out.append(" " * (j - i)); i = j
        elif ch == '"':
            out.append(" "); i += 1                   # keep the CONTENT, drop the quotes
        else:
            out.append(ch); i += 1
    return "".join(out)


def guard(sql):
    """Refuse anything that isn't a single read. Returns the cleaned statement or raises SqlRefused."""
    raw = (sql or "").strip()
    if not raw:
        raise SqlRefused("empty statement")
    bare = _strip(raw)
    # One statement. A trailing ';' is fine; a second statement is not — that is how a read becomes a
    # read plus a write.
    if ";" in bare.rstrip().rstrip(";"):
        raise SqlRefused("one statement at a time — remove the ';' and everything after it")
    stmt = raw.rstrip().rstrip(";").rstrip()
    bare = bare.rstrip().rstrip(";").rstrip()
    head = (re.match(r"\(*\s*([a-zA-Z_]+)", bare) or [None, ""])[1].lower()
    if head in _BANNED or head in _BANNED_FN:
        # Name the verb rather than listing the openers — "COPY not allowed" is actionable, "must start
        # with SELECT/WITH/…" reads like the console is broken.
        raise SqlRefused("read-only console — %s not allowed" % head.upper())
    if head not in _OPENERS:
        raise SqlRefused("read-only console — a statement must start with %s (got %r)"
                         % ("/".join(x.upper() for x in _OPENERS[:6]), head or "?"))
    words = set(re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", bare.lower()))
    bad = sorted((words & _BANNED) | (words & _BANNED_FN))
    if bad:
        raise SqlRefused("read-only console — %s not allowed" % ", ".join(w.upper() for w in bad))
    return stmt


def _con():
    """One warm connection. `INSTALL/LOAD httpfs` + S3 config costs ~6s, so it is paid once, not per query."""
    if _CON["con"] is None:
        import warehouse
        _CON["con"] = warehouse.connect()
    return _CON["con"]


def tables():
    """Every readable warehouse table: [{name, rows, columns, partitioned}]. The console's sidebar."""
    import monitor
    snap = monitor.snapshot()
    out = []
    for s in snap.get("sources", []):
        out.append({"name": s["name"], "rows": s.get("rows"), "partitioned": bool(s.get("partitioned")),
                    "modified": s.get("modified"), "kind": s.get("kind")})
    return sorted(out, key=lambda x: x["name"])


def _known():
    import monitor
    return {s["name"] for s in (monitor._CACHE["data"] or {}).get("sources", [])}


# A partitioned table is a DIRECTORY of parts, and `read_parquet(glob, union_by_name=true)` opens the
# footer of EVERY ONE of them to unify their schemas before a single row is read. Measured on the live
# warehouse: retail_observations is 4,301 parts. Listing them takes 4s; unifying their footers did not
# finish in over six minutes, and — this is the part that matters — `con.interrupt()` did not stop it,
# because the C-level read holds the GIL and the timer thread never gets to run.
#
# So the protection cannot be a timeout. It has to be the SIZE of the bind. Under the threshold we bind
# everything; over it we bind the most recent window and SAY SO — in the API response, in the footer, and
# in a banner. A cap you are not told about would make a 30-day answer look like an all-time one, which
# is exactly the failure this console exists to prevent.
# 400 was a guess and it was wrong. MEASURED on the serving box against retail_observations (4,319
# parts), bind + count:
#     10 parts   4.9s        100 parts   31.9s
#     25 parts   9.9s        200 parts  134.4s
#     50 parts  17.1s
# Linear at ~0.33s/part to 100, then it falls off a cliff. 60 costs ~20s and leaves the rest of the
# 120s budget for the query the person actually asked. Opening ALL 4,319 would be ~24 MINUTES, which
# is the real story: the cost is the file COUNT, not the data.
FULL_BIND_MAX = int(os.environ.get("SQL_FULL_BIND_MAX", "60"))    # parts we'll unify without scoping
ALL_PARTS = os.environ.get("SQL_ALL_PARTS") == "1"                # opt in to the full (slow) bind


def _part_date(path):
    """The ISO date a part filename leads with (parts are `<date>_<source>…`), or "" if unnamed."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return m.group(1) if m else ""


def _scoped_expr(name, all_parts=False):
    """(duckdb source expression | None, scope|None) for a table.

    `None` for the expression means "this table needs warehouse.attach_view" — see resolve().
    scope is None when everything is bound. When it isn't None the caller MUST surface it.
    """
    import monitor
    import warehouse
    # BUCKETED (v2) tables are a manifest, not a directory of parquet files: the rows live at
    # <name>/__b=<hex>/part-v<n>.parquet, so the partitioned glob <name>/*.parquet matches NOTHING.
    # Binding that glob makes the three largest catalogs in the warehouse unreachable by name —
    # binnys_products (1,534,862), src_outlets (1,916,357), ubereats_products (2,160,806), 5.6M rows —
    # while monitor still LISTS them, so they look present and answer "table does not exist".
    # warehouse.attach_view already resolves both layouts off the manifest; use it rather than
    # re-deriving the path here, which is exactly how the two would drift apart again.
    man = warehouse.read_manifest(name)
    if man and man.get("layout") == "bucketed":
        return None, None
    snap = (monitor._CACHE["data"] or {}).get("sources", [])
    s = next((x for x in snap if x["name"] == name), None)
    if not (s and s.get("partitioned")) or all_parts or ALL_PARTS:
        return monitor.read_expr(name), None
    try:
        files = warehouse._partition_files_strict(name)
    except Exception:
        return monitor.read_expr(name), None      # listing failed → fall back; never fake a scope
    if len(files) <= FULL_BIND_MAX:
        return monitor.read_expr(name), None
    # Walk dates newest-first and stop when the NEXT day would push the bind over the limit. Scoping by
    # a fixed number of DAYS does not work: retail_observations writes one part per date x source, so 30
    # days across ~60 sources is still ~1,800 parts — under the 4,301 total but far over the 400 that
    # defines "too many to open". The bound has to be in the same unit as the threshold.
    sel, keep, partial = _select_parts(files)
    pref = "s3://%s" if warehouse.remote() else "%s"
    lst = ", ".join("'%s'" % (pref % f).replace("'", "") for f in sel)
    return ("read_parquet([%s], union_by_name=true)" % lst), {
        "table": name, "bound_parts": len(sel), "total_parts": len(files),
        "days": len(keep), "from": min(keep) if keep else None, "to": max(keep) if keep else None,
        "partial_day": partial}


def _select_parts(files):
    """(parts to bind, dates kept, the one date taken only partially or None).

    Walk dates newest-first and stop when the next day would push the bind past FULL_BIND_MAX. Scoping
    by a fixed number of DAYS does not work: retail_observations writes one part per date x source, so
    30 days across ~60 sources is ~1,800 parts — under the 4,319 total but far over the budget. The
    bound has to be in the same unit as the threshold.
    """
    by_date, undated = {}, []
    for f in files:
        d = _part_date(f)
        if d:
            by_date.setdefault(d, []).append(f)
        else:
            undated.append(f)
    # Parts with no date in the name can't be placed in the window, so they stay IN — dropping them
    # would be a silent loss on top of a scope.
    sel, keep, partial = list(undated), set(), None
    for d in sorted(by_date, reverse=True):
        room = FULL_BIND_MAX - len(sel)
        if room <= 0:
            break
        if len(by_date[d]) > room:
            if keep:
                break                                    # stop at a whole-day boundary where we can
            # A SINGLE day bigger than the whole budget. "At least one day always binds" was the
            # escape hatch here, and it is a hole: one busy day could bind thousands of parts and
            # blow the budget the cap exists to enforce. Take part of the day instead, and say so —
            # a partial day silently presented as a day is the same lie as a partial table presented
            # as a table.
            sel += sorted(by_date[d])[-room:]
            keep.add(d)
            partial = d
            break
        sel += by_date[d]
        keep.add(d)
    return sel, keep, partial


def resolve(sql, names=None, all_parts=False):
    """Create a view for every warehouse table the statement names, and return the ones it bound.

    Identifier scan, not a parser: we take every bare word in the statement, intersect it with the set
    of tables that actually exist, and bind those. A word that isn't a table (a column, a function, an
    alias) simply doesn't intersect. The cost of a false positive is one unused view; the cost of
    missing one is an error the user can read, so erring toward binding is right.
    """
    import monitor
    known = names if names is not None else _known()
    if not known:
        monitor.snapshot()
        known = _known()
    words = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", _strip_idents(sql))
    want = [w for w in dict.fromkeys(words) if w in known]
    import warehouse
    con, bound, failed, scopes = _con(), [], {}, []
    for name in want:
        try:
            expr, scope = _scoped_expr(name, all_parts=all_parts)
            if expr is None:                             # bucketed → the manifest-aware binder owns it
                if not warehouse.attach_view(con, name, view='"%s"' % name):
                    failed[name] = "bucketed table has no active parts (genuinely empty)"
                    continue
            else:
                con.execute('CREATE OR REPLACE TEMP VIEW "%s" AS SELECT * FROM %s' % (name, expr))
            bound.append(name)
            if scope:
                scopes.append(scope)
        except Exception as e:
            failed[name] = str(e)[:160]
    return bound, failed, scopes


def run(sql, limit=None, timeout_s=None, all_parts=False):
    """Run one read. Returns a dict the console renders directly — always including WHAT it bound and
    HOW LONG it took, so a surprising result can be explained without a second round trip."""
    t0 = time.time()
    stmt = guard(sql)
    cap = min(int(limit or ROW_CAP), ROW_CAP)
    with _LOCK:
        con = _con()
        timed = {"v": False}

        def kill():
            timed["v"] = True
            try:
                con.interrupt()
            except Exception:
                pass

        # The timer covers BINDING as well as the query. Creating the view for a partitioned table is
        # not free: `read_parquet(glob, union_by_name=true)` has to open the footer of EVERY part to
        # unify the schema, and a table with thousands of date×source parts on object storage can sit
        # there a long time. With the timer started after resolve() (as it was first written) that bind
        # had NO bound at all — an unbounded wait, which is worse than a refusal because nothing tells
        # you why. The ordering is the defect; how slow a given table's bind actually is has not been
        # measured here.
        timer = threading.Timer(float(timeout_s or TIMEOUT_S), kill)
        timer.start()
        bound, failed, scopes = [], {}, []
        try:
            bound, failed, scopes = resolve(stmt, all_parts=all_parts)
            if timed["v"]:
                raise RuntimeError("interrupted while binding")
            cur = con.execute(stmt)
            cols = [d[0] for d in cur.description]
            # fetchmany(cap + 1) — one row past the cap is how we know the result was TRUNCATED rather
            # than merely short. Reporting "5000 rows" for a 4M-row answer would be a lie by omission.
            raw = cur.fetchmany(cap + 1)
            truncated = len(raw) > cap
            rows = [[_cell(v) for v in r] for r in raw[:cap]]
        except Exception as e:
            msg = str(e)
            if timed["v"]:
                # Say WHICH phase ran out, because the fix differs: a slow bind means the table has too
                # many parts to unify (nothing in the SQL will help), a slow scan means narrow the query.
                msg = ("timed out after %.0fs while OPENING %s — that table is partitioned into enough "
                       "parts that unifying their schemas exceeds the limit. Query one part, or raise "
                       "SQL_TIMEOUT_S." % (float(timeout_s or TIMEOUT_S), ", ".join(_pending(stmt, bound)))
                       if not bound or _pending(stmt, bound) else
                       "timed out after %.0fs — narrow the scan (add a WHERE, or a LIMIT on a subquery)"
                       % float(timeout_s or TIMEOUT_S))
            return {"ok": False, "error": msg[:600], "bound": bound, "unbound": failed,
                    "scopes": scopes, "elapsed_s": round(time.time() - t0, 2), "sql": stmt}
        finally:
            timer.cancel()
    return {"ok": True, "columns": cols, "rows": rows, "row_count": len(rows), "truncated": truncated,
            "bound": bound, "unbound": failed, "scopes": scopes,
            "elapsed_s": round(time.time() - t0, 2), "sql": stmt}


def _pending(sql, bound):
    """Tables the statement names that never finished binding — the ones a bind timeout is about."""
    known = _known()
    words = re.findall(r"[a-zA-Z_][a-zA-Z_0-9]*", _strip_idents(sql))
    return [w for w in dict.fromkeys(words) if w in known and w not in set(bound)]


def _cell(v):
    """JSON-safe cell. Dates/decimals/bytes stringify; a giant blob is elided rather than shipped —
    a raw_json column would otherwise make a 100-row result a 40MB response."""
    if v is None or isinstance(v, (bool, int, float, str)):
        if isinstance(v, str) and len(v) > 4000:
            return v[:4000] + "… (%d chars)" % len(v)
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return None                                  # NaN/Inf are not JSON
        return v
    if isinstance(v, (bytes, bytearray)):
        return "<%d bytes>" % len(v)
    s = str(v)
    return s if len(s) <= 4000 else s[:4000] + "…"


def columns(name):
    """A table's column names + types, without reading rows (Parquet footer via DESCRIBE).

    Goes through the same resolution as a query — a sidebar that can show a table's columns while a
    query against it says "does not exist" is worse than showing nothing.
    """
    with _LOCK:
        con = _con()
        expr, _scope = _scoped_expr(name)
        if expr is None:
            import warehouse
            if not warehouse.attach_view(con, name, view='"_desc"'):
                return []
            cur = con.execute('DESCRIBE SELECT * FROM "_desc"')
        else:
            cur = con.execute("DESCRIBE SELECT * FROM %s" % expr)
        return [{"name": r[0], "type": r[1]} for r in cur.fetchall()]
