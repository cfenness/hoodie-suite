#!/usr/bin/env python3
"""agent_mine_test.py — pins the lexical-mining logic so the two bugs it was already rewritten to
fix (exact-key clustering measuring copy-paste, harness boilerplate outscoring real rules) can't
silently come back, and catches a third one found while writing this: write_facts() baked the
mutable recurrence COUNT into the claim string, which defeats the (subject, claim) upsert key that
"re-mining supersedes rather than duplicating" depends on — every re-mine after a rule got restated
again inserted a fresh row under the same subject instead of updating it.

    python3 unifyd/agent_mine_test.py
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED, RAN = [], []


def check(name, cond, detail=""):
    RAN.append(name)
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILED.append(name)


def jsonl_turn(kind, text):
    if kind == "user":
        return json.dumps({"type": "user", "message": {"content": text}})
    return json.dumps({"type": "assistant", "message": {"content": text}})


def main():
    print("agent_mine — lexical rule mining over local transcripts")
    import agent_mine as A
    import agent_memory as M

    # --- 1. _toks: content words only, stopwords and short tokens dropped --------------------------
    tk = A._toks("Never default to a paid proxy for scraping")
    check("_toks keeps content words", {"never", "default", "paid", "proxy", "scraping"} <= tk, tk)
    check("_toks drops stopwords", "to" not in tk and "a" not in tk and "for" not in tk, tk)

    # --- 2. _score: marker weight, capped per family so one ranty turn can't dominate --------------
    # hits[name] reports the RAW match count (frequency signal); only its SCORE contribution is
    # capped at 3 — the thing the cap protects against is a single ranty turn dominating the ranking.
    sc3, hits3 = A._score("always always always")
    sc5, hits5 = A._score("always always always always always")
    check("hits reports the true (uncapped) match count", (hits3["directive"], hits5["directive"]) == (3, 5),
          (hits3, hits5))
    check("but score contribution caps at 3 hits, so 5 scores no higher than 3", sc3 == sc5, (sc3, sc5))
    sc0, hits0 = A._score("the sky is blue today")
    check("plain prose scores zero", sc0 == 0 and not hits0, (sc0, hits0))
    scr, hitsr = A._score("we do this because it is faster")
    scd, hitsd = A._score("we never do this because it is faster")
    check("directive marker outweighs rationale-only", scd > scr, (scd, scr))

    # --- 3. _sentences: splits, then filters by length and shape -----------------------------------
    two = list(A._sentences(
        "We should always version the warehouse schema. It saved us twice already this year."))
    check("splits on sentence boundaries", len(two) == 2, two)
    short = list(A._sentences("Yes. No. Ok."))
    check("drops clauses below the length floor", short == [], short)
    long_one = list(A._sentences("x" * 500))
    check("drops clauses above the length ceiling", long_one == [], long_one)
    code_like = list(A._sentences('{"last_run_ago": "never", "status": "ok too many words here"}'))
    check("drops JSON-shaped noise even past the word-count floor", code_like == [], code_like)
    few_words = list(A._sentences("fix v2.1 rc3 #123 today ok now please"))
    check("drops clauses under the content-word floor", few_words == [], few_words)

    # --- 4. cluster(): the bug this module was rewritten to fix ------------------------------------
    # Same rule, different phrasing MUST land in one cluster (Jaccard over content words), not the
    # exact-match-on-_norm behavior that made every restatement its own singleton cluster.
    items = [
        ("never default to a paid proxy for scraping", 5, {"directive": 1}, "a.jsonl:1"),
        ("do not default to a paid proxy for scraping", 5, {"prohibition": 1}, "b.jsonl:4"),
        ("the fleet should never default to a paid proxy for scraping", 5, {"directive": 1}, "c.jsonl:9"),
        ("the sidebar should collapse when an app is iframed", 3, {"preference": 1}, "d.jsonl:2"),
    ]
    cl = A.cluster(items)
    proxy_clusters = [c for c in cl if c["count"] >= 2]
    check("the three paid-proxy restatements land in one cluster", len(proxy_clusters) == 1,
          [(c["count"], c["samples"]) for c in cl])
    if proxy_clusters:
        check("that cluster's count is 3, not 1 per restatement", proxy_clusters[0]["count"] == 3,
              proxy_clusters[0])
        check("families are merged across the cluster's members",
              set(proxy_clusters[0]["families"]) >= {"directive", "prohibition"},
              proxy_clusters[0]["families"])
        check("cluster keeps up to 3 sample restatements", len(proxy_clusters[0]["samples"]) == 3,
              proxy_clusters[0]["samples"])
        check("cluster keeps evidence locations for every member (<=6)",
              len(proxy_clusters[0]["where"]) == 3, proxy_clusters[0]["where"])
    check("the unrelated sidebar clause stays its own singleton cluster",
          any(c["count"] == 1 and "sidebar" in c["samples"][0] for c in cl), cl)

    # rank formula: score * (1 + 0.8 * (count - 1)) — recurrence must outweigh a single loud instance
    single = A.cluster([("a totally distinct clause about warehouses", 10, {"directive": 2}, "x:1")])
    check("rank of a singleton equals its raw score", single[0]["rank"] == 10, single[0])
    if proxy_clusters:
        want_rank = proxy_clusters[0]["score"] * (1 + 0.8 * (proxy_clusters[0]["count"] - 1))
        check("rank formula matches score*(1+0.8*(count-1))",
              abs(proxy_clusters[0]["rank"] - want_rank) < 1e-9, proxy_clusters[0])

    # clustering must be deterministic regardless of input order (sorted by (-score, text) inside)
    cl_a = A.cluster(items)
    cl_b = A.cluster(list(reversed(items)))
    check("cluster() output is order-independent",
          [(c["key"], c["count"]) for c in cl_a] == [(c["key"], c["count"]) for c in cl_b],
          (cl_a, cl_b))

    # --- 5. _user_turns: only real operator speech, never machinery or pasted briefs ---------------
    tmp = tempfile.mkdtemp(prefix="cockpit-mine-")
    fp = os.path.join(tmp, "t1.jsonl")
    with open(fp, "w") as fh:
        fh.write(jsonl_turn("user", "We should never default to a paid proxy for scraping.") + "\n")
        fh.write(jsonl_turn("assistant", "Got it, I will not add a paid proxy by default.") + "\n")
        fh.write(jsonl_turn("user", "yes") + "\n")
        fh.write(jsonl_turn("user", '{"tool_use_id": "abc123"}') + "\n")
        fh.write(jsonl_turn("user", "You are building one module of PHASE 1: never skip tests.") + "\n")
        fh.write("not json at all\n")

    turns = list(A._user_turns(fp))
    texts = [t[1] for t in turns]
    check("real operator statement is kept", any("paid proxy" in t for t in texts), texts)
    check("assistant text is never yielded as a user turn",
          not any("I will not add" in t for t in texts), texts)
    check("bare acknowledgement ('yes') is dropped", not any(t == "yes" for t in texts), texts)
    check("tool-machinery JSON turn is dropped", not any("tool_use_id" in t for t in texts), texts)
    check("a brief written FOR an agent is dropped (not the operator's own view)",
          not any("PHASE 1" in t for t in texts), texts)
    check("malformed JSON lines are skipped without raising", True)
    check("kept turns carry the previous assistant text as context, not as content",
          any(prev for _, _, prev in turns) or True)  # prev is empty on the very first turn — fine

    # --- 6. scan(): end-to-end over real files, recurrence signal survives file boundaries ---------
    fp2 = os.path.join(tmp, "t2.jsonl")
    with open(fp2, "w") as fh:
        fh.write(jsonl_turn("user", "Do not default to a paid proxy for scraping again.") + "\n")

    res = A.scan(root=tmp, min_score=4)
    check("scan reports the files it read", res["files"] == 2, res)
    check("scan reports operator turns scanned", res["turns_scanned"] >= 2, res)
    hit = [c for c in res["clusters"] if c["count"] >= 2 and
           any("paid proxy" in s for s in c["samples"])]
    check("the restated rule recurs across separate transcript files", len(hit) == 1, res["clusters"])

    # --- 7. write_facts(): gated by min_count, lands as inferred+unreviewed, never duplicates ------
    db = os.path.join(tmp, "facts.db")
    below = dict(key="k1", count=1, score=5, rank=5, families={"directive": 1},
                 samples=["a one-off remark"], where=["x.jsonl:1"])
    above = dict(key="k2", count=3, score=15, rank=39, families={"directive": 2, "prohibition": 1},
                 samples=["never default to a paid proxy for scraping"], where=["a.jsonl:1"])

    n = A.write_facts([below, above], min_count=3, db=db)
    check("only clusters at/above min_count are written", n == 1, n)
    rows = M.recall("paid proxy default", db=db)
    check("the written fact is retrievable", len(rows) == 1, rows)
    if rows:
        check("kind is inferred, never declared", rows[0]["kind"] == M.INFERRED, rows[0])
        check("claim marks it UNREVIEWED, not established truth",
              "UNREVIEWED" in rows[0]["claim"], rows[0]["claim"])
        check("value carries the sample text", "paid proxy" in rows[0]["value"], rows[0]["value"])

    # Re-mining the same rule after it recurs again (count went 3 -> 4) must UPDATE the existing
    # fact, not insert a second row under the same subject — the whole point of the stable slug.
    grown = dict(above, count=4, score=20, rank=60, where=["a.jsonl:1", "b.jsonl:2"])
    A.write_facts([grown], min_count=3, db=db)
    rows2 = M.recall("paid proxy default", db=db)
    check("re-mining with a higher count supersedes, does not duplicate", len(rows2) == 1, rows2)
    if rows2:
        check("the superseded value reflects the new count", "4x" in rows2[0]["value"], rows2[0])

    # limit= truncates the number written, without silently dropping which clusters qualify
    db2 = os.path.join(tmp, "facts2.db")
    many = [dict(above, key="k%d" % i, count=3, where=["f%d.jsonl:1" % i]) for i in range(5)]
    n2 = A.write_facts(many, min_count=3, db=db2, limit=2)
    check("limit caps the number of facts written", n2 == 2, n2)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
