#!/usr/bin/env python3
"""agent_tickets.py — a real ticket, not a chat transcript standing in for one.

WHY THIS EXISTS:
The Cockpit answers questions and dispatches work, but nothing about a chat commits anyone to a
specific, gradeable definition of "done" — a conversation drifts, a spec doesn't. A ticket is the
Cockpit's chat turned into something a human edits once and everything downstream is held to: the
author (the engineer stage) builds against it, the checkers (QA, reviewer) grade against the SAME
text, not a fresh guess at what "done" means. See `agent_roles.py`'s ROLES[PM] — its whole job is
producing "a gradeable definition of done"; a ticket's edited acceptance criteria simply replaces
that stage's output, because a human already did that job.

TICKETS ARE REAL MARKDOWN FILES, NOT ROWS LOCKED IN A DATABASE:
The body lives at agent_state/cockpit/tickets/<id>.md — one file, appended to as work happens (a
crew stage's report, a docs update) exactly the way a human would keep a running doc. That means:
  - the live-view app (apps/md-viewer.html) just polls the file's content, nothing ticket-specific
    to build there
  - "embed test reports as the process goes" is a file append, not a new mechanism
  - Chris can open the same file in ANY editor (including the native Mac markdown viewer built the
    same day this module was) — the ticket isn't locked behind one UI
The JSON index (tickets.json) is only the queryable metadata: status, flags, timestamps. Mirrors
`server.py`'s ORDERS/orders.json — the closest existing precedent for a lifecycle object with a
JSON store.

FORWARD-ONLY STATUS, same shape as server.py's `_ORDER_FLOW`/`order_status_ep`:
draft -> accepted -> in_progress -> testing -> done, with `blocked`/`cancelled` as sinks reachable
from any non-terminal status (never from `done`, which is closed the same way `delivered` closes an
order). The point, same as orders: a forward-only ladder keeps status_history an honest audit trail
instead of something that can be quietly walked back.

    python3 unifyd/agent_tickets.py --create --title "..." --body "..."
    python3 unifyd/agent_tickets.py --list
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "agent_state", "cockpit")
TICKETS_DIR = os.path.join(STATE, "tickets")
INDEX = os.path.join(STATE, "tickets.json")

TICKET_FLOW = ["draft", "accepted", "in_progress", "testing", "done"]
SINKS = ("blocked", "cancelled")


def _now_ms():
    return int(time.time() * 1000)


def _load_index():
    try:
        with open(INDEX, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def _save_index(rows):
    """Atomic write — same tmp+os.replace pattern as agent_exec._save_thread, for the same reason:
    a half-written index loses every ticket's status, not just the one being updated."""
    os.makedirs(STATE, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
    os.replace(tmp, INDEX)


def _rows(db):
    """The list every read/mutate call in this module operates on: the real on-disk index, or the
    caller's own list when `db` is given (tests, or a caller batching several ticket ops in one
    transaction-ish pass without hitting disk between each). Always load ONCE per call and pass the
    SAME list through get/mutate/persist — reloading independently at each step (as an earlier draft
    of this module did) means a record fetched by one call and a record mutated by the next are two
    different dicts, and the mutation silently never reaches disk."""
    return _load_index() if db is None else db


def _persist(rows, db):
    if db is None:
        _save_index(rows)


def _body_path(tid):
    return os.path.join(TICKETS_DIR, "%s.md" % tid)


def read_body(tid):
    try:
        with open(_body_path(tid), encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def write_body(tid, body_md):
    """Atomic write for the same reason the index is: a live viewer polling this file must never see
    a half-written document, and a crash mid-write must never look like a truncated ticket."""
    os.makedirs(TICKETS_DIR, exist_ok=True)
    p = _body_path(tid)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(body_md)
    os.replace(tmp, p)


def get(tid, db=None):
    rows = _rows(db)
    return next((r for r in rows if r["id"] == tid), None)


def list_tickets(status=None, db=None):
    rows = _rows(db)
    out = [r for r in rows if not status or r.get("status") == status]
    return sorted(out, key=lambda r: -r.get("updated", 0))


_HEADING = re.compile(r"^#{1,6}\s*(.*)$")


def _clean_title_line(line):
    """Strip markdown formatting from one line so it reads as a plain title, not a fragment of
    markup: leading heading hashes/bullets/numbering, **bold**/`code` markers.

    Bullet/number stripping requires the trailing whitespace ("- ", "1. ") rather than a bare
    character class — a bare `[-*>\\d.\\s]+` also eats the leading `**` of a bolded first line
    (no space after it), leaving the bold-strip regex below with only a trailing `**` and nothing
    to pair it with, so the closing asterisks never got removed. Found via a test that used a
    genuinely bold first line, not a hypothetical."""
    line = _HEADING.sub(r"\1", line).strip()
    line = re.sub(r"^\d+\.\s+", "", line)
    line = re.sub(r"^[-*+>]\s+", "", line)
    line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    # Observed on a second real draft: no "## Outcome" heading at all, just an inline "**Outcome:**"
    # label leading the paragraph — the bold-strip above unwraps the ** but leaves the literal label
    # text sitting in front of the actual sentence.
    line = re.sub(r"^(?:the\s+)?outcome\s*:\s*", "", line, flags=re.I)
    return line.strip()


def derive_title(draft, fallback):
    """Pull a real title out of a PM draft, robust to the model wrapping its answer in its OWN
    markdown headings rather than leading with plain prose. Observed live: asked for "the outcome
    in one sentence first", a real draft instead opened with a "## Acceptance criteria" heading and
    put the outcome sentence under a LATER "## Outcome" heading — a naive first-line grab returned
    the word "Outcome" as the title. Two-pass fix: prefer the paragraph under a heading literally
    named "outcome" wherever it landed structurally; otherwise fall back to the first real line of
    prose, skipping headings and blank lines rather than assuming line 1 is ever the title."""
    lines = [l.strip() for l in (draft or "").splitlines()]
    for i, l in enumerate(lines):
        m = _HEADING.match(l)
        if m and m.group(1).strip().lower().startswith("outcome"):
            for after in lines[i + 1:]:
                if after and not _HEADING.match(after):
                    return _clean_title_line(after)[:120] or fallback
    for l in lines:
        if l and not _HEADING.match(l):
            return _clean_title_line(l)[:120] or fallback
    return fallback


def create(title, body_md, source_chat_id=None, requires_docs=False, db=None):
    """New ticket, status `draft`. `db` (a list) is for tests that want isolation from the real index
    file — production callers omit it and the real STATE/tickets.json is used, same convention
    agent_memory.py's `db=None` parameter uses for the same reason."""
    tid = "ticket:" + hashlib.sha256(("%s%r" % (time.time(), title)).encode()).hexdigest()[:12]
    now = _now_ms()
    rec = dict(id=tid, title=title, status="draft",
               status_history=[dict(status="draft", at=now)],
               source_chat_id=source_chat_id, requires_docs=bool(requires_docs), docs_done=False,
               created=now, updated=now)
    write_body(tid, body_md)
    rows = _rows(db)
    rows.append(rec)
    _persist(rows, db)
    return rec


def append_section(tid, heading, text, db=None):
    """The mechanism behind "embed test reports as the process goes": one more section on the SAME
    file, never a new one. Returns False (does nothing) for an unknown ticket rather than creating a
    file with no index entry — an orphaned .md file is worse than a clear failure."""
    rows = _rows(db)
    rec = next((r for r in rows if r["id"] == tid), None)
    if not rec:
        return False
    body = read_body(tid) or ""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    body += "\n\n## %s — %s\n%s\n" % (heading, stamp, text)
    write_body(tid, body)
    rec["updated"] = _now_ms()
    _persist(rows, db)
    return True


def edit_body(tid, body_md, db=None):
    """A human (or a UI) REPLACING the body outright — editing the acceptance criteria — as opposed
    to `append_section`, which only ever adds. Bumps `updated` so a live viewer's poll notices,
    same as every other mutator here."""
    rows = _rows(db)
    rec = next((r for r in rows if r["id"] == tid), None)
    if not rec:
        return False
    write_body(tid, body_md)
    rec["updated"] = _now_ms()
    _persist(rows, db)
    return True


def advance_status(tid, new_status, db=None):
    """Forward-only, byte-for-byte the same ladder logic as server.py's order_status_ep: `new_status`
    must be a known flow step or a sink; a closed ticket (`done`) refuses anything but itself;
    stepping backward within the flow is rejected. Returns {ok, status, status_history} either way —
    the caller decides what an ok=False means (an HTTP 409, a CLI error), this module doesn't."""
    rows = _rows(db)
    rec = next((r for r in rows if r["id"] == tid), None)
    if not rec:
        return dict(ok=False, error="not found")
    new_status = (new_status or "").strip().lower()
    if new_status not in TICKET_FLOW and new_status not in SINKS:
        return dict(ok=False, error="status must be one of: %s, %s"
                    % (", ".join(TICKET_FLOW), ", ".join(SINKS)))
    cur = rec.get("status", "draft")
    if cur == "done" and new_status != "done":
        return dict(ok=False, error="a done ticket is closed")
    if new_status not in SINKS and cur in TICKET_FLOW and new_status in TICKET_FLOW and \
            TICKET_FLOW.index(new_status) < TICKET_FLOW.index(cur):
        return dict(ok=False, error="can't move a ticket backward (%s -> %s)" % (cur, new_status))
    rec["status"] = new_status
    rec.setdefault("status_history", []).append(dict(status=new_status, at=_now_ms()))
    rec["updated"] = _now_ms()
    _persist(rows, db)
    return dict(ok=True, status=new_status, status_history=rec["status_history"])


def set_requires_docs(tid, val, db=None):
    rows = _rows(db)
    rec = next((r for r in rows if r["id"] == tid), None)
    if not rec:
        return False
    rec["requires_docs"] = bool(val)
    rec["updated"] = _now_ms()
    _persist(rows, db)
    return True


def mark_docs_done(tid, db=None):
    rows = _rows(db)
    rec = next((r for r in rows if r["id"] == tid), None)
    if not rec:
        return False
    rec["docs_done"] = True
    rec["updated"] = _now_ms()
    _persist(rows, db)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ticket lifecycle — create, list, advance.")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--title", default=None)
    ap.add_argument("--body", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default=None, help="with --list: filter; with --advance: the target")
    ap.add_argument("--advance", default=None, help="ticket id to advance to --status")
    a = ap.parse_args(argv)

    if a.create:
        if not a.title:
            ap.error("--title is required with --create")
        rec = create(a.title, a.body)
        print("created %s (%s)" % (rec["id"], rec["status"]))
        return 0
    if a.advance:
        res = advance_status(a.advance, a.status)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1
    if a.list:
        for t in list_tickets(status=a.status):
            print("  %-9s  %-24s  %s" % (t["status"], t["id"], t["title"]))
        return 0
    ap.error("nothing to do — use --create, --list, or --advance")


if __name__ == "__main__":
    sys.exit(main())
