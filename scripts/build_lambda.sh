#!/usr/bin/env bash
#
# build_lambda.sh - stage the Lambda deployment package.
#
# Produces infra/build/lambda_src/ containing the application package (app/)
# and its runtime dependencies (excluding boto3/botocore, which the Lambda
# Python runtime already provides). Terraform's archive_file zips this staged
# directory, so the source hash changes only when packaged content changes and
# Terraform redeploys only then.
#
# Usage: scripts/build_lambda.sh
#
# The Lambda runtime is Python 3.12 (see infra/main.tf lambda_runtime). Install
# dependencies with a matching interpreter for binary compatibility. The only
# non-runtime dependency here is markdown-it-py (pure Python) and its dep mdurl,
# so platform wheels are not a concern; if heavier native deps are added later,
# install with --platform manylinux2014_x86_64 to match the Lambda runtime.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/infra/build/lambda_src"

echo "==> Cleaning ${BUILD_DIR}"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

echo "==> Copying application package"
# Copy app/ but exclude compiled caches so the artifact is reproducible.
rsync -a --exclude '__pycache__' --exclude '*.pyc' \
  "${REPO_ROOT}/app" "${BUILD_DIR}/"

echo "==> Installing runtime dependencies (excluding boto3/botocore)"
# boto3 and botocore are provided by the Lambda runtime; installing them would
# only bloat the package. Install just the application's third-party runtime
# deps into the package root so they import at the top level.
python -m pip install \
  --quiet \
  --target "${BUILD_DIR}" \
  --only-binary=:all: \
  "markdown-it-py>=3.0"

echo "==> Pruning packaging metadata and caches"
find "${BUILD_DIR}" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "${BUILD_DIR}" -type d -name '*.dist-info' -prune -exec rm -rf {} +
find "${BUILD_DIR}" -type d -name 'boto3' -prune -exec rm -rf {} +
find "${BUILD_DIR}" -type d -name 'botocore' -prune -exec rm -rf {} +
# pip may install console-script shims into bin/; not needed at runtime.
rm -rf "${BUILD_DIR}/bin"

echo "==> Staged Lambda package at ${BUILD_DIR}"
du -sh "${BUILD_DIR}" 2>/dev/null || true
