#!/bin/sh
# Portable sync: copy scripts/CMakeLists.txt into any repo dir containing config.cmake

set -eu

usage() {
  printf '%s\n' "Usage: $0 [-n] [-v]
  -n  dry run (show what would change, no writes)
  -v  verbose (also report up-to-date targets)"
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

# Resolve paths
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SRC_CMAKE="$SCRIPT_DIR/CMakeLists.txt"

[ -f "$SRC_CMAKE" ] || { printf 'Error: %s not found.\n' "$SRC_CMAKE" >&2; exit 1; }

# copy helper (only if missing or content differs)
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
    # ensure directory exists (it does, but keep this harmless)
    mkdir -p -- "$dest_dir"
    cp -- "$SRC_CMAKE" "$dest"
    if [ -f "$dest" ]; then
      printf 'Updated: %s\n' "$dest_dir"
    else
      printf 'Created: %s\n' "$dest_dir"
    fi
  fi
}

# Walk these roots; prune common build/VC dirs; act on any config.cmake
scan_root() {
  base=$1
  [ -d "$base" ] || return 0

  # POSIX-find friendly prune & match
  # shellcheck disable=SC2039
  find "$base" \
    -type d \( -name .git -o -name .svn -o -name .hg -o -name build -o -name dist -o -name out -o -name bin -o -name obj -o -name ".flags" -o -name ".idea" -o -name ".vscode" -o -name "cmake-build-*" \) -prune -o \
    -type f -name config.cmake -print |
  while IFS= read -r cfg; do
    repo_dir=$(dirname -- "$cfg")
    copy_if_needed "$repo_dir"
  done
}

# Limit search to the “repositories” you showed
scan_root "$ROOT_DIR/examples"
scan_root "$ROOT_DIR/libraries"
scan_root "$ROOT_DIR/programs"
scan_root "$ROOT_DIR/templates"

exit 0
