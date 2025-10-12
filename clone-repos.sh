#!/usr/bin/env bash
set -euo pipefail

REPOS_FILE="repos.txt"

if [[ ! -f "$REPOS_FILE" ]]; then
  echo "Error: $REPOS_FILE not found in current directory." >&2
  exit 1
fi

command -v git >/dev/null 2>&1 || { echo "Error: git not found in PATH." >&2; exit 1; }

# Read repos.txt, skipping blank lines and comments.
# Each line: <git_url>|<target_dir>|[type]
while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
  # strip comments and trim
  line="${raw%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue

  IFS='|' read -r repo_url target_dir repo_type <<<"$line"

  if [[ -z "${repo_url:-}" || -z "${target_dir:-}" ]]; then
    echo "Skip malformed line: $raw" >&2
    continue
  fi

  echo "==> ${target_dir} ($( [[ -n "${repo_type:-}" ]] && echo "$repo_type" || echo "-" ))"

  # Ensure parent exists
  mkdir -p -- "$(dirname -- "$target_dir")"

  if [[ -d "$target_dir" ]]; then
    # Must be a git repo
    if [[ ! -d "$target_dir/.git" ]]; then
      echo "  ! Exists but not a git repo — skipping."
      continue
    fi

    # Warn if origin doesn't match the URL we’re expecting
    current_origin="$(git -C "$target_dir" remote get-url origin 2>/dev/null || echo "")"
    if [[ -n "$current_origin" && "$current_origin" != "$repo_url" ]]; then
      echo "  ! Origin mismatch:"
      echo "     current: $current_origin"
      echo "     wanted : $repo_url"
      # carry on anyway
    fi

    echo "  -> Fetching..."
    git -C "$target_dir" fetch --tags --prune

    # Rebase onto upstream to avoid merge commits
    # If no upstream, just report and continue.
    if git -C "$target_dir" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      echo "  -> Rebase onto upstream..."
      git -C "$target_dir" pull --rebase --autostash
    else
      echo "  ! No upstream tracking branch; skipping pull."
    fi
  else
    echo "  -> Cloning $repo_url"
    if git clone --recursive "$repo_url" "$target_dir"; then
      echo "  -> Clone OK."
    else
      echo "  ! Clone failed — skipping."
      continue
    fi
  fi

  # Init/update submodules if present
  if [[ -f "$target_dir/.gitmodules" ]]; then
    echo "  -> Updating submodules..."
    git -C "$target_dir" submodule update --init --recursive
  fi

  echo
done < "$REPOS_FILE"

echo "All repositories processed."
