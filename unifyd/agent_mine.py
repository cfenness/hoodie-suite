#!/usr/bin/env python3
"""agent_mine.py — mine STATED RULES AND TASTE out of the local Claude transcripts.

WHY THIS EXISTS:
The fact store seeded from source_registry holds 414 deterministic facts about the system. What it
does NOT hold is the reasoning: why TTB was demoted from spine despite being the most complete
source, why the locator ranks by price percentile instead of % off, which arguments were had and
discarded. That lives in ~228 local session transcripts (34.5B tokens) and nowhere queryable — which
is why the store is tens of thousands of facts short, not hundreds.

THE CHEAP WAY IN — lexical first, LLM only on the shortlist:
An LLM pass over 34.5B tokens to "find the important bits" would cost more than everything it saves,
which would be a self-defeating tool on a surface built to cut spend. But stated rules have
LINGUISTIC MARKERS. People say "never", "hard rule", "no — the point is", "I'd rather", "because".
Those are findable with regex at zero cost. So:
    stage 1 (this module, free)  : scan every USER turn, score it, keep the marked ones
    stage 2 (optional, cheap)    : hand only the shortlist to a model to distil into facts
A few thousand candidates is an affordable LLM pass. 34.5B tokens is not.

WHY ONLY THE USER'S TURNS:
Taste is expressed by the operator, not the assistant. Mining assistant text would mostly recover
this tool's own prose restated — a model agreeing with a rule is not evidence of the rule. The
assistant turn is kept only as CONTEXT, so a bare "no, the other way" has a referent.

FREQUENCY IS THE CONFIDENCE SIGNAL — the key design idea:
A rule stated once is a passing remark. A rule restated twelve times across eight months is
load-bearing, and the restatement is itself the evidence (CLAUDE.md literally annotates several
rules "restated 3x" / "keeps getting reverted"). So candidates are clustered by normalized wording
and ranked by RECURRENCE, not by how emphatic any single instance sounded.

Everything written lands as kind=`inferred` — extracted from prose, never dressed up as declared
truth. Evidence is the transcript path + line, so the staleness check still applies.

    python3 unifyd/agent_mine.py --scan                 # report what's there, write nothing
    python3 unifyd/agent_mine.py --scan --write --min 3  # store rules seen 3+ times
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PROJECTS = os.path.expanduser("~/.claude/projects")

# Marker families. Each is (name, weight, pattern). Weight reflects how reliably the marker
# indicates a durable rule rather than a one-off instruction — `directive` and `prohibition` are the
# strongest, `rationale` alone is weak (everything has a "because" in it somewhere).
MARKERS = [
    ("directive",   5, r"\b(?:always|never|hard rule|standing rule|load.?bearing|non.?negotiable|"
                       r"from now on|every time|must (?:be|always|never)|rule is)\b"),
    ("prohibition", 5, r"\b(?:do ?n[o']?t ever|never again|do not|don'?t (?:ever|use|add|assume|"
                       r"default|propose|re-?add))\b"),
    ("correction",  4, r"(?:^|[.!?]\s+)(?:no|nope|wrong|incorrect)\b|\b(?:that'?s not|not what i|"
                       r"i said|stop (?:doing|using)|quit )\b|\bactually,?\s"),
    ("preference",  4, r"\bi (?:want|like|prefer|need|expect|hate|don'?t like)\b|\bi'?d rather\b|"
                       r"\bthe way i\b|\bideally\b|\bmy preference\b|\blet'?s (?:always|never)\b"),
    ("principle",   3, r"\bthe (?:point|whole point|idea|goal|reason) is\b|\bthink (?:of|about) it as\b|"
                       r"\bwhat matters is\b|\bthe bar is\b|\bfirst principle"),
    ("rationale",   1, r"\bbecause\b|\botherwise\b|\bso that\b|\bthe reason\b"),
]
_COMPILED = [(n, w, re.compile(p, re.I)) for n, w, p in MARKERS]

# Turns that are machinery, not speech. Mining these produces confident nonsense — a hook's text or
# a pasted tool result is not the operator stating a preference.
_NOISE = re.compile(
    r"^\s*(?:<|\[|\{)|tool_use_id|<system-reminder|<command-name|<local-command|"
    r"Caveat: The messages below|^\s*(?:PostToolUse|PreToolUse)|^\s*Running…|"
    r"^\s*\d+\s*$|^\s*(?:y|yes|no|ok|okay|k|sure|go|do it|continue|thanks|ty)\s*[.!]?\s*$", re.I)

# PASTED AGENT BRIEFS are the single biggest source of false positives, and they were the entire top
# of the first ranking: a brief written FOR a subagent is dense with "never / do not / must", so it
# scores like a manifesto while containing none of the operator's own preferences. It's second-person
# role assignment ("You are building one module of PHASE 1…"), so it's cheap to recognise — and it
# has to be dropped at the TURN level, before sentence splitting, or its clauses leak through
# individually.
_PASTED_BRIEF = re.compile(
    r"^\s*you are (?:a|an|the|building|acting|working)\b|^\s*your (?:task|job|role|mission) is\b|"
    r"^\s*(?:role|task|objective|context|deliverable)s?\s*:", re.I)

# Sentence-ish splitter. Deliberately not linguistic: transcripts are full of paths, versions and
# code, and a real sentence tokenizer would shred `unifyd/publix.py` or `v2.1`. Split on terminal
# punctuation followed by whitespace + a capital or a newline, and on hard line breaks.
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])|\n{1,}")

_STOP = set("the a an and or but of to in on for with at by from is are was were be been it this "
            "that these those i we you do does did done can could should would will now then as if "
            "not no yes so up out off over under again very just really into about your our my me "
            "have has had they them their there here what when which who how why all any some more "
            "most much many too also than only own same such other".split())


def _norm(text):
    """Cluster key: content words, sorted, deduped.

    Applied to a SENTENCE, not a whole turn. Fingerprinting an entire message was the bug that made
    recurrence useless — a long turn's word-set is unique to that turn, so restatements of the same
    rule in different surrounding prose never collided and every cluster came back count=1. A rule is
    a clause; the clause is the unit that repeats. Capped at 8 words so 'never default to paid
    proxies' and 'do not default to a paid proxy' land on the same key."""
    words = [w for w in re.findall(r"[a-z][a-z0-9_./-]{2,}", text.lower()) if w not in _STOP]
    return " ".join(sorted(set(words))[:8])


