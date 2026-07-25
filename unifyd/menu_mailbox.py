"""menu_mailbox.py — auto-ingest distributor menus that arrive by EMAIL (IMAP).

Distributors email their weekly menu as a spreadsheet attachment (Curaleaf NY is the reference). This
polls a mailbox, pulls the menu attachments off new messages, runs each through
`menu_ingest.parse_smart` (deterministic + Claude fallback), lands `distributor_menu_items`, and marks
the message seen — so the ordering catalog stays current with NO manual uploads. The heavy lifting
(parse + land) is the same path the manual upload uses; this only adds the mailbox front-door.

Creds are env-gated — nothing runs without them:
  MENU_IMAP_HOST, MENU_IMAP_PORT(=993), MENU_IMAP_USER, MENU_IMAP_PASS,
  MENU_IMAP_FOLDER(=INBOX), MENU_IMAP_SENDERS(optional CSV allowlist of from-addresses/domains)

    python menu_mailbox.py            # poll unseen → parse + land, mark seen
    python menu_mailbox.py --dry-run  # parse only; don't land, don't mark seen
"""
import argparse
import email
import email.utils
import imaplib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import menu_ingest

_EXT = (".xlsx", ".xlsm", ".csv")


def _cfg():
    host = os.environ.get("MENU_IMAP_HOST", "").strip()
    if not host:
        return None
    return {"host": host, "port": int(os.environ.get("MENU_IMAP_PORT", "993") or 993),
            "user": os.environ.get("MENU_IMAP_USER", "").strip(), "pass": os.environ.get("MENU_IMAP_PASS", ""),
            "folder": os.environ.get("MENU_IMAP_FOLDER", "INBOX").strip() or "INBOX",
            "senders": [s.strip().lower() for s in os.environ.get("MENU_IMAP_SENDERS", "").split(",") if s.strip()]}


def configured():
    return _cfg() is not None


def _sender_hint(msg):
    """A distributor name guessed from the From header — display name, else the domain's first label.
    Only used when the menu file doesn't already self-identify."""
    name, addr = email.utils.parseaddr(msg.get("From", ""))
    if name and name.strip():
        return name.strip()
    dom = addr.split("@")[-1] if "@" in addr else ""
    return dom.split(".")[0].title() if dom else ""


def menu_attachments(msg):
    """(filename, bytes) for every spreadsheet attachment on the message."""
    out = []
    for part in msg.walk():
        fn = part.get_filename()
        if not fn:
            continue
        try:
            fn = str(email.header.make_header(email.header.decode_header(fn)))
        except Exception:
            pass
        if fn.lower().endswith(_EXT):
            payload = part.get_payload(decode=True)
            if payload:
                out.append((fn, payload))
    return out


def process_email(raw, land=True, log=print):
    """Parse + (optionally) land every menu attachment on ONE raw RFC822 message.
    Returns [{file, ok, distributor, lines, method, error?}] — [] if the mail carries no menu file."""
    msg = email.message_from_bytes(raw)
    hint = _sender_hint(msg)
    results = []
    for fn, data in menu_attachments(msg):
        try:
            parsed = menu_ingest.parse_smart(data, filename=fn, distributor="", log=log)
            # If the file didn't self-identify, retry once seeded with the sender name.
            if hint and parsed.get("distributor") in ("", "Unknown"):
                parsed = menu_ingest.parse_smart(data, filename=fn, distributor=hint, log=log)
            n = menu_ingest.land(parsed) if (land and parsed.get("ok")) else len(parsed.get("items") or [])
            results.append({"file": fn, "ok": bool(parsed.get("ok")), "distributor": parsed.get("distributor"),
                            "lines": n, "method": parsed.get("method"),
                            "warnings": parsed.get("warnings", [])})
        except Exception as e:
            results.append({"file": fn, "ok": False, "error": str(e)[:180]})
    return results


def poll(limit=25, dry_run=False, log=print):
    """Poll the mailbox for UNSEEN messages with menu attachments → parse + land, mark seen.
    Returns {ok, scanned, landed, menus[...]} or {ok:False, error} when IMAP isn't configured."""
    cfg = _cfg()
    if not cfg:
        return {"ok": False, "error": "mailbox-not-configured",
                "note": "Set MENU_IMAP_HOST/USER/PASS (+ optional MENU_IMAP_FOLDER / MENU_IMAP_SENDERS)."}
    M = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
    try:
        M.login(cfg["user"], cfg["pass"])
        M.select(cfg["folder"])
        typ, dat = M.search(None, "UNSEEN")
        ids = (dat[0].split() if dat and dat[0] else [])[:int(limit)]
        log("[menu_mailbox] %d unseen message(s) to scan" % len(ids))
        scanned, landed, menus = 0, 0, []
        for i in ids:
            typ, d = M.fetch(i, "(RFC822)")
            if not (d and d[0]):
                continue
            raw = d[0][1]
            frm = email.utils.parseaddr(email.message_from_bytes(raw).get("From", ""))[1].lower()
            if cfg["senders"] and not any(s in frm for s in cfg["senders"]):
                continue                                 # not an allowlisted sender — leave it unseen
            res = process_email(raw, land=not dry_run, log=log)
            if not res:
                continue                                 # no menu attachment — leave it unseen
            scanned += 1
            menus.extend(res)
            landed += sum(r.get("lines", 0) for r in res if r.get("ok"))
            if not dry_run:
                M.store(i, "+FLAGS", "\\Seen")
        return {"ok": True, "scanned": scanned, "landed": landed, "menus": menus, "dry_run": dry_run}
    finally:
        try:
            M.logout()
        except Exception:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Poll a mailbox for distributor menus and ingest them.")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true", help="parse only; don't land or mark seen")
    a = ap.parse_args(argv)
    if not configured():
        print("IMAP not configured — set MENU_IMAP_HOST/USER/PASS (+ optional MENU_IMAP_FOLDER/SENDERS).")
        return
    r = poll(limit=a.limit, dry_run=a.dry_run)
    if not r.get("ok"):
        print("ERROR:", r.get("error")); return
    print("scanned %d message(s), landed %d line(s)%s" %
          (r["scanned"], r["landed"], " (dry-run)" if r["dry_run"] else ""))
    for m in r["menus"]:
        print("  • %s → %s | %s lines | %s%s" % (m.get("file"), m.get("distributor"), m.get("lines"),
              m.get("method") or "?", "" if m.get("ok") else " [%s]" % m.get("error", "failed")))


if __name__ == "__main__":
    main()
