#!/usr/bin/env bash
# build-repo-top.sh — Orchestrate tool discovery, flag probing, linking, and building all repos

set -euo pipefail

# ----------------- defaults -----------------
c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers="address,leak,pointer_overflow,undefined"
dry_run=false

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

# ----------------- parse args -----------------
# Allow a long option for --dry-run in addition to getopts short flags.
LONG_DRY_RUN=0
args=()
for a in "$@"; do
  if [[ "$a" == "--dry-run" ]]; then
    LONG_DRY_RUN=1
  else
    args+=("$a")
  fi
done
set -- "${args[@]}"

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

# ----------------- helpers -----------------
# Resolve any executable (name or absolute path). No basename policing.
resolve_any_tool() {
  local user_value="$1"
  local path
  if [[ "$user_value" = /* ]]; then
    path="$user_value"
    [[ -x "$path" ]] || { printf "Error: '%s' is not executable\n" "$path" >&2; exit 2; }
  else
    path="$(command -v "$user_value" 2>/dev/null)" \
      || { printf "Error: could not find '%s' in PATH\n" "$user_value" >&2; exit 2; }
  fi
  printf "%s" "$path"
}

# Resolve a clang* tool with optional version suffixes (clang-tidy[-N], clang-format[-N]).
resolve_clang_named_tool() {
  local want_base="$1"   # "clang-tidy" or "clang-format"
  local user_value="$2"
  local path bn
  path="$(resolve_any_tool "$user_value")"
  bn="$(basename "$path")"
  case "$bn" in
    "$want_base"|$want_base-[0-9]*) ;;  # ok
    *)
      printf "Error: resolved '%s' -> '%s' but expected '%s' or '%s-<ver>'\n" \
        "$user_value" "$path" "$want_base" "$want_base" >&2
      exit 2
      ;;
  esac
  printf "%s" "$path"
}

# Print a command safely quoted; run it unless dry-run
run_or_echo() {
  # print quoted
  local q=()
  for a in "$@"; do
    # %q is bash-specific but available on macOS bash 3.2+
    q+=( "$(printf '%q' "$a")" )
  done
  printf '[%s] %s\n' "$([[ $dry_run == true ]] && echo dry-run || echo run)" "${q[*]}"
  # execute
  if ! $dry_run; then
    "$@"
  fi
}

# ----------------- resolve tool paths -----------------
CC_PATH="$(resolve_any_tool "$c_compiler")"
CXX_PATH="$(resolve_any_tool "$cxx_compiler")"
CLANG_FORMAT_PATH="$(resolve_clang_named_tool "clang-format" "$clang_format_name")"
CLANG_TIDY_PATH="$(resolve_clang_named_tool "clang-tidy" "$clang_tidy_name")"
CPPCHECK_PATH="$(resolve_any_tool "$cppcheck_name")"

# ----------------- banner -----------------
printf "Configuring with:\n"
printf "  CC               = %s\n" "$CC_PATH"
printf "  CXX              = %s\n" "$CXX_PATH"
printf "  clang-format     = %s\n" "$CLANG_FORMAT_PATH"
printf "  clang-tidy       = %s\n" "$CLANG_TIDY_PATH"
printf "  cppcheck         = %s\n" "$CPPCHECK_PATH"
printf "  sanitizers       = %s\n" "${sanitizers:-<none>}"
$dry_run && printf "  mode             = DRY RUN\n"

# ----------------- repo prep -----------------
run_or_echo ./pull.sh

# Verify environment tools exist (cmake/compilers/formatters/tidy/cppcheck)
run_or_echo ./check-env.sh \
  -c "$CC_PATH" -x "$CXX_PATH" \
  -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH" \
  -s "$sanitizers"

# Clone or update repos listed in repos.txt
run_or_echo ./clone-repos.sh

# ----------------- flags cache management -----------------
flags_version="../.flags/version.txt"
current_version="./version.txt"
update=false

[[ -f supported_c_compilers.txt && -f supported_cxx_compilers.txt ]] || update=true
if [[ -f "$flags_version" && -f "$current_version" ]]; then
  if ! diff -q "$flags_version" "$current_version" >/dev/null; then
    update=true
  fi
else
  update=true
fi

if $update; then
  run_or_echo ./check-compilers.sh
  run_or_echo ./generate-flags.sh
  if ! $dry_run; then
    mkdir -p "$(dirname "$flags_version")"
    cp "$current_version" "$flags_version"
  else
    printf '[dry-run] cp %q %q\n' "$current_version" "$flags_version"
  fi
fi

# ----------------- sanity: supported compilers lists -----------------
in_supported() {
  local needle_full="$1" needle_base file
  needle_base="$(basename "$needle_full")"
  file="$2"
  [[ -f "$file" ]] || return 1
  grep -Fxq "$needle_full" "$file" && return 0
  grep -Fxq "$needle_base" "$file" && return 0
  return 1
}

if ! in_supported "$CC_PATH" supported_c_compilers.txt; then
  printf "Error: The specified compiler '%s' is not in supported_c_compilers.txt.\n" "$CC_PATH" >&2
  printf "Supported compilers:\n" >&2
  cat supported_c_compilers.txt >&2 || true
  exit 3
fi

if ! in_supported "$CXX_PATH" supported_cxx_compilers.txt; then
  printf "Error: The specified C++ compiler '%s' is not in supported_cxx_compilers.txt.\n" "$CXX_PATH" >&2
  printf "Supported C++ compilers:\n" >&2
  cat supported_cxx_compilers.txt >&2 || true
  exit 3
fi

# ----------------- link discovered flags & compilers into each repo -----------------
run_or_echo ./link-flags.sh
run_or_echo ./link-compilers.sh

# ----------------- build all repos -----------------
run_or_echo ./build-repo.sh \
  -c "$CC_PATH" \
  -x "$CXX_PATH" \
  -f "$CLANG_FORMAT_PATH" \
  -t "$CLANG_TIDY_PATH" \
  -k "$CPPCHECK_PATH" \
  -s "$sanitizers" \
  -S

printf "All done.\n"
