#!/usr/bin/env bash
# update.sh — Orchestrate tool discovery, flag probing, linking, and building all repos

# Interactive runs may remain paused while this repository is edited or
# fast-forwarded. Bash reads a script incrementally, so changing the active
# file can otherwise leave the running shell at stale byte offsets and produce
# a spurious parse error after the repository build completes. Re-execute one
# immutable snapshot and retain the original workspace root explicitly.
if [[ -z "${P101_UPDATE_SNAPSHOT:-}" ]]; then
  P101_UPDATE_ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
  P101_UPDATE_SNAPSHOT="$(mktemp "${TMPDIR:-/tmp}/p101-update.XXXXXX")"
  cp -- "${BASH_SOURCE[0]}" "$P101_UPDATE_SNAPSHOT"
  export P101_UPDATE_ROOT P101_UPDATE_SNAPSHOT
  exec bash "$P101_UPDATE_SNAPSHOT" "$@"
fi

cleanup_update_snapshot() {
  rm -f -- "$P101_UPDATE_SNAPSHOT"
}
trap cleanup_update_snapshot EXIT

# Strict mode
set -euo pipefail
IFS=$' \t\n'

# Always operate from the original scripts repository.
CDPATH='' cd -- "$P101_UPDATE_ROOT"
# shellcheck source=../shared/compilers.sh
. ./shared/compilers.sh

# ----------------- globals and defaults -----------------
c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers_default="address,leak,pointer_overflow,undefined"
sanitizers=""
sanitizers_given=false
dry_run=false
no_flags=false
standard=false
skip_install=false
interactive=false
skip_self_update=false
latest=false
format=false
prepare_only=false
build_only=false
finalize_only=false
defer_install=false

# Files and helper scripts expected in the current directory
# CACHE_ROOT / FLAGS_VERSION_FILE are re-pointed for the --standard profile
# after option parsing.
CACHE_ROOT="../.flags"
FLAGS_VERSION_FILE="../.flags/version.txt"
CURRENT_VERSION_FILE="./version.txt"
FORMAT_RECEIPT="${TMPDIR:-/tmp}/p101-format-workspace.json"
SUPPORTED_C_COMPILERS="supported_c_compilers.txt"
SUPPORTED_CXX_COMPILERS="supported_cxx_compilers.txt"
flag_c_list_file="$SUPPORTED_C_COMPILERS"
flag_cxx_list_file="$SUPPORTED_CXX_COMPILERS"

REFRESH_REPO_SH="./distribution/refresh-repo.sh"
CHECK_ENV_SH="./workspace/check-env.sh"
CLONE_REPOS_SH="./distribution/clone-repos.sh"
CHECK_COMPILERS_SH="./workspace/check-compilers.sh"
COMPILER_FINGERPRINT_SH="./workspace/compiler-fingerprint.sh"
GENERATE_FLAGS_SH="./generators/generate-flags.sh"
FILTER_SANITIZERS_SH="./workspace/filter-sanitizers.sh"
LINK_FLAGS_SH="./distribution/link-flags.sh"
LINK_COMPILERS_SH="./distribution/link-compilers.sh"
LINK_CMAKE_SH="./distribution/link-cmake.sh"
BUILD_REPO_SH="./workspace/build-repo.sh"
COPY_SCRIPTS_SH="./distribution/copy-scripts.sh"
COPY_PLAYGROUND_TRACK_SCRIPTS_SH="./distribution/copy-playground-track-scripts.sh"
REMOVE_RETIRED_REPOS_SH="./distribution/remove-retired-repos.sh"

# ----------------- messaging helpers -----------------
die() { printf "Error: %s\n" "$*" >&2; exit 2; }
note() { printf "%s\n" "$*"; }

# ----------------- usage -----------------
# detected-compiler helpers (smarter --help; harmless if lists absent)
_p101_names() { [ -f "$1" ] && awk 'NF && $0 !~ /^[[:space:]]*#/ {n=split($0,a,"/"); printf "%s%s",(c++?", ":""),a[n]}' "$1"; }
_p101_cxx_of() { p101_derive_cxx_name "$1"; }

