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

# STATE: disk (agent_state/) is ephemeral here. Set STATE_BUCKET (+ optional STATE_PREFIX)
# to persist datasets/runs to S3 so they survive redeploys; the runtime needs s3:Get/PutObject
# on that bucket (instance role / task role). See unifyd/README.md.
# ONE worker on purpose — state is in-process; a single worker keeps it coherent.
CMD ["sh", "-c", "gunicorn -w 1 -b 0.0.0.0:${PORT:-8080} server:app"]
