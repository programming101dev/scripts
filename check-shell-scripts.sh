#!/usr/bin/env bash
# Syntax-check every maintained shell script and run ShellCheck's warning tier.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

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
scripts=()
while IFS= read -r -d '' git_metadata; do
  repository_candidate="${git_metadata%/.git}"
  repository_root="$(git -C "$repository_candidate" rev-parse --show-toplevel 2>/dev/null || true)"
  [[ -n "$repository_root" ]] || continue

  while IFS= read -r -d '' relative_script; do
    scripts+=("$repository_root/$relative_script")
  done < <(git -C "$repository_root" ls-files -z -- '*.sh')
done < <(
  find "$workspace" \
    \( -type d \( -name '.flags*' -o -name 'build*' -o -name .p101-script-backups \) -prune \) -o \
    \( -name .git -print0 -prune \)
)
# p101 is a tracked shell entry point without a .sh suffix.
if [[ -f "$workspace/scripts/p101" ]] \
   && git -C "$workspace/scripts" ls-files --error-unmatch p101 >/dev/null 2>&1; then
  scripts+=("$workspace/scripts/p101")
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