usage() {
  cat <<'USAGE'
Usage: update.sh -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-C <c-list>] [-X <cxx-list>] [--dry-run]

  -c  C compiler       (e.g. gcc, clang, gcc-16, /opt/llvm/bin/clang)
  -x  C++ compiler     (e.g. g++, clang++, g++-16, /opt/llvm/bin/clang++)
  -f  clang-format     (default: clang-format; accepts absolute path or name)
  -t  clang-tidy       (default: clang-tidy;  accepts absolute path or name)
  -k  cppcheck         (default: cppcheck;    accepts absolute path or name)
  -C  C compiler list  used when flag caches need probing
                       (default: supported_c_compilers.txt)
  -X  C++ compiler list used when flag caches need probing
                       (default: supported_cxx_compilers.txt)
  -s  sanitizers       (default: whatever sanitizers.txt currently holds,
                        falling back to address,leak,pointer_overflow,undefined)
      If empty (e.g. -s ""), downstream decides "no sanitizers".
  --dry-run            Show what would run without executing builds.
  --no-flags           One-off build with NO probed compiler flags and NO
                       sanitizers. Leaves the .flags caches and
                       flag-selection.json untouched; a later run without
                       --no-flags restores full flags with no re-probe.
  --standard           Reasonable safe build: a small curated subset
                       (flag-selection.standard.json — high-signal warnings,
                       -O2, -g, stack protector, linker hardening; no
                       sanitizers), probed into its own .flags-standard
                       cache. Leaves the maximal flags/ + .flags/ untouched.
  --coverage           Opt-in: also instrument every target (libraries and
                       binaries) for code coverage (--coverage / gcov). Off
                       by default. Combines with any other mode.
  --profile            Opt-in: also instrument every target for profiling
                       (-pg / gprof). Off by default. Combines with --coverage.
  --skip-install       Build repositories but do not run their install.sh
                       scripts. Useful for CI and smoke checks.
  --interactive        If a repository configure, build, or install phase
                       fails, pause, pull the pushed fix, and retry that phase.
  --skip-self-update   Do not run refresh-repo.sh. Used by update-all.sh after it has
                       already handled the scripts repository once.
  --latest             Follow moving upstream branches instead of repos.lock.
                       Refresh repos.lock explicitly before strict acceptance.
  --format             Apply clang-format to every tracked workspace source
                       before building, so the format-check gate cannot fail
                       on formatting alone. Modifies tracked files.
  --prepare-only       Refresh and prepare the shared workspace, then stop.
                       Internal matrix phase used by update-all.sh.
  --build-only         Build one compiler pair from an already-prepared
                       workspace. Internal matrix phase used by update-all.sh.
  --finalize-only      Publish the selected host-pair markers and install its
                       existing artifacts. Internal matrix phase.
  --defer-install      Build runtime artifacts but defer their installation
                       until --finalize-only.

Examples:
  ./update.sh -c clang -x clang++
  ./update.sh -c gcc-16 -x g++-16 -f clang-format-20 -t clang-tidy-20
USAGE
  _cc="$(_p101_names supported_c_compilers.txt)"; _cxx="$(_p101_names supported_cxx_compilers.txt)"
  if [ -n "$_cc" ] || [ -n "$_cxx" ]; then
    printf '\nCompilers detected on this machine (./workspace/check-compilers.sh):\n'
    printf '  C:   %s\n' "${_cc:-<none>}"
    printf '  C++: %s\n' "${_cxx:-<none>}"
    _fc="${_cc%%,*}"; _fx="$(_p101_cxx_of "$_fc")"
    if [ -n "$_fc" ] && [ -n "$_fx" ]; then printf '  e.g. %s -c %s -x %s\n' "$0" "$_fc" "$_fx"; fi
  fi
  exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

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
MAP_FILE="compiler_paths.txt"

