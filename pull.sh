#!/usr/bin/env bash
set -euo pipefail

dir_name=${PWD##*/}

# Ensure we're in a git repo
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "$dir_name is not a git repository."
  exit 2
fi

# Skip detached HEADs (no branch)
branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if [[ -z "$branch" ]]; then
  echo "$dir_name is on a detached HEAD; skipping pull."
  exit 0
fi

# Ensure an upstream is configured
if ! git rev-parse --verify -q "@{u}" >/dev/null; then
  echo "$dir_name has no upstream configured; skipping pull."
  exit 0
fi

# Update refs and compare
git fetch --quiet --prune

behind=$(git rev-list --count HEAD..@{u})
ahead=$(git rev-list --count @{u}..HEAD)

if (( behind == 0 && ahead == 0 )); then
  echo "$dir_name is already up to date."
  exit 0
fi

if (( behind > 0 )); then
  # Pull only if it can fast-forward; avoid accidental merge commits
  if git pull --ff-only --no-stat --no-edit; then
    echo "Updates were pulled in $dir_name. Please re-run the script."
    exit 1
  else
    echo "Cannot fast-forward $dir_name (local changes ahead or divergence). Resolve manually."
    exit 3
  fi
fi

# We're ahead (and not behind): nothing to pull
echo "$dir_name is ahead of upstream by $ahead commit(s); not pulling."
exit 0
