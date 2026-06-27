#!/usr/bin/env bash
# provision-state.sh — create the PRIVATE S3 bucket that backs the Unifyd agent's state
# (so pulled datasets + run history survive container redeploys), then print the env vars
# and IAM policy to finish wiring it to the backend.
#
#   STATE_BUCKET=hoodie-suite-state AWS_REGION=us-east-1 ./scripts/provision-state.sh
#
# Requires aws CLI + `aws configure`. Idempotent, S3-only (creates nothing else). This is
# a SEPARATE bucket from the website bucket — state must never be web-served.
set -euo pipefail

STATE_BUCKET="${STATE_BUCKET:-hoodie-suite-state}"
AWS_REGION="${AWS_REGION:-us-east-1}"
STATE_PREFIX="${STATE_PREFIX:-unifyd-state}"

echo "• state bucket: $STATE_BUCKET ($AWS_REGION)"
if aws s3api head-bucket --bucket "$STATE_BUCKET" 2>/dev/null; then
  echo "  already exists — skipping create"
else
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$STATE_BUCKET" --region us-east-1
  else
    aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION"
  fi
  echo "  created"
fi

echo "• blocking ALL public access (state is private, never served)"
aws s3api put-public-access-block --bucket "$STATE_BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

cat <<DONE

────────────────────────────────────────────────────────────────────
✓ State bucket ready: s3://$STATE_BUCKET/$STATE_PREFIX/

Finish wiring it to the backend:

1) Set the env var on the ECS Express Mode service — in .github/workflows/deploy-api.yml
   uncomment the environment-variables line and set:
       STATE_BUCKET   $STATE_BUCKET
       STATE_PREFIX   $STATE_PREFIX     (optional; this is the default)

2) Give the service's TASK ROLE this IAM policy (least privilege), and pass its ARN
   as the task-role-arn in the workflow:
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::$STATE_BUCKET/$STATE_PREFIX/*" }
  ]
}

3) Keep the service at min = max = 1 instance — state is per-instance; multiple
   instances would each keep their own copy. (Fine for a single-user control plane.)

Restart the service. Pulls now persist to S3 and survive redeploys; on boot the
agent loads datasets.json / runs.json back from the bucket.
────────────────────────────────────────────────────────────────────
DONE
