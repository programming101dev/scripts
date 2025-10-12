#!/bin/sh
# Copy scripts/CMakeLists.txt into each repo listed in repos.txt.
# repos.txt format: <git-url>|<dest-path>|<lang>

set -eu

usage() {
  printf '%s\n' "Usage: $0 [-n] [-v]
  -n  dry run (show what would change, no writes)
  -v  verbose (also report up-to-date or skipped targets)"
  exit 0
}

DRYRUN=0
VERBOSE=0
while getopts "nvh" opt; do
  case "$opt" in
    n) DRYRUN=1 ;;
    v) VERBOSE=1 ;;
    h) usage ;;
    *) usage ;;
  esac
done

# Paths
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
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
    if [ -f "$dest" ]; then
      printf '[dry-run] update: %s\n' "$dest_dir"
    else
      printf '[dry-run] create: %s\n' "$dest_dir"
    fi
  else
    mkdir -p "$dest_dir"
    cp "$SRC_CMAKE" "$dest"
    if [ -f "$dest" ]; then
      printf 'Updated: %s\n' "$dest_dir"
    else
      printf 'Created: %s\n' "$dest_dir"
    fi
  fi
}

# Process repos.txt in current directory
while IFS= read -r line || [ -n "$line" ]; do
  # Strip CR if CRLF file, trim whitespace
  line=${line%$'\r'}
  line=$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -z "$line" ] && continue
  case "$line" in \#*) continue ;; esac

  dest_field=$(printf '%s' "$line" | awk -F'|' '{print $2}')

  if [ -z "$dest_field" ]; then
    printf 'Skip: bad line (missing dest): %s\n' "$line" >&2
    continue
  fi

  # Resolve to absolute path (in case of relative like ../libraries/lib_c)
  dest_dir=$(CDPATH= cd -- "$dest_field" 2>/dev/null && pwd) || {
    [ "$VERBOSE" -eq 1 ] && printf 'Skip: cannot resolve %s\n' "$dest_field"
    continue
  }

  if [ ! -d "$dest_dir" ]; then
    [ "$VERBOSE" -eq 1 ] && printf 'Skip: not a directory: %s\n' "$dest_dir"
    continue
  fi

  if [ ! -f "$dest_dir/config.cmake" ]; then
    [ "$VERBOSE" -eq 1 ] && printf 'Skip: no config.cmake in %s\n' "$dest_dir"
    continue
  fi

  copy_if_needed "$dest_dir"
done < "$REPOS_FILE"

exit 0
