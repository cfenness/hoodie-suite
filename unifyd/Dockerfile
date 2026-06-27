# Container image for the Unifyd /api/* backend — the engine's server.py served by
# gunicorn. Portable: run it locally, on App Runner (image mode via ECR), Lightsail
# containers, ECS, anywhere. Build from the unifyd/ directory:
#
#   docker build -t hoodie-unifyd unifyd/
#   docker run --rm -p 8080:8080 hoodie-unifyd
#   curl localhost:8080/api/health
#
FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# the agent + scraper (fixtures/runtime excluded via .dockerignore)
COPY . .

# App Runner / most platforms inject PORT; default to 8080.
ENV PORT=8080
EXPOSE 8080

# NOTE: state (agent_state/) is on the container's ephemeral disk — fine to prove the
# wire, but pulled data resets on redeploy. Persistence (S3-backed state / a DB) is the
# follow-on once the path is proven. See unifyd/README.md.
CMD ["sh", "-c", "gunicorn -w 2 -b 0.0.0.0:${PORT:-8080} server:app"]
