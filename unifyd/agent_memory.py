#!/usr/bin/env python3
"""agent_memory.py — RETRIEVAL INSTEAD OF RE-DERIVATION. The fact store that makes a cheap question
cheap.

THE MEASUREMENT THAT JUSTIFIES THIS MODULE (from 228 local session files, 34.5B tokens):
  triage prompts     1,639  ->  45,301 assistant messages  ->  66.9% of all effective cost
That is 27.6 assistant messages per "quick question". Asking "is the abc-fws scraper still enabled"
costs a 28-message agentic loop because the answer is RE-DERIVED from the repo every single time.
Meanwhile only 10.3% of spend is output and 0.2% is fresh input — the money is in re-reading and
re-discovering, not in answering.

So the fix isn't a better model for that question, it's not having to rediscover the answer:
  miss  -> pay the expensive loop ONCE, then write the finding down with its evidence
  hit   -> one cheap turn, answer + citation, no repo crawl
Cost then scales with RELEVANCE, not with history length.

WHY LEXICAL RETRIEVAL AND NOT EMBEDDINGS (deliberate, not a shortcut):
Queries here are identifier-heavy — `abc-fws`, `write_accumulate`, `dim_item`, `source_registry`.
Exact and prefix matching on identifiers is MORE precise than semantic similarity for that shape,
and SQLite's FTS5 gives real bm25 ranking from the standard library: no model to download, no
service to run, no embedding cost, and — the part that matters most here — deterministic results
that can be unit-tested. Semantic recall is the upgrade path if measurement shows lexical misses,
not the starting point.

STALENESS IS THE LOAD-BEARING DESIGN, NOT A FEATURE:
A cached fact that has quietly gone wrong is worse than no cache — it is exactly the "quiet degrade"
failure class, and it would launder a stale answer as a confident one. So every fact stores a hash
of the file it was derived from. On recall the hash is recomputed: if the file moved, the fact is
returned marked `stale` and NEVER presented as fresh. `recall()` cannot return a fact without a
verdict — there is no code path that yields an unlabelled fact.

Facts are also tagged `deterministic` vs `inferred` and the two are never blurred, same rule the DQ
engine follows.

    python3 unifyd/agent_memory.py --harvest          # seed from source_registry
    python3 unifyd/agent_memory.py --ask "is abc-fws enabled"
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "agent_state", "cockpit")
DB = os.path.join(STATE, "memory.db")

FRESH, STALE, UNVERIFIABLE = "fresh", "stale", "unverifiable"
DETERMINISTIC, INFERRED = "deterministic", "inferred"


def _conn(path=None):
    os.makedirs(STATE, exist_ok=True)
    c = sqlite3.connect(path or DB)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS facts (
      id            INTEGER PRIMARY KEY,
      subject       TEXT NOT NULL,     -- the thing the fact is about (an identifier, ideally)
      claim         TEXT NOT NULL,     -- what question this answers, in words
      value         TEXT NOT NULL,     -- the answer
      kind          TEXT NOT NULL,     -- deterministic | inferred  (never blurred)
      evidence_path TEXT,              -- file the fact was read out of
      evidence_line INTEGER,
      evidence_cmd  TEXT,              -- or the command whose output produced it
      file_sha      TEXT,              -- hash of evidence_path AT WRITE TIME -> staleness check
      ts            REAL NOT NULL,
      hits          INTEGER DEFAULT 0,
      UNIQUE(subject, claim)           -- one current answer per question; re-write supersedes
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
      USING fts5(subject, claim, value, content='facts', content_rowid='id', tokenize='porter');
    -- Triggers keep the FTS index in lockstep with `facts`. Without them a re-written fact stays
    -- searchable under its OLD text, so a superseded answer keeps winning retrieval — the exact
    -- silent-staleness failure this module exists to prevent.
    CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
      INSERT INTO facts_fts(rowid, subject, claim, value)
        VALUES (new.id, new.subject, new.claim, new.value);
    END;
    CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
      INSERT INTO facts_fts(facts_fts, rowid, subject, claim, value)
        VALUES ('delete', old.id, old.subject, old.claim, old.value);
    END;
    CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
      INSERT INTO facts_fts(facts_fts, rowid, subject, claim, value)
        VALUES ('delete', old.id, old.subject, old.claim, old.value);
      INSERT INTO facts_fts(rowid, subject, claim, value)
        VALUES (new.id, new.subject, new.claim, new.value);
    END;
    """)
    return c