map_lookup() {
  # $1 = compiler name; prints the pinned path from compiler_paths.txt
  local name="$1" line
  [[ -f "$MAP_FILE" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in "$name="*) printf '%s' "${line#*=}"; return 0 ;; esac
  done < "$MAP_FILE"
  return 1
}

map_is_stale() {
  # stale when absent, or when ANY pinned path no longer executes —
  # e.g. after a package upgrade replaced the binary
  local line p
  [[ -f "$MAP_FILE" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in ''|'#'*) continue ;; esac
    p="${line#*=}"
    [[ -x "$p" ]] || return 0
  done < "$MAP_FILE"
  return 1
}

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

resolve_compiler() {
  # Compiler names resolve through the pinned map first, then PATH; if
  # neither works, re-run discovery once (self-heal) and retry the map.
  local user_value="$1" path
  if path="$(p101_resolve_compiler "$user_value" "$MAP_FILE" 2>/dev/null)"; then
    printf "%s" "$path"
    return
  fi
  note "compiler '$user_value' not in $MAP_FILE or PATH — re-running discovery..." >&2
  "$CHECK_COMPILERS_SH" >&2 || true
  if path="$(p101_resolve_compiler "$user_value" "$MAP_FILE" 2>/dev/null)"; then
    printf "%s" "$path"
    return
  fi
  die "could not resolve compiler '$user_value' (not in $MAP_FILE, not in PATH)"
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
LONG_NO_FLAGS=0
LONG_STANDARD=0
LONG_COVERAGE=0
LONG_PROFILE=0
LONG_SKIP_INSTALL=0
LONG_INTERACTIVE=0
LONG_SKIP_SELF_UPDATE=0
LONG_LATEST=0
LONG_FORMAT=0
LONG_PREPARE_ONLY=0
LONG_BUILD_ONLY=0
LONG_FINALIZE_ONLY=0
LONG_DEFER_INSTALL=0
declare -a _argv=()
for _a in "$@"; do
  if [[ "$_a" == "--dry-run" ]]; then
    LONG_DRY_RUN=1
  elif [[ "$_a" == "--no-flags" ]]; then
    LONG_NO_FLAGS=1
  elif [[ "$_a" == "--standard" ]]; then
    LONG_STANDARD=1
  elif [[ "$_a" == "--coverage" ]]; then
    LONG_COVERAGE=1
  elif [[ "$_a" == "--profile" ]]; then
    LONG_PROFILE=1
  elif [[ "$_a" == "--skip-install" ]]; then
    LONG_SKIP_INSTALL=1
  elif [[ "$_a" == "--interactive" ]]; then
    LONG_INTERACTIVE=1
  elif [[ "$_a" == "--skip-self-update" ]]; then
    LONG_SKIP_SELF_UPDATE=1
  elif [[ "$_a" == "--latest" ]]; then
    LONG_LATEST=1
  elif [[ "$_a" == "--format" ]]; then
    LONG_FORMAT=1
  elif [[ "$_a" == "--prepare-only" ]]; then
    LONG_PREPARE_ONLY=1
  elif [[ "$_a" == "--build-only" ]]; then
    LONG_BUILD_ONLY=1
  elif [[ "$_a" == "--finalize-only" ]]; then
    LONG_FINALIZE_ONLY=1
  elif [[ "$_a" == "--defer-install" ]]; then
    LONG_DEFER_INSTALL=1
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

while getopts ":c:x:f:t:k:s:C:X:" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG"; sanitizers_given=true ;;
    C) flag_c_list_file="$OPTARG" ;;
    X) flag_cxx_list_file="$OPTARG" ;;
    \?|:) usage ;;
  esac
