#!/usr/bin/env bash
# add-api-cloudfront-behavior.sh — wire /api/* on the existing CloudFront distribution
# to the deployed Unifyd backend (App Runner / Lightsail / any HTTPS origin). After this,
# the suite and its API share ONE domain: /api/* -> backend, everything else -> S3. The
# apps already fetch /api/* (with embedded fallback), so the MDM console goes live with
# zero front-end changes.
#
#   API_ORIGIN_DOMAIN=xxxx.us-east-1.awsapprunner.com \
#   S3_BUCKET=hoodie-suite ./scripts/add-api-cloudfront-behavior.sh
#
# Requires: aws CLI, jq. SAFE-ish: it writes the current distribution config to a
# timestamped backup before changing anything, is idempotent (re-run is a no-op), and
# never deletes. Still — it edits a LIVE distribution and was not run against a live
# account here. Read it, and keep the backup it prints until you've verified.
set -euo pipefail

API_ORIGIN_DOMAIN="${API_ORIGIN_DOMAIN:?set API_ORIGIN_DOMAIN to the backend HTTPS host (no scheme), e.g. xxxx.us-east-1.awsapprunner.com}"
S3_BUCKET="${S3_BUCKET:-hoodie-suite}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ORIGIN_ID="unifyd-api"
command -v jq >/dev/null || { echo "jq is required"; exit 1; }

# Managed policies: CachingDisabled (don't cache API), AllViewerExceptHostHeader
# (forward everything to a custom origin EXCEPT Host — forwarding Host breaks App Runner).
CACHE_POLICY_DISABLED="4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
ORIGIN_REQ_ALLVIEWER_NOHOST="b689b0a8-53d0-40ab-baf2-68738e2966ac"

# Find the distribution by its S3 origin (same heuristic as aws-bootstrap.sh).
ORIGIN_DOMAIN="${S3_BUCKET}.s3.${AWS_REGION}.amazonaws.com"
DIST_ID="$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Origins.Items[?DomainName=='$ORIGIN_DOMAIN']].Id | [0]" \
  --output text)"
[ -n "$DIST_ID" ] && [ "$DIST_ID" != "None" ] || { echo "No distribution found for origin $ORIGIN_DOMAIN — run aws-bootstrap.sh first."; exit 1; }
echo "• distribution: $DIST_ID"

TS="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo backup)"
BACKUP="cloudfront-config.$DIST_ID.$TS.json"
aws cloudfront get-distribution-config --id "$DIST_ID" > "$BACKUP"
ETAG="$(jq -r '.ETag' "$BACKUP")"
echo "• current config backed up to $BACKUP (ETag $ETAG)"

# Idempotency: bail cleanly if /api/* is already wired.
if jq -e '.DistributionConfig.CacheBehaviors.Items // [] | any(.PathPattern == "/api/*")' "$BACKUP" >/dev/null; then
  echo "✓ /api/* behavior already present — nothing to do."
  exit 0
fi

NEWCFG="$(mktemp)"
jq --arg dom "$API_ORIGIN_DOMAIN" --arg oid "$ORIGIN_ID" \
   --arg cp "$CACHE_POLICY_DISABLED" --arg orp "$ORIGIN_REQ_ALLVIEWER_NOHOST" '
  .DistributionConfig
  # 1) add the API origin (custom HTTPS origin)
  | .Origins.Items += [{
      "Id": $oid, "DomainName": $dom, "OriginPath": "",
      "CustomHeaders": {"Quantity": 0},
      "CustomOriginConfig": {
        "HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "https-only",
        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
        "OriginReadTimeout": 30, "OriginKeepaliveTimeout": 5
      },
      "ConnectionAttempts": 3, "ConnectionTimeout": 10,
      "OriginShield": {"Enabled": false}
    }]
  | .Origins.Quantity = (.Origins.Items | length)
  # 2) add the /api/* behavior -> API origin (all methods, no caching, host stripped)
  | .CacheBehaviors.Items = ((.CacheBehaviors.Items // []) + [{
      "PathPattern": "/api/*", "TargetOriginId": $oid,
      "ViewerProtocolPolicy": "redirect-to-https",
      "AllowedMethods": {
        "Quantity": 7, "Items": ["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"],
        "CachedMethods": {"Quantity": 2, "Items": ["GET","HEAD"]}
      },
      "Compress": true,
      "CachePolicyId": $cp,
      "OriginRequestPolicyId": $orp,
      "SmoothStreaming": false,
      "FieldLevelEncryptionId": "",
      "LambdaFunctionAssociations": {"Quantity": 0},
      "FunctionAssociations": {"Quantity": 0}
    }])
  | .CacheBehaviors.Quantity = (.CacheBehaviors.Items | length)
' "$BACKUP" > "$NEWCFG"

echo "• applying update ..."
aws cloudfront update-distribution --id "$DIST_ID" --if-match "$ETAG" \
  --distribution-config "file://$NEWCFG" >/dev/null
rm -f "$NEWCFG"

cat <<DONE

✓ /api/* is now routed to https://$API_ORIGIN_DOMAIN  (propagates in ~5 min).
  Verify:  curl -s https://<your-domain>/api/health
  The MDM console (apps/mdm.html) will switch from "preview" to live automatically.

Notes:
  • The password gate (cloudfront/basic-auth.js) is on the DEFAULT behavior only.
    If you want /api/* gated too, attach the same CloudFront Function to this behavior.
  • Backend state is ephemeral until persistence is added (see unifyd/README.md).
  • Rollback: aws cloudfront update-distribution --id $DIST_ID --if-match <new-etag> \\
      --distribution-config file://<(jq .DistributionConfig $BACKUP)
DONE
