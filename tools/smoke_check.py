#!/usr/bin/env python3
"""smoke_check.py — the suite's deterministic smoke layer: does every registered app actually serve, and does
everything it references actually exist? Stdlib-only, self-serving (spins its own HTTP server), zero deps —
run it before "ship it" and after any shell/spine change.

What it proves (all deterministic — the runtime/console-error layer is the /smoke skill's browser pass):
  registry   every APPS entry's file exists on disk; no duplicate ids; every group is a declared GROUP
  orphans    apps/*.html not in APPS and not referenced by any other page (composite tabs count as referenced)
  serving    the shell, every registered app, and the spine serve HTTP 200 with real content over a real origin
  wiring     every LOCAL src/href/iframe reference inside each served page resolves to a file that exists
             (a broken ../spine/spine.js or a renamed iframe target fails HERE, not silently in production)

Exit 0 = clean. Exit 1 = failures (each printed with the exact file and reference that broke).
Usage: python3 tools/smoke_check.py [--json]
"""
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# local-reference extraction: src= / href= on any tag. The lookbehind keeps JS assignments like `o._src="x"`
# from matching. Skips externals, anchors, data:, mailto:, and template/JS-generated urls (anything with ${ or
# a quote inside — those need the runtime layer, not this one).
_REF_RE = re.compile(r"""(?<![\w.$-])(?:src|href)\s*=\s*["']([^"'<>{}$]+)["']""", re.I)
_SKIP_PREFIX = ("http://", "https://", "//", "#", "data:", "mailto:", "tel:", "javascript:", "blob:")
# runtime-only paths a static check must not fail on: API (needs the agent), auth routes, hash routes
_RUNTIME_PREFIX = ("/api/", "/auth/", "api/", "?")


def parse_apps(index_html):
    """The APPS array out of index.html — one entry per line, keys are bare identifiers, values 'quoted'."""
    m = re.search(r"const APPS\s*=\s*\[(.*?)\];", index_html, re.S)
    if not m:
        raise SystemExit("FATAL: could not locate `const APPS = [...]` in index.html")
    apps = []
    for entry in re.finditer(r"\{([^}]*)\}", m.group(1)):
        kv = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", entry.group(1)))
        if kv.get("id") and kv.get("file"):
            apps.append(kv)
    gm = re.search(r"const GROUPS\s*=\s*\[(.*?)\];", index_html, re.S)
    groups = re.findall(r"'([^']+)'", gm.group(1)) if gm else []
    return apps, groups


def local_refs(html, base_dir):
    """Resolved filesystem paths for every local reference in a page (relative to the page, or root-absolute)."""
    out = []
    for ref in _REF_RE.findall(html):
        r = ref.strip()
        if not r or r.startswith(_SKIP_PREFIX) or r.startswith(_RUNTIME_PREFIX):
            continue
        r = r.split("#")[0].split("?")[0]
        if not r:
            continue
        fs = os.path.normpath(os.path.join(ROOT, r.lstrip("/")) if r.startswith("/")
                              else os.path.join(base_dir, r))
        out.append((ref, fs))
    return out


def run(as_json=False):
    t0 = time.time()
    failures, warnings = [], []

    index_path = os.path.join(ROOT, "index.html")
    index_html = open(index_path, encoding="utf-8").read()
    apps, groups = parse_apps(index_html)

    # ── registry integrity ────────────────────────────────────────────────────────────────────────────────────
    seen = {}
    for a in apps:
        if a["id"] in seen:
            failures.append(("registry", a["id"], "duplicate app id in APPS"))
        seen[a["id"]] = a
        if not os.path.exists(os.path.join(ROOT, a["file"])):
            failures.append(("registry", a["id"], "APPS entry points at a missing file: %s" % a["file"]))
        if groups and a.get("group") and a["group"] not in groups:
            failures.append(("registry", a["id"], "group %r is not in GROUPS %s" % (a["group"], groups)))

    # ── orphans: an app file nothing points at is either dead weight or a registration someone forgot.
    # Referenced = its filename appears in index.html or in ANY OTHER page (static src, data-src, or a JS
    # string — composite tabs like sources.html/mdm.html reference children either way).
    registered = {a["file"] for a in apps}
    all_html = sorted(f for f in os.listdir(os.path.join(ROOT, "apps")) if f.endswith(".html"))
    contents = {f: open(os.path.join(ROOT, "apps", f), encoding="utf-8", errors="replace").read()
                for f in all_html}
    for f in all_html:
        if ("apps/" + f) in registered:
            continue
        referenced = f in index_html or any(f in c for name, c in contents.items() if name != f)
        if not referenced:
            warnings.append(("orphan", f, "apps/%s is not in APPS and no page references it" % f))

    # ── serve + wiring: real origin, real fetches (iframes/fetch need HTTP — mirrors how production serves) ───
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def translate_path(self, path):
            p = super().translate_path(path)
            return os.path.join(ROOT, os.path.relpath(p, os.getcwd()))

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def fetch(rel):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/%s" % (port, rel), timeout=10) as r:
                return r.status, r.read()
        except Exception as e:
            return None, str(e).encode()

    pages = [("shell", "index.html")] + [(a["id"], a["file"]) for a in apps
                                         if os.path.exists(os.path.join(ROOT, a["file"]))]
    checked_refs = 0
    for app_id, rel in pages:
        status, body = fetch(rel)
        if status != 200:
            failures.append(("serve", app_id, "%s → HTTP %s" % (rel, status)))
            continue
        if len(body) < 1024:
            failures.append(("serve", app_id, "%s served only %d bytes — empty/truncated page" % (rel, len(body))))
            continue
        html = body.decode("utf-8", errors="replace")
        if "</html>" not in html.lower():
            warnings.append(("serve", app_id, "%s has no closing </html> tag (browsers tolerate it; "
                                              "flagging so a truncated write can't hide)" % rel))
        for ref, fs in local_refs(html, os.path.join(ROOT, os.path.dirname(rel))):
            checked_refs += 1
            if not os.path.exists(fs):
                failures.append(("wiring", app_id, "%s references %r — resolves to a missing file" % (rel, ref)))

    for spine_file in ("spine/spine.js", "spine/hierarchy.sample.json"):
        status, body = fetch(spine_file)
        if status != 200 or len(body) < 100:
            failures.append(("wiring", "spine", "%s → HTTP %s (%d bytes)" % (spine_file, status, len(body or b""))))

    httpd.shutdown()

    result = {
        "as_of": time.time(), "took_ms": round((time.time() - t0) * 1000),
        "apps_registered": len(apps), "pages_served": len(pages), "refs_checked": checked_refs,
        "ok": not failures, "failures": [{"layer": l, "app": a, "detail": d} for l, a, d in failures],
        "warnings": [{"layer": l, "app": a, "detail": d} for l, a, d in warnings],
    }
    if as_json:
        print(json.dumps(result, indent=1))
    else:
        print("SUITE SMOKE — %d apps, %d pages served, %d local refs checked (%.1fs)" % (
            len(apps), len(pages), checked_refs, result["took_ms"] / 1000))
        if not failures and not warnings:
            print("ALL CLEAR — every registered app serves, every local reference resolves.")
        for l, a, d in failures:
            print("  FAIL [%s] %-14s %s" % (l, a, d))
        for l, a, d in warnings:
            print("  warn [%s] %-14s %s" % (l, a, d))
        if failures:
            print("%d failure(s)." % len(failures))
        elif warnings:
            print("Clean, with %d warning(s) worth a look." % len(warnings))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run(as_json="--json" in sys.argv))
