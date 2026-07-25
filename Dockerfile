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

# The gov data sources (TTB ttbonline.gov, FL DBPR myfloridalicense.com) fail TLS verification in
# the slim image — "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate" — even though
# they work on a laptop. Refresh the system CA bundle (used by urllib → FL) so those roots/intermediates
# are present; certifi (used by requests → TTB) is upgraded below.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libzbar0 \
    && update-ca-certificates && rm -rf /var/lib/apt/lists/*
# libzbar0: the zbar system library pyzbar binds to, so the TTB enrich pass can decode the UPC barcode
# off each COLA label image (ttb_cola_labels.extract_upc_from_label). Without it pyzbar imports but no-ops.

# Headful Chrome + Xvfb — for the anti-bot sources that need a REAL browser to mint their token
# (UberEats/Postmates botdefense, Kroger Akamai, Total Wine PerimeterX, Albertsons Kasada, Ahold
# DataDome), then replay the chain's first-party API. Only the `runner` process group launches a
# browser (under a virtual display, via fly.toml's runner command); the public `app` group never does.
# The chrome .deb pulls its own runtime deps; xvfb gives headful Chrome a display on a headless box.
RUN apt-get update && apt-get install -y --no-install-recommends wget gnupg xvfb fonts-liberation \
    && wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -f /tmp/chrome.deb && rm -rf /var/lib/apt/lists/*

WORKDIR /app/unifyd

# deps first for layer caching (+ keep certifi current so `requests`-based gov scrapers verify certs).
# patchright (stealth playwright) drives the system Google Chrome (channel="chrome") for the headful sources.
COPY unifyd/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn patchright && pip install --no-cache-dir --upgrade certifi

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
# NOTE: this default CMD is the `app` (public) process group. The `runner` group overrides it in fly.toml
# to start Xvfb first (headful Chrome needs a display).
CMD ["sh", "-c", "gunicorn -w 1 --threads 24 --timeout 120 -b 0.0.0.0:${PORT:-8080} server:app"]
