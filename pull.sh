#!/usr/bin/env bash
set -euo pipefail

allow_snapshot=false
case "${1:-}" in
  -h|--help)
    cat <<'P101_USAGE'
Usage: pull.sh [--allow-snapshot]

Fetch and fast-forward the repository containing this script.
  --allow-snapshot  succeed without fetching when usable Git metadata was not
                    copied with the source tree
P101_USAGE
    exit 0
    ;;
  --allow-snapshot)
    allow_snapshot=true
    shift
    ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Error: unknown option: %s\n' "$1" >&2; exit 2; }

# Always operate on the repo this script lives in, regardless of cwd.
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

dir_name=${PWD##*/}

# Ensure Git resolves this exact directory as the repository root. VM actions
# can copy a .git indirection without copying the referenced metadata, and a
# parent checkout must not make this snapshot look like its own repository.
script_root="$(pwd -P)"
repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
resolved_repo_root=""
if [[ -n "$repo_root" ]]; then
  resolved_repo_root="$(CDPATH='' cd -- "$repo_root" 2>/dev/null && pwd -P || true)"
fi
if [[ -z "$resolved_repo_root" || "$resolved_repo_root" != "$script_root" ]]; then
  if $allow_snapshot; then
    echo "$dir_name is a source snapshot without usable Git metadata; skipping self-update."
    exit 0
  fi
  echo "Error: $dir_name is not a git repository." >&2
  exit 2
fi

# Skip detached HEADs (no branch)
branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
if [[ -z "$branch" ]]; then
  echo "Error: $dir_name is on a detached HEAD." >&2
  exit 2
fi

# Ensure an upstream is configured
if ! git rev-parse --verify -q "@{u}" >/dev/null; then
  echo "Error: $dir_name has no upstream configured." >&2
  exit 2
fi

# Update refs and compare
git fetch --quiet --prune

behind=$(git rev-list --count 'HEAD..@{u}')
ahead=$(git rev-list --count '@{u}..HEAD')

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
