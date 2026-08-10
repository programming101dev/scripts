#!/usr/bin/env bash

# Strict mode
set -euo pipefail

# Always operate from the directory this script lives in.
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# shellcheck source=shared/compilers.sh
. ./shared/compilers.sh

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"
interactive=false
latest=false
format=false
format_receipt="${TMPDIR:-/tmp}/p101-format-workspace.json"

# Function to display script usage
# detected-compiler helpers (smarter --help; harmless if lists absent)
_p101_names() {
    if [ -f "$1" ]; then
        awk 'NF && $0 !~ /^[[:space:]]*#/ {n=split($0,a,"/"); printf "%s%s",(c++?", ":""),a[n]}' "$1"
    fi
    return 0
}
_p101_cxx_of() { p101_derive_cxx_name "$1"; }

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
    echo "  --interactive     Pause, pull the pushed fix, and retry the failed phase"
    echo "  --latest          Follow moving upstream branches instead of repos.lock"
    echo "  --format          Apply clang-format to every tracked workspace source"
    echo "                    before building; modifies tracked files"
    _cc="$(_p101_names supported_c_compilers.txt)"; _cxx="$(_p101_names supported_cxx_compilers.txt)"
    if [ -n "$_cc" ] || [ -n "$_cxx" ]; then
        echo ""
        echo "Compilers detected on this machine (./workspace/check-compilers.sh):"
        echo "  C:   ${_cc:-<none>}"
        echo "  C++: ${_cxx:-<none>}"
        _fc="${_cc%%,*}"; _fx="$(_p101_cxx_of "$_fc")"
        if [ -n "$_fc" ] && [ -n "$_fx" ]; then echo "  e.g. $0 -c $_fc -x $_fx"; fi
    else
        echo ""
        echo "  (run ./workspace/check-compilers.sh first to detect the compilers installed here)"
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
    --interactive) interactive=true ;;
    --latest) latest=true ;;
    --format) format=true ;;
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

# Self-update the scripts repo first. refresh-repo.sh exits 1 after a successful refresh
# to signal "re-run so the new scripts are used" — handle that explicitly
# instead of dying with a generic set -e failure.
pull_rc=0
./distribution/refresh-repo.sh --allow-snapshot . || pull_rc=$?
if [ "$pull_rc" -eq 1 ]; then
  echo "The scripts repository was just updated. Please re-run: $0 $*" >&2
  exit 1
elif [ "$pull_rc" -ne 0 ]; then
  echo "Error: refresh-repo.sh failed (exit $pull_rc)." >&2
  exit "$pull_rc"
fi

clone_args=()
if $interactive; then
  clone_args+=(--interactive)
fi
if $latest; then
  clone_args+=(--latest)
fi
if ((${#clone_args[@]})); then
  ./distribution/clone-repos.sh "${clone_args[@]}"
else
  ./distribution/clone-repos.sh
fi
# Discovery runs BEFORE the environment check so keg-only compilers (found
# by check-compilers.sh, absent from PATH) resolve through the map.
./workspace/check-compilers.sh

MAP_FILE="compiler_paths.txt"
CC_PATH="$(p101_resolve_compiler "$c_compiler" "$MAP_FILE")"
CXX_PATH="$(p101_resolve_compiler "$cxx_compiler" "$MAP_FILE")"

./workspace/check-env.sh -c "$CC_PATH" -x "$CXX_PATH" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers"

in_supported() {
  # The supported lists hold compiler names. Older generated lists may hold
  # paths, so accept a match on exact text or basename in either direction.
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

compiler_realpath() {
  local compiler="$1"
  if command -v realpath >/dev/null 2>&1; then
    realpath "$compiler" 2>/dev/null || printf '%s' "$compiler"
  else
    printf '%s' "$compiler"
  fi
}

compiler_path_has_supported_alias() {
  # $1 = compiler path, $2 = supported compiler list
  local needle="$1" file="$2" needle_real line name path path_real
  [ -f "$MAP_FILE" ] && [ -f "$file" ] || return 1
  needle_real="$(compiler_realpath "$needle")"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac
    name="${line%%=*}"
    path="${line#*=}"
    in_supported "$name" "$file" || continue
    path_real="$(compiler_realpath "$path")"
    if [ "$path" = "$needle" ] || [ "$path_real" = "$needle_real" ]; then
      return 0
    fi
  done < "$MAP_FILE"
  return 1
}

compiler_supported() {
  # $1 = requested compiler (name/path), $2 = resolved path, $3 = list file
  local requested="$1" resolved="$2" file="$3"
  in_supported "$requested" "$file" \
    || in_supported "$resolved" "$file" \
    || compiler_path_has_supported_alias "$requested" "$file" \
    || compiler_path_has_supported_alias "$resolved" "$file"
}

# Ensure the compiler is listed in supported_c_compilers.txt
if ! compiler_supported "$c_compiler" "$CC_PATH" supported_c_compilers.txt; then
    echo "Error: The specified compiler '$c_compiler' (resolved to '$CC_PATH') is not in supported_c_compilers.txt."
    echo "Supported compilers:"
    cat supported_c_compilers.txt
    exit 1
fi

# Ensure the C++ compiler is listed in supported_cxx_compilers.txt
if ! compiler_supported "$cxx_compiler" "$CXX_PATH" supported_cxx_compilers.txt; then
    echo "Error: The specified C++ compiler '$cxx_compiler' (resolved to '$CXX_PATH') is not in supported_cxx_compilers.txt."
    echo "Supported C++ compilers:"
    cat supported_cxx_compilers.txt
    exit 1
fi

./generators/generate-flags.sh
./distribution/link-flags.sh
./distribution/link-compilers.sh
mkdir -p -- "$(dirname -- "$flags_version")"
cp "$current_version" "$flags_version"
# Opt-in. clang-format -i over every tracked, non-vendored workspace source, so
# the per-repo format-check gate (a dependency of every build target) cannot
# fail on formatting alone. This modifies tracked files, which is why the
# default setup never does it.
if $format; then
  ./checks/format-workspace.py --formatter "$clang_format_name" --receipt "$format_receipt"
fi

build_repo_args=(-c "$CC_PATH" -x "$CXX_PATH" -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" -s "$sanitizers")
if $interactive; then
  build_repo_args+=(--interactive)
fi
./workspace/build-repo.sh "${build_repo_args[@]}"
