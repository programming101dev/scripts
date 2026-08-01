#!/bin/sh
# update-all.sh — drive ./update.sh across the supported compiler lists
# Portable: POSIX sh (macOS, Linux, FreeBSD); uses grep, printf, getopts
#
# Pairing: each C compiler is paired with its matching C++ compiler by name
# (gcc-15 -> g++-15, clang -> clang++, clang22 -> clang++22). C compilers
# with no matching C++ compiler in the list are skipped with a warning —
# never paired positionally with an unrelated compiler.

set -eu

# Always operate from the directory this script lives in.
cd -- "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"

# Defaults
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""
sanitizers_given=0
no_flags=0
standard=0
skip_install=0
interactive=0

c_list_file="supported_c_compilers.txt"
cxx_list_file="supported_cxx_compilers.txt"
driver="./update.sh"

usage() {
    printf '%s\n' \
"Usage: $0 [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-C <c-list>] [-X <cxx-list>] [-u <update.sh>]
  -f clang-format   Name of clang-format, default ${clang_format_name}
  -t clang-tidy     Name of clang-tidy,   default ${clang_tidy_name}
  -k cppcheck       Name of cppcheck,     default ${cppcheck_name}
  -s sanitizers     Comma list; default: current sanitizers.txt
  -C file           C compilers list,     default ${c_list_file}
  -X file           C++ compilers list,   default ${cxx_list_file}
  -u file           Path to update.sh,    default ${driver}
  -N                --no-flags: build every pair with NO probed compiler
                    flags and NO sanitizers (caches/selection untouched)
  -S                --standard: build every pair with the reasonable safe
                    subset (flag-selection.standard.json), no sanitizers
  --coverage        Opt-in: instrument every pair for code coverage (gcov)
  --profile         Opt-in: instrument every pair for profiling (gprof)
  --skip-install    Build every pair but do not run repo install.sh scripts
  --interactive     Pause and retry the failed repository phase after a fix"
    if [ -f "$c_list_file" ]; then
        printf '\nCompiler pairs this will build (from %s):\n' "$c_list_file"
        while IFS= read -r _l || [ -n "$_l" ]; do
            _l=${_l%%#*}
            set -f; set -- $_l; set +f
            _c=${1:-}
            if [ -z "$_c" ]; then continue; fi
            _cb=$(basename "$_c")
            case "$_cb" in
              gcc*)   _xb="g++${_cb#gcc}" ;;
              clang*) _xb="clang++${_cb#clang}" ;;
              *)      _xb="(no C++ pair)" ;;
            esac
            printf '  %s : %s\n' "$_cb" "$_xb"
        done < "$c_list_file"
    fi
    exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

# Parse short and long spellings without reconstructing argv through eval.
while [ "$#" -gt 0 ]; do
  case "$1" in
    -f|-t|-k|-s|-C|-X|-u)
      [ "$#" -ge 2 ] || { printf 'Error: %s requires an argument.\n' "$1" >&2; exit 2; }
      _opt=$1
      _value=$2
      case "$_opt" in
        -f) clang_format_name=$_value ;;
        -t) clang_tidy_name=$_value ;;
        -k) cppcheck_name=$_value ;;
        -s) sanitizers=$_value; sanitizers_given=1 ;;
        -C) c_list_file=$_value ;;
        -X) cxx_list_file=$_value ;;
        -u) driver=$_value ;;
      esac
      shift 2
      ;;
    -N|--no-flags) no_flags=1; shift ;;
    -S|--standard) standard=1; shift ;;
    -I|--skip-install) skip_install=1; shift ;;
    -i|--interactive) interactive=1; shift ;;
    --coverage) export P101_COVERAGE=1; shift ;;
    --profile) export P101_PROFILE=1; shift ;;
    -h|--help) usage ;;
    --) shift; [ "$#" -eq 0 ] || { printf 'Error: unexpected arguments.\n' >&2; exit 2; } ;;
    *)
      printf 'Error: unknown option: %s\n' "$1" >&2
      usage
      ;;
  esac
done

if [ "$no_flags" -eq 1 ] && [ "$standard" -eq 1 ]; then
  printf 'Error: -N/--no-flags and -S/--standard are mutually exclusive.\n' >&2
  exit 2
fi

# Preconditions
[ -f "$c_list_file" ]   || { printf 'Error: C list not found: %s\n' "$c_list_file" >&2; exit 2; }
[ -f "$cxx_list_file" ] || { printf 'Error: C++ list not found: %s\n' "$cxx_list_file" >&2; exit 2; }

