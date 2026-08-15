#!/usr/bin/env bash
# Remove governed transient build, test, analysis, and dependency artifacts.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

mode=""

usage() {
  cat <<'EOF'
Usage: ./workspace/clean-workspace.sh --dry-run|--all

Inspect every present repository in repos.txt plus scripts itself and the
explicit workspace-root transient allowlist. The command refuses the whole
operation if a repository is dirty. Repository candidate discovery admits only
ignored, untracked paths. --dry-run prints the exact removal set; --all removes
that set and leaves source files, Git metadata, and installed files untouched.
EOF
}

while (($# > 0)); do
  case "$1" in
    --dry-run|--all)
      [[ -z "$mode" ]] || {
        printf 'Error: choose exactly one of --dry-run or --all\n' >&2
        exit 2
      }
      mode="$1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ -n "$mode" ]] || {
  printf 'Error: choose --dry-run or --all\n' >&2
  usage >&2
  exit 2
}
[[ -f repos.txt ]] || { printf 'Error: repos.txt is missing\n' >&2; exit 2; }

scripts_root="$(pwd -P)"
workspace_root="$(CDPATH='' cd .. && pwd -P)"
repositories=("$scripts_root")
missing=0

trim() {
  local value="${1-}"
  value="${value#"${value%%[![:space:]]*}"}"
  printf '%s' "${value%"${value##*[![:space:]]}"}"
}

while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
  raw="${raw%$'\r'}"
  raw="${raw%%#*}"
  line="$(trim "$raw")"
  [[ -n "$line" ]] || continue
  IFS='|' read -r _url repository _kind <<< "$line"
  repository="$(trim "${repository:-}")"
  [[ -n "$repository" ]] || continue
  if [[ ! -d "$repository" ]]; then
    printf 'SKIP missing repository: %s\n' "$repository"
    missing=$((missing + 1))
    continue
  fi
  repository="$(CDPATH='' cd -- "$repository" && pwd -P)"
  case "$repository" in
    "$workspace_root"/*) ;;
    *)
      printf 'Error: repository escapes workspace: %s\n' "$repository" >&2
      exit 2
      ;;
  esac
  repositories+=("$repository")
done < repos.txt

failures=0
for repository in "${repositories[@]}"; do
  git_root="$(git -C "$repository" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ "$git_root" != "$repository" ]]; then
    printf 'REFUSED invalid repository boundary: %s\n' "$repository" >&2
    failures=$((failures + 1))
    continue
  fi
  status="$(git -C "$repository" status --porcelain --untracked-files=normal)"
  if [[ -n "$status" ]]; then
    printf 'REFUSED dirty repository: %s\n%s\n' "$repository" "$status" >&2
    failures=$((failures + 1))
  fi
done
if ((failures > 0)); then
  printf 'Workspace cleanup refused: %d repository safety failure(s).\n' "$failures" >&2
  exit 1
fi

state="$(mktemp -d "${TMPDIR:-/tmp}/p101-workspace-clean.XXXXXX")"
trap 'rm -rf -- "$state"' EXIT
candidates="$state/candidates"
: > "$candidates"

append_candidate() {
  local repository="$1"
  local candidate="$2"
  local relative

  [[ -e "$candidate" || -L "$candidate" ]] || return 0
  relative="${candidate#"$repository"/}"
  [[ "$relative" != "$candidate" && -n "$relative" ]] || {
    printf 'REFUSED unsafe cleanup path: %s\n' "$candidate" >&2
    failures=$((failures + 1))
    return 0
  }
  if [[ -n "$(git -C "$repository" ls-files -- "$relative")" ]]; then
    return 0
  fi
  if ! git -C "$repository" check-ignore --quiet --no-index -- "$relative"; then
    return 0
  fi
  printf '%s\0' "$candidate" >> "$candidates"
}

append_workspace_candidate() {
  local candidate="$1"

  [[ -e "$candidate" || -L "$candidate" ]] || return 0
  case "$candidate" in
    "$workspace_root/.flags"|"$workspace_root/.coverage"|\
    "$workspace_root/compile_commands.json"|\
    "$workspace_root"/.p101-audit-debug*)
      printf '%s\0' "$candidate" >> "$candidates"
      ;;
    *)
      printf 'REFUSED unsafe workspace-root cleanup path: %s\n' "$candidate" >&2
      failures=$((failures + 1))
      ;;
  esac
}

for repository in "${repositories[@]}"; do
  while IFS= read -r -d '' candidate; do
    append_candidate "$repository" "$candidate"
  done < <(
    find -P "$repository" \
      -path "$repository/.git" -prune -o \
      -path "$repository/target" -prune -o \
      \( \
        -name build -o -name 'build-*' -o -name 'cmake-build-*' -o \
        -name _deps -o -name '*.pyc' -o -name '*.pyo' -o \
        -name .flags -o -name .facts-cache -o -name .compiler-links -o \
        -name .p101-script-backups -o \
        -name debug -o -name 'debug-*' -o \
        -name coverage -o -name 'coverage-*' -o -name coverage_report -o \
        -name profile -o -name 'profile-*' -o \
        -name flag_report -o -name toolchain-report -o \
        -name findings -o -name artifacts -o \
        -name CMakeFiles -o -name CMakeScripts -o -name Testing -o \
        -name CMakeCache.txt -o -name cmake_install.cmake -o \
        -name install_manifest.txt -o -name CTestTestfile.cmake -o \
        -name Makefile -o -name makefile -o \
        -name '*.d' -o -name '*.o' -o -name '*.obj' -o \
        -name '*.a' -o -name '*.so' -o -name '*.so.*' -o \
        -name '*.dylib' -o -name '*.exe' -o -name '*.out' -o \
        -name '*.dSYM' -o -name '*.su' -o \
        -name '*.gcno' -o -name '*.gcda' -o -name '*.gcov' -o \
        -name coverage.info -o \
        -name main -o -name client -o -name server -o \
        -name '*-traceable' -o \
        -name compile_commands.json -o \
        -name .last-build-dir -o -name .last-runtime-build-dir \
      \) -prune -print0
  )

  if [[ "$repository" == "$scripts_root" && -d "$repository/target" ]]; then
    while IFS= read -r -d '' candidate; do
      append_candidate "$repository" "$candidate"
    done < <(
      find -P "$repository/target" -mindepth 1 -maxdepth 1 \
        ! -name .gitignore -print0
    )
  fi
done

append_workspace_candidate "$workspace_root/.flags"
append_workspace_candidate "$workspace_root/.coverage"
append_workspace_candidate "$workspace_root/compile_commands.json"
while IFS= read -r -d '' candidate; do
  append_workspace_candidate "$candidate"
done < <(
  find -P "$workspace_root" -mindepth 1 -maxdepth 1 \
    -name '.p101-audit-debug*' -print0
)

if ((failures > 0)); then
  printf 'Workspace cleanup refused: %d candidate safety failure(s).\n' "$failures" >&2
  exit 1
fi

candidate_count=0
while IFS= read -r -d '' candidate; do
  candidate_count=$((candidate_count + 1))
  if [[ "$mode" == "--dry-run" ]]; then
    printf 'WOULD REMOVE: %s\n' "$candidate"
  else
    printf 'REMOVE: %s\n' "$candidate"
    rm -rf -- "$candidate"
  fi
done < "$candidates"

if [[ "$mode" == "--dry-run" ]]; then
  printf 'Workspace cleanup dry run: %d path(s), %d missing repository/repositories.\n' \
    "$candidate_count" "$missing"
else
  printf 'Workspace cleanup complete: %d path(s), %d missing repository/repositories.\n' \
    "$candidate_count" "$missing"
fi
