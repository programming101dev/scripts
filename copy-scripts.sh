#!/bin/sh
# copy-scripts.sh — distribute the canonical per-repo helper scripts from the
# templates into every repo, so they stay identical (kills drift). Same model
# as copy-cmake.sh.
#
#   C repos   <- templates/template-c/<script>
#   C++ repos <- templates/template-cxx/<script>
#
# EXAMPLES ARE SKIPPED: their build.sh is a different make-tree script, not the
# single-project version.
#
# By default only a file the target repo ALREADY has is overwritten. With -a
# (adopt), files a repo lacks are added too — use this once after adding a new
# script to the templates, then plain runs keep everything current.
#
# copy-template.sh is deliberately NOT in the sync list: it is the template's
# own self-copier and does not belong in the repos stamped out from it.
#
# install.sh / uninstall.sh only exist in LIBRARY repos (programs and templates
# are not installed), so their canonical lives HERE in the scripts repo and they
# are only distributed to repos under libraries/.
set -eu

usage() {
  printf '%s\n' "Usage: $0 [-n] [-v] [-a]
  -n  dry run (show what would change, no writes)
  -v  verbose (also report up-to-date / skipped)
  -a  adopt (also ADD canonical files a repo lacks, not just update existing)"
  exit "${1:-0}"
}
case " $* " in *" --help "*|*" -h "*) ( usage 0 ) || true; exit 0 ;; esac

DRYRUN=0; VERBOSE=0; ADOPT=0
while getopts "nvah" opt; do
  case "$opt" in
    n) DRYRUN=1 ;;
    v) VERBOSE=1 ;;
    a) ADOPT=1 ;;
    h) usage 0 ;;
    *) usage 2 ;;
  esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPOS_FILE="$SCRIPT_DIR/repos.txt"
C_SRC="$SCRIPT_DIR/../templates/template-c"
CXX_SRC="$SCRIPT_DIR/../templates/template-cxx"

# The per-repo scripts kept in lock-step across repos (chmod +x on copy).
SYNC_SCRIPTS="build-all.sh build.sh change-compiler.sh check-compilers.sh check-env.sh check.sh clean.sh coverage-report.sh create-links.sh debug.sh doctor.sh fuzz.sh profile-report.sh report.sh test-all.sh test.sh"

# Non-executable canonical files kept in lock-step the same way.
SYNC_FILES=".gitignore"

# Library-only scripts; canonical is this scripts repo itself (see header).
SYNC_LIB_SCRIPTS="install.sh uninstall.sh"

[ -f "$REPOS_FILE" ] || { printf 'Error: %s not found.\n' "$REPOS_FILE" >&2; exit 1; }
[ -d "$C_SRC" ]      || { printf 'Error: C canonical %s not found.\n' "$C_SRC" >&2; exit 1; }

abspath() { ( CDPATH= cd -- "$1" 2>/dev/null && pwd ); }

# sync_one <src> <destdir> <destlabel> <file> <exec?>
sync_one() {
  s_src="$1"; s_destdir="$2"; s_dest="$3"; s_f="$4"; s_x="$5"
  [ -f "$s_src/$s_f" ] || return 0            # canonical must have it
  if [ ! -f "$s_destdir/$s_f" ]; then
    if [ "$ADOPT" -eq 1 ]; then
      if [ "$DRYRUN" -eq 1 ]; then
        printf '[dry-run] adopt:  %s/%s\n' "$s_dest" "$s_f"
      else
        cp "$s_src/$s_f" "$s_destdir/$s_f"
        [ "$s_x" = "x" ] && chmod +x "$s_destdir/$s_f"
        printf 'Adopted: %s/%s\n' "$s_dest" "$s_f"
      fi
      updated=$((updated + 1))
    elif [ "$VERBOSE" -eq 1 ]; then
      printf 'Absent (use -a to add): %s/%s\n' "$s_dest" "$s_f"
    fi
    return 0
  fi
  if cmp -s "$s_src/$s_f" "$s_destdir/$s_f"; then
    [ "$VERBOSE" -eq 1 ] && printf 'Up-to-date: %s/%s\n' "$s_dest" "$s_f"
    return 0
  fi
  if [ "$DRYRUN" -eq 1 ]; then
    printf '[dry-run] update: %s/%s\n' "$s_dest" "$s_f"
  else
    cp "$s_src/$s_f" "$s_destdir/$s_f"
    [ "$s_x" = "x" ] && chmod +x "$s_destdir/$s_f"
    printf 'Updated: %s/%s\n' "$s_dest" "$s_f"
  fi
  updated=$((updated + 1))
}

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

  case "$lang" in
    c|C) src="$C_SRC" ;;
    cxx|CXX|CPP) src="$CXX_SRC" ;;
    *) [ "$VERBOSE" -eq 1 ] && printf 'Skip (unsupported language %s): %s\n' "$lang" "$dest"; continue ;;
  esac
  [ -d "$src" ] || { [ "$VERBOSE" -eq 1 ] && printf 'Skip (no canonical for %s): %s\n' "$lang" "$dest"; continue; }

  # Don't copy a canonical onto itself.
  if [ "$(abspath "$src")" = "$(abspath "$destdir")" ]; then
    [ "$VERBOSE" -eq 1 ] && printf 'Canonical: %s\n' "$dest"
    continue
  fi

  for s in $SYNC_SCRIPTS; do
    sync_one "$src" "$destdir" "$dest" "$s" x
  done
  for s in $SYNC_FILES; do
    sync_one "$src" "$destdir" "$dest" "$s" -
  done

  # Library repos additionally get install/uninstall from this scripts repo.
  case "$dest" in
    *libraries/*)
      for s in $SYNC_LIB_SCRIPTS; do
        sync_one "$SCRIPT_DIR" "$destdir" "$dest" "$s" x
      done ;;
  esac
done < "$REPOS_FILE"

if [ "$DRYRUN" -eq 1 ]; then
  printf '(dry-run) %d file(s) would change.\n' "$updated"
else
  printf 'Done: %d file(s) updated.\n' "$updated"
fi