done
shift $((OPTIND-1))
[[ $LONG_DRY_RUN -eq 1 ]] && dry_run=true
[[ $LONG_NO_FLAGS -eq 1 ]] && no_flags=true
[[ $LONG_STANDARD -eq 1 ]] && standard=true
# Opt-in coverage / profiling: exported so the shared CMakeLists (and every
# repo build below) instruments compile + link. Off unless requested. They
# compose with each other and with a normal or sanitizer build.
[[ $LONG_COVERAGE -eq 1 ]] && export P101_COVERAGE=1
[[ $LONG_PROFILE  -eq 1 ]] && export P101_PROFILE=1
[[ $LONG_SKIP_INSTALL -eq 1 ]] && skip_install=true
[[ $LONG_INTERACTIVE -eq 1 ]] && interactive=true
[[ $LONG_SKIP_SELF_UPDATE -eq 1 ]] && skip_self_update=true
[[ $LONG_LATEST -eq 1 ]] && latest=true
[[ $LONG_FORMAT -eq 1 ]] && format=true
[[ $LONG_PREPARE_ONLY -eq 1 ]] && prepare_only=true
[[ $LONG_BUILD_ONLY -eq 1 ]] && build_only=true
[[ $LONG_FINALIZE_ONLY -eq 1 ]] && finalize_only=true
[[ $LONG_DEFER_INSTALL -eq 1 ]] && defer_install=true

mode_count=0
$prepare_only && mode_count=$((mode_count + 1))
$build_only && mode_count=$((mode_count + 1))
$finalize_only && mode_count=$((mode_count + 1))
if ((mode_count > 1)); then
  die "--prepare-only, --build-only, and --finalize-only are mutually exclusive."
fi
if $defer_install && ! $build_only; then
  die "--defer-install requires --build-only."
fi

if $no_flags && $standard; then
  die "--no-flags and --standard are mutually exclusive (one means no flags, the other a fixed standard set)."
fi

# --no-flags: a one-off build with NO probed compiler flags and NO
# sanitizers, WITHOUT disturbing the .flags caches or flag-selection.json.
# We force sanitizers empty and export P101_NO_FLAGS so the shared
# CMakeLists (propagated to each repo below) suppresses everything for this
# configure. Probing is skipped entirely — nothing to probe, nothing to
# restore afterward: a later run without --no-flags returns to full flags.
if $no_flags; then
  sanitizers=""
  sanitizers_given=true
  export P101_NO_FLAGS=1
fi

# --standard: a reasonable safe build — a small curated subset
# (flag-selection.standard.json: high-signal warnings, -O2, -g, stack
# protector, linker hardening; no sanitizers). It lives in its OWN cache
# (.flags-standard/) so the maximal flags/ + .flags/ are never touched;
# switching back to a normal run needs no re-probe of the maximal set.
# P101_FLAGS_PROFILE=standard steers generate-flags.sh, link-flags.sh, and
# the shared CMakeLists to the standard cache.
if $standard; then
  sanitizers=""
  sanitizers_given=true
  export P101_FLAGS_PROFILE=standard
  CACHE_ROOT="../.flags-standard"
  FLAGS_VERSION_FILE="${CACHE_ROOT}/version.txt"
fi

# If -s was not given, respect the current sanitizers.txt (chosen at setup
# time) instead of silently resetting it to the built-in default.
if ! $sanitizers_given; then
  if [[ -f sanitizers.txt ]]; then
    sanitizers="$(head -n 1 sanitizers.txt 2>/dev/null || true)"
  else
    sanitizers="$sanitizers_default"
  fi
fi
requested_sanitizers="$sanitizers"

[[ -n "$c_compiler"   ]] || { printf "Error: -c (C compiler) is required\n" >&2; usage; }
[[ -n "$cxx_compiler" ]] || { printf "Error: -x (C++ compiler) is required\n" >&2; usage; }

# ----------------- sanity: required helper scripts present -----------------
for f in "$REFRESH_REPO_SH" "$CHECK_ENV_SH" "$CLONE_REPOS_SH" "$CHECK_COMPILERS_SH" "$COMPILER_FINGERPRINT_SH" "$GENERATE_FLAGS_SH" "$FILTER_SANITIZERS_SH" "$LINK_FLAGS_SH" "$LINK_COMPILERS_SH" "$LINK_CMAKE_SH" "$BUILD_REPO_SH" "$REMOVE_RETIRED_REPOS_SH"; do
  [[ -x "$f" ]] || die "required helper script missing or not executable: $f"
