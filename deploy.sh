#!/usr/bin/env bash
# Manual deploy — run locally when you don't want to push through GitHub.
# Requires the AWS CLI installed and `aws configure` already done.
#
#   ./deploy.sh
#
# Reads these from your shell environment (or edit the defaults below):
set -euo pipefail

S3_BUCKET="${S3_BUCKET:-hoodie-suite}"                       # your bucket name
CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-}" # optional

echo "→ Syncing site to s3://$S3_BUCKET ..."
# IMPORTANT: keep this exclude list in sync with .github/workflows/deploy.yml. The
# engine (unifyd/, *.py), internal docs, and dev tooling must NEVER ship.
aws s3 sync . "s3://$S3_BUCKET" \
  --delete \
  --exclude ".git/*" \
  --exclude ".github/*" \
  --exclude ".claude/*" \
  --exclude "cloudfront/*" \
  --exclude "unifyd/*" \
  --exclude "api/*" \
  --exclude "scripts/*" \
  --exclude "*.py" \
  --exclude "README.md" \
  --exclude "CLAUDE.md" \
  --exclude "SPINE.md" \
  --exclude "FULLREAD.md" \
  --exclude "DOMAIN_RULESET.md" \
  --exclude "deploy.sh" \
  --exclude "dev.sh" \
  --exclude ".gitignore" \
  --exclude ".env" \
  --exclude ".env.*" \
  --exclude "*.test.js" \
  --exclude "Dockerfile" \
  --exclude ".dockerignore" \
  --exclude "fly.toml" \
  --exclude "DEPLOY-FLY.md" \
  --exclude "ENGINEERING_HANDOFF.md" \
  --cache-control "public,max-age=300"

if [ -n "$CLOUDFRONT_DISTRIBUTION_ID" ]; then
  echo "→ Invalidating CloudFront cache ..."
  aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths "/*" >/dev/null
fi

echo "✓ Done."