# Sentence-level noise that survived the turn-level filters, each found by reading the first ranking:
#  • HARNESS BOILERPLATE — "Resume directly…", "Pick up the last task as if the break never
#    happened" ranked 1st and 2nd at 47x each. They are injected by the tool on session resume, not
#    typed by the operator, and they are dense with "do not" so they score like manifestos.
#  • PASTED DATA — `"last_run_ago": "never"` ranked 3rd at 13x: a JSON fragment whose value happened
#    to be the word "never". Structure, not speech.
# Frequency cannot save you here: boilerplate is the MOST repeated text in the corpus, so the
# recurrence signal actively promotes it. It has to be excluded by shape.
_SENT_NOISE = re.compile(
    r"resume directly|as if the break never happened|do not acknowledge the summary|"
    r"^\s*[\"'\[{]|[\"']\s*:\s*[\"'\[{]|^\s*(?:def|class|import|from|return|SELECT|INSERT)\b|"
    r"^[A-Z_]{4,}\s*=|</?[a-z-]+>|^\s*[-*+]\s*\[[ x]\]", re.I)


def _sentences(text):
    """Split a turn into candidate rule clauses."""
    for s in _SENT.split(text or ""):
        s = re.sub(r"\s+", " ", s).strip(" -•*\t")
        if not (20 <= len(s) <= 400):
            continue
        if _SENT_NOISE.search(s):
            continue
        # A clause that is mostly identifiers/punctuation is pasted output, not a stated rule.
        words = re.findall(r"[a-z]{3,}", s.lower())
        if len(words) < 5:
            continue
        yield s


