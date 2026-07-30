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

    print("\n%d checks, %d failed" % (len(RAN), len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
