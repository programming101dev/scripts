#!/usr/bin/env bash
# build-repo-top.sh — Orchestrate tool discovery, flag probing, linking, and building all repos

# Strict mode
set -euo pipefail
IFS=$' \t\n'

# ----------------- globals and defaults -----------------
c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"
dry_run=false

# Files and helper scripts expected in the current directory
FLAGS_VERSION_FILE="../.flags/version.txt"
CURRENT_VERSION_FILE="./version.txt"
SUPPORTED_C_COMPILERS="supported_c_compilers.txt"
SUPPORTED_CXX_COMPILERS="supported_cxx_compilers.txt"

PULL_SH="./pull.sh"
CHECK_ENV_SH="./check-env.sh"
CLONE_REPOS_SH="./clone-repos.sh"
CHECK_COMPILERS_SH="./check-compilers.sh"
GENERATE_FLAGS_SH="./generate-flags.sh"
LINK_FLAGS_SH="./link-flags.sh"
LINK_COMPILERS_SH="./link-compilers.sh"
BUILD_REPO_SH="./build-repo.sh"

# ----------------- messaging helpers -----------------
die() { printf "Error: %s\n" "$*" >&2; exit 2; }
note() { printf "%s\n" "$*"; }

# ----------------- usage -----------------
usage() {
  cat <<'USAGE'
Usage: build-repo-top.sh -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [--dry-run]

  -c  C compiler       (e.g. gcc, clang, gcc-15, /opt/llvm/bin/clang)
  -x  C++ compiler     (e.g. g++, clang++, g++-15, /opt/llvm/bin/clang++)
  -f  clang-format     (default: clang-format; accepts absolute path or name)
  -t  clang-tidy       (default: clang-tidy;  accepts absolute path or name)
  -k  cppcheck         (default: cppcheck;    accepts absolute path or name)
  -s  sanitizers       (default: address,leak,pointer_overflow,undefined)
      If empty (e.g. -s ""), downstream decides "no sanitizers".
  --dry-run            Show what would run without executing builds.

Examples:
  ./build-repo-top.sh -c clang -x clang++
  ./build-repo-top.sh -c gcc-15 -x g++-15 -f clang-format-18 -t clang-tidy-18
USAGE
  exit 1
}

# ----------------- command runner -----------------
run_or_echo() {
  # Print a safely-quoted command and run it unless dry_run
  local q=() a
  for a in "$@"; do q+=( "$(printf '%q' "$a")" ); done
  printf '[%s] %s\n' "$([[ $dry_run == true ]] && echo dry-run || echo run)" "${q[*]}"
  if ! $dry_run; then
    "$@"
  fi
}