JACCARD = 0.55        # token-set overlap at which two clauses count as the same rule


def _toks(text):
    # The char class allows '.', '/', '-' so a path or version stays one token instead of shredding
    # (`unifyd/publix.py`, `v2.1`). Side effect: a clause-final period glues onto whatever word ends
    # the sentence, so "...for scraping." (sentence-final) and "...for scraping," (mid-sentence, comma
    # not in the class) tokenize as two DIFFERENT words — "scraping." vs "scraping" — which silently
    # lost the Jaccard match between two restatements of the same rule that just happened to fall at
    # different points in their sentences. A trailing dot is never part of a real identifier (paths
    # end in an extension, versions end in a digit), so it's dropped after matching, not excluded from
    # the class itself — the class still needs '.' mid-token for `publix.py`.
    return {w for w in (m.rstrip(".") for m in re.findall(r"[a-z][a-z0-9_./-]{2,}", (text or "").lower()))
            if w and w not in _STOP}


def cluster(items, threshold=JACCARD):
    """Group clauses that say the same thing. items = [(text, score, hits, where)].

    WHY SIMILARITY AND NOT AN EXACT KEY — this was the bug that broke the core premise:
    `_norm` produced a sorted token-set string and clustered on equality, so ONE extra word made a new
    cluster. "Most pages do not need a flashy, gigantic hero" and the same sentence plus "keep it
    composed" landed in different clusters. The consequence was worse than untidy: the only clauses
    that ever reached a high count were VERBATIM pastes (harness boilerplate, doc text), while real
    restatements in varied wording all stayed at count=1. So the recurrence signal — the entire basis
    for "frequency is confidence" — was measuring copy-paste, not conviction.

    Jaccard overlap on content-word sets fixes that: same rule, different phrasing, same cluster.
    Greedy single-pass against cluster representatives, over input sorted by (-score, text), so the
    output is deterministic and testable. O(n·k) with k clusters — fine at the few-hundred-candidate
    scale this operates on, and worth far more than the speed of an exact-match dict."""
    reps = []          # [(token_set, cluster_dict)]
    for text, score, hits, where in sorted(items, key=lambda x: (-x[1], x[0])):
        tk = _toks(text)
        if not tk:
            continue
        found = None
        for rtk, c in reps:
            union = tk | rtk
            if union and len(tk & rtk) / float(len(union)) >= threshold:
                found = c
                break
        if found is None:
            found = dict(count=0, score=0, families=defaultdict(int), samples=[], where=[])
            reps.append((tk, found))
        found["count"] += 1
        found["score"] += score
        for k, v in (hits or {}).items():
            found["families"][k] += v
        if len(found["samples"]) < 3 and text not in found["samples"]:
            found["samples"].append(text[:400])
        if len(found["where"]) < 6:
            found["where"].append(where)
    out = []
    for tk, c in reps:
        out.append(dict(key=" ".join(sorted(tk)[:8]), count=c["count"], score=c["score"],
                        rank=c["score"] * (1 + 0.8 * (c["count"] - 1)),
                        families=dict(c["families"]), samples=c["samples"], where=c["where"]))
    out.sort(key=lambda x: -x["rank"])
    return out


def _score(text):
    hits = {}
    total = 0
    for name, weight, rx in _COMPILED:
        n = len(rx.findall(text))
        if n:
            hits[name] = n
            total += weight * min(n, 3)          # cap per family so one ranty turn can't dominate
    return total, hits


