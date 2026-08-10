#!/bin/sh
# Copy scripts/CMakeLists.txt into each repo listed in repos.txt.
# repos.txt format: <git-url>|<dest-path>|<type>
# c-bootstrap repositories are cloned but intentionally skipped until their
# project contract has been populated.

set -eu

usage() {
  # $1 = exit status (0 for -h, non-zero for bad options)
  printf '%s\n' "Usage: $0 [-n] [-v] [-c]
  -n  dry run (show what would change, no writes)
  -v  verbose (also report up-to-date or skipped targets)
  -c  check only; fail when a required copy or shared link has drifted"
  exit "${1:-0}"
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

DRYRUN=0
VERBOSE=0
CHECK=0
while getopts "nvch" opt; do
  case "$opt" in
    n) DRYRUN=1 ;;
    v) VERBOSE=1 ;;
    c) CHECK=1; DRYRUN=1 ;;
    h) usage 0 ;;
    *) usage 2 ;;
  esac
done

# Paths
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
SRC_CMAKE="$SCRIPT_DIR/CMakeLists.txt"
REPOS_FILE="$SCRIPT_DIR/repos.txt"

[ -f "$SRC_CMAKE" ] || { printf 'Error: %s not found.\n' "$SRC_CMAKE" >&2; exit 1; }
[ -f "$REPOS_FILE" ] || { printf 'Error: %s not found.\n' "$REPOS_FILE" >&2; exit 1; }

# Copy helper
copy_if_needed() {
  dest_dir=$1
  dest="$dest_dir/CMakeLists.txt"

  if [ -f "$dest" ] && cmp -s "$SRC_CMAKE" "$dest"; then
    [ "$VERBOSE" -eq 1 ] && printf '✓ Up-to-date: %s\n' "$dest_dir"
    return 0
  fi

  if [ "$DRYRUN" -eq 1 ]; then
    if [ "$CHECK" -eq 1 ]; then
      printf 'FAIL: stale or missing: %s\n' "$dest" >&2
      failures=$((failures + 1))
    elif [ -f "$dest" ]; then
      printf '[dry-run] update: %s\n' "$dest_dir"
    else
      printf '[dry-run] create: %s\n' "$dest_dir"
    fi
  else
    # Record existence BEFORE the cp — checking after always says "Updated"
    existed=0
    if [ -f "$dest" ]; then
      existed=1
    fi
    mkdir -p "$dest_dir"
    cp "$SRC_CMAKE" "$dest"
    if [ "$existed" -eq 1 ]; then
      printf 'Updated: %s\n' "$dest_dir"
    else
      printf 'Created: %s\n' "$dest_dir"
    fi
  fi
}

# Process repos.txt (which lives alongside this script)
failures=0
while IFS= read -r line || [ -n "$line" ]; do
  # Strip CR if CRLF file, trim whitespace.
  # NOTE: use tr, not ${line%$'\r'} — $'...' is not POSIX sh and this script
  # runs under /bin/sh on Linux, macOS, and FreeBSD.
  line=$(printf '%s' "$line" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -z "$line" ] && continue
  case "$line" in \#*) continue ;; esac

  dest_field=$(printf '%s' "$line" | awk -F'|' '{print $2}')
  repo_type=$(printf '%s' "$line" | awk -F'|' '{print $3}')

  if [ -z "$dest_field" ] || [ -z "$repo_type" ]; then
    printf 'FAIL: bad repos.txt line: %s\n' "$line" >&2
    failures=$((failures + 1))
    continue
  fi
  case "$repo_type" in c|cxx) ;; python|c-bootstrap) continue ;; *)
    printf 'FAIL: unsupported repo type %s: %s\n' "$repo_type" "$dest_field" >&2
    failures=$((failures + 1))
    continue ;;
  esac

  # Resolve to absolute path; relative dests (../libraries/lib_c) are
  # relative to the scripts directory, not the caller's cwd.
  case "$dest_field" in
    /*) dest_probe=$dest_field ;;
    *)  dest_probe="$SCRIPT_DIR/$dest_field" ;;
  esac
  dest_dir=$(CDPATH='' cd -- "$dest_probe" 2>/dev/null && pwd) || {
    printf 'FAIL: cannot resolve configured repo %s\n' "$dest_field" >&2
    failures=$((failures + 1))
    continue
  }

  if [ ! -d "$dest_dir" ]; then
    printf 'FAIL: not a directory: %s\n' "$dest_dir" >&2
    failures=$((failures + 1))
    continue
  fi

  if [ ! -f "$dest_dir/config.cmake" ]; then
    case "$dest_field" in
      *examples/c-examples) continue ;;
    esac
    printf 'FAIL: no config.cmake in %s\n' "$dest_dir" >&2
    failures=$((failures + 1))
    continue
  fi

  copy_if_needed "$dest_dir"
done < "$REPOS_FILE"

# The slimmed CMakeLists sources its helpers from cmake/; make sure every
# repo has that symlink so the freshly-copied CMakeLists can find them.
if [ -x "$SCRIPT_DIR/distribution/link-cmake.sh" ]; then
  if [ "$CHECK" -eq 1 ]; then
    "$SCRIPT_DIR/distribution/link-cmake.sh" -c || failures=$((failures + 1))
  elif [ "$DRYRUN" -eq 1 ]; then
    "$SCRIPT_DIR/distribution/link-cmake.sh" -n
  else
    "$SCRIPT_DIR/distribution/link-cmake.sh"
  fi
else
  printf 'FAIL: link-cmake.sh is missing or not executable.\n' >&2
  failures=$((failures + 1))
fi

if [ "$failures" -gt 0 ]; then
  printf 'CMake distribution failed: %d problem(s).\n' "$failures" >&2
  exit 1
fi
if [ "$CHECK" -eq 1 ]; then
  printf 'PASS: distributed CMakeLists.txt files and helper links are current.\n'
fi
