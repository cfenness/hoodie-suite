# All-in-one image (Fly.io / any container host): one gunicorn process serves the /api/* backend
# AND the static suite from a single origin, so the apps' same-origin /api/* fetches work with no
# separate frontend host and no CORS. The engine source is in the image but is NEVER web-served —
# server.py only serves an allowlist of public files (see SUITE_ROOT / _SUITE_OK_TOP).
#
#   docker build -t hoodie-suite .
#   docker run --rm -p 8080:8080 -e ANTHROPIC_API_KEY=sk-... hoodie-suite
#   open http://localhost:8080          # the launcher
#   curl localhost:8080/api/health      # the backend
#
FROM python:3.12-slim

WORKDIR /app/unifyd

# deps first for layer caching
COPY unifyd/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# the whole repo: the engine (unifyd/) + the static suite (index.html, apps/, spine/, …)
COPY . /app

# SUITE_ROOT switches server.py into all-in-one mode (serve static + /api). PORT is injected by the host.
ENV PORT=8080 SUITE_ROOT=/app
EXPOSE 8080

# ONE worker on purpose — state is in-process; a single worker keeps it coherent. But add THREADS so
# a slow request (a bot-walled analyze routing through Bright Data can take ~1 min) doesn't freeze the
# whole app, and raise --timeout well above the default 30s so gunicorn doesn't KILL that request
# mid-fetch (which surfaced in the client as a bogus "Analyzer is offline"). Threads are safe here:
# the work is I/O-bound (network) so the GIL is released during waits; shared state stays in one process.
# gunicorn runs from /app/unifyd (WORKDIR) so `server:app` resolves; it serves the suite from SUITE_ROOT=/app.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:${PORT:-8080} server:app"]
