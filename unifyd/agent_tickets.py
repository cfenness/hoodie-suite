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

DURABLE STORAGE, ONE JSON FILE PER TICKET (v2):
Ticket state lives at `unifyd/cockpit_tickets/tickets/<id>.json` — inside the repo and TRACKED by
git, unlike the original `unifyd/agent_state/cockpit/` location, which is `.gitignore`d (the same
bucket as scrape caches and run logs). A ticket is a durable artifact, not runtime state: it needs
to survive any single machine and sync the same way any other file in this repo does — commit it,
push it, `git log` it. No bespoke sync mechanism, no auto-commit-per-mutation; it's a normal file.

ONE FILE PER TICKET, DELIBERATELY NOT ONE SHARED INDEX:
A single `tickets.json` array (or an index file alongside per-ticket bodies) means two chats editing
DIFFERENT tickets collide on the SAME file — exactly the shared-mutable-file contention
`agent_chats.py`'s whole anti-clobber system exists to guard against (57 worktrees, concurrent
chats, is the number that module was built for). One file per ticket means that collision class
cannot happen: `list_tickets()` globs the directory and reads each file, which is plenty fast at the
scale of dozens-to-hundreds of tickets a single operator (or a small team) accumulates.

STRUCTURED FIELDS, NOT A DUPLICATE MARKDOWN FILE:
The original version kept a JSON index row PLUS a separate raw-markdown body file per ticket — two
files that could desync, and a markdown file that was pure storage waste (the same information the
JSON already needed to hold structured for editing anyway). `render_markdown()` renders the ticket's
`description_md` + its `activity[]` log into the same markdown shape apps/md-viewer.html and the
`/raw` endpoint already expect, ON DEMAND — nothing is stored twice. `read_body()` keeps its old
name/contract (returns a markdown string) purely so server.py's existing call sites don't change.

JIRA-PARITY FIELDS + EPICS:
`issue_type` / `priority` / `labels` / `story_points` / `epic_id` put this on the same footing as a
real Jira ticket, and `epics/<id>.json` gives tickets somewhere to roll up into. `jira_csv()` /
`jira_csv_rows()` export in Jira's standard bulk-CSV-importer column shape — the bridge until EPIC-4
(real Jira sync) exists; the same shape a live sync would eventually push.

PR / JIRA LINKAGE FIELDS:
`pr` and `jira` are plain data on the ticket record (`set_pr`/`set_jira`) — this module has no
network/git access of its own (stdlib-only, unit-tested standalone); server.py's routes are
responsible for populating them (manual PATCH, or a best-effort `gh pr view` lookup).

FORWARD-ONLY STATUS, same shape as server.py's `_ORDER_FLOW`/`order_status_ep`:
draft -> accepted -> in_progress -> testing -> done, with `blocked`/`cancelled` as sinks reachable
from any non-terminal status (never from `done`, which is closed the same way `delivered` closes an
order). The point, same as orders: a forward-only ladder keeps status_history an honest audit trail
instead of something that can be quietly walked back.

    python3 unifyd/agent_tickets.py --create --title "..." --body "..."
    python3 unifyd/agent_tickets.py --list
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STATE = os.path.join(HERE, "cockpit_tickets")
TICKETS_DIR = os.path.join(STATE, "tickets")
EPICS_DIR = os.path.join(STATE, "epics")

TICKET_FLOW = ["draft", "accepted", "in_progress", "testing", "done"]
SINKS = ("blocked", "cancelled")

ISSUE_TYPES = ("story", "bug", "task", "chore")
PRIORITIES = ("lowest", "low", "medium", "high", "highest")
EPIC_STATUSES = ("open", "in_progress", "done", "cancelled")

_JIRA_ISSUE_TYPE = dict(story="Story", bug="Bug", task="Task", chore="Task")
_JIRA_PRIORITY = dict(lowest="Lowest", low="Low", medium="Medium", high="High", highest="Highest")


def _now_ms():
    return int(time.time() * 1000)