done

# ----------------- resolve tool paths -----------------
CC_PATH="$(resolve_compiler "$c_compiler")"
CXX_PATH="$(resolve_compiler "$cxx_compiler")"
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
# refresh-repo.sh exits 1 after a successful refresh to signal "re-run so the new
# scripts are used" — handle that explicitly instead of a bare set -e death.
pull_rc=0
if $build_only || $finalize_only || $skip_self_update; then
  :
elif ! $dry_run; then
  "$REFRESH_REPO_SH" --allow-snapshot . || pull_rc=$?
else
  printf '[dry-run] %s --allow-snapshot .\n' "$REFRESH_REPO_SH"
fi
if [[ "$pull_rc" -eq 1 ]]; then
  note "The scripts repository was just updated. Please re-run this command."
  exit 1
elif [[ "$pull_rc" -ne 0 ]]; then
  die "refresh-repo.sh failed (exit $pull_rc)"
fi

# Verify environment tools exist and are usable by downstream. Transient
# profiles must not overwrite the user's durable sanitizer selection.
if ! $finalize_only; then
  check_env_args=(
    -c "$CC_PATH" -x "$CXX_PATH"
    -f "$CLANG_FORMAT_PATH" -t "$CLANG_TIDY_PATH" -k "$CPPCHECK_PATH"
    -s "$requested_sanitizers"
  )
  if $no_flags || $standard || $build_only; then
    check_env_args+=(--no-record)
  fi
  run_or_echo "$CHECK_ENV_SH" "${check_env_args[@]}"
fi

