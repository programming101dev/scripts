#!/usr/bin/env bash
# check-github-actions-template.sh — ensure the starter workflow matches CI.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

live_workflow=".github/workflows/p101-stack.yml"
starter_workflow="github-actions/p101-stack.yml"

if [ ! -f "$live_workflow" ]; then
  echo "FAIL: missing live workflow: $live_workflow" >&2
  exit 1
fi

if [ ! -f "$starter_workflow" ]; then
  echo "FAIL: missing starter workflow: $starter_workflow" >&2
  exit 1
fi

if ! cmp -s "$live_workflow" "$starter_workflow"; then
  echo "FAIL: GitHub Actions starter workflow drifted from the live workflow." >&2
  echo "Live workflow:    $live_workflow" >&2
  echo "Starter workflow: $starter_workflow" >&2
  echo >&2
  diff -u "$live_workflow" "$starter_workflow" >&2 || true
  exit 1
fi

if ! grep -Fq 'git config --global --add safe.directory "$(pwd -P)"' "$live_workflow"; then
  echo "FAIL: FreeBSD CI does not trust its exact rsynced scripts checkout." >&2
  exit 1
fi

echo "PASS: GitHub Actions starter workflow matches the live workflow."
