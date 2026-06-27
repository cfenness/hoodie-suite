#!/usr/bin/env bash
# provision-ecs-express.sh — one-time setup for the Unifyd /api/* backend on
# Amazon ECS Express Mode (AWS's successor to App Runner): an ECR repo to hold the
# image, plus the two IAM roles Express Mode requires. Prints the values to paste
# into GitHub so the deploy-api workflow can build + deploy on every push.
#
#   AWS_REGION=us-east-1 ECR_REPOSITORY=hoodie-unifyd ./scripts/provision-ecs-express.sh
#
# Requires aws CLI + `aws configure`. Idempotent, creates nothing destructive.
# NOTE: a brand-new AWS account may need to be verified before ECS/Fargate (and
# CloudFront) will provision — same support case as CloudFront. Role names + policies
# are per the AWS Express Mode docs.
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPOSITORY="${ECR_REPOSITORY:-hoodie-unifyd}"
EXEC_ROLE="ecsTaskExecutionRole"
INFRA_ROLE="ecsInfrastructureRoleForExpressServices"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

say() { printf '\n\033[1m• %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 1. ECR repo
say "ECR repository: $ECR_REPOSITORY ($AWS_REGION)"
if aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" --region "$AWS_REGION" >/dev/null 2>&1; then
  echo "  already exists"
else
  aws ecr create-repository --repository-name "$ECR_REPOSITORY" --region "$AWS_REGION" \
    --image-scanning-configuration scanOnPush=true >/dev/null
  echo "  created"
fi
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"

# ---------------------------------------------------------------- 2. IAM roles
ensure_role() { # name  trust-service-principal  managed-policy-arn  [sid]
  local name="$1" svc="$2" policy="$3" sid="${4:-}"
  local trust
  trust="$(printf '{"Version":"2012-10-17","Statement":[{%s"Effect":"Allow","Principal":{"Service":"%s"},"Action":"sts:AssumeRole"}]}' \
            "${sid:+\"Sid\":\"$sid\",}" "$svc")"
  if aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    echo "  role $name already exists"
  else
    aws iam create-role --role-name "$name" --assume-role-policy-document "$trust" >/dev/null
    echo "  created role $name"
  fi
  aws iam attach-role-policy --role-name "$name" --policy-arn "$policy" >/dev/null
}

say "Task execution role: $EXEC_ROLE"
ensure_role "$EXEC_ROLE" "ecs-tasks.amazonaws.com" \
  "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"

say "Express Mode infrastructure role: $INFRA_ROLE"
ensure_role "$INFRA_ROLE" "ecs.amazonaws.com" \
  "arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices" \
  "AllowAccessInfrastructureForECSExpressServices"

EXEC_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${EXEC_ROLE}"
INFRA_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${INFRA_ROLE}"

# ---------------------------------------------------------------- done
cat <<DONE

────────────────────────────────────────────────────────────────────
✓ ECR + IAM ready.   Image will live at:  $ECR_URI

Now wire the deploy-api workflow. In GitHub → repo → Settings → Secrets and
variables → Actions:

  Secrets (if not already set for the static deploy):
      AWS_ACCESS_KEY_ID        <deploy user's key>
      AWS_SECRET_ACCESS_KEY    <deploy user's secret>

  Variables:
      AWS_REGION            $AWS_REGION
      ECR_REPOSITORY        $ECR_REPOSITORY
      ECS_SERVICE_NAME      hoodie-unifyd
      ECS_EXEC_ROLE_ARN     $EXEC_ARN
      ECS_INFRA_ROLE_ARN    $INFRA_ARN

Then push to main (or run the "Deploy Unifyd API" workflow manually). It builds
unifyd/Dockerfile, pushes to ECR, and the AWS Express Mode action creates the
service on first run and updates it after. The service URL prints in the run logs
as  https://hoodie-unifyd.ecs.${AWS_REGION}.on.aws/  (test /api/health there).

Later (durable state): set ECS_TASK_ROLE_ARN + STATE_BUCKET — see unifyd/README.md.
────────────────────────────────────────────────────────────────────
DONE