def _user_turns(path):
    """Yield (line_no, text, prev_assistant_snippet) for real operator turns in one transcript."""
    prev = ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            m = d.get("message") or {}
            ct = m.get("content")
            txt = ""
            if isinstance(ct, str):
                txt = ct
            elif isinstance(ct, list):
                txt = " ".join(b.get("text", "") for b in ct
                               if isinstance(b, dict) and b.get("type") == "text")
            txt = (txt or "").strip()
            if t == "assistant" and txt:
                prev = txt[-400:]
            elif t == "user" and txt:
                if _NOISE.search(txt) or len(txt) < 12:
                    continue
                if _PASTED_BRIEF.search(txt):
                    continue          # a brief written FOR an agent, not the operator's own view
                yield i, txt, prev


def scan(root=None, min_score=4, limit_files=None):
    """Stage 1. Returns clusters ranked by recurrence x marker strength. Costs nothing."""
    root = root or PROJECTS
    files = sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
    if limit_files:
        files = files[:limit_files]
    items, scanned = [], 0
    for fp in files:
        try:
            for line_no, turn, prev in _user_turns(fp):
                scanned += 1
                for txt in _sentences(turn):
                    sc, hits = _score(txt)
                    if sc < min_score:
                        continue
                    items.append((txt, sc, hits,
                                  "%s:%d" % (os.path.relpath(fp, root), line_no)))
        except Exception:
            continue
    return dict(files=len(files), turns_scanned=scanned, clusters=cluster(items))


def write_facts(clusters, min_count=3, db=None, limit=None):
    """Store recurring clauses as UNREVIEWED CANDIDATES — explicitly not as established rules.

    THE LIMIT THIS FUNCTION HAS TO RESPECT (measured, and it is not fixable lexically):
    A regex cannot distinguish the operator STATING a preference from the operator PASTING a document
    that contains preferences. Run against the real corpus, the top-ranked clusters included
    "Build with real content throughout, never lorem" and "Choose neutrals, don't default to them" —
    text from a design-guidance doc, not anybody's stated view. Recurrence does not help: pasted docs
    are pasted repeatedly, so frequency actively promotes them.

    Writing those in as "Chris's rules" would be laundering — the store would then cite quoted
    material back as established preference, with a transcript citation making it look verified. That
    is the same failure the staleness verdict exists to prevent, so the claim text says UNREVIEWED
    and the kind is `inferred`: nothing here reads as confirmed until a human confirms it.

    The judgment call ("is this his voice or quoted material?") is what stage 2's model pass is for,
    on the few hundred candidates rather than the whole corpus."""
    import agent_memory as M
    n = 0
    for c in clusters:
        if c["count"] < min_count:
            continue
        sample = c["samples"][0] if c["samples"] else ""
        # Subject is a short stable slug so the fact is addressable and re-mining supersedes rather
        # than duplicating. The claim text must stay FIXED across re-mines too — remember() upserts
        # on (subject, claim), so a claim that embeds the count/family (both of which change as more
        # transcripts accrue) would defeat that key and insert a fresh row every re-mine instead of
        # updating the existing one. The mutable shape (count, families) goes in `value`, which is
        # exactly the column the upsert is meant to refresh.
        slug = "candidate:" + hashlib.sha256(c["key"].encode()).hexdigest()[:10]
        fams = ",".join(sorted(c["families"]))
        M.remember(slug,
                   "UNREVIEWED candidate rule — confirm or reject before relying on it",
                   "%s  [%s, seen %dx]" % (sample, fams, c["count"]),
                   kind=M.INFERRED,
                   evidence_cmd="transcripts: " + "; ".join(c["where"][:3]),
                   db=db)
        n += 1
        if limit and n >= limit:
            break
    return n