# ----------------- tool resolution -----------------
resolve_any_tool() {
  # Input is either an absolute path or a bare name, output is an absolute, executable path
  local user_value="$1" path
  if [[ "$user_value" = /* ]]; then
    path="$user_value"
    [[ -x "$path" ]] || die "'$path' is not executable"
  else
    path="$(command -v "$user_value" 2>/dev/null)" || die "could not find '$user_value' in PATH"
  fi
  printf "%s" "$path"
}

resolve_clang_named_tool() {
  # Accepts clang-format or clang-tidy with optional -<N> suffix
  local want_base="$1" user_value="$2" path bn
  path="$(resolve_any_tool "$user_value")"
  bn="$(basename "$path")"
  case "$bn" in
    "$want_base"|$want_base-[0-9]*) ;;  # ok
    *)
      die "resolved '$user_value' -> '$path' but expected '$want_base' or '$want_base-<ver>'"
      ;;
  esac
  printf "%s" "$path"
}

# ----------------- argument parsing -----------------
# Accept a long --dry-run in addition to short flags.
LONG_DRY_RUN=0
declare -a _argv=()
for _a in "$@"; do
  if [[ "$_a" == "--dry-run" ]]; then
    LONG_DRY_RUN=1
  else
    _argv+=("$_a")
  fi
done
if ((${#_argv[@]})); then
  set -- "${_argv[@]}"
else
  set --
fi
unset _argv _a

while getopts ":c:x:f:t:k:s:" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG" ;;
    \?|:) usage ;;
  esac
done
shift $((OPTIND-1))
[[ $LONG_DRY_RUN -eq 1 ]] && dry_run=true

[[ -n "$c_compiler"   ]] || { printf "Error: -c (C compiler) is required\n" >&2; usage; }
[[ -n "$cxx_compiler" ]] || { printf "Error: -x (C++ compiler) is required\n" >&2; usage; }

# ----------------- sanity: required helper scripts present -----------------
for f in "$PULL_SH" "$CHECK_ENV_SH" "$CLONE_REPOS_SH" "$CHECK_COMPILERS_SH" "$GENERATE_FLAGS_SH" "$LINK_FLAGS_SH" "$LINK_COMPILERS_SH" "$BUILD_REPO_SH"; do
  [[ -x "$f" ]] || die "required helper script missing or not executable: $f"
done

# ----------------- resolve tool paths -----------------
CC_PATH="$(resolve_any_tool "$c_compiler")"
CXX_PATH="$(resolve_any_tool "$cxx_compiler")"
CLANG_FORMAT_PATH="$(resolve_clang_named_tool "clang-format" "$clang_format_name")"
CLANG_TIDY_PATH="$(resolve_clang_named_tool "clang-tidy" "$clang_tidy_name")"
CPPCHECK_PATH="$(resolve_any_tool "$cppcheck_name")"

# ----------------- banner -----------------
note "Configuring with:"
note "  CC               = $CC_PATH"
note "  CXX              = $CXX_PATH"
note "  clang-format     = $CLANG_FORMAT_PATH"
note "  clang-tidy       = $CLANG_TIDY_PATH"
note "  cppcheck         = $CPPCHECK_PATH"
note "  sanitizers       = ${sanitizers:-<none>}"
$dry_run && note "  mode             = DRY RUN"

# ----------------- repo prep -----------------
run_or_echo "$PULL_SH"

# Verify environment tools exist and are usable by downstream
run_or_echo "$CHECK_ENV_SH" \
  -c "$CC_PATH" -x "$CXX_PATH" \
  -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH" \
  -s "$sanitizers"

# Clone or update repos listed in repos.txt
run_or_echo "$CLONE_REPOS_SH"

# ----------------- flags cache management -----------------
update=false

if [[ ! -f "$SUPPORTED_C_COMPILERS" || ! -f "$SUPPORTED_CXX_COMPILERS" ]]; then
  update=true
fi

if [[ -f "$FLAGS_VERSION_FILE" && -f "$CURRENT_VERSION_FILE" ]]; then
  if ! diff -q "$FLAGS_VERSION_FILE" "$CURRENT_VERSION_FILE" >/dev/null 2>&1; then
    update=true
  fi
else
  update=true
fi

if $update; then
  run_or_echo "$CHECK_COMPILERS_SH"
  run_or_echo "$GENERATE_FLAGS_SH"
  if ! $dry_run; then
    mkdir -p "$(dirname "$FLAGS_VERSION_FILE")"
    cp "$CURRENT_VERSION_FILE" "$FLAGS_VERSION_FILE"
  else
    printf '[dry-run] cp %q %q\n' "$CURRENT_VERSION_FILE" "$FLAGS_VERSION_FILE"
  fi
fi

# ----------------- sanity: supported compilers lists -----------------
in_supported() {
  # Accept either full path or basename in the supported list
  local needle_full="$1" file="$2" needle_base
  needle_base="$(basename "$needle_full")"
  [[ -f "$file" ]] || return 1
  if grep -Fxq "$needle_full" "$file"; then
    return 0
  fi
  if grep -Fxq "$needle_base" "$file"; then
    return 0
  fi
  return 1
}

if ! in_supported "$CC_PATH" "$SUPPORTED_C_COMPILERS"; then
  printf "Error: The specified compiler '%s' is not in %s.\n" "$CC_PATH" "$SUPPORTED_C_COMPILERS" >&2
  printf "Supported compilers:\n" >&2
  { cat "$SUPPORTED_C_COMPILERS" 2>/dev/null || true; } >&2
  exit 3
fi

if ! in_supported "$CXX_PATH" "$SUPPORTED_CXX_COMPILERS"; then
  printf "Error: The specified C++ compiler '%s' is not in %s.\n" "$CXX_PATH" "$SUPPORTED_CXX_COMPILERS" >&2
  printf "Supported C++ compilers:\n" >&2
  { cat "$SUPPORTED_CXX_COMPILERS" 2>/dev/null || true; } >&2
  exit 3
fi

# ----------------- link discovered flags & compilers into each repo -----------------
run_or_echo "$LINK_FLAGS_SH"
run_or_echo "$LINK_COMPILERS_SH"

# ----------------- build all repos -----------------
run_or_echo "$BUILD_REPO_SH" \
  -c "$CC_PATH" \
  -x "$CXX_PATH" \
  -f "$CLANG_FORMAT_PATH" \
  -t "$CLANG_TIDY_PATH" \
  -k "$CPPCHECK_PATH" \
  -s "$sanitizers" \
  -S

note "All done."
