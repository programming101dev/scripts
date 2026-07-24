#!/usr/bin/env bash
# update.sh — Orchestrate tool discovery, flag probing, linking, and building all repos

# Strict mode
set -euo pipefail
IFS=$' \t\n'

# Always operate from the directory this script lives in.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

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

# Files and helper scripts expected in the current directory
# CACHE_ROOT / FLAGS_VERSION_FILE are re-pointed for the --standard profile
# after option parsing.
CACHE_ROOT="../.flags"
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
LINK_CMAKE_SH="./link-cmake.sh"
BUILD_REPO_SH="./build-repo.sh"

# ----------------- messaging helpers -----------------
die() { printf "Error: %s\n" "$*" >&2; exit 2; }
note() { printf "%s\n" "$*"; }

# ----------------- usage -----------------
# detected-compiler helpers (smarter --help; harmless if lists absent)
_p101_names() { [ -f "$1" ] && awk 'NF && $0 !~ /^[[:space:]]*#/ {n=split($0,a,"/"); printf "%s%s",(c++?", ":""),a[n]}' "$1"; }
_p101_cxx_of() { case "$1" in gcc*) printf 'g++%s' "${1#gcc}";; clang*) printf 'clang++%s' "${1#clang}";; *) printf '';; esac; }

usage() {
  cat <<'USAGE'
Usage: update.sh -c <C compiler> -x <C++ compiler> [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [--dry-run]

  -c  C compiler       (e.g. gcc, clang, gcc-16, /opt/llvm/bin/clang)
  -x  C++ compiler     (e.g. g++, clang++, g++-16, /opt/llvm/bin/clang++)
  -f  clang-format     (default: clang-format; accepts absolute path or name)
  -t  clang-tidy       (default: clang-tidy;  accepts absolute path or name)
  -k  cppcheck         (default: cppcheck;    accepts absolute path or name)
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

Examples:
  ./update.sh -c clang -x clang++
  ./update.sh -c gcc-16 -x g++-16 -f clang-format-20 -t clang-tidy-20
USAGE
  _cc="$(_p101_names supported_c_compilers.txt)"; _cxx="$(_p101_names supported_cxx_compilers.txt)"
  if [ -n "$_cc" ] || [ -n "$_cxx" ]; then
    printf '\nCompilers detected on this machine (./check-compilers.sh):\n'
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
  if [[ "$user_value" = /* ]]; then
    [[ -x "$user_value" ]] || die "'$user_value' is not executable"
    printf "%s" "$user_value"
    return
  fi
  if path="$(map_lookup "$user_value")" && [[ -x "$path" ]]; then
    printf "%s" "$path"
    return
  fi
  if path="$(command -v "$user_value" 2>/dev/null)"; then
    printf "%s" "$path"
    return
  fi
  note "compiler '$user_value' not in $MAP_FILE or PATH — re-running discovery..." >&2
  "$CHECK_COMPILERS_SH" >&2 || true
  if path="$(map_lookup "$user_value")" && [[ -x "$path" ]]; then
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
    s) sanitizers="$OPTARG"; sanitizers_given=true ;;
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

[[ -n "$c_compiler"   ]] || { printf "Error: -c (C compiler) is required\n" >&2; usage; }
[[ -n "$cxx_compiler" ]] || { printf "Error: -x (C++ compiler) is required\n" >&2; usage; }

# ----------------- sanity: required helper scripts present -----------------
for f in "$PULL_SH" "$CHECK_ENV_SH" "$CLONE_REPOS_SH" "$CHECK_COMPILERS_SH" "$GENERATE_FLAGS_SH" "$LINK_FLAGS_SH" "$LINK_COMPILERS_SH" "$LINK_CMAKE_SH" "$BUILD_REPO_SH"; do
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
# pull.sh exits 1 after a successful pull to signal "re-run so the new
# scripts are used" — handle that explicitly instead of a bare set -e death.
pull_rc=0
if ! $dry_run; then
  "$PULL_SH" || pull_rc=$?
else
  printf '[dry-run] %s\n' "$PULL_SH"
fi
if [[ "$pull_rc" -eq 1 ]]; then
  note "The scripts repository was just updated. Please re-run this command."
  exit 1
elif [[ "$pull_rc" -ne 0 ]]; then
  die "pull.sh failed (exit $pull_rc)"
fi

# ----------------- sanitizer COMBINATION validation -----------------
# Individual sanitizer groups probe fine one at a time, but sanitizers can
# depend on or forbid one another (address+thread, address+safe_stack, ...)
# and only the combined invocation reveals it. Ask the actual pinned C
# compiler ONCE with every selected group's primary flags together, so a
# bad -s selection dies here with the compiler's own error instead of an
# hour into the build matrix. (bash 3.2 safe; combo lines like cfi's
# "-fsanitize=cfi -flto ..." come through whole.)
if [[ -n "$sanitizers" ]] && ! $dry_run; then
  _combo_flags=""
  _IFS_saved="$IFS"; IFS=','
  for _san in $sanitizers; do
    IFS="$_IFS_saved"
    _san="$(printf '%s' "$_san" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    [[ -z "$_san" ]] && { IFS=','; continue; }
    _sfile="flags/${_san}_sanitizer_flags.txt"
    if [[ -f "$_sfile" ]]; then
      _sline="$(sed 's/#.*//; s/"/ /g' "$_sfile" | grep -m1 -- '-fsanitize=' || true)"
      [[ -n "$_sline" ]] && _combo_flags="$_combo_flags $_sline"
    fi
    IFS=','
  done
  IFS="$_IFS_saved"
  if [[ -n "${_combo_flags// /}" ]]; then
    _combo_tmp="$(mktemp -d 2>/dev/null || mktemp -d -t sancombo)"
    printf 'int main(void){return 0;}\n' > "$_combo_tmp/t.c"
    # shellcheck disable=SC2086
    if ! _combo_err="$("$CC_PATH" $_combo_flags -fsyntax-only "$_combo_tmp/t.c" 2>&1)"; then
      # The full selection failed. Separate two very different causes:
      #   (a) this target does not support a sanitizer AT ALL (e.g. clang has
      #       no standalone -fsanitize=leak on arm64-darwin — leak is folded
      #       into ASan there). Policy: unsupported => not tried, so DROP it,
      #       exactly as the per-compiler flag probe already does.
      #   (b) sanitizers the compiler DOES support conflict with each other
      #       (address+thread, ...). That is a genuine bad -s selection and
      #       stays a hard error.
      # Probe each selected group's flags alone; keep those that compile, then
      # re-test the survivors together. (bash 3.2 safe: no arrays.)
      _kept_flags=""; _kept_names=""; _dropped_names=""
      _IFS_saved="$IFS"; IFS=','
      for _san in $sanitizers; do
        IFS="$_IFS_saved"
        _san="$(printf '%s' "$_san" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
        [[ -z "$_san" ]] && { IFS=','; continue; }
        _sfile="flags/${_san}_sanitizer_flags.txt"
        _sline=""
        [[ -f "$_sfile" ]] && _sline="$(sed 's/#.*//; s/"/ /g' "$_sfile" | grep -m1 -- '-fsanitize=' || true)"
        # shellcheck disable=SC2086
        if [[ -n "$_sline" ]] && "$CC_PATH" $_sline -fsyntax-only "$_combo_tmp/t.c" >/dev/null 2>&1; then
          _kept_flags="$_kept_flags $_sline"
          _kept_names="${_kept_names:+$_kept_names,}$_san"
        else
          _dropped_names="${_dropped_names:+$_dropped_names,}$_san"
        fi
        IFS=','
      done
      IFS="$_IFS_saved"
      # Survivors still conflicting => real error (case b).
      # shellcheck disable=SC2086
      if [[ -n "${_kept_flags// /}" ]] \
         && ! _combo_err2="$("$CC_PATH" $_kept_flags -fsyntax-only "$_combo_tmp/t.c" 2>&1)"; then
        rm -rf "$_combo_tmp"
        printf 'Error: the selected sanitizers (%s) cannot be combined for %s:\n' "$_kept_names" "$CC_PATH" >&2
        printf '%s\n' "$_combo_err2" | head -5 | sed 's/^/  | /' >&2
        printf 'See flag_report/<cc>-sanitize-combos.txt (harvest-flags.py) for the full pairwise matrix.\n' >&2
        exit 2
      fi
      if [[ -n "$_dropped_names" ]]; then
        note "sanitizer(s) unsupported by $CC_PATH on this target dropped: ${_dropped_names} (using: ${_kept_names:-none})"
      fi
      # Narrow what downstream (check-env, CMake -DSANITIZER_LIST) receives to
      # the set that actually works for THIS compiler — other compilers in the
      # update-all matrix keep their own full set.
      sanitizers="$_kept_names"
    fi
    rm -rf "$_combo_tmp"
  fi
fi

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
  for _list in "$SUPPORTED_C_COMPILERS" "$SUPPORTED_CXX_COMPILERS"; do
    [[ -f "$_list" ]] || continue
    while IFS= read -r _name || [[ -n "$_name" ]]; do
      _name="${_name%%#*}"
      _name="${_name#"${_name%%[![:space:]]*}"}"
      _name="${_name%"${_name##*[![:space:]]}"}"
      [[ -z "$_name" ]] && continue
      _have_cache=false
      for _f in "${CACHE_ROOT}/$_name"/*.txt; do
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
  run_or_echo ./render-flags.py --selection flag-selection.standard.json --out flags-standard
fi

# In --no-flags mode there is nothing to probe: the caches are left exactly
# as they are (ignored this build via P101_NO_FLAGS) and no version stamp is
# touched, so a normal run afterward needs no re-probe.
if $no_flags; then
  note "--no-flags: skipping flag probing; building with no compiler flags or sanitizers."
elif $update; then
  run_or_echo "$CHECK_COMPILERS_SH"
  # generate-flags.sh reads P101_FLAGS_PROFILE: 'standard' probes
  # flags-standard/ -> .flags-standard/, else flags/ -> .flags/.
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
  # The supported lists hold full paths (check-compilers.sh pins them).
  # Accept a match on exact path, or on basename — in either direction —
  # so both `-c /usr/bin/clang` and `-c clang` validate against a list
  # entry of /usr/bin/clang.
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

# In dry-run mode the lists may not have been (re)generated; don't fail on
# a file that the real run would have produced.
if $dry_run && [[ ! -f "$SUPPORTED_C_COMPILERS" || ! -f "$SUPPORTED_CXX_COMPILERS" ]]; then
  note "[dry-run] skipping supported-compiler check (lists not generated yet)"
else
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
fi

# --no-flags and --standard both rely on the env-aware shared CMakeLists
# (P101_NO_FLAGS / P101_FLAGS_PROFILE). Make sure each repo has the current
# one. copy-cmake.sh is idempotent and the CMakeLists behaves identically to
# before when neither env var is set.
if { $no_flags || $standard; } && [[ -x ./copy-cmake.sh ]]; then
  run_or_echo ./copy-cmake.sh
fi

# ----------------- link discovered flags & compilers into each repo -----------------
run_or_echo "$LINK_FLAGS_SH"
run_or_echo "$LINK_COMPILERS_SH"
# Symlink the shared cmake/ helpers into each repo so the slimmed CMakeLists
# finds them (single source of truth in scripts/cmake/).
run_or_echo "$LINK_CMAKE_SH"

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
