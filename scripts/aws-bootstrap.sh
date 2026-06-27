#!/usr/bin/env bash
# aws-bootstrap.sh — one-time, one-command provisioning of the Hoodie Suite hosting:
# a PRIVATE S3 bucket served only through CloudFront (Origin Access Control), then a
# first deploy. After this runs, shipping is just `git push` (once the GitHub secrets
# it prints are set) or `./deploy.sh`.
#
#   S3_BUCKET=hoodie-suite-<unique> AWS_REGION=us-east-1 ./scripts/aws-bootstrap.sh
#
# NOTE: S3 bucket names are GLOBALLY unique across all AWS accounts — pick a unique
# name (e.g. suffix your account id: hoodie-suite-123456789012), not a bare "hoodie-suite".
#
# Requirements: AWS CLI v2 installed and `aws configure` done with an identity that can
# create S3 buckets, CloudFront distributions/OAC, and put bucket policies.
#
# Safe to re-run: every step checks for existing resources first (idempotent). It does
# NOT touch the password gate (see cloudfront/basic-auth.js) or DNS — those stay manual.
#
# NOTE: this was written to match the current AWS API but has not been run against a
# live account here — read it once before running. Nothing is destructive.
set -euo pipefail

S3_BUCKET="${S3_BUCKET:-hoodie-suite}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

say() { printf '\n\033[1m• %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. S3 bucket
say "S3 bucket: $S3_BUCKET ($AWS_REGION)"
if aws s3api head-bucket --bucket "$S3_BUCKET" 2>/dev/null; then
  echo "  already exists — skipping create"
else
  if [ "$AWS_REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$S3_BUCKET" --region us-east-1
  else
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=$AWS_REGION"
  fi
  echo "  created"
fi

say "Blocking ALL public access (we serve only through CloudFront)"
aws s3api put-public-access-block --bucket "$S3_BUCKET" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# ---------------------------------------------------------------- 2. OAC
say "Origin Access Control"
OAC_NAME="${S3_BUCKET}-oac"
OAC_ID="$(aws cloudfront list-origin-access-controls \
  --query "OriginAccessControlList.Items[?Name=='$OAC_NAME'].Id | [0]" --output text 2>/dev/null || echo None)"
if [ "$OAC_ID" = "None" ] || [ -z "$OAC_ID" ]; then
  OAC_ID="$(aws cloudfront create-origin-access-control --origin-access-control-config \
    "Name=$OAC_NAME,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
    --query 'OriginAccessControl.Id' --output text)"
  echo "  created: $OAC_ID"
else
  echo "  reusing: $OAC_ID"
fi

# ---------------------------------------------------------------- 3. Distribution
ORIGIN_DOMAIN="${S3_BUCKET}.s3.${AWS_REGION}.amazonaws.com"
CALLER_REF="hoodie-suite-$S3_BUCKET"
say "CloudFront distribution (origin: $ORIGIN_DOMAIN)"
DIST_ID="$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Origins.Items[0].DomainName=='$ORIGIN_DOMAIN'].Id | [0]" \
  --output text 2>/dev/null || echo None)"
if [ "$DIST_ID" = "None" ] || [ -z "$DIST_ID" ]; then
  CONFIG="$(mktemp)"
  cat > "$CONFIG" <<JSON
{
  "CallerReference": "$CALLER_REF",
  "Comment": "Hoodie Suite",
  "Enabled": true,
  "DefaultRootObject": "index.html",
  "Origins": { "Quantity": 1, "Items": [ {
    "Id": "s3-$S3_BUCKET",
    "DomainName": "$ORIGIN_DOMAIN",
    "OriginPath": "",
    "CustomHeaders": { "Quantity": 0 },
    "OriginAccessControlId": "$OAC_ID",
    "S3OriginConfig": { "OriginAccessIdentity": "" },
    "ConnectionAttempts": 3,
    "ConnectionTimeout": 10,
    "OriginShield": { "Enabled": false }
  } ] },
  "DefaultCacheBehavior": {
    "TargetOriginId": "s3-$S3_BUCKET",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": { "Quantity": 2, "Items": ["GET","HEAD"],
                        "CachedMethods": { "Quantity": 2, "Items": ["GET","HEAD"] } },
    "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
    "Compress": true
  }
}
JSON
  DIST_ID="$(aws cloudfront create-distribution --distribution-config "file://$CONFIG" \
    --query 'Distribution.Id' --output text)"
  rm -f "$CONFIG"
  echo "  created: $DIST_ID"
else
  echo "  reusing: $DIST_ID"
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
DIST_ARN="arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${DIST_ID}"
DIST_DOMAIN="$(aws cloudfront get-distribution --id "$DIST_ID" --query 'Distribution.DomainName' --output text)"

# ---------------------------------------------------------------- 4. Bucket policy (CloudFront-only read)
say "Bucket policy: allow read ONLY from this distribution"
POLICY="$(mktemp)"
cat > "$POLICY" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [ {
    "Sid": "AllowCloudFrontServicePrincipalReadOnly",
    "Effect": "Allow",
    "Principal": { "Service": "cloudfront.amazonaws.com" },
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::${S3_BUCKET}/*",
    "Condition": { "StringEquals": { "AWS:SourceArn": "$DIST_ARN" } }
  } ]
}
JSON
aws s3api put-bucket-policy --bucket "$S3_BUCKET" --policy "file://$POLICY"
rm -f "$POLICY"

# ---------------------------------------------------------------- 5. First deploy
say "First deploy (excludes the engine + docs, per deploy.sh)"
( cd "$ROOT" && S3_BUCKET="$S3_BUCKET" CLOUDFRONT_DISTRIBUTION_ID="$DIST_ID" ./deploy.sh )

# ---------------------------------------------------------------- done
cat <<DONE

────────────────────────────────────────────────────────────────────
✓ Provisioned.   Live (after ~5 min first propagation):  https://$DIST_DOMAIN

Two manual steps left, both one-time:

1) Auto-deploy on push — add these GitHub repo secrets
   (Settings → Secrets and variables → Actions):
       AWS_ACCESS_KEY_ID          <a deploy IAM user's key>
       AWS_SECRET_ACCESS_KEY      <that user's secret>
       AWS_REGION                 $AWS_REGION
       S3_BUCKET                  $S3_BUCKET
       CLOUDFRONT_DISTRIBUTION_ID $DIST_ID

2) Password gate (optional but recommended) — cloudfront/basic-auth.js:
       echo -n 'hoodie:YOUR-PASSWORD' | base64        # paste into the token
   then publish it as a CloudFront Function on the viewer-request event
   (see README → "Gate it with a password").
────────────────────────────────────────────────────────────────────
DONE
