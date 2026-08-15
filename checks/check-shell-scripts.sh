#!/usr/bin/env bash
# Syntax-check every maintained shell script and run ShellCheck's warning tier.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "check-shell-scripts.sh — workspace shell syntax and ShellCheck gate." \
      "Usage: ./check-shell-scripts.sh [-j jobs]"
    exit 0 ;;
esac
available_processors() {
  local count=""

  count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
  case "$count" in
    ''|*[!0-9]*|0)
      count="$(sysctl -n hw.logicalcpu 2>/dev/null || true)"
      ;;
  esac
  case "$count" in
    ''|*[!0-9]*|0) count=4 ;;
  esac
  # ShellCheck is CPU-heavy but modestly sized batches stop scaling beyond
  # eight workers on the measured hosts. Respect smaller VM allocations.
  if [ "$count" -gt 8 ]; then
    count=8
  fi
  printf '%s' "$count"
}

jobs="${P101_SHELLCHECK_JOBS:-$(available_processors)}"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -j)
      jobs="${2:?missing job count}"
      shift 2
      ;;
    *)
      echo "Usage: ./check-shell-scripts.sh [-j jobs]" >&2
      exit 2
      ;;
  esac
done
case "$jobs" in
  ''|*[!0-9]*|0)
    echo "FAIL: shell check job count must be a positive integer." >&2
    exit 2
    ;;
esac
command -v shellcheck >/dev/null 2>&1 || {
  echo "FAIL: shellcheck is required for the shell-script gate." >&2
  exit 2
}

workspace="$(CDPATH='' cd .. && pwd -P)"
repos_file="$workspace/scripts/repos.txt"
scripts=()
discovery_failures=0

append_script_if_shell() {
  local candidate="$1"
  local first_line=""

  [[ -f "$candidate" ]] || return 0
  IFS= read -r first_line < "$candidate" || true
  case "$first_line" in
    '#!'*'/sh'|'#!'*'/sh '*|'#!'*'/bash'|'#!'*'/bash '*|'#!'*'/env sh'|'#!'*'/env bash')
      scripts+=("$candidate")
      ;;
  esac
}

append_repository_scripts() {
  local repository_candidate="$1"
  local allow_source_snapshot="${2:-false}"
  local repository_root
  local requested_root
  local relative_path

  requested_root="$(CDPATH='' cd -- "$repository_candidate" 2>/dev/null && pwd -P || true)"
  repository_root="$(git -C "$repository_candidate" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "$requested_root" && -n "$repository_root" && "$requested_root" == "$repository_root" ]]; then
    while IFS= read -r -d '' relative_path; do
      append_script_if_shell "$repository_root/$relative_path"
    done < <(git -C "$repository_root" ls-files -z)
    return
  fi

  # vmactions/freebsd-vm copies the checked-out scripts repository into the VM
  # without its .git directory. Only this owning repository may use snapshot
  # discovery; every manifest repository must still prove its Git boundary.
  if [[ "$allow_source_snapshot" == true \
        && -n "$requested_root" \
        && -f "$requested_root/repos.txt" \
        && -f "$requested_root/check-after-update-all.sh" ]]; then
    while IFS= read -r -d '' relative_path; do
      append_script_if_shell "$relative_path"
    done < <(
      find "$requested_root" \
        \( -type d \( \
          -name .git -o \
          -name .flags -o \
          -name .compiler-links -o \
          -name .p101-script-backups -o \
          -name ci-output -o \
          -name 'build*' \
        \) -prune \) -o \
        \( -type f -perm -u+x -print0 \)
    )
    return
  fi

  if [[ -z "$requested_root" || -z "$repository_root" || "$requested_root" != "$repository_root" ]]; then
    echo "FAIL: configured repository is missing or invalid: $repository_candidate" >&2
    discovery_failures=$((discovery_failures + 1))
    return
  fi
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
append_repository_scripts "$workspace/scripts" true
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

if [[ "$discovery_failures" -gt 0 ]]; then
  echo "Shell-script discovery failed: $discovery_failures problem(s)." >&2
  exit 1
fi
[[ ${#scripts[@]} -gt 0 ]] || { echo "FAIL: no shell scripts found." >&2; exit 1; }

shellcheck_scripts=()
bash_scripts=()
sh_scripts=()
syntax_failures=0
for script in "${scripts[@]}"; do
  first_line=""
  IFS= read -r first_line < "$script" || true
  case "$first_line" in
    *bash*) bash_scripts+=("$script") ;;
    *) sh_scripts+=("$script") ;;
  esac
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

# Each shell accepts multiple input files. Checking each file in a fresh
# interpreter made this gate spend most of its time starting ~1,000 processes.
syntax_status="$syntax_failures"
if [[ "${#bash_scripts[@]}" -gt 0 ]]; then
  bash -n "${bash_scripts[@]}" || syntax_status=1
fi
if [[ "${#sh_scripts[@]}" -gt 0 ]]; then
  sh -n "${sh_scripts[@]}" || syntax_status=1
fi
if [[ "$syntax_status" -ne 0 ]]; then
  echo "Shell syntax check failed." >&2
  exit 1
fi

# Warning severity also catches ignored failures, unsafe globbing, masked
# statuses, and suspicious control flow. Intentional exceptions belong next
# to the relevant line, not in a workspace-wide ignore list.
printf '%s\0' "${shellcheck_scripts[@]}" |
  xargs -0 -n 128 -P "$jobs" shellcheck --severity=warning
printf 'PASS: %d shell scripts passed syntax and ShellCheck warning checks.\n' "${#scripts[@]}"
