#!/usr/bin/env bash
#
# deploy.sh - build and deploy the engine to ANY AWS account.
#
# Usage:
#   scripts/deploy.sh <aws-profile> [--email you@example.com] [--plan-only]
#
# What it does:
#   1. Resolves the target account from the profile (sts get-caller-identity).
#   2. Uses a Terraform WORKSPACE named after the account ID, so each account has
#      its own isolated state and deploys never clobber one another.
#   3. Builds the Lambda package, then plans (and applies unless --plan-only).
#
# Config comes from config/my-campaign.tfvars (gitignored). If it is missing it
# is created from config/example.tfvars; pass --email to set the notification
# address (or edit the file). The repo is public: never commit a real email.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TFVARS="${REPO_ROOT}/config/my-campaign.tfvars"
EXAMPLE="${REPO_ROOT}/config/example.tfvars"

PROFILE="${1:-}"
if [[ -z "${PROFILE}" || "${PROFILE}" == --* ]]; then
  echo "usage: $0 <aws-profile> [--email you@example.com] [--plan-only]" >&2
  exit 2
fi
shift

EMAIL=""
PLAN_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --email) EMAIL="${2:?--email needs a value}"; shift 2 ;;
    --plan-only) PLAN_ONLY=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

export AWS_PROFILE="${PROFILE}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "==> Target account: ${ACCOUNT_ID} (profile ${PROFILE})"

if [[ ! -f "${TFVARS}" ]]; then
  echo "==> Creating ${TFVARS} from example.tfvars"
  cp "${EXAMPLE}" "${TFVARS}"
fi

if [[ -n "${EMAIL}" ]]; then
  # Portable in-place edit (BSD and GNU sed).
  sed -i.bak -E "s|^notification_email[[:space:]]*=.*|notification_email = \"${EMAIL}\"|" "${TFVARS}"
  rm -f "${TFVARS}.bak"
fi

if grep -q 'you@example.com' "${TFVARS}"; then
  echo "ERROR: notification_email is still the placeholder. Re-run with --email you@example.com" >&2
  exit 1
fi

echo "==> Building Lambda package"
"${REPO_ROOT}/scripts/build_lambda.sh"

cd "${REPO_ROOT}/infra"
terraform init -input=false
terraform workspace select -or-create "acct-${ACCOUNT_ID}"
echo "==> Terraform workspace: $(terraform workspace show)"

if [[ "${PLAN_ONLY}" -eq 1 ]]; then
  terraform plan -input=false -var-file="${TFVARS}"
else
  terraform apply -input=false -auto-approve -var-file="${TFVARS}"
  echo
  echo "==> Deployed. Confirm the SNS subscription email AWS just sent."
  echo "    Then generate articles (see docs) and run batch_publish with AWS_PROFILE=${PROFILE}."
fi
