#!/usr/bin/env python3
"""agent_import_chat.py — import the claude.ai data export and mine it with the SAME pipeline.

WHY THIS FILE EXISTS:
`~/.claude/projects` holds Claude CODE sessions only — 229 transcripts, all of them terminal work.
The claude.ai conversations (including the long stretch where the HTML surfaces were designed) live
server-side; the desktop app keeps only a 3.7MB evictable LevelDB cache, which is not a corpus. So
the biggest body of reasoning about this project is reachable exactly one way: the official data
export. This module consumes it.

It deliberately reuses agent_mine's scoring, sentence splitting and clustering rather than
reimplementing them. One definition of "this looks like a stated rule" means results from the two
corpora are directly comparable and rank against each other — two similar-but-different scorers would
silently produce two incompatible rankings, and you'd never know which one to trust.

THE SAME HONESTY CONSTRAINT CARRIES OVER: everything lands as an UNREVIEWED candidate, kind=inferred.
Measured on the Claude Code corpus, the top lexical hits included pasted design-doc text
("never lorem", "don't default to neutrals") that is not the operator's own view at all. A regex
cannot tell stating from quoting, so nothing here is written as established preference.

    python3 unifyd/agent_import_chat.py --path ~/Downloads/data-export.zip
    python3 unifyd/agent_import_chat.py --path ~/Downloads/conversations.json --write --min 3
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_mine as MINE


def _load(path):
    """Return the parsed conversations list from a zip or a bare json.

    The export ships as a zip containing conversations.json (plus projects.json / users.json). Taking
    either shape means no manual unzip step, which is one less place for a stale half-extracted copy
    to sit around."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise SystemExit("no such file: %s" % path)
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith("conversations.json")]
            if not names:
                raise SystemExit("zip has no conversations.json (found: %s)"
                                 % ", ".join(z.namelist()[:8]))
            with z.open(names[0]) as fh:
                return json.load(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
    with open(path, encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def _text_of(msg):
    """Pull the human-readable text out of a message, whatever shape the export uses.

    Written defensively on purpose: the export schema is not a stable public contract, and `text` has
    historically been either a top-level string or a list of content blocks. Reading only one shape
    would yield a silent zero-result import — which looks identical to "you have no history"."""
    t = msg.get("text")
    if isinstance(t, str) and t.strip():
        return t
    out = []
    for key in ("content", "blocks"):
        blocks = msg.get(key)
        if isinstance(blocks, list):
            for b in blocks:
                if isinstance(b, dict):
                    if isinstance(b.get("text"), str):
                        out.append(b["text"])
                    elif b.get("type") == "text" and isinstance(b.get("value"), str):
                        out.append(b["value"])
                elif isinstance(b, str):
                    out.append(b)
    return "\n".join(x for x in out if x).strip()


def _is_human(msg):
    s = (msg.get("sender") or msg.get("role") or "").lower()
    return s in ("human", "user")


# ── TOPICAL SCOPE — required, for two independent reasons ────────────────────────────────────────
# 1. RELEVANCE. The export is the operator's entire Claude history, not a project archive. Run
#    unscoped over the real 310 conversations, the top-ranked "rules" were: learning R for
#    interviews, "teach me in a way I can absorb" (an AI/ML tutoring thread), a children's storybook
#    app, resume tailoring, and pasted EEO boilerplate from job listings. All genuine preferences —
#    none of them about this project. The brief was hoodie and bev-alc.
# 2. PRIVACY. The same corpus contains medical questions, betting analysis and family matters. A
#    queryable fact store that ingests those is a liability, not a feature: it would surface personal
#    content in answers to unrelated engineering questions, and it never should have held it.
#    Scoping is therefore a hard gate, not a ranking tweak — excluded conversations are never read.
_IN_SCOPE = re.compile(
    r"hoodie|unifyd|bev.?alc|beverage alcohol|spirits?|wine|beer|liquor|distribut|"
    r"scrape|scraper|crawl|warehouse|parquet|duckdb|snowflake|mdm|master data|"
    r"sku|upc|gtin|planogram|depletion|velocity|on.?premise|off.?premise|"
    r"retail|store.?level|price|inventory|assortment|ttb|cola|vip|sipsource|"
    r"suite|dashboard|locator|prism|relations|collect|fly\.io|flyctl", re.I)

# Domains to exclude outright even if a scope word appears in passing (a betting thread mentioning
# "spirits", a medical thread mentioning "price"). Checked against the conversation TITLE, where the
# subject is stated most plainly.
_OUT_OF_SCOPE = re.compile(
    r"night sweats|medication|withdrawal|symptom|diagnos|therapy|anxiety|depress|"
    r"resume|cover letter|interview|job (?:role|description|post)|salary|recruit|"
    r"betting|parlay|vs\.? cape verde|trivia|storybook|children'?s|dbt and cbt|"
    r"custody|divorce|attorney|negotiating authority", re.I)


def in_scope(conv):
    """Is this conversation about the project? Title first, then a bounded content sample.

    Returns (bool, reason) so an excluded conversation can be reported as a count rather than
    silently vanishing — a filter you can't audit is a filter that quietly loses data."""
    title = (conv.get("name") or conv.get("title") or "")
    if _OUT_OF_SCOPE.search(title):
        return False, "excluded-topic"
    if _IN_SCOPE.search(title):
        return True, "title"
    # No signal in the title — sample the first few human turns rather than the whole thread, so one
    # stray mention deep in a long personal conversation can't pull it into scope.
    msgs = conv.get("chat_messages") or conv.get("messages") or []
    sample = " ".join(_text_of(m) for m in msgs[:6] if isinstance(m, dict))[:4000]
    if _OUT_OF_SCOPE.search(sample):
        return False, "excluded-topic"
    # TWO DISTINCT TERMS, not one. A single incidental keyword let clearly personal threads through —
    # "Preach's unwavering loyalty" and "How Toastmasters works" both qualified off one passing word
    # like "price" or "wine", and their mined clauses were autobiography. Requiring two different
    # domain terms is the cheapest signal that a conversation is actually ABOUT the work rather than
    # merely mentioning it. Titles still pass on one, because a title word is a statement of subject.
    found = {m.group(0).lower() for m in _IN_SCOPE.finditer(sample)}
    if len(found) >= 2:
        return True, "content"
    return False, "weak-signal" if found else "no-signal"


def scan_export(path, min_score=4):
    """Mine the export with agent_mine's scorer AND clusterer. Same cluster shape as MINE.scan()."""
    convos = _load(path)
    if isinstance(convos, dict):                    # tolerate {"conversations": [...]}
        convos = convos.get("conversations") or convos.get("data") or []
    items = []
    n_convo = n_turn = n_skipped = 0
    skipped_reasons = defaultdict(int)
    for conv in convos or []:
        if not isinstance(conv, dict):
            continue
        # SCOPE GATE FIRST — this is a personal account. Out-of-scope conversations are never read,
        # not merely down-ranked, so nothing personal can reach the store even as a low-scoring row.
        ok, why = in_scope(conv)
        if not ok:
            n_skipped += 1
            skipped_reasons[why] += 1
            continue
        n_convo += 1
        title = (conv.get("name") or conv.get("title") or "")[:70]
        msgs = conv.get("chat_messages") or conv.get("messages") or []
        for mi, msg in enumerate(msgs):
            if not isinstance(msg, dict) or not _is_human(msg):
                continue
            turn = _text_of(msg)
            if not turn or len(turn) < 12:
                continue
            if MINE._NOISE.search(turn) or MINE._PASTED_BRIEF.search(turn):
                continue
            n_turn += 1
            for sent in MINE._sentences(turn):
                sc, hits = MINE._score(sent)
                if sc < min_score:
                    continue
                items.append((sent, sc, hits,
                              "chat:%s#%d" % (title or conv.get("uuid", "?")[:8], mi)))
    return dict(conversations=n_convo, turns_scanned=n_turn, skipped=n_skipped,
                skipped_reasons=dict(skipped_reasons), clusters=MINE.cluster(items))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import + mine the claude.ai data export.")
    ap.add_argument("--path", required=True, help="data-export.zip or conversations.json")
    ap.add_argument("--write", action="store_true", help="store recurring clauses as CANDIDATES")
    ap.add_argument("--min", type=int, default=3)
    ap.add_argument("--min-score", type=int, default=4)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args(argv)

    res = scan_export(a.path, min_score=a.min_score)
    cl = res["clusters"]
    print("in scope: %d conversations / %d human turns -> %d candidate clusters"
          % (res["conversations"], res["turns_scanned"], len(cl)))
    print("skipped %d conversations as out of scope (%s) — personal account, never read"
          % (res.get("skipped", 0),
             ", ".join("%s=%d" % kv for kv in sorted((res.get("skipped_reasons") or {}).items()))))
    if not res["turns_scanned"]:
        print("\nNo human turns parsed. That usually means the export schema moved — check one "
              "message's keys and extend _text_of/_is_human rather than assuming empty history.")
        return 1
    rec = [c for c in cl if c["count"] >= a.min]
    print("%d clusters recur >=%dx\n" % (len(rec), a.min))
    for c in cl[:a.top]:
        fam = ",".join("%s:%d" % (k, v) for k, v in sorted(c["families"].items()))
        print("  %3dx  rank %6.0f  [%s]" % (c["count"], c["rank"], fam))
        print("        %s" % re.sub(r"\s+", " ", c["samples"][0])[:150])
        print("        seen: %s" % ", ".join(c["where"][:2]))
    if a.write:
        n = MINE.write_facts(cl, min_count=a.min)
        print("\nstored %d clusters as UNREVIEWED candidates (confirm before relying on them)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
