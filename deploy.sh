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
aws s3 sync . "s3://$S3_BUCKET" \
  --delete \
  --exclude ".git/*" \
  --exclude ".github/*" \
  --exclude "cloudfront/*" \
  --exclude "unifyd/*" \
  --exclude "api/*" \
  --exclude "*.py" \
  --exclude "README.md" \
  --exclude "CLAUDE.md" \
  --exclude "SPINE.md" \
  --exclude "deploy.sh" \
  --exclude ".gitignore" \
  --cache-control "public,max-age=300"

if [ -n "$CLOUDFRONT_DISTRIBUTION_ID" ]; then
  echo "→ Invalidating CloudFront cache ..."
  aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths "/*" >/dev/null
fi

echo "✓ Done."
