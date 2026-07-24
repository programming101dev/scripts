#!/bin/sh
# copy-scripts.sh — distribute the canonical per-repo helper scripts from the
# templates into every repo, so they stay identical (kills drift). Same model
# as copy-cmake.sh.
#
#   C repos   <- templates/template-c/<script>
#   C++ repos <- templates/template-cxx/<script>
#
# EXAMPLES ARE SKIPPED: their build.sh is a different make-tree script, not the
# single-project version. Only a script the target repo ALREADY has is
# overwritten (this never adds scripts to a repo that lacks them).
set -eu

usage() {
  printf '%s\n' "Usage: $0 [-n] [-v]
  -n  dry run (show what would change, no writes)
  -v  verbose (also report up-to-date / skipped)"
  exit "${1:-0}"
}
case " $* " in *" --help "*|*" -h "*) ( usage 0 ) || true; exit 0 ;; esac

DRYRUN=0; VERBOSE=0
while getopts "nvh" opt; do
  case "$opt" in
    n) DRYRUN=1 ;;
    v) VERBOSE=1 ;;
    h) usage 0 ;;
    *) usage 2 ;;
  esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOS_FILE="$SCRIPT_DIR/repos.txt"
C_SRC="$SCRIPT_DIR/../templates/template-c"
CXX_SRC="$SCRIPT_DIR/../templates/template-cxx"

# The per-repo scripts kept in lock-step across repos.
SYNC_SCRIPTS="build.sh change-compiler.sh check-env.sh coverage-report.sh profile-report.sh report.sh test.sh test-all.sh"

[ -f "$REPOS_FILE" ] || { printf 'Error: %s not found.\n' "$REPOS_FILE" >&2; exit 1; }
[ -d "$C_SRC" ]      || { printf 'Error: C canonical %s not found.\n' "$C_SRC" >&2; exit 1; }

abspath() { ( CDPATH= cd -- "$1" 2>/dev/null && pwd ); }

updated=0
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue ;; esac
  dest=$(printf '%s\n' "$line" | cut -d'|' -f2)
  lang=$(printf '%s\n' "$line" | cut -d'|' -f3)
  [ -n "$dest" ] || continue

  # Skip the examples repos (different build.sh family).
  case "$dest" in *examples*) [ "$VERBOSE" -eq 1 ] && printf 'Skip (examples): %s\n' "$dest"; continue ;; esac

  destdir="$SCRIPT_DIR/$dest"
  if [ ! -d "$destdir" ]; then
    [ "$VERBOSE" -eq 1 ] && printf 'Skip (missing): %s\n' "$dest"
    continue
  fi

  case "$lang" in cxx|CXX|CPP) src="$CXX_SRC" ;; *) src="$C_SRC" ;; esac
  [ -d "$src" ] || { [ "$VERBOSE" -eq 1 ] && printf 'Skip (no canonical for %s): %s\n' "$lang" "$dest"; continue; }

  # Don't copy a canonical onto itself.
  if [ "$(abspath "$src")" = "$(abspath "$destdir")" ]; then
    [ "$VERBOSE" -eq 1 ] && printf 'Canonical: %s\n' "$dest"
    continue
  fi

  for s in $SYNC_SCRIPTS; do
    [ -f "$src/$s" ] || continue          # canonical must have it
    [ -f "$destdir/$s" ] || continue      # only overwrite what already exists
    if cmp -s "$src/$s" "$destdir/$s"; then
      [ "$VERBOSE" -eq 1 ] && printf 'Up-to-date: %s/%s\n' "$dest" "$s"
      continue
    fi
    if [ "$DRYRUN" -eq 1 ]; then
      printf '[dry-run] update: %s/%s\n' "$dest" "$s"
    else
      cp "$src/$s" "$destdir/$s"
      chmod +x "$destdir/$s"
      printf 'Updated: %s/%s\n' "$dest" "$s"
    fi
    updated=$((updated + 1))
  done
done < "$REPOS_FILE"

if [ "$DRYRUN" -eq 1 ]; then
  printf '(dry-run) %d file(s) would change.\n' "$updated"
else
  printf 'Done: %d file(s) updated.\n' "$updated"
fi