abs_path() {
  # $1 = file path, absolute or relative to this scripts directory
  case "$1" in
    /*) printf '%s' "$1" ;;
    *)
      _dir=$(dirname "$1")
      _base=$(basename "$1")
      printf '%s/%s' "$(CDPATH='' cd -- "$_dir" && pwd -P)" "$_base"
      ;;
  esac
}

# If update.sh decides the flag cache needs probing, pass the same compiler
# lists this update-all invocation is using. Without this, a CI smoke that
# asks for clang:clang++ still probes every detected compiler on a fresh runner
# before it builds anything.
flag_c_list_file=$(abs_path "$c_list_file")
flag_cxx_list_file=$(abs_path "$cxx_list_file")

# Resolve driver (name or path)
case "$driver" in
  /*|./*|../*)
    [ -x "$driver" ] || { printf 'Error: driver not executable: %s\n' "$driver" >&2; exit 2; }
    ;;
  *)
    driver_path=$(command -v "$driver" 2>/dev/null || true)
    [ -n "$driver_path" ] && driver=$driver_path
    [ -x "$driver" ] || { printf 'Error: driver not found/executable: %s\n' "$driver" >&2; exit 2; }
    ;;
esac

# Pull the scripts repo ONCE up front so a self-update aborts cleanly here,
# not partway through the compiler loop. Some CI runners copy the checked-out
# files into a VM with missing or unusable .git metadata; that snapshot is still
# a valid input, but it cannot self-update. Require Git itself to resolve this
# directory as the repository root instead of trusting the presence of .git.
scripts_root=$(pwd -P)
git_root=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -n "$git_root" ] && [ "$(CDPATH='' cd -- "$git_root" && pwd -P)" = "$scripts_root" ]; then
  pull_rc=0
  ./pull.sh || pull_rc=$?
  if [ "$pull_rc" -eq 1 ]; then
    printf 'The scripts repository was just updated. Please re-run: %s\n' "$0" >&2
    exit 1
  elif [ "$pull_rc" -ne 0 ]; then
    printf 'Error: pull.sh failed (exit %d).\n' "$pull_rc" >&2
    exit "$pull_rc"
  fi
else
  printf 'scripts is a source snapshot without usable Git metadata; skipping self-update.\n'
fi

# Derive the C++ compiler NAME that corresponds to a C compiler name.
derive_cxx() {
  case "$1" in
    gcc*)   printf 'g++%s' "${1#gcc}" ;;
    clang*) printf 'clang++%s' "${1#clang}" ;;
    *)      printf '' ;;
  esac
}

# Find the list entry whose BASENAME matches $1. Supported lists hold names
# now; older generated lists may hold paths, so this still handles both.
find_by_basename() {
  # $1 = wanted basename, $2 = list file
  awk -v want="$1" '
    /^[[:space:]]*(#|$)/ { next }
    {
      line=$0
      sub(/^[[:space:]]+/, "", line); sub(/[[:space:]]+$/, "", line)
      n=split(line, parts, "/")
      if (parts[n] == want) { print line; exit }
    }
  ' "$2"
}

pairs_run=0
skipped=0

# CR for CRLF-stripping without $'\r' (not POSIX sh)
cr=$(printf '\r')

# Notes on the loop below:
# - plain `read -r c` (default IFS) trims leading/trailing whitespace,
#   matching the old awk-based parsing
# - the list is read on fd 3 so the update.sh pipeline (git prompts etc.)
#   keeps the real stdin
while read -r c <&3 || [ -n "$c" ]; do
  c=${c%"$cr"}
  # Word-split to trim ALL surrounding whitespace (a trailing "spaces + CR"
  # sequence survives read's IFS stripping); compiler names contain no
  # spaces, so taking the first field is safe. set -f guards against glob
  # expansion of stray wildcards.
  set -f
  set -- $c
  set +f
  c=${1:-}
  # skip blanks and comments
  case "$c" in ''|\#*) continue ;; esac

  # entries are names or legacy full paths; pair by basename
  cbase=$(basename "$c")
  xbase=$(derive_cxx "$cbase")
  if [ -z "$xbase" ]; then
    printf 'WARN: no C++ counterpart rule for %s; skipping.\n' "$cbase" >&2
    skipped=$((skipped+1))
    continue
  fi

  x=$(find_by_basename "$xbase" "$cxx_list_file")
  if [ -z "$x" ]; then
    printf 'WARN: %s has no matching %s in %s; skipping.\n' "$cbase" "$xbase" "$cxx_list_file" >&2
    skipped=$((skipped+1))
    continue
  fi

  printf 'Updating repositories with: %s : %s\n' "$c" "$x"
  set -- "$driver" -c "$c" -x "$x" -C "$flag_c_list_file" -X "$flag_cxx_list_file" \
    -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name"
  if [ "$skip_install" -eq 1 ]; then
    set -- "$@" --skip-install
  fi
  if [ "$interactive" -eq 1 ]; then
    set -- "$@" --interactive
  fi
  if [ "$no_flags" -eq 1 ]; then
    set -- "$@" --no-flags
  elif [ "$standard" -eq 1 ]; then
    set -- "$@" --standard
  elif [ "$sanitizers_given" -eq 1 ]; then
    set -- "$@" -s "$sanitizers"
  fi
  "$@"
  pairs_run=$((pairs_run+1))
done 3< "$c_list_file"

if [ "$pairs_run" -eq 0 ]; then
  printf 'Error: no usable C/C++ compiler pairs found (skipped: %d).\n' "$skipped" >&2
  exit 3
fi

printf 'Done: %d compiler pair(s) built, %d skipped.\n' "$pairs_run" "$skipped"
