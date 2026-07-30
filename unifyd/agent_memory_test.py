#!/usr/bin/env python3
"""agent_memory_test.py — proves the fact store cannot serve a confident wrong answer.

A cache over your own findings is only safe if staleness is mechanical. If it isn't, the store
becomes a laundering machine: an answer that was true in June gets served in September with the same
confidence and a citation that makes it look verified. That is the "quiet degrade" class, and it is
worse than having no cache at all — you'd at least have re-checked.

So the centerpiece here is: mutate the evidence file, and the fact MUST come back `stale`, and
`answer()` must NOT report `hit`. Everything else is supporting cast.

    python3 unifyd/agent_memory_test.py
"""
import os
import sys
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


def main():
    print("agent_memory — retrieval instead of re-derivation")
    import agent_memory as M

    tmp = tempfile.mkdtemp(prefix="cockpit-mem-")
    db = os.path.join(tmp, "t.db")
    ev = os.path.join(tmp, "evidence.py")
    with open(ev, "w") as fh:
        fh.write("enabled = True\n")

    # --- 1. roundtrip -----------------------------------------------------------------------------
    fid = M.remember("abc-fws", "enabled", "True", evidence_path=ev, evidence_line=67, db=db)
    check("remember returns an id", bool(fid), fid)
    got = M.recall("abc-fws enabled", db=db)
    check("recall finds the fact", len(got) >= 1, got)
    check("recall returns the stored value", got[0]["value"] == "True", got[0])
    check("recall carries the evidence path", got[0]["evidence_path"] == ev)
    check("recall carries the evidence line", got[0]["evidence_line"] == 67)

    # --- 2. STALENESS — the load-bearing rule -----------------------------------------------------
    check("unmodified evidence reads FRESH", got[0]["verdict"] == M.FRESH, got[0]["verdict"])
    res = M.answer("abc-fws enabled", db=db)
    check("answer() reports hit while fresh", res["status"] == "hit", res["status"])

    with open(ev, "a") as fh:                      # the file the fact came from changes
        fh.write("enabled = False   # flipped\n")

    got2 = M.recall("abc-fws enabled", db=db)
    check("mutated evidence flips the fact to STALE", got2[0]["verdict"] == M.STALE,
          got2[0]["verdict"])
    res2 = M.answer("abc-fws enabled", db=db)
    check("STALE IS NOT A HIT (the whole point)", res2["status"] == "stale", res2["status"])
    check("stale answer still hands back the citation to re-verify",
          res2["facts"][0]["evidence_path"] == ev)
    check("stale guidance tells the caller to re-verify",
          "re-verify" in res2["guidance"].lower(), res2["guidance"])
    check("include_stale=False can filter stale out entirely",
          M.recall("abc-fws enabled", db=db, include_stale=False) == [])

    # A deleted evidence file must NOT read fresh — absence of proof is not proof.
    os.remove(ev)
    got3 = M.recall("abc-fws enabled", db=db)
    check("deleted evidence reads UNVERIFIABLE, never fresh",
          got3[0]["verdict"] == M.UNVERIFIABLE, got3[0]["verdict"])

    # --- 3. no evidence -> can never claim fresh --------------------------------------------------
    M.remember("hunch", "best approach", "probably shard it", kind=M.INFERRED, db=db)
    h = M.recall("hunch best approach", db=db)
    check("a fact with no evidence is UNVERIFIABLE", h[0]["verdict"] == M.UNVERIFIABLE,
          h[0]["verdict"])
    check("inferred facts keep their kind (never blurred into deterministic)",
          h[0]["kind"] == M.INFERRED, h[0]["kind"])
    try:
        M.remember("x", "y", "z", kind="probably", db=db)
        check("rejects an invented kind", False, "no error raised")
    except ValueError:
        check("rejects an invented kind", True)
    for bad in (("", "claim"), ("subj", "")):
        try:
            M.remember(bad[0], bad[1], "v", db=db)
            check("rejects empty subject/claim %r" % (bad,), False, "no error")
        except ValueError:
            check("rejects empty subject/claim %r" % (bad,), True)

    # --- 4. supersede: the FTS index must not keep serving the OLD text ---------------------------
    # Without the AFTER UPDATE trigger the superseded value stays searchable and keeps winning
    # retrieval — a stale answer that looks current. This is that trigger's regression test.
    ev2 = os.path.join(tmp, "e2.py")
    with open(ev2, "w") as fh:
        fh.write("cadence = 'daily'\n")
    M.remember("kroger", "cadence", "daily", evidence_path=ev2, db=db)
    M.remember("kroger", "cadence", "weekly", evidence_path=ev2, db=db)
    k = M.recall("kroger cadence", db=db)
    check("re-writing supersedes rather than duplicating",
          len([f for f in k if f["subject"] == "kroger"]) == 1, k)
    check("recall returns the NEW value", k[0]["value"] == "weekly", k[0]["value"])
    check("the superseded value is not retrievable",
          not any(f["value"] == "daily" for f in k), k)

    # --- 5. identifier-shaped queries must not blow up FTS5 --------------------------------------
    # These are the REAL query shapes here; every one of them is FTS5 operator syntax if unquoted.
    M.remember("dim_item.sku", "grain", "product x size", evidence_path=ev2, db=db)
    for q in ("abc-fws", "dim_item.sku", "write_accumulate()", "a AND b", 'quote" inside',
              "NEAR(x y)", "*", "-", "", "   ", "OR OR OR"):
        try:
            M.recall(q, db=db)
            check("identifier/operator query %-16r does not raise" % q, True)
        except Exception as e:
            check("identifier/operator query %-16r does not raise" % q, False, repr(e))
    check("hyphenated identifier still retrieves its fact",
          any(f["subject"] == "dim_item.sku" for f in M.recall("dim_item sku", db=db)))

    # --- 6. miss semantics ------------------------------------------------------------------------
    miss = M.answer("something never recorded about quantum bananas", db=db)
    check("unmatched query is a MISS, not an empty hit", miss["status"] == "miss", miss["status"])
    check("miss guidance names the write-back step (how the cache learns)",
          "remember" in miss["guidance"], miss["guidance"])
    check("miss returns no facts", miss["facts"] == [])

    # --- 6b. RELEVANCE GATE: a shared word is not an answer --------------------------------------
    # The bug this pins, found end-to-end: asking about `write_accumulate` returned three unrelated
    # sources whose long `note` shared common words, and answer() called it a HIT — so it answered
    # wrongly AND suppressed the model that would have answered correctly. A false hit is strictly
    # worse than a miss: a miss falls through and gets the right answer.
    ev3 = os.path.join(tmp, "e3.py")
    with open(ev3, "w") as fh:
        fh.write("x = 1\n")
    M.remember("naop", "note", "DoorDash on-premise menus, consumes doordash stores in batches",
               evidence_path=ev3, db=db)
    M.remember("write_accumulate", "behaviour", "merges instead of overwriting",
               evidence_path=ev3, db=db)

    # Words appear only inside another fact's prose -> must be a MISS, with the weak matches demoted.
    r = M.answer("what does the batches process consume for menus on premise", db=db)
    check("prose-only overlap is a MISS, not a hit", r["status"] == "miss", r["status"])
    check("...and the weak matches come back as `related`, not as the answer",
          len(r.get("related") or []) >= 1 and r["facts"] == [], r)
    check("miss guidance says why (incidental overlap, not an answer)",
          "overlap" in r["guidance"] or "subject" in r["guidance"], r["guidance"])

    # A subject match still answers.
    r2 = M.answer("write_accumulate behaviour", db=db)
    check("a SUBJECT match is a real hit", r2["status"] == "hit", r2["status"])
    check("the hit is the right fact", r2["facts"][0]["subject"] == "write_accumulate", r2["facts"][0])
    check("hits report where the match landed",
          r2["facts"][0]["matched_on"] in ("subject", "claim"), r2["facts"][0].get("matched_on"))

    # A CLAIM match answers too (asking for a property by name).
    r3 = M.answer("naop note", db=db)
    check("a CLAIM match is a real hit", r3["status"] == "hit", r3["status"])
    check("every returned fact carries matched_on",
          all("matched_on" in f for f in M.recall("naop", db=db)))

    # --- 6c. the tokenizer must not defeat the gate ----------------------------------------------
    # Observed: `_tokens` splits on non-word chars, so the subject "stop-and-shop" became
    # ['and','shop','stop'] and ANY question containing the word "and" scored a SUBJECT match against
    # it. "what does unifyd/deploy_guard.py refuse to do and why?" came back as a confident hit on
    # stop-and-shop facts, matched entirely on "and". The gate was correct; its input was not.
    check("stopwords are not tokens", "and" not in M._tokens("stop-and-shop"),
          M._tokens("stop-and-shop"))
    check("2-letter identifier fragments are dropped", "py" not in M._tokens("deploy_guard.py"),
          M._tokens("deploy_guard.py"))
    check("real identifier survives tokenizing",
          "deploy_guard" in M._tokens("unifyd/deploy_guard.py"), M._tokens("unifyd/deploy_guard.py"))
    M.remember("stop-and-shop", "enabled", "False", evidence_path=ev3, db=db)
    r4 = M.answer("what does deploy_guard refuse to do and why", db=db)
    check("a question sharing only 'and' with a subject is a MISS",
          r4["status"] == "miss", (r4["status"], [f["subject"] for f in r4.get("related") or []]))
    r5 = M.answer("is stop-and-shop enabled", db=db)
    check("...but a genuine subject match on the same fact still HITS",
          r5["status"] == "hit" and r5["facts"][0]["subject"] == "stop-and-shop", r5["status"])

    # --- 6d. write-back: the loop that makes this a cache, not a lookup table --------------------
    wq = "what does unifyd/warehouse.py write_accumulate do differently"
    wa = "`write_accumulate` merges rows, while unifyd/warehouse.py write_parquet overwrites."
    wid = M.remember_answer(wq, wa, chat_id="chat:test", db=db)
    check("a model answer is written back", bool(wid), wid)
    back = M.answer("what does write_accumulate do", db=db)
    check("the same question is now a HIT", back["status"] == "hit", back["status"])
    f0 = back["facts"][0]
    check("a written-back answer is INFERRED, never deterministic", f0["kind"] == M.INFERRED, f0["kind"])
    check("...and is anchored to the file the answer cited, so it can go stale",
          f0["evidence_path"] == "unifyd/warehouse.py", f0["evidence_path"])
    check("...and records that a model produced it", "model answer" in (f0["evidence_cmd"] or ""),
          f0["evidence_cmd"])
    # Guards: nothing worth retrieving, nothing written.
    check("no identifiable subject -> nothing stored",
          M.remember_answer("what is the best approach here", "some prose", db=db) is None)
    check("empty answer -> nothing stored", M.remember_answer("about x_y_z", "", db=db) is None)
    check("trivial answer -> nothing stored", M.remember_answer("about x_y_z", "yes", db=db) is None)

    # --- 6e. the crew's blocker: a lone generic word is not identification ----------------------
    # Found by an independent QA+review pass, not by me. Both reproductions come straight from that
    # review, and both defeated the previous fix — which is the point: `_TOK_STOP` was
    # patch-by-observation, so it only ever contained the words that had already burned us.
    M.remember("data-console", "purpose", "the one trustworthy data-inspection surface",
               evidence_path=ev3, db=db)
    M.remember("total-wine", "note", "PerimeterX — browser required", evidence_path=ev3, db=db)
    for i in range(12):          # make `note` common, as harvest_registry makes it in the real store
        M.remember("src-%d" % i, "note", "some free text about a source", evidence_path=ev3, db=db)

    r6 = M.answer("what format does this csv data use", db=db)
    check("a lone generic SUBJECT token ('data') is not a hit", r6["status"] == "miss", r6["status"])
    r7 = M.answer("leave a quick note about lunch", db=db)
    check("a lone CLAIM token ('note') with no subject is not a hit",
          r7["status"] == "miss", r7["status"])
    check("...and the reason is stated, not silent",
          "identifies nothing" in (r7["guidance"] + str(r7.get("related"))) or r7["facts"] == [],
          r7["guidance"])
    # The real lookups must survive all of it.
    for q in ("total-wine note", "data-console purpose"):
        check("a subject+property question still hits: %r" % q,
              M.answer(q, db=db)["status"] == "hit", M.answer(q, db=db)["status"])
    check("genericness is measured from the store, not a word list",
          callable(getattr(M, "_df", None)) and callable(getattr(M, "_qualifies", None)))
    df, n = M._df(db)
    check("df counts every fact", n >= 14, n)
    check("'note' is measured as common", df.get("note", 0) >= 13, df.get("note"))

    # --- 6f. crew finding D1: unverifiable must not be described as stale -----------------------
    # Both are un-servable, but they need different action — one re-verifies against a file, the other
    # has no file to verify against. Reporting one as the other told you to go re-check something that
    # never existed.
    dbu = os.path.join(tmp, "unv.db")
    M.remember("lonely_subject", "some property", "a value with no evidence at all", db=dbu)
    ru = M.answer("lonely_subject some property", db=dbu)
    check("an evidence-less match is 'unverifiable', not 'stale'",
          ru["status"] == "unverifiable", ru["status"])
    check("...and is never a hit", ru["status"] != "hit")
    check("its guidance does NOT claim a file changed",
          "changed" not in ru["guidance"], ru["guidance"])

    # --- 7. stats -----------------------------------------------------------------------------
    st = M.stats(db=db)
    check("stats counts facts", st["facts"] >= 3, st)
    check("stats separates deterministic from inferred",
          st["deterministic"] >= 1 and st["inferred"] >= 1, st)
    check("stats reports a verdict breakdown", set(st["verdicts"]) ==
          {M.FRESH, M.STALE, M.UNVERIFIABLE}, st["verdicts"])

    # --- 8. the real seed works and is deterministic-only -----------------------------------------
    db2 = os.path.join(tmp, "seed.db")
    n = M.harvest_registry(db=db2)
    check("harvest seeds a substantial number of facts", n > 200, n)
    s2 = M.stats(db=db2)
    check("harvested facts are all deterministic (read from declared truth)",
          s2["inferred"] == 0, s2)
    check("harvested facts are FRESH against the live registry",
          s2["verdicts"][M.FRESH] == s2["facts"], s2["verdicts"])
    hit = M.answer("is abc-fws enabled", db=db2)
    check("the seeded store answers a real triage question on the first ask",
          hit["status"] == "hit", hit["status"])
    check("...and cites source_registry.py",
          "source_registry.py" in (hit["facts"][0]["evidence_path"] or ""), hit["facts"][0])

    # --- 9. synthesis_prompt: the hybrid path's grounding, not a second answer() ---------------------
    check("no facts -> no prompt (an ungrounded call must never wear this function's name)",
          M.synthesis_prompt("why does this matter", []) is None)
    check("...same for None", M.synthesis_prompt("q", None) is None)

    facts = M.answer("is abc-fws enabled", db=db2)["facts"]
    p = M.synthesis_prompt("should I trust abc-fws right now", facts)
    check("prompt is grounded in the actual fact text", "abc-fws" in p, p)
    check("prompt carries the question verbatim", "should I trust abc-fws right now" in p, p)
    check("prompt forbids tool use (there's nothing left to explore)", "do not use any tools" in p, p)
    check("prompt tells the model not to re-derive what's already known", "re-derive" in p, p)
    check("prompt surfaces the evidence citation, not just the bare value",
          "source_registry.py" in p, p)

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
