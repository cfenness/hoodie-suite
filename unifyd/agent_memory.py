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
        # No file to hash. An evidence_cmd records HOW the fact was produced but is not re-runnable
        # here, so it cannot establish freshness either. (Was a ternary with identical branches — a
        # dead expression that read as if the two cases differed. Crew finding D4.)
        return UNVERIFIABLE
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


# Words that carry no identifying information. WITHOUT this filter the relevance gate is defeated by
# its own tokenizer: `_tokens` splits on non-word characters, so the subject `stop-and-shop` becomes
# ['and', 'shop', 'stop'] — and any question containing the word "and" then scored a SUBJECT match
# against it. Observed: "what does unifyd/deploy_guard.py refuse to do and why?" returned stop-and-shop
# facts as a confident hit, matched entirely on the word "and".
#
# The 3-character floor does the rest: splitting `unifyd/deploy_guard.py` yields a useful `deploy_guard`
# plus a useless `py`, and 2-letter fragments of identifiers match everything.
_TOK_STOP = set("the a an and or but not for with from into per via out off top all any one two "
                "does did was are is be been has have had can could should would will what when "
                "which who how why this that these those you your our its it in on at to of as if "
                "do done make made get got use used run ran new old sentence short long same other "
                "more most much many some such then than also only very just about here there".split())


def _tokens(s):
    """Content tokens for relevance matching: split on non-word chars, then drop noise.

    Both filters are load-bearing — see _TOK_STOP above for the false hit that motivated them."""
    return [t for t in re.split(r"[^\w]+", (s or "").lower())
            if len(t) >= 3 and t not in _TOK_STOP]


GENERIC_DF = 0.25       # a token in >25% of facts identifies nothing


def _df(db=None, _cache={}):
    """Document frequency of every token across all subjects and claims.

    THE STRUCTURAL FIX for what a stopword list could never solve. `_TOK_STOP` is patch-by-observation:
    `and` was added only after a live false hit, and the next two — `data` (subject `data-console`
    matching "what format does this csv data use") and `note` (EVERY harvested source carries a literal
    `note` claim, so any question containing the ordinary word "note" hit an unrelated source) — were
    not in it and could not have been predicted. A hand-maintained list of generic words is always one
    incident behind.

    Genericness is a property of THIS store's contents, so measure it instead of guessing: a token
    appearing in a quarter of all facts distinguishes nothing, whatever the word happens to be. That
    adapts as the store grows and needs no maintenance."""
    key = db or DB
    c = _conn(db)
    n = c.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"] or 0
    hit = _cache.get(key)
    if hit and hit[0] == n:
        return hit[1], n
    df = {}
    for r in c.execute("SELECT subject, claim FROM facts").fetchall():
        for t in set(_tokens(r["subject"])) | set(_tokens(r["claim"])):
            df[t] = df.get(t, 0) + 1
    _cache[key] = (n, df)
    return df, n


def _qualifies(shared, whole, db=None):
    """Does the query name ENOUGH of `whole` to identify it? Returns (bool, why).

    COVERAGE, not frequency. My first fix measured how common a token is across the store, and that is
    the wrong quantity twice over: in a one-fact store every token is 100% frequent so nothing
    qualifies, and in a twenty-fact store `data` looks rare and distinctive. The verdict changed with
    the size of the database rather than with the question — a rule you cannot reason about.

    What actually distinguishes a real lookup from an accident is whether the asker named the subject
    or merely collided with a fragment of it. `data-console` shatters into {data, console}; a question
    about "csv data" supplies one of the two and has not named the subject. `abc-fws` shatters into
    {abc, fws} and a question containing `abc-fws` supplies both. So: match at least two of the
    subject's tokens, or all of them when it only has one.

    This is independent of store size, needs no maintained word list, and states its reason."""
    if not shared:
        return False, "no overlap"
    need = min(2, len(whole)) or 1
    if len(shared) >= need:
        return True, "named %d of %d token(s)" % (len(shared), len(whole))
    missing = sorted(whole - shared)
    return False, ("only %s of %d tokens matched — %s not named, so the subject was not identified"
                   % (sorted(shared), len(whole), missing))


