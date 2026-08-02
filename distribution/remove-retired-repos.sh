#!/usr/bin/env bash
# Remove repositories that remain under a managed collection after repos.txt
# stops declaring them.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

apply=false
assume_yes=false
interactive=false

usage() {
  cat <<'P101_USAGE'
Usage: ./remove-retired-repos.sh [--apply] [--yes] [--interactive]

By default, list repositories under libraries/, programs/, templates/, and
examples/ that are no longer declared in repos.txt.

  --apply  Remove eligible retired repositories.
  --yes    Do not ask for confirmation; requires --apply.
  --interactive
           If a retired repository is blocked, pause so it can be resolved in
           another terminal, then retry. Enter q to abort.

A repository is eligible only when its worktree is clean, it has a configured
upstream, and it has no commits ahead of that upstream. Dirty, unpushed, or
misconfigured repositories are refused and never modified.
P101_USAGE
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --apply) apply=true; shift ;;
    --yes) assume_yes=true; shift ;;
    --interactive) interactive=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done
if $assume_yes && ! $apply; then
  printf 'Error: --yes requires --apply.\n' >&2
  exit 2
fi

workspace="$(CDPATH='' cd .. && pwd -P)"
repos_file="$workspace/scripts/repos.txt"
[[ -f "$repos_file" ]] || {
  printf 'Error: repository manifest not found: %s\n' "$repos_file" >&2
  exit 2
}

trim_whitespace() {
  local value="$1"

  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

active_repositories=()
while IFS= read -r manifest_line || [[ -n "$manifest_line" ]]; do
  manifest_line="${manifest_line%%#*}"
  manifest_line="$(trim_whitespace "$manifest_line")"
  [[ -n "$manifest_line" ]] || continue
  IFS='|' read -r _repository_url repository_path _repository_type <<< "$manifest_line"
  repository_path="$(trim_whitespace "${repository_path:-}")"
  [[ -n "$repository_path" ]] || {
    printf 'Error: malformed repository manifest entry: %s\n' "$manifest_line" >&2
    exit 2
  }
  repository_root="$(CDPATH='' cd -- "$workspace/scripts/$repository_path" 2>/dev/null && pwd -P || true)"
  [[ -n "$repository_root" ]] || {
    printf 'Error: active repository is missing: %s\n' "$repository_path" >&2
    exit 2
  }
  active_repositories+=("$repository_root")
done < "$repos_file"

is_active_repository() {
  local candidate="$1"
  local active_repository

  for active_repository in "${active_repositories[@]}"; do
    [[ "$candidate" != "$active_repository" ]] || return 0
  done
  return 1
}

retired_repositories=()
for collection in libraries programs templates examples; do
  collection_path="$workspace/$collection"
  [[ -d "$collection_path" ]] || continue
  for candidate in "$collection_path"/*; do
    [[ -d "$candidate" && ! -L "$candidate" && -e "$candidate/.git" ]] || continue
    candidate_root="$(CDPATH='' cd -- "$candidate" 2>/dev/null && pwd -P || true)"
    [[ -n "$candidate_root" ]] || continue
    is_active_repository "$candidate_root" && continue
    retired_repositories+=("$candidate_root")
  done
done

if [[ "${#retired_repositories[@]}" -eq 0 ]]; then
  printf 'No retired repositories found.\n'
  exit 0
fi

eligible_repositories=()
blocked=0
for repository in "${retired_repositories[@]}"; do
  if [[ ! -d "$repository/.git" ]]; then
    printf 'BLOCKED (linked worktree or nonstandard Git metadata): %s\n' "$repository" >&2
    blocked=$((blocked + 1))
    continue
  fi
  if [[ -n "$(git -C "$repository" status --porcelain --untracked-files=all 2>/dev/null || printf 'invalid')" ]]; then
    printf 'BLOCKED (dirty): %s\n' "$repository" >&2
    blocked=$((blocked + 1))
    continue
  fi
  upstream="$(git -C "$repository" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
  if [[ -z "$upstream" ]]; then
    printf 'BLOCKED (no upstream): %s\n' "$repository" >&2
    blocked=$((blocked + 1))
    continue
  fi
  ahead="$(git -C "$repository" rev-list --count "${upstream}..HEAD" 2>/dev/null || printf 'invalid')"
  if [[ "$ahead" == "invalid" ]]; then
    printf 'BLOCKED (invalid upstream): %s\n' "$repository" >&2
    blocked=$((blocked + 1))
    continue
  fi
  if [[ "$ahead" -gt 0 ]]; then
    printf 'BLOCKED (ahead by %s commit(s)): %s\n' "$ahead" "$repository" >&2
    blocked=$((blocked + 1))
    continue
  fi
  eligible_repositories+=("$repository")
done

if ! $apply; then
  for repository in "${eligible_repositories[@]}"; do
    printf 'WOULD REMOVE: %s\n' "$repository"
  done
  printf 'Retired repositories: %d eligible, %d blocked.\n' \
    "${#eligible_repositories[@]}" "$blocked"
  [[ "$blocked" -eq 0 ]]
  exit
fi

if [[ "${#eligible_repositories[@]}" -gt 0 ]] && ! $assume_yes; then
  printf 'The following clean, fully-pushed retired repositories will be removed:\n'
  printf '  %s\n' "${eligible_repositories[@]}"
  printf 'Type REMOVE to continue: '
  IFS= read -r answer
  if [[ "$answer" != "REMOVE" ]]; then
    printf 'Removal cancelled.\n'
    exit 1
  fi
fi

removed=0
for repository in "${eligible_repositories[@]}"; do
  rm -rf -- "$repository"
  printf 'REMOVED: %s\n' "$repository"
  removed=$((removed + 1))
done
printf 'Retired repository cleanup: %d removed, %d blocked.\n' "$removed" "$blocked"
if [[ "$blocked" -gt 0 ]] && $interactive; then
  printf 'Resolve the blocked repositories, then press Enter to retry; enter q to abort: '
  if ! IFS= read -r answer; then
    printf '\nInteractive input closed; aborting.\n' >&2
    exit 1
  fi
  case "$answer" in
    q|Q|quit|QUIT)
      printf 'Retired repository cleanup aborted.\n' >&2
      exit 1
      ;;
  esac
  exec "$0" --apply --yes --interactive
fi
[[ "$blocked" -eq 0 ]]