# Clone or update repos listed in repos.txt. Interactive mode applies to this
# phase too: local edits or branch divergence must be resolved by the user, but
# the update can then retry the same repository and continue the matrix.
if ! $build_only && ! $finalize_only; then
  clone_args=()
  if $interactive; then
    clone_args+=(--interactive)
  fi
  if $latest; then
    clone_args+=(--latest)
  fi
  if ((${#clone_args[@]})); then
    run_or_echo "$CLONE_REPOS_SH" "${clone_args[@]}"
  else
    run_or_echo "$CLONE_REPOS_SH"
  fi
  if $interactive; then
    run_or_echo "$REMOVE_RETIRED_REPOS_SH" --apply --yes --interactive
  else
    run_or_echo "$REMOVE_RETIRED_REPOS_SH" --apply --yes
  fi
fi

# ----------------- flags cache management -----------------
if ! $build_only && ! $finalize_only; then
update=false

if [[ ! -f "$SUPPORTED_C_COMPILERS" || ! -f "$SUPPORTED_CXX_COMPILERS" ]]; then
  update=true
fi

# Self-heal: if any pinned compiler path stopped existing (package upgrade,
# uninstall), rediscover and re-probe regardless of version match.
if map_is_stale; then
  note "compiler map is missing or stale — forcing rediscovery and re-probe."
  update=true
fi

# Self-heal: a NEWLY discovered compiler has no probed flag cache yet —
# without this check it would build with zero flags until the next version
# bump. The check requires actual CONTENT, not just the directory: an empty
# .flags/<name>/ dir (interrupted probe, dir created by something else)
# would otherwise satisfy a bare -d test and the compiler would silently
# build with zero flags. Any *.txt result file counts as a cache.
if ! $update; then
  for _list in "$flag_c_list_file" "$flag_cxx_list_file"; do
    [[ -f "$_list" ]] || continue
    while IFS= read -r _name || [[ -n "$_name" ]]; do
      _name="${_name%%#*}"
      _name="${_name#"${_name%%[![:space:]]*}"}"
      _name="${_name%"${_name##*[![:space:]]}"}"
      [[ -z "$_name" ]] && continue
      _cache_name="$(basename "$_name")"
      _have_cache=false
      for _f in "${CACHE_ROOT}/$_cache_name"/*.txt; do
        if [[ -f "$_f" ]]; then
          _have_cache=true
          break
        fi
      done
      if ! $_have_cache; then
        note "compiler '$_name' has no probed flag cache yet — forcing re-probe."
        update=true
        break 2
      fi
      _compiler_path=""
      if [[ "$_name" = /* ]]; then
        _compiler_path="$_name"
      else
        _compiler_path="$(map_lookup "$_name" || true)"
        if [[ -z "$_compiler_path" ]]; then
          _compiler_path="$(command -v "$_name" 2>/dev/null || true)"
        fi
      fi
      if [[ -z "$_compiler_path" ]] ||
         ! "$COMPILER_FINGERPRINT_SH" check "$_compiler_path" \
             "${CACHE_ROOT}/$_cache_name/.compiler-fingerprint"; then
        note "compiler '$_name' does not match its probed flag cache — forcing re-probe."
        update=true
        break 2
      fi
    done < "$_list"
  done
fi

if [[ -f "$FLAGS_VERSION_FILE" && -f "$CURRENT_VERSION_FILE" ]]; then
  if ! diff -q "$FLAGS_VERSION_FILE" "$CURRENT_VERSION_FILE" >/dev/null 2>&1; then
    update=true
  fi
else
  update=true
fi

# --standard: render the standard subset into flags-standard/ before any
# probe decision, so a fresh or edited standard selection is picked up.
if $standard && ! $dry_run; then
  run_or_echo ./generators/render-flags.py --selection flag-selection.standard.json --out flags-standard
fi

# In --no-flags mode there is nothing to probe: the caches are left exactly
# as they are (ignored this build via P101_NO_FLAGS) and no version stamp is
# touched, so a normal run afterward needs no re-probe.
if $no_flags; then
  note "--no-flags: skipping flag probing; building with no compiler flags or sanitizers."
elif $update; then
  run_or_echo "$CHECK_COMPILERS_SH"
  # generate-flags.sh reads P101_FLAGS_PROFILE for the cache profile
  # (standard vs maximal), while -C/-X explicitly constrain which compiler
  # lists are probed for this run.
  run_or_echo "$GENERATE_FLAGS_SH" -C "$flag_c_list_file" -X "$flag_cxx_list_file"
  if ! $dry_run; then
    mkdir -p "$(dirname "$FLAGS_VERSION_FILE")"
    cp "$CURRENT_VERSION_FILE" "$FLAGS_VERSION_FILE"
  else
    printf '[dry-run] cp %q %q\n' "$CURRENT_VERSION_FILE" "$FLAGS_VERSION_FILE"
  fi
fi
fi

# ----------------- sanitizer capability/combination validation -----------------
# Validate the exact probed flags CMake will consume, not the broader source
# candidate list. A sanitizer umbrella can be accepted by the compiler while
# one of its harvested sub-flags is rejected for the current target. Filtering
# before flag probing previously admitted precisely that inconsistent state.
if [[ -n "$sanitizers" ]] && ! $dry_run; then
  sanitizer_cache_dir="${CACHE_ROOT}/$(basename "$CC_PATH")"
  sanitizers="$(
    "$FILTER_SANITIZERS_SH" "$CC_PATH" "$sanitizer_cache_dir" "$sanitizers"
  )"
  if [[ "$sanitizers" != "$requested_sanitizers" ]]; then
    note "  effective sanitizers = ${sanitizers:-<none>}"
  fi
fi

# ----------------- sanity: supported compilers lists -----------------
in_supported() {
  # The supported lists hold compiler names. Older generated lists may hold
  # paths, so accept a match on exact text or basename in either direction.
  local needle_full="$1" file="$2" needle_base line line_base
  needle_base="$(basename "$needle_full")"
  [[ -f "$file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] && continue
    line_base="$(basename "$line")"
    if [[ "$line" == "$needle_full" || "$line_base" == "$needle_base" ]]; then
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
  [[ -f "$MAP_FILE" && -f "$file" ]] || return 1
  needle_real="$(compiler_realpath "$needle")"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ "$line" == *=* ]] || continue
    name="${line%%=*}"
    path="${line#*=}"
    in_supported "$name" "$file" || continue
    path_real="$(compiler_realpath "$path")"
    if [[ "$path" == "$needle" || "$path_real" == "$needle_real" ]]; then
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

# In dry-run mode the lists may not have been (re)generated; don't fail on
# a file that the real run would have produced.
if $finalize_only; then
  :
elif $dry_run && [[ ! -f "$SUPPORTED_C_COMPILERS" || ! -f "$SUPPORTED_CXX_COMPILERS" ]]; then
  note "[dry-run] skipping supported-compiler check (lists not generated yet)"
else
  if ! compiler_supported "$c_compiler" "$CC_PATH" "$SUPPORTED_C_COMPILERS"; then
    printf "Error: The specified compiler '%s' (resolved to '%s') is not in %s.\n" "$c_compiler" "$CC_PATH" "$SUPPORTED_C_COMPILERS" >&2
    printf "Supported compilers:\n" >&2
    { cat "$SUPPORTED_C_COMPILERS" 2>/dev/null || true; } >&2
    exit 3
  fi

  if ! compiler_supported "$cxx_compiler" "$CXX_PATH" "$SUPPORTED_CXX_COMPILERS"; then
    printf "Error: The specified C++ compiler '%s' (resolved to '%s') is not in %s.\n" "$cxx_compiler" "$CXX_PATH" "$SUPPORTED_CXX_COMPILERS" >&2
    printf "Supported C++ compilers:\n" >&2
    { cat "$SUPPORTED_CXX_COMPILERS" 2>/dev/null || true; } >&2
    exit 3
  fi
fi

# The shared CMakeLists is part of the build system contract, not a per-repo
# fork. Refresh it before every build so normal update-all runs pick up fixes
# to tool flags, rpaths, analysis gates, and platform handling immediately.
if ! $build_only && ! $finalize_only; then
if [[ -x ./distribution/copy-cmake.sh ]]; then
  run_or_echo ./distribution/copy-cmake.sh
fi
run_or_echo "$COPY_SCRIPTS_SH" -a
run_or_echo "$COPY_PLAYGROUND_TRACK_SCRIPTS_SH"

# ----------------- link discovered flags & compilers into each repo -----------------
run_or_echo "$LINK_FLAGS_SH"
run_or_echo "$LINK_COMPILERS_SH"
# Symlink the shared cmake/ helpers into each repo so the slimmed CMakeLists
# finds them (single source of truth in scripts/cmake/).
run_or_echo "$LINK_CMAKE_SH"

# ----------------- format all repos -----------------
# Opt-in. clang-format -i over every tracked, non-vendored workspace source, so
# the per-repo format-check gate (a dependency of every build target) cannot
# fail on formatting alone. This modifies tracked files, which is why the
# default build never does it.
if $format; then
  run_or_echo ./checks/format-workspace.py \
    --formatter "$CLANG_FORMAT_PATH" \
    --receipt "$FORMAT_RECEIPT"
fi
fi

if $prepare_only; then
  note "Workspace preparation complete."
  exit 0
fi

# ----------------- build all repos -----------------
build_repo_args=(
  -c "$CC_PATH"
  -x "$CXX_PATH"
  -f "$CLANG_FORMAT_PATH"
  -t "$CLANG_TIDY_PATH"
  -k "$CPPCHECK_PATH"
  -s "$sanitizers"
  -S
)
if $skip_install; then
  build_repo_args+=(-I)
fi
if $interactive; then
  build_repo_args+=(--interactive)
fi
if $defer_install; then
  build_repo_args+=(--defer-install)
fi
if $finalize_only; then
  build_repo_args+=(--finalize-only)
fi
run_or_echo "$BUILD_REPO_SH" "${build_repo_args[@]}"

note "All done."
