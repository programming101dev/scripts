#!/usr/bin/env bash
# check-p101-tool-contracts.sh — lightweight README contract check for p101 tools.
#
# This is intentionally small and heuristic. It does not prove documentation is
# good; it catches the easy regression where a new p101 tool has no visible
# boundaries, exit behavior, or replayable usage/check evidence.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

programs_dir="../programs"
quiet=0

usage() {
  cat <<'USAGE'
Usage: ./check-p101-tool-contracts.sh [-q] [-p <programs-dir>]

Checks p101-* README files for the lightweight p101 tool contract:
bounded/limited claims, exit status, and replayable usage/check evidence.

Options:
  -p <dir>  Programs directory. Default: ../programs
  -q        Quiet; print only failures.
  -h        Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -p) programs_dir="${2:?}"; shift 2 ;;
    -q) quiet=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ ! -d "$programs_dir" ]; then
  echo "Programs directory not found: $programs_dir" >&2
  exit 2
fi

failed=0
checked=0

has_pattern() {
  file="$1"
  pattern="$2"
  grep -Eiq "$pattern" "$file"
}

for tool_dir in "$programs_dir"/p101-*; do
  [ -d "$tool_dir" ] || continue
  readme="$tool_dir/README.md"
  name="$(basename "$tool_dir")"
  checked=$((checked + 1))

  if [ ! -f "$readme" ]; then
    echo "FAIL: $name has no README.md"
    failed=1
    continue
  fi

  missing=""
  if ! has_pattern "$readme" 'boundar|blind|limit|cannot|can only|not proof|not a proof|heuristic|invisible|outside|not OS tracing'; then
    missing="$missing boundaries"
  fi
  if ! has_pattern "$readme" 'exit status|exit code|returns 0|non-zero|failed|fails'; then
    missing="$missing exit-status"
  fi
  if ! has_pattern "$readme" 'usage|example|check|test|smoke|tour|regression'; then
    missing="$missing receipt"
  fi

  if [ -n "$missing" ]; then
    echo "FAIL: $name missing:$missing"
    failed=1
  elif [ "$quiet" -eq 0 ]; then
    echo "PASS: $name"
  fi
done

if [ "$checked" -eq 0 ]; then
  echo "No p101-* tool directories found under $programs_dir" >&2
  exit 2
fi

if [ "$failed" -ne 0 ]; then
  echo "p101 tool contract check failed"
  exit 1
fi

if [ "$quiet" -eq 0 ]; then
  echo "p101 tool contract check passed ($checked tools)"
fi