def matched_on(row, query, db=None):
    """WHERE the match landed: 'subject' | 'claim' | 'prose'. This is the relevance gate.

    THE BUG THIS EXISTS TO KILL (found end-to-end, and it was the worst one):
    asked "what does write_accumulate do differently from write_parquet", the store returned
    `naop note`, `abc-catalog note` and `kroger-api note` — three unrelated sources whose long free-text
    `note` happened to share common words — and `answer()` reported it as a HIT. So it confidently
    answered a question it has no answer to AND suppressed the model that could have answered properly.
    A false hit is strictly worse than a miss: a miss falls through to the expensive path and gets the
    right answer, while a false hit gets a wrong one for free and stops there.

    Sharing a word with a paragraph is not knowing the answer. A real hit has to match the thing the
    fact is ABOUT (its subject) or the property being asked for (its claim) — prose-only matches are
    demoted to related-reading, never served as the answer."""
    qt = set(_tokens(query))
    subj = set(_tokens(row.get("subject")))
    ok, why = _qualifies(qt & subj, subj, db)
    if ok:
        row["match_why"] = "subject: " + why
        return "subject"
    # A CLAIM ALONE IS NOT AN ANSWER, and the df threshold could not express this.
    # The claim is a PROPERTY; the subject is what the property belongs to. Matching only the claim
    # tells you someone's `note` / `cadence` / `enabled` — but not whose, so it cannot answer anything.
    # That is why "leave a quick note about lunch" hit `total-wine note` and survived the frequency
    # rule: `note` sits on ~14% of facts, under any threshold loose enough to keep real hits.
    # Requiring 2+ shared claim tokens shuts that door without touching the real cases, because a
    # question that genuinely targets a property by name says more than one word about it
    # ("warehouse tables written"), while every legitimate lookup here also names its subject.
    claim_toks = set(_tokens(row.get("claim")))
    claim_shared = qt & claim_toks
    ok, why = _qualifies(claim_shared, claim_toks, db)
    if ok and len(claim_shared) >= 2:
        row["match_why"] = "claim: " + why
        return "claim"
    if ok:
        why = ("only the property %r matched and no subject did — a property with no subject "
               "identifies nothing" % next(iter(claim_shared)))
    row["match_why"] = why
    return "prose"


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
        d["matched_on"] = matched_on(d, query, db)
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
        return dict(status="miss", query=query, facts=[], related=[],
                    guidance="No stored fact matched. Run the expensive path once, then call "
                             "remember() with the evidence so this question is cheap next time.")
    # RELEVANCE GATE. A fact only answers the question if the match landed on its subject (what it is
    # about) or its claim (the property asked for). A prose-only match — a shared word inside somebody's
    # long `note` — is related reading, not an answer, and serving it as a hit both misinforms and
    # suppresses the model that would have got it right. See matched_on() for the observed failure.
    relevant = [f for f in got if f["matched_on"] in ("subject", "claim")]
    weak = [f for f in got if f["matched_on"] == "prose"]
    if not relevant:
        return dict(status="miss", query=query, facts=[], related=weak[:3],
                    guidance="Nothing stored matched on subject or property — only incidental word "
                             "overlap in prose, which is not an answer. Run the expensive path, then "
                             "remember() the result so this becomes cheap.")
    fresh = [f for f in relevant if f["verdict"] == FRESH]
    if fresh:
        return dict(status="hit", query=query, facts=fresh, related=weak[:2],
                    guidance="Answer from these facts and cite evidence_path:evidence_line.")
    got = relevant
    # D1 (found by the crew): STALE and UNVERIFIABLE were both reported as "stale", with guidance
    # telling you to re-check a file that in the unverifiable case never existed. Never safe to serve
    # either as an answer, but they need different action — one re-verifies, the other has nothing to
    # verify against — and describing one as the other is the same dishonesty the verdict exists to
    # prevent, just quieter.
    if any(f["verdict"] == STALE for f in got):
        return dict(status="stale", query=query, facts=got, related=weak[:2],
                    guidance="Matched facts exist but their evidence files have changed since they "
                             "were recorded. Re-verify against the cited paths, then remember() the "
                             "update.")
    return dict(status="unverifiable", query=query, facts=got, related=weak[:2],
                guidance="Matched facts have no checkable evidence (no file recorded, or the file is "
                         "gone). They cannot be confirmed — treat as a lead, not an answer, and "
                         "re-derive if it matters.")


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