def list_candidates(db=None):
    """All still-UNREVIEWED candidates, ordered by subject — a stable order across calls (as long as
    nothing is confirmed/rejected in between), which is what lets `--review`'s printed index line up
    with a later `--confirm`/`--reject` in the same sitting."""
    import agent_memory as M
    c = M._conn(db)
    rows = c.execute("""SELECT subject, claim, value, evidence_cmd FROM facts
                        WHERE subject LIKE 'candidate:%' AND claim LIKE 'UNREVIEWED%'
                        ORDER BY subject""").fetchall()
    return [dict(r) for r in rows]


def confirm_candidate(subject, db=None):
    """Promote one candidate from mined guess to human-confirmed. Kind stays `inferred` — it's still
    text mined from prose, not derived from code — but the subject drops the `candidate:` prefix and
    the claim drops UNREVIEWED, which is the only signal recall()/answer() have that a human actually
    looked at this one. The old candidate row is removed so a stale duplicate can never out-rank the
    confirmed fact on a shared query. Returns False if the subject wasn't a live candidate (already
    reviewed, or never existed) rather than silently no-op'ing."""
    import agent_memory as M
    c = M._conn(db)
    row = c.execute("SELECT * FROM facts WHERE subject=? AND claim LIKE 'UNREVIEWED%'",
                     (subject,)).fetchone()
    if not row:
        return False
    M.remember("rule:" + subject.split(":", 1)[1],
               "CONFIRMED — stated by the operator, kept on review",
               row["value"], kind=M.INFERRED, evidence_cmd=row["evidence_cmd"], db=db)
    M.forget(subject, db=db)
    return True


def reject_candidate(subject, db=None):
    """Discard one candidate outright — pasted material, a one-off, or simply wrong. Returns False if
    there was nothing to remove, so a caller can tell a real reject from a no-op."""
    import agent_memory as M
    return M.forget(subject, db=db) > 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mine stated rules from local Claude transcripts.")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--write", action="store_true", help="store recurring rules as inferred facts")
    ap.add_argument("--min", type=int, default=3, help="min recurrence to store (default 3)")
    ap.add_argument("--min-score", type=int, default=4)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--files", type=int, default=None, help="limit files (for a fast look)")
    ap.add_argument("--review", action="store_true", help="list stored UNREVIEWED candidates")
    ap.add_argument("--confirm", help="comma-separated --review indices to confirm")
    ap.add_argument("--reject", help="comma-separated --review indices to reject")
    a = ap.parse_args(argv)

    if a.review or a.confirm or a.reject:
        cands = list_candidates()
        if a.review:
            if not cands:
                print("no unreviewed candidates")
            for i, c in enumerate(cands, 1):
                print("  [%2d] %s" % (i, re.sub(r"\s+", " ", c["value"]).strip()[:160]))
        for flag, fn, verb in ((a.confirm, confirm_candidate, "confirmed"),
                               (a.reject, reject_candidate, "rejected")):
            if not flag:
                continue
            for tok in flag.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                i = int(tok)
                if not (1 <= i <= len(cands)):
                    print("  [%2d] out of range (1-%d)" % (i, len(cands)))
                    continue
                ok = fn(cands[i - 1]["subject"])
                print("  [%2d] %s -> %s" % (i, verb, "ok" if ok else "already gone"))
        return 0

    res = scan(min_score=a.min_score, limit_files=a.files)
    cl = res["clusters"]
    print("scanned %d transcripts / %d operator turns -> %d candidate clusters"
          % (res["files"], res["turns_scanned"], len(cl)))
    rec = [c for c in cl if c["count"] >= a.min]
    print("%d clusters recur >=%dx (the load-bearing ones)\n" % (len(rec), a.min))
    for c in cl[:a.top]:
        fam = ",".join("%s:%d" % (k, v) for k, v in sorted(c["families"].items()))
        print("  %3dx  rank %6.0f  [%s]" % (c["count"], c["rank"], fam))
        print("        %s" % re.sub(r"\s+", " ", c["samples"][0])[:150])
    if a.write:
        n = write_facts(cl, min_count=a.min)
        print("\nstored %d recurring rules as inferred facts" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
