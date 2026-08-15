#!/usr/bin/env bash
# Verify workspace repos consume the shared expensive/generated build inputs.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "check-shared-links.sh — verify shared workspace symlinks."
    exit 0 ;;
esac
[[ "$#" -eq 0 ]] || { echo "Usage: ./check-shared-links.sh" >&2; exit 2; }

failures=0
workspace="$(CDPATH='' cd .. && pwd -P)"

canonical_path() {
  realpath "$1"
}

check_link() {
  link_path="$1"
  expected="$2"
  label="$3"
  if [[ ! -L "$link_path" ]]; then
    echo "FAIL: $label is not a symlink: $link_path" >&2
    failures=$((failures + 1))
    return
  fi
  if [[ ! -e "$link_path" ]]; then
    echo "FAIL: $label is dangling: $link_path -> $(readlink "$link_path")" >&2
    failures=$((failures + 1))
    return
  fi
  actual="$(canonical_path "$link_path")"
  wanted="$(canonical_path "$expected")"
  if [[ "$actual" != "$wanted" ]]; then
    echo "FAIL: $label points to $actual; expected $wanted" >&2
    failures=$((failures + 1))
  fi
}

while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
  raw="${raw%$'\r'}"
  raw="${raw%%#*}"
  [[ -n "${raw//[[:space:]]/}" ]] || continue
  IFS='|' read -r _url dest repo_type <<< "$raw"
  if [[ -z "${dest:-}" || -z "${repo_type:-}" ]]; then
    echo "FAIL: malformed repos.txt line: $raw" >&2
    failures=$((failures + 1))
    continue
  fi
  case "$repo_type" in c|cxx) ;; c-reference|python|c-bootstrap) continue ;; *)
    echo "FAIL: unsupported repo type '$repo_type': $dest" >&2
    failures=$((failures + 1))
    continue ;;
  esac
  if ! repo="$(CDPATH='' cd -- "$dest" 2>/dev/null && pwd -P)"; then
    echo "FAIL: configured repository is missing: $dest" >&2
    failures=$((failures + 1))
    continue
  fi

  check_link "$repo/cmake" "$PWD/cmake" "CMake helpers"
  check_link "$repo/.flags" "$workspace/.flags" "compiler flags"
  check_link "$repo/sanitizers.txt" "$PWD/sanitizers.txt" "sanitizer selection"
  check_link "$repo/compiler_paths.txt" "$PWD/compiler_paths.txt" "compiler path map"
  if [[ "$repo_type" == "cxx" ]]; then
    check_link "$repo/supported_cxx_compilers.txt" "$PWD/supported_cxx_compilers.txt" "C++ compiler list"
  else
    check_link "$repo/supported_c_compilers.txt" "$PWD/supported_c_compilers.txt" "C compiler list"
  fi
done < repos.txt

if [[ "$failures" -gt 0 ]]; then
  echo "Shared-link distribution failed: $failures problem(s)." >&2
  exit 1
fi
echo "PASS: all C/C++ repositories use the canonical shared workspace links."
