#!/usr/bin/env bash
# Syntax-check every maintained shell script and run ShellCheck's warning tier.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "check-shell-scripts.sh — workspace shell syntax and ShellCheck gate."
    exit 0 ;;
esac
[[ "$#" -eq 0 ]] || { echo "Usage: ./check-shell-scripts.sh" >&2; exit 2; }
command -v shellcheck >/dev/null 2>&1 || {
  echo "FAIL: shellcheck is required for the shell-script gate." >&2
  exit 2
}

workspace="$(CDPATH='' cd .. && pwd -P)"
repos_file="$workspace/scripts/repos.txt"
scripts=()
discovery_failures=0

append_repository_scripts() {
  local repository_candidate="$1"
  local repository_root
  local requested_root
  local relative_script

  requested_root="$(CDPATH='' cd -- "$repository_candidate" 2>/dev/null && pwd -P || true)"
  repository_root="$(git -C "$repository_candidate" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -z "$requested_root" || -z "$repository_root" || "$requested_root" != "$repository_root" ]]; then
    echo "FAIL: configured repository is missing or invalid: $repository_candidate" >&2
    discovery_failures=$((discovery_failures + 1))
    return
  fi
  while IFS= read -r -d '' relative_script; do
    scripts+=("$repository_root/$relative_script")
  done < <(git -C "$repository_root" ls-files -z -- '*.sh')
}

trim_whitespace() {
  local value="$1"

  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

[[ -f "$repos_file" ]] || {
  echo "FAIL: repository manifest not found: $repos_file" >&2
  exit 1
}

# The manifest is the workspace ownership boundary. Old repositories may remain
# on a developer machine after a library split; they are not maintained inputs
# once repos.txt stops naming them.
append_repository_scripts "$workspace/scripts"
if [[ -e "$workspace/setup/.git" ]]; then
  append_repository_scripts "$workspace/setup"
fi
while IFS= read -r manifest_line || [[ -n "$manifest_line" ]]; do
  manifest_line="${manifest_line%%#*}"
  manifest_line="$(trim_whitespace "$manifest_line")"
  [[ -n "$manifest_line" ]] || continue
  IFS='|' read -r _repository_url repository_path _repository_type <<< "$manifest_line"
  repository_path="$(trim_whitespace "${repository_path:-}")"
  [[ -n "$repository_path" ]] || {
    echo "FAIL: malformed repository manifest entry: $manifest_line" >&2
    discovery_failures=$((discovery_failures + 1))
    continue
  }
  append_repository_scripts "$workspace/scripts/$repository_path"
done < "$repos_file"

# p101 is a tracked shell entry point without a .sh suffix.
if [[ -f "$workspace/scripts/p101" ]] \
   && git -C "$workspace/scripts" ls-files --error-unmatch p101 >/dev/null 2>&1; then
  scripts+=("$workspace/scripts/p101")
fi
if [[ "$discovery_failures" -gt 0 ]]; then
  echo "Shell-script discovery failed: $discovery_failures problem(s)." >&2
  exit 1
fi
[[ ${#scripts[@]} -gt 0 ]] || { echo "FAIL: no shell scripts found." >&2; exit 1; }

syntax_failures=0
shellcheck_scripts=()
for script in "${scripts[@]}"; do
  first_line="$(head -n 1 "$script" 2>/dev/null || true)"
  case "$first_line" in
    *bash*) shell_bin="$(command -v bash)" ;;
    *) shell_bin="$(command -v sh)" ;;
  esac
  if ! "$shell_bin" -n "$script"; then
    echo "FAIL: syntax: $script" >&2
    syntax_failures=$((syntax_failures + 1))
  fi
  # ip-prompt.sh is deliberately source-compatible with both shells. Bash is
  # its declared interpreter; when Zsh is installed, validate that side of the
  # contract too.
  if [[ "$script" == "$workspace/setup/ip-prompt.sh" ]] \
     && command -v zsh >/dev/null 2>&1 \
     && ! zsh -n "$script"; then
    echo "FAIL: zsh syntax: $script" >&2
    syntax_failures=$((syntax_failures + 1))
  fi
  shellcheck_scripts+=("$script")
done
if [[ "$syntax_failures" -gt 0 ]]; then
  echo "Shell syntax check failed: $syntax_failures file(s)." >&2
  exit 1
fi

# Warning severity also catches ignored failures, unsafe globbing, masked
# statuses, and suspicious control flow. Intentional exceptions belong next
# to the relevant line, not in a workspace-wide ignore list.
shellcheck --severity=warning "${shellcheck_scripts[@]}"
printf 'PASS: %d shell scripts passed syntax and ShellCheck warning checks.\n' "${#scripts[@]}"