def _sha(path):
    """Hash a file's bytes. None when unreadable — which becomes `unverifiable`, never `fresh`."""
    if not path:
        return None
    p = path if os.path.isabs(path) else os.path.join(os.path.dirname(HERE), path)
    try:
        with open(p, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except Exception:
        return None


def remember(subject, claim, value, kind=DETERMINISTIC, evidence_path=None,
             evidence_line=None, evidence_cmd=None, db=None):
    """Write one fact. Re-writing the same (subject, claim) SUPERSEDES — the store holds the current
    answer, and history lives in the ledger. Returns the fact id.

    A fact with neither evidence_path nor evidence_cmd is accepted but can only ever read back as
    `unverifiable`: there is nothing to re-check it against. That is intentional — the store will
    hold your judgment calls, it just won't dress them up as verified."""
    if kind not in (DETERMINISTIC, INFERRED):
        raise ValueError("kind must be %r or %r, got %r" % (DETERMINISTIC, INFERRED, kind))
    if not subject or not claim:
        raise ValueError("subject and claim are required")
    c = _conn(db)
    with c:
        c.execute("""INSERT INTO facts (subject, claim, value, kind, evidence_path, evidence_line,
                                        evidence_cmd, file_sha, ts)
                     VALUES (?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(subject, claim) DO UPDATE SET
                       value=excluded.value, kind=excluded.kind,
                       evidence_path=excluded.evidence_path, evidence_line=excluded.evidence_line,
                       evidence_cmd=excluded.evidence_cmd, file_sha=excluded.file_sha,
                       ts=excluded.ts""",
                  (str(subject), str(claim), str(value), kind, evidence_path, evidence_line,
                   evidence_cmd, _sha(evidence_path), time.time()))
    row = c.execute("SELECT id FROM facts WHERE subject=? AND claim=?",
                    (str(subject), str(claim))).fetchone()
    return row["id"] if row else None


def verdict(fact):
    """fresh | stale | unverifiable for one fact row. The ONLY way a fact is labelled."""
    path, was = fact.get("evidence_path"), fact.get("file_sha")
    if not path:
        return UNVERIFIABLE if not fact.get("evidence_cmd") else UNVERIFIABLE
    now = _sha(path)
    if now is None:
        return UNVERIFIABLE          # file gone or unreadable — cannot assert freshness
    if was is None:
        return UNVERIFIABLE
    return FRESH if now == was else STALE


# FTS5 treats these as syntax. An identifier-heavy query like `abc-fws` or `dim_item.sku` would
# otherwise be parsed as operators and raise, so every term is quoted into a phrase instead.
_FTS_SPECIAL = re.compile(r'[^\w\s]')


def _fts_query(q):
    """Turn free text into a safe FTS5 MATCH expression. Terms are OR'd so partial recall beats no
    recall — bm25 ordering surfaces the best match, and the caller sees the score."""
    terms = [t for t in re.split(r"\s+", (q or "").strip()) if t]
    out = []
    for t in terms:
        t = _FTS_SPECIAL.sub(" ", t).strip()
        if not t:
            continue
        out.append('"%s"' % t)                      # phrase-quote: never operator syntax
    return " OR ".join(out)


def _tokens(s):
    return [t for t in re.split(r"[^\w]+", (s or "").lower()) if t]


def _rerank(rows, query):
    """Re-rank bm25 hits by WHERE the match landed. Measured need, not theory.

    Asking "is the abc-fws scraper still enabled" against the seeded store returned
    `abc-fws entrypoint code` above `abc-fws enabled`, and dragged in `abc-catalog note` and
    `vip-finder-census note` — both matched only because their long free-text `note` shares common
    words with the question. bm25 ranks by term rarity across the whole indexed row, so a fact whose
    SUBJECT is exactly what you asked about scores no better than one that merely mentions it in
    prose.

    For identifier-shaped questions the subject is the answer's address, so:
      subject exact-token match  +10 each   — "abc-fws" naming the subject is the strongest signal
      claim token match          +3 each    — "enabled" naming which property you want
      long-prose-only match      -2         — a `note` hit with no subject/claim hit is usually noise
    Deterministic and cheap (pure token sets), so it stays testable."""
    qt = set(_tokens(query))
    scored = []
    for r in rows:
        st, ct = set(_tokens(r.get("subject"))), set(_tokens(r.get("claim")))
        boost = 10 * len(qt & st) + 3 * len(qt & ct)
        if not (qt & st) and not (qt & ct):
            boost -= 2                      # matched only in the value/note prose
        # bm25 is negative-better, so subtract the boost to keep "lower is better" ordering.
        scored.append((r.get("score", 0.0) - boost, r))
    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored]


