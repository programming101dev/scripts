#!/usr/bin/env bash
# Compile every maintained header alone in a fresh translation unit so each
# public and internal interface is proven to include what it uses.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

usage() {
  printf '%s\n' "check-p101-header-standalone.sh — every header compiles on its own." \
    "Usage: ./checks/check-p101-header-standalone.sh -c <cc> [-o <dir>]"
}

case " $* " in
  *" --help "*|*" -h "*)
    usage
    exit 0 ;;
esac

cc_path=""
out_dir=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -c)
      cc_path="${2:?missing compiler}"
      shift 2
      ;;
    -o)
      out_dir="${2:?missing output directory}"
      shift 2
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done
[ -n "$cc_path" ] || {
  echo "FAIL: -c <cc> is required." >&2
  exit 2
}
command -v "$cc_path" >/dev/null 2>&1 || [ -x "$cc_path" ] || {
  echo "FAIL: compiler is not executable: $cc_path" >&2
  exit 2
}
[ -n "$out_dir" ] || out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-header-standalone.XXXXXX")"
mkdir -p "$out_dir"

workspace="$(CDPATH='' cd .. && pwd -P)"
report="$out_dir/header-standalone.md"
tu="$out_dir/standalone-tu.c"

include_args=()
for root in "$workspace"/libraries/lib_*/include; do
  [ -d "$root" ] && include_args+=("-I$root")
done
[ "${#include_args[@]}" -gt 0 ] || {
  echo "FAIL: no library include roots found under $workspace." >&2
  exit 2
}

flags=(-std=c17 -fsyntax-only -Wall -Wextra -Werror -D_POSIX_C_SOURCE=200809L -D_XOPEN_SOURCE=700)
case "$(uname -s)" in
  Darwin)
    flags+=(-D_DARWIN_C_SOURCE)
    if command -v xcrun >/dev/null 2>&1; then
      sdk_path="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
      if [ -n "$sdk_path" ] && [ -d "$sdk_path" ]; then
        flags+=(-isysroot "$sdk_path")
      fi
    fi
    ;;
  *)
    :
    ;;
esac

total=0
failures=0
{
  printf '# p101 header standalone report\n\n'
  printf 'Each header is compiled alone in a fresh translation unit.\n\n'
} > "$report"

check_header() {
  local header=$1
  local relative=$2
  shift 2
  total=$((total + 1))
  printf '#include <%s>\n\nint p101_header_standalone_marker;\n' "$relative" > "$tu"
  if ! "$cc_path" "${flags[@]}" "$@" "${include_args[@]}" "$tu" 2> "$out_dir/header-error.txt"; then
    failures=$((failures + 1))
    {
      printf -- '- FAIL `%s`\n\n' "$header"
      printf '```\n'
      cat "$out_dir/header-error.txt"
      printf '```\n\n'
    } >> "$report"
    printf 'FAIL %s\n' "$header" >&2
  fi
}

while IFS= read -r header; do
  root="${header%"${header#*/include/}"}"
  root="${root%/}"
  check_header "$header" "${header#"$root"/}"
done < <(find "$workspace"/libraries/lib_*/include -name '*.h' | sort)

for tool_dir in "$workspace"/programs/p101-*; do
  [ -d "$tool_dir/include" ] || continue
  while IFS= read -r header; do
    check_header "$header" "${header#"$tool_dir"/include/}" "-I$tool_dir/include" "-I$tool_dir/src"
  done < <(find "$tool_dir/include" -name '*.h' | sort)
done

for src_dir in "$workspace"/libraries/lib_*/src "$workspace"/programs/p101-*/src; do
  [ -d "$src_dir" ] || continue
  parent="$(dirname "$src_dir")"
  while IFS= read -r header; do
    check_header "$header" "${header#"$src_dir"/}" "-I$src_dir" "-I$parent/include"
  done < <(find "$src_dir" -name '*.h' -not -path '*/design/*' -not -path '*/unity/*' | sort)
done

printf '\nHeaders checked: %d\nFailures: %d\n' "$total" "$failures" >> "$report"
printf 'Headers checked: %d\n' "$total"
printf 'Report: %s\n' "$report"
if [ "$failures" -gt 0 ]; then
  printf 'FAIL: %d header(s) do not compile on their own.\n' "$failures" >&2
  exit 1
fi
printf 'PASS: every maintained header stands alone.\n'