# ── WRITE-BACK: the loop that makes this a cache instead of a lookup table ───────────────────────
# Without this, every question stays a first-time question forever and the store only ever knows what
# was seeded. The measured prize is large: `triage` is 66.9% of spend at 27.6 assistant messages per
# question, and each of those answers is thrown away the moment it's read.
#
# THE HONESTY CONSTRAINT, and it's the whole reason this isn't three lines:
# A model's answer is NOT a registry value. It is `inferred`, and it must never read back with the same
# authority as a fact parsed out of source_registry.py — otherwise the store slowly fills with
# plausible recollections wearing the same citation format as declared truth.
# So: kind=INFERRED always, and the evidence is a FILE THE ANSWER CITED. That last part is what makes
# it self-correcting — if the answer says "warehouse.py merges rows" and warehouse.py later changes,
# the hash moves and the fact goes `stale` instead of confidently repeating itself.

# Identifier-shaped tokens: snake_case, dotted paths, hyphenated source ids. In this domain the subject
# of a question is almost always one of these, which is why lexical extraction is enough and an extra
# model turn to "identify the subject" would be paying to learn something already on the page.
_IDENT = re.compile(r"\b[a-z][a-z0-9_]*_[a-z0-9_]+\b|\b[a-z][\w.]*\.(?:py|html|json|md)\b"
                    r"|\b[a-z][a-z0-9]*-[a-z0-9-]+\b|\b[a-z_]+\.[a-z_]+\(\)")
_PATH = re.compile(r"\b((?:unifyd|apps|tools|spine|snowflake)/[\w./-]+\.\w+)\b")


def _subject_of(question):
    """Pick the fact's subject from the question. Longest identifier wins — the most specific token is
    the one a future question about the same thing will also contain."""
    cands = _IDENT.findall(question or "")
    cands = [c for c in cands if len(c) > 3]
    return max(cands, key=len) if cands else None


def _cited_file(text):
    """First repo-relative path mentioned in the answer that actually exists. That file becomes the
    staleness anchor, so the fact expires when the code it describes moves."""
    root = os.path.dirname(HERE)
    for m in _PATH.finditer(text or ""):
        if os.path.exists(os.path.join(root, m.group(1))):
            return m.group(1)
    return None


def remember_answer(question, answer, chat_id=None, db=None):
    """Store a model answer so the same question is free next time. Returns the fact id, or None.

    Returns None (writes nothing) when there is no identifiable subject, because a fact with no subject
    can only ever be retrieved by prose match — and prose matches are exactly what the relevance gate
    refuses to serve. Storing one would be adding a row that can never legitimately answer anything."""
    q = (question or "").strip()
    a = (answer or "").strip()
    if not q or not a or len(a) < 8:
        return None
    subject = _subject_of(q)
    if not subject:
        return None
    # The claim is the question itself, trimmed — so a later ask matches on claim even if the wording
    # drifts. Capped because the claim is half the retrieval surface, not a place for an essay.
    claim = re.sub(r"\s+", " ", q).strip(" ?.!")[:180]
    return remember(subject, claim, a[:4000], kind=INFERRED,
                    evidence_path=_cited_file(a),
                    evidence_cmd="model answer%s" % (" via %s" % chat_id if chat_id else ""),
                    db=db)


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