def recall(query, k=5, db=None, include_stale=True):
    """Retrieve facts for a question, each with a freshness verdict and its evidence.

    Returns [] on no match — and a caller seeing [] should fall through to the expensive path and
    then `remember()` the result. That write-back is what turns this from a lookup table into a
    cache that learns; without it every question stays a first-time question forever."""
    expr = _fts_query(query)
    if not expr:
        return []
    c = _conn(db)
    try:
        # Over-fetch, then re-rank: bm25 alone puts prose matches above subject matches (see
        # _rerank). Widening the candidate pool is what gives the re-rank something to fix.
        rows = c.execute("""SELECT f.*, bm25(facts_fts) AS score
                            FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid
                            WHERE facts_fts MATCH ? ORDER BY score LIMIT ?""",
                         (expr, max(int(k) * 8, 40))).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for r in _rerank([dict(x) for x in rows], query):
        d = dict(r)
        d["verdict"] = verdict(d)
        if d["verdict"] == STALE and not include_stale:
            continue
        out.append(d)
        with c:
            c.execute("UPDATE facts SET hits = hits + 1 WHERE id=?", (d["id"],))
        if len(out) >= k:
            break
    return out


def answer(query, k=5, db=None):
    """The shape a cheap turn actually needs: a verdict plus citations, or an honest miss.

    `status` is one of hit | stale | miss. `stale` is deliberately NOT a hit: the honest outcome is
    "I have an answer but the file it came from has changed", which should send the caller to
    re-verify rather than let it serve a confident wrong answer."""
    got = recall(query, k=k, db=db)
    if not got:
        return dict(status="miss", query=query, facts=[],
                    guidance="No stored fact matched. Run the expensive path once, then call "
                             "remember() with the evidence so this question is cheap next time.")
    fresh = [f for f in got if f["verdict"] == FRESH]
    if fresh:
        return dict(status="hit", query=query, facts=fresh,
                    guidance="Answer from these facts and cite evidence_path:evidence_line.")
    return dict(status="stale", query=query, facts=got,
                guidance="Matched facts exist but their evidence files have changed since they were "
                         "recorded. Re-verify against the cited paths, then remember() the update.")


# ── SEEDING: harvest the repo's own source of truth ──────────────────────────────────────────────
# Seeding matters because an empty cache makes the FIRST ask of every question expensive, and the
# measurement says triage questions are 73% of prompts. source_registry.py is the single highest-value
# seed in the repo: it is the declared truth for every source, so "is X enabled / what cadence / what
# tables" becomes a hit on day one instead of after a 28-message crawl.
def harvest_registry(db=None, path=None):
    """Read source_registry.SOURCES and store the facts triage questions actually ask for.

    Imports the module rather than regex-parsing it: the registry IS python, so importing gets the
    real evaluated values, and a parse that drifts from the code would seed confident wrong facts."""
    sys.path.insert(0, HERE)
    import source_registry as SR
    rel = path or "unifyd/source_registry.py"
    n = 0
    for s in SR.SOURCES:
        sid = s.get("id")
        if not sid:
            continue
        for claim, val in (
            ("enabled", s.get("enabled")),
            ("cadence", s.get("cadence")),
            ("klass (execution class)", s.get("klass")),
            ("warehouse tables written", ", ".join(s.get("tables") or []) or None),
            ("cost class", s.get("cost_class")),
            ("required credentials", ", ".join(s.get("requires") or []) or None),
            ("entrypoint code", s.get("code")),
            ("note", s.get("note")),
        ):
            if val in (None, ""):
                continue
            remember(sid, claim, val, kind=DETERMINISTIC, evidence_path=rel, db=db)
            n += 1
    return n


def stats(db=None):
    c = _conn(db)
    rows = [dict(r) for r in c.execute("SELECT * FROM facts").fetchall()]
    v = {FRESH: 0, STALE: 0, UNVERIFIABLE: 0}
    for r in rows:
        v[verdict(r)] += 1
    return dict(facts=len(rows), subjects=len({r["subject"] for r in rows}),
                deterministic=sum(1 for r in rows if r["kind"] == DETERMINISTIC),
                inferred=sum(1 for r in rows if r["kind"] == INFERRED),
                hits=sum(r["hits"] or 0 for r in rows), verdicts=v)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hoodie Cockpit fact store.")
    ap.add_argument("--harvest", action="store_true", help="seed from source_registry.py")
    ap.add_argument("--ask", default=None)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args(argv)
    if a.harvest:
        print("seeded %d facts from source_registry" % harvest_registry())
    if a.ask:
        res = answer(a.ask)
        print("status: %s" % res["status"])
        for f in res["facts"]:
            cite = f.get("evidence_path") or f.get("evidence_cmd") or "(no evidence)"
            print("  [%s] %s — %s = %s   <- %s" % (f["verdict"], f["subject"], f["claim"],
                                                   f["value"], cite))
        print("  %s" % res["guidance"])
    if a.stats or not (a.harvest or a.ask):
        print(json.dumps(stats(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