def _atomic_write_json(path, obj):
    """Same tmp+os.replace pattern this module has always used for writes: a half-written file must
    never be what a live-refreshing viewer (or a concurrent reader) sees."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
    os.replace(tmp, path)


# ── tickets: file-per-record storage ────────────────────────────────────────────────────────────
def _tpath(tid):
    return os.path.join(TICKETS_DIR, "%s.json" % tid)


def _save(tid, rec, db):
    """`db`, when given, is an in-memory dict {id: record} substituting the filesystem entirely —
    for tests that want isolation from the real on-disk store without touching disk at all. `db=None`
    (the production default) writes the real tracked file."""
    if db is not None:
        db[tid] = rec
        return
    _atomic_write_json(_tpath(tid), rec)


def get(tid, db=None):
    if db is not None:
        return db.get(tid)
    try:
        with open(_tpath(tid), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def list_tickets(status=None, epic_id=None, db=None):
    if db is not None:
        rows = list(db.values())
    else:
        rows = []
        if os.path.isdir(TICKETS_DIR):
            for fn in sorted(os.listdir(TICKETS_DIR)):
                if not fn.endswith(".json") or fn.endswith(".tmp"):
                    continue
                rec = get(fn[:-5])
                if rec:
                    rows.append(rec)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if epic_id:
        rows = [r for r in rows if r.get("epic_id") == epic_id]
    return sorted(rows, key=lambda r: -r.get("updated", 0))


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


def create(title, description_md, source_chat_id=None, requires_docs=False,
           issue_type="task", priority="medium", labels=None, story_points=None,
           epic_id=None, db=None):
    """New ticket, status `draft`. `db` (a dict) is for tests that want isolation from the real
    tracked directory — production callers omit it and the real TICKETS_DIR is used."""
    if issue_type not in ISSUE_TYPES:
        issue_type = "task"
    if priority not in PRIORITIES:
        priority = "medium"
    tid = "ticket:" + hashlib.sha256(("%s%r" % (time.time(), title)).encode()).hexdigest()[:12]
    now = _now_ms()
    rec = dict(
        id=tid, title=title, status="draft",
        status_history=[dict(status="draft", at=now)],
        issue_type=issue_type, priority=priority, labels=list(labels or []),
        story_points=story_points, epic_id=epic_id,
        description_md=description_md, activity=[],
        source_chat_id=source_chat_id, requires_docs=bool(requires_docs), docs_done=False,
        pr=dict(number=None, url=None, branch=None, state=None, repo=None),
        jira=dict(key=None, url=None, last_synced_at=None),
        cost=dict(input_tokens=0, output_tokens=0, by_stage={}),
        created=now, updated=now,
    )
    _save(tid, rec, db)
    return rec


def render_markdown(rec):
    """Render a ticket record to the same markdown shape the original version stored on disk —
    description first, then each activity entry as a `## <role> — <timestamp>` block in the order
    it happened. Consumers (apps/md-viewer.html via the /raw endpoint) see no difference; only the
    storage underneath changed."""
    if not rec:
        return ""
    body = rec.get("description_md") or ""
    for a in rec.get("activity") or []:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime((a.get("ts") or 0) / 1000.0))
        body += "\n\n## %s — %s\n%s\n" % (a.get("role") or a.get("kind") or "Update", stamp,
                                          a.get("text_md") or "")
    return body


def read_body(tid, db=None):
    """Kept for compatibility with every existing caller (server.py's /raw route, the ticket-run
    route's `criteria = T.read_body(tid)`) — same contract, a markdown string or None, now rendered
    from the structured record instead of read off a second file."""
    rec = get(tid, db=db)
    return render_markdown(rec) if rec is not None else None


def add_activity(tid, kind, role_label, text, usage=None, db=None):
    """Append one structured entry to the ticket's activity log — the mechanism behind "embed test
    reports as the process goes." Returns False (does nothing) for an unknown ticket, same as before.
    `usage` (an {input_tokens, output_tokens} dict off a dispatch record) rolls into the ticket's
    running cost total when given — nearly free, since every dispatch already carries it."""
    rec = get(tid, db=db)
    if not rec:
        return False
    rec.setdefault("activity", []).append(dict(
        ts=_now_ms(), kind=kind, role=role_label, text_md=text, usage=usage or {}))
    if usage:
        cost = rec.setdefault("cost", dict(input_tokens=0, output_tokens=0, by_stage={}))
        cost["input_tokens"] = cost.get("input_tokens", 0) + int(usage.get("input_tokens") or 0)
        cost["output_tokens"] = cost.get("output_tokens", 0) + int(usage.get("output_tokens") or 0)
        cost.setdefault("by_stage", {})[kind] = usage
    rec["updated"] = _now_ms()
    _save(tid, rec, db)
    return True


def append_section(tid, heading, text, db=None):
    """Same call site every existing caller already uses (`T.append_section(tid, "Engineer report",
    ...)`) — now appends a structured activity entry instead of literal markdown, with `kind` derived
    from the heading text."""
    kind = re.sub(r"\s+", "_", (heading or "update").strip().lower())
    return add_activity(tid, kind, heading, text, db=db)


def edit_body(tid, body_md, db=None):
    """A human (or a UI) REPLACING the ticket's description outright — editing the acceptance
    criteria — as opposed to `append_section`/`add_activity`, which only ever adds. Bumps `updated`
    so a live viewer's poll notices."""
    rec = get(tid, db=db)
    if not rec:
        return False
    rec["description_md"] = body_md
    rec["updated"] = _now_ms()
    _save(tid, rec, db)
    return True


def advance_status(tid, new_status, db=None):
    """Forward-only, byte-for-byte the same ladder logic as server.py's order_status_ep: `new_status`
    must be a known flow step or a sink; a closed ticket (`done`) refuses anything but itself;
    stepping backward within the flow is rejected. Returns {ok, status, status_history} either way —
    the caller decides what an ok=False means (an HTTP 409, a CLI error), this module doesn't."""
    rec = get(tid, db=db)
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
    _save(tid, rec, db)
    return dict(ok=True, status=new_status, status_history=rec["status_history"])


def set_requires_docs(tid, val, db=None):
    rec = get(tid, db=db)
    if not rec:
        return False
    rec["requires_docs"] = bool(val)
    rec["updated"] = _now_ms()
    _save(tid, rec, db)
    return True


def mark_docs_done(tid, db=None):
    rec = get(tid, db=db)
    if not rec:
        return False
    rec["docs_done"] = True
    rec["updated"] = _now_ms()
    _save(tid, rec, db)
    return True


def set_fields(tid, epic_id=None, issue_type=None, priority=None, labels=None,
               story_points=None, db=None):
    """Patch the Jira-parity fields. Each arg is applied only when not None (labels: an empty list
    IS a meaningful value — "clear the labels" — so it's checked against None, not truthiness)."""
    rec = get(tid, db=db)
    if not rec:
        return False
    if epic_id is not None:
        rec["epic_id"] = epic_id or None
    if issue_type is not None and issue_type in ISSUE_TYPES:
        rec["issue_type"] = issue_type
    if priority is not None and priority in PRIORITIES:
        rec["priority"] = priority
    if labels is not None:
        rec["labels"] = list(labels)
    if story_points is not None:
        rec["story_points"] = story_points
    rec["updated"] = _now_ms()
    _save(tid, rec, db)
    return True


def set_pr(tid, number=None, url=None, branch=None, state=None, repo=None, db=None):
    rec = get(tid, db=db)
    if not rec:
        return False
    pr = rec.setdefault("pr", dict(number=None, url=None, branch=None, state=None, repo=None))
    for k, v in (("number", number), ("url", url), ("branch", branch),
                ("state", state), ("repo", repo)):
        if v is not None:
            pr[k] = v
    rec["updated"] = _now_ms()
    _save(tid, rec, db)
    return True


def set_jira(tid, key=None, url=None, db=None):
    rec = get(tid, db=db)
    if not rec:
        return False
    jira = rec.setdefault("jira", dict(key=None, url=None, last_synced_at=None))
    if key is not None:
        jira["key"] = key
    if url is not None:
        jira["url"] = url
    jira["last_synced_at"] = _now_ms()
    rec["updated"] = _now_ms()
    _save(tid, rec, db)
    return True


# ── epics ────────────────────────────────────────────────────────────────────────────────────────
def _epath(eid):
    return os.path.join(EPICS_DIR, "%s.json" % eid)


def _save_epic(eid, rec, db):
    if db is not None:
        db[eid] = rec
        return
    _atomic_write_json(_epath(eid), rec)


def get_epic(eid, db=None):
    if db is not None:
        return db.get(eid)
    try:
        with open(_epath(eid), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def list_epics(db=None):
    if db is not None:
        rows = list(db.values())
    else:
        rows = []
        if os.path.isdir(EPICS_DIR):
            for fn in sorted(os.listdir(EPICS_DIR)):
                if not fn.endswith(".json") or fn.endswith(".tmp"):
                    continue
                rec = get_epic(fn[:-5])
                if rec:
                    rows.append(rec)
    return sorted(rows, key=lambda r: -r.get("updated", 0))


def create_epic(title, description_md="", story_points_planned=None, db=None):
    eid = "epic:" + hashlib.sha256(("%s%r" % (time.time(), title)).encode()).hexdigest()[:12]
    now = _now_ms()
    rec = dict(id=eid, title=title, description_md=description_md, status="open",
               story_points_planned=story_points_planned, created=now, updated=now)
    _save_epic(eid, rec, db)
    return rec


def edit_epic(eid, title=None, description_md=None, status=None, story_points_planned=None, db=None):
    rec = get_epic(eid, db=db)
    if not rec:
        return False
    if title is not None:
        rec["title"] = title
    if description_md is not None:
        rec["description_md"] = description_md
    if status is not None and status in EPIC_STATUSES:
        rec["status"] = status
    if story_points_planned is not None:
        rec["story_points_planned"] = story_points_planned
    rec["updated"] = _now_ms()
    _save_epic(eid, rec, db)
    return True


def epic_rollup(eid, db=None, ticket_db=None):
    """Ticket counts by status for one epic — what the Epics sub-view renders per row."""
    tickets = list_tickets(epic_id=eid, db=ticket_db)
    counts = {}
    for t in tickets:
        counts[t.get("status", "draft")] = counts.get(t.get("status", "draft"), 0) + 1
    return dict(epic=get_epic(eid, db=db), ticket_count=len(tickets), counts=counts,
               story_points=sum(t.get("story_points") or 0 for t in tickets))


# ── Jira export — the bridge until EPIC-4's real sync exists ──────────────────────────────────────
def jira_csv_rows(tickets, epics_by_id=None):
    """Header row + one row per ticket, in Jira's standard bulk-CSV-importer column names. Pure data
    transform, no I/O — the same shape a live Jira sync (T-4.2) will eventually push, so this export
    and that future sync payload are meant to converge on one function."""
    epics_by_id = epics_by_id or {}
    rows = [["Summary", "Issue Type", "Priority", "Labels", "Epic Link", "Story Points",
             "Description", "Status"]]
    for t in tickets:
        epic = epics_by_id.get(t.get("epic_id")) or {}
        rows.append([
            t.get("title") or "",
            _JIRA_ISSUE_TYPE.get(t.get("issue_type"), "Task"),
            _JIRA_PRIORITY.get(t.get("priority"), "Medium"),
            " ".join(t.get("labels") or []),
            epic.get("title") or "",
            "" if t.get("story_points") is None else str(t["story_points"]),
            render_markdown(t),
            t.get("status") or "",
        ])
    return rows


def jira_csv(tickets, epics_by_id=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    for row in jira_csv_rows(tickets, epics_by_id):
        w.writerow(row)
    return buf.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ticket lifecycle — create, list, advance.")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--title", default=None)
    ap.add_argument("--body", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--status", default=None, help="with --list: filter; with --advance: the target")
    ap.add_argument("--epic-id", default=None, help="with --list: filter by epic")
    ap.add_argument("--advance", default=None, help="ticket id to advance to --status")
    ap.add_argument("--create-epic", default=None, help="epic title")
    ap.add_argument("--list-epics", action="store_true")
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
    if a.create_epic:
        rec = create_epic(a.create_epic)
        print("created %s" % rec["id"])
        return 0
    if a.list_epics:
        for e in list_epics():
            print("  %-11s  %-28s  %s" % (e["status"], e["id"], e["title"]))
        return 0
    if a.list:
        for t in list_tickets(status=a.status, epic_id=a.epic_id):
            print("  %-9s  %-24s  %s" % (t["status"], t["id"], t["title"]))
        return 0
    ap.error("nothing to do — use --create, --list, --advance, --create-epic, or --list-epics")


if __name__ == "__main__":
    sys.exit(main())
