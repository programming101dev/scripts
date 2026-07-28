#!/usr/bin/env bash

# Strict mode
set -euo pipefail

# Always operate from the directory this script lives in.
CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"

# Function to display script usage
# detected-compiler helpers (smarter --help; harmless if lists absent)
_p101_names() { [ -f "$1" ] && awk 'NF && $0 !~ /^[[:space:]]*#/ {n=split($0,a,"/"); printf "%s%s",(c++?", ":""),a[n]}' "$1"; }
_p101_cxx_of() { case "$1" in gcc*) printf 'g++%s' "${1#gcc}";; clang*) printf 'clang++%s' "${1#clang}";; *) printf '';; esac; }

usage()
{
    echo "Usage: $0 -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>]"
    echo "  -c c compiler     Specify the c compiler name (e.g. gcc or clang)"
    echo "  -x cxx compiler   Specify the cxx compiler name (e.g. g++ or clang++)"
    echo "  -f clang-format   Specify the clang-format name (e.g. clang-format or clang-format-17)"
    echo "  -t clang-tidy     Specify the clang-tidy name (e.g. clang-tidy or clang-tidy-17)"
    echo "  -k cppcheck       Specify the cppcheck name (e.g. cppcheck)"
    echo "  -s sanitizers     Specify the sanitizers to use (e.g. address,undefined)"
    echo "  --coverage        Opt-in: instrument the initial build for code coverage (gcov)"
    echo "  --profile         Opt-in: instrument the initial build for profiling (gprof)"
    _cc="$(_p101_names supported_c_compilers.txt)"; _cxx="$(_p101_names supported_cxx_compilers.txt)"
    if [ -n "$_cc" ] || [ -n "$_cxx" ]; then
        echo ""
        echo "Compilers detected on this machine (./check-compilers.sh):"
        echo "  C:   ${_cc:-<none>}"
        echo "  C++: ${_cxx:-<none>}"
        _fc="${_cc%%,*}"; _fx="$(_p101_cxx_of "$_fc")"
        if [ -n "$_fc" ] && [ -n "$_fx" ]; then echo "  e.g. $0 -c $_fc -x $_fx"; fi
    else
        echo ""
        echo "  (run ./check-compilers.sh first to detect the compilers installed here)"
    fi
    exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

# Opt-in coverage / profiling are long flags; getopts below only takes short
# ones (and stops at the leading '--'), so pull them out first and export the
# env vars. They propagate into the build this script runs (build-repo.sh ->
# CMake), which instruments compile + link.
_setup_argv=()
for _a in "$@"; do
  case "$_a" in
    --coverage) export P101_COVERAGE=1 ;;
    --profile)  export P101_PROFILE=1 ;;
    *)          _setup_argv+=("$_a") ;;
  esac
done
if ((${#_setup_argv[@]})); then set -- "${_setup_argv[@]}"; else set --; fi
unset _setup_argv _a

# Parse command-line options
while getopts ":c:x:f:t:k:s:" opt; do
  case $opt in
    c)
      c_compiler="$OPTARG"
      ;;
    x)
      cxx_compiler="$OPTARG"
      ;;
    f)
      clang_format_name="$OPTARG"
      ;;
    t)
      clang_tidy_name="$OPTARG"
      ;;
    k)
      cppcheck_name="$OPTARG"
      ;;
    s)
      sanitizers="$OPTARG"
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      usage
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      usage
      ;;
  esac
done

# Check if the compiler argument is provided
if [ -z "$c_compiler" ]; then
  echo "Error: c compiler argument (-c) is required."
  usage
fi

# Check if the compiler argument is provided
if [ -z "$cxx_compiler" ]; then
  echo "Error: cxx compiler argument (-x) is required."
  usage
fi

flags_version="../.flags/version.txt"
current_version="./version.txt"

# Self-update the scripts repo first. pull.sh exits 1 after a successful pull
# to signal "re-run so the new scripts are used" — handle that explicitly
# instead of dying with a generic set -e failure.
pull_rc=0
./pull.sh || pull_rc=$?
if [ "$pull_rc" -eq 1 ]; then
  echo "The scripts repository was just updated. Please re-run: $0 $*" >&2
  exit 1
elif [ "$pull_rc" -ne 0 ]; then
  echo "Error: pull.sh failed (exit $pull_rc)." >&2
  exit "$pull_rc"
fi

./clone-repos.sh
# Discovery runs BEFORE the environment check so keg-only compilers (found
# by check-compilers.sh, absent from PATH) resolve through the map.
./check-compilers.sh

MAP_FILE="compiler_paths.txt"
map_lookup() {
  local name="$1" line
  [ -f "$MAP_FILE" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in "$name="*) printf '%s' "${line#*=}"; return 0 ;; esac
  done < "$MAP_FILE"
  return 1
}

resolve_compiler() {
  # pinned map first, then PATH; absolute paths pass through
  local v="$1" p
  case "$v" in
    /*) [ -x "$v" ] || { echo "Error: '$v' is not executable" >&2; exit 1; }
        printf '%s' "$v"; return ;;
  esac
  if p="$(map_lookup "$v")" && [ -x "$p" ]; then printf '%s' "$p"; return; fi
  if p="$(command -v "$v" 2>/dev/null)"; then printf '%s' "$p"; return; fi
  echo "Error: could not resolve compiler '$v' (not in $MAP_FILE, not in PATH)" >&2
  exit 1
}

CC_PATH="$(resolve_compiler "$c_compiler")"
CXX_PATH="$(resolve_compiler "$cxx_compiler")"

./check-env.sh -c "$CC_PATH" -x "$CXX_PATH" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"

# The supported lists hold full paths (check-compilers.sh pins them).
# Accept a match on exact path, or on basename in either direction, so both
# `-c /usr/bin/clang` and `-c clang` validate against /usr/bin/clang.
in_supported() {
  # $1 = compiler (name or path), $2 = list file
  local needle_full="$1" file="$2" needle_base line line_base
  needle_base="$(basename "$needle_full")"
  [ -f "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    line_base="$(basename "$line")"
    if [ "$line" = "$needle_full" ] || [ "$line_base" = "$needle_base" ]; then
      return 0
    fi
  done < "$file"
  return 1
}

# Ensure the compiler is listed in supported_c_compilers.txt
if ! in_supported "$c_compiler" supported_c_compilers.txt; then
    echo "Error: The specified compiler '$c_compiler' is not in supported_c_compilers.txt."
    echo "Supported compilers:"
    cat supported_c_compilers.txt
    exit 1
fi

# Ensure the C++ compiler is listed in supported_cxx_compilers.txt
if ! in_supported "$cxx_compiler" supported_cxx_compilers.txt; then
    echo "Error: The specified C++ compiler '$cxx_compiler' is not in supported_cxx_compilers.txt."
    echo "Supported C++ compilers:"
    cat supported_cxx_compilers.txt
    exit 1
fi

./generate-flags.sh
./link-flags.sh
./link-compilers.sh
mkdir -p -- "$(dirname -- "$flags_version")"
cp "$current_version" "$flags_version"
./build-repo.sh -c "$CC_PATH" -x "$CXX_PATH" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"
