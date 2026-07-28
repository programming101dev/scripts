#!/usr/bin/env bash
# check-cmake-distribution.sh — ensure copied CMakeLists.txt files are current.

set -euo pipefail
CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

source_file="CMakeLists.txt"
repos_file="repos.txt"
failures=0

if [[ ! -f "$source_file" ]]; then
  echo "FAIL: missing source CMakeLists.txt: $source_file" >&2
  exit 1
fi

if [[ ! -f "$repos_file" ]]; then
  echo "FAIL: missing repos list: $repos_file" >&2
  exit 1
fi

while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
  raw="${raw%$'\r'}"
  raw="${raw%%#*}"
  raw="$(printf '%s' "$raw" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [[ -z "$raw" ]] && continue

  IFS='|' read -r _url dest _type <<EOF
$raw
EOF
  [[ -n "${dest:-}" ]] || continue
  [[ "${_type:-}" == "c" || "${_type:-}" == "cxx" ]] || continue

  case "$dest" in
    /*) repo_dir="$dest" ;;
    *) repo_dir="$(CDPATH= cd -- "$dest" 2>/dev/null && pwd -P)" || continue ;;
  esac

  [[ -f "$repo_dir/config.cmake" ]] || continue

  candidate="$repo_dir/CMakeLists.txt"
  if [[ ! -f "$candidate" ]]; then
    echo "FAIL: missing $candidate"
    failures=$((failures + 1))
  elif ! cmp -s "$source_file" "$candidate"; then
    echo "FAIL: stale $candidate"
    failures=$((failures + 1))
  fi
done < "$repos_file"

if [[ "$failures" -gt 0 ]]; then
  echo "CMake distribution check failed: $failures stale/missing copies." >&2
  echo "Run ./copy-cmake.sh and commit the affected repositories." >&2
  exit 1
fi

echo "PASS: distributed CMakeLists.txt files match scripts/CMakeLists.txt."
