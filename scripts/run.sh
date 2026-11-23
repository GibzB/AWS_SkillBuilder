#!/usr/bin/env bash
#
# run.sh - run a project tool with the project's own Python, from anywhere.
#
# Saves activating the virtual environment first, which is the usual cause of
# "zsh: command not found: python".
#
# Usage:
#   scripts/run.sh engage daily                  # the morning routine
#   scripts/run.sh engage --done like comment    # log what you did
#   scripts/run.sh engage                        # what is open today
#   scripts/run.sh publish --from 27 --to 27 --auto-tags
#   scripts/run.sh progress --day 27 --state published
#   scripts/run.sh wish                          # draft a wish (never submits)
#
# Any other first word is treated as a script name under tools/builder_publish/.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
TOOLS="${REPO_ROOT}/tools/builder_publish"

if [[ ! -x "${PYTHON}" ]]; then
  echo "No virtual environment at ${REPO_ROOT}/.venv. Create it first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  sed -n '3,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi

name="$1"
shift
case "${name}" in
  engage)   script="engage.py" ;;
  publish)  script="batch_publish.py" ;;
  progress) script="progress_tracker.py" ;;
  wish)     script="draft_wish.py" ;;
  *)        script="${name%.py}.py" ;;
esac

if [[ ! -f "${TOOLS}/${script}" ]]; then
  echo "Unknown tool '${name}': no ${TOOLS}/${script}" >&2
  exit 2
fi

# The publish tool reads the campaign table, so it needs an AWS profile.
if [[ "${script}" == "batch_publish.py" || "${script}" == "bootstrap_campaign.py" ]]; then
  export AWS_PROFILE="${AWS_PROFILE:-default}"
fi

exec "${PYTHON}" "${TOOLS}/${script}" "$@"
