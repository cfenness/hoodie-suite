#!/usr/bin/env python3
"""
dev_server.py — one-origin local dev server for the Hoodie Suite.

Mirrors the production routing model (CloudFront: /api/* -> backend, everything
else -> static S3) on a single localhost origin, so apps fetch `/api/*` exactly
the way they will in prod — no per-environment URL switching.

  * Serves the repo statically from the suite root.
  * Reverse-proxies /api/*  ->  the Unifyd agent (default http://127.0.0.1:8765).
  * If the agent isn't running, /api/* returns 502 fast and the apps fall back to
    their embedded preview data (same graceful degradation as prod-without-backend).

Usage (normally launched by ../dev.sh, but standalone too):
    PORT=8000 AGENT=http://127.0.0.1:8765 python3 scripts/dev_server.py
"""
import os, sys, urllib.request, urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root (one up from scripts/)
PORT  = int(os.environ.get("PORT", "8000"))
AGENT = os.environ.get("AGENT", "http://127.0.0.1:8765").rstrip("/")


class Handler(SimpleHTTPRequestHandler):
    # Serve from the suite root regardless of where this is invoked.
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def _proxy(self, method):
        url = AGENT + self.path
        body = None
        if "content-length" in self.headers:
            body = self.rfile.read(int(self.headers["content-length"]))
        req = urllib.request.Request(url, data=body, method=method)
        for h in ("content-type", "accept"):
            if h in self.headers:
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                self.send_response(r.status)
                payload = r.read()
                self.send_header("content-type", r.headers.get("content-type", "application/json"))
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as e:          # agent answered with an error — pass it through
            payload = e.read()
            self.send_response(e.code)
            self.send_header("content-type", e.headers.get("content-type", "application/json"))
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception:                            # agent not running / unreachable -> fall back
            payload = b'{"ok":false,"error":"unifyd agent not running (start it with ./dev.sh)"}'
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy("GET")
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy("POST")
        self.send_error(405)

    def end_headers(self):
        # No caching in dev so edits show on refresh — the whole point of the loop.
        if not self.path.startswith("/api/"):
            self.send_header("cache-control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  dev  %s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"Hoodie Suite dev server  ->  http://localhost:{PORT}")
    print(f"  /api/* proxied to {AGENT}  (falls back to embedded data if the agent is down)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
