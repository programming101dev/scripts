#!/usr/bin/env bash
set -euo pipefail

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""

usage() {
  cat <<'USAGE'
Usage: check-env.sh -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-h]
  -c <cc>          C compiler (e.g. gcc, clang, gcc-15)
  -x <cxx>         C++ compiler (e.g. g++, clang++, g++-15)
  -f <name>        clang-format executable name [default: clang-format]
  -t <name>        clang-tidy executable name   [default: clang-tidy]
  -k <name>        cppcheck executable name     [default: cppcheck]
  -s <list>        sanitizers (comma-separated, optional; e.g. address,undefined)
  -h               show this help and exit
Exit status: number of missing/invalid tools (0 means all good).
USAGE
}

# Parse options
while getopts ":c:x:f:t:k:s:h" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    h) usage; exit 0 ;;
    \?) echo "Invalid option: -$OPTARG" >&2; usage; exit 2 ;;
    :)  echo "Option -$OPTARG requires an argument." >&2; usage; exit 2 ;;
  esac
done

# Required args
if [[ -z "$c_compiler" ]]; then
  echo "Error: -c <C compiler> is required." >&2
  usage; exit 2
fi
if [[ -z "$cxx_compiler" ]]; then
  echo "Error: -x <C++ compiler> is required." >&2
  usage; exit 2
fi

# Helpers
have() { command -v "$1" >/dev/null 2>&1; }

compile_test() {
  # compile_test <compiler> <lang>
  local cc="$1" lang="$2"
  local tmpdir src exe
  tmpdir="$(mktemp -d 2>/dev/null || mktemp -d -t ccprobe)"
  src="$tmpdir/t.$lang"
  exe="$tmpdir/a.out"
  if [[ "$lang" == "c" ]]; then
    printf 'int main(void){return 0;}\n' >"$src"
  else
    printf 'int main(){return 0;}\n' >"$src"
  fi
  if "$cc" -x "$lang" "$src" -o "$exe" >/dev/null 2>&1; then
    rm -rf "$tmpdir"
    return 0
  else
    rm -rf "$tmpdir"
    return 1
  fi
}

# Build the unique tools list, preserving order
declare -a tools=()
append_unique() {
  local x
  for x in "$@"; do
    [[ -z "$x" ]] && continue
    local seen=0
    local y
    for y in "${tools[@]:-}"; do
      if [[ "$y" == "$x" ]]; then seen=1; break; fi
    done
    (( seen == 0 )) && tools+=("$x")
  done
}
append_unique "cmake" "$c_compiler" "$cxx_compiler" "$clang_format_name" "$clang_tidy_name" "$cppcheck_name"

missing=0

# Simple presence checks
for t in "${tools[@]}"; do
  if ! have "$t"; then
    echo "missing: $t"
    ((missing++))
  fi
done

# Compiler sanity checks only if present
if have "$c_compiler"; then
  if ! compile_test "$c_compiler" "c"; then
    echo "broken: $c_compiler (cannot compile a trivial C program)"
    ((missing++))
  fi
fi
if have "$cxx_compiler"; then
  if ! compile_test "$cxx_compiler" "c++"; then
    echo "broken: $cxx_compiler (cannot compile a trivial C++ program)"
    ((missing++))
  fi
fi

# Optional: just record sanitizers, do not validate here.
if [[ -n "${sanitizers:-}" ]]; then
  printf '%s\n' "$sanitizers" > sanitizers.txt
fi

# Summary and exit code equals number of missing/broken items
if (( missing == 0 )); then
  echo "All required tools OK."
else
  echo "Total missing/broken tools: $missing"
fi

exit "$missing"
