#!/bin/sh
# copy-scripts.sh — distribute repository policy files and retire obsolete
# per-repository command shims.
#
#   C repos   <- templates/template-c/<script>
#   C++ repos <- templates/template-cxx/<script>
#
# Source-reference archives have no executable helper scripts. Library-example
# repositories are ordinary root CMake projects, so their
# build/configure/environment/doctor helpers use the same canonical template
# copies as every other C repo.
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
#
# When a differing file is overwritten, the previous target is first copied to
# .p101-script-backups/<relative-repo>/<file>.<timestamp>. This keeps the common
# "refresh all helper scripts" workflow non-interactive while making accidental
# local drift recoverable instead of silently clobbered.
set -eu

usage() {
  printf '%s\n' "Usage: $0 [-n] [-v] [-a] [-c]
  -n  dry run (show what would change, no writes)
  -v  verbose (also report up-to-date / skipped)
  -a  adopt (also ADD canonical files a repo lacks, not just update existing)
  -c  check only; fail if a required copy is missing, stale, or not executable"
  exit "${1:-0}"
}
case " $* " in *" --help "*|*" -h "*) ( usage 0 ) || true; exit 0 ;; esac

DRYRUN=0; VERBOSE=0; ADOPT=0; CHECK=0
while getopts "nvach" opt; do
  case "$opt" in
    n) DRYRUN=1 ;;
    v) VERBOSE=1 ;;
    a) ADOPT=1 ;;
    c) CHECK=1; DRYRUN=1; ADOPT=1 ;;
    h) usage 0 ;;
    *) usage 2 ;;
  esac
done

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
REPOS_FILE="$SCRIPT_DIR/repos.txt"
C_SRC="$SCRIPT_DIR/../templates/template-c"
CXX_SRC="$SCRIPT_DIR/../templates/template-cxx"
BACKUP_DIR="${P101_SCRIPT_BACKUP_DIR:-$SCRIPT_DIR/.p101-script-backups}"
BACKUP_STAMP=$(date +%Y%m%d%H%M%S)

# Build, test, analysis, and installation are CMake/workspace responsibilities.
# These historical shims must not be copied back into repositories.
SYNC_SCRIPTS=""
RETIRED_SCRIPTS="build-all.sh build.sh change-compiler.sh check-compilers.sh check-env.sh check.sh clean.sh coverage-report.sh create-links.sh debug.sh doctor.sh fuzz.sh profile-report.sh report.sh test-all.sh test.sh install.sh uninstall.sh"

# Non-executable canonical files kept in lock-step the same way.
SYNC_FILES=".gitignore .clang-format LICENSE coverage.txt profile.txt"
SYNC_EXAMPLE_SCRIPTS=""
SYNC_EXAMPLE_FILES=".gitignore LICENSE coverage.txt profile.txt"
# Library-only scripts; canonical is this scripts repo itself (see header).
SYNC_LIB_SCRIPTS=""
LIB_SRC="$SCRIPT_DIR/shared/library"

[ -f "$REPOS_FILE" ] || { printf 'Error: %s not found.\n' "$REPOS_FILE" >&2; exit 1; }
[ -d "$C_SRC" ]      || { printf 'Error: C canonical %s not found.\n' "$C_SRC" >&2; exit 1; }

abspath() { ( CDPATH='' cd -- "$1" 2>/dev/null && pwd ); }

backup_existing() {
  b_dest="$1"; b_label="$2"; b_file="$3"
  b_dir="$BACKUP_DIR/$(printf '%s\n' "$b_label" | sed 's#[^A-Za-z0-9._-]#_#g')"
  b_path="$b_dir/$b_file.$BACKUP_STAMP"

  mkdir -p "$b_dir"
  cp -p "$b_dest/$b_file" "$b_path"
  printf 'Backup: %s/%s -> %s\n' "$b_label" "$b_file" "$b_path"
}

# sync_one <src> <destdir> <destlabel> <file> <exec?>
sync_one() {
  s_src="$1"; s_destdir="$2"; s_dest="$3"; s_f="$4"; s_x="$5"
  if [ ! -f "$s_src/$s_f" ]; then
    printf 'FAIL: canonical file is missing: %s/%s\n' "$s_src" "$s_f" >&2
    failures=$((failures + 1))
    return 0
  fi
  if [ ! -f "$s_destdir/$s_f" ]; then
    if [ "$CHECK" -eq 1 ]; then
      printf 'FAIL: missing: %s/%s\n' "$s_dest" "$s_f" >&2
      failures=$((failures + 1))
    elif [ "$ADOPT" -eq 1 ]; then
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
    if [ "$s_x" = "x" ] && [ ! -x "$s_destdir/$s_f" ]; then
      if [ "$CHECK" -eq 1 ]; then
        printf 'FAIL: not executable: %s/%s\n' "$s_dest" "$s_f" >&2
        failures=$((failures + 1))
      elif [ "$DRYRUN" -eq 1 ]; then
        printf '[dry-run] chmod +x: %s/%s\n' "$s_dest" "$s_f"
        updated=$((updated + 1))
      else
        chmod +x "$s_destdir/$s_f"
        printf 'Made executable: %s/%s\n' "$s_dest" "$s_f"
        updated=$((updated + 1))
      fi
    fi
    [ "$VERBOSE" -eq 1 ] && printf 'Up-to-date: %s/%s\n' "$s_dest" "$s_f"
    return 0
  fi
  if [ "$CHECK" -eq 1 ]; then
    printf 'FAIL: stale: %s/%s\n' "$s_dest" "$s_f" >&2
    failures=$((failures + 1))
  elif [ "$DRYRUN" -eq 1 ]; then
    printf '[dry-run] update: %s/%s\n' "$s_dest" "$s_f"
  else
    backup_existing "$s_destdir" "$s_dest" "$s_f"
    cp "$s_src/$s_f" "$s_destdir/$s_f"
    [ "$s_x" = "x" ] && chmod +x "$s_destdir/$s_f"
    printf 'Updated: %s/%s\n' "$s_dest" "$s_f"
  fi
  updated=$((updated + 1))
}

retire_one() {
  r_destdir="$1"; r_dest="$2"; r_file="$3"
  [ -e "$r_destdir/$r_file" ] || return 0
  if [ "$CHECK" -eq 1 ]; then
    printf 'FAIL: retired repository shim remains: %s/%s\n' "$r_dest" "$r_file" >&2
    failures=$((failures + 1))
  elif [ "$DRYRUN" -eq 1 ]; then
    printf '[dry-run] retire: %s/%s\n' "$r_dest" "$r_file"
    updated=$((updated + 1))
  else
    backup_existing "$r_destdir" "$r_dest" "$r_file"
    rm -f -- "$r_destdir/$r_file"
    printf 'Retired: %s/%s\n' "$r_dest" "$r_file"
    updated=$((updated + 1))
  fi
}

updated=0
failures=0
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue ;; esac
  dest=$(printf '%s\n' "$line" | cut -d'|' -f2)
  lang=$(printf '%s\n' "$line" | cut -d'|' -f3)
  if [ -z "$dest" ] || [ -z "$lang" ]; then
    printf 'FAIL: malformed repos.txt line: %s\n' "$line" >&2
    failures=$((failures + 1))
    continue
  fi

  example_kind=none
  case "$dest" in *examples*) example_kind=library ;; esac

  destdir="$SCRIPT_DIR/$dest"
  if [ ! -d "$destdir" ]; then
    printf 'FAIL: configured repository is missing: %s\n' "$dest" >&2
    failures=$((failures + 1))
    continue
  fi

  # The canonical template retains test.sh/fuzz.sh as the two shared
  # runners used by workspace acceptance until they are native CMake drivers.
  for retired in $RETIRED_SCRIPTS; do
    if [ "$dest" = "../templates/template-c" ] &&
       { [ "$retired" = test.sh ] || [ "$retired" = fuzz.sh ]; }; then
      continue
    fi
    retire_one "$destdir" "$dest" "$retired"
  done

  case "$lang" in
    c|C) src="$C_SRC" ;;
    cxx|CXX|CPP) src="$CXX_SRC" ;;
    c-reference) [ "$VERBOSE" -eq 1 ] && printf 'Skip (source-reference archive): %s\n' "$dest"; continue ;;
    python) [ "$VERBOSE" -eq 1 ] && printf 'Skip (Python tool has its own scripts): %s\n' "$dest"; continue ;;
    c-bootstrap) [ "$VERBOSE" -eq 1 ] && printf 'Skip (C repository is not populated yet): %s\n' "$dest"; continue ;;
    *)
      printf 'FAIL: unsupported language %s: %s\n' "$lang" "$dest" >&2
      failures=$((failures + 1))
      continue ;;
  esac
  if [ ! -d "$src" ]; then
    printf 'FAIL: no canonical directory for %s: %s\n' "$lang" "$src" >&2
    failures=$((failures + 1))
    continue
  fi

  # Don't copy a canonical onto itself.
  if [ "$(abspath "$src")" = "$(abspath "$destdir")" ]; then
    [ "$VERBOSE" -eq 1 ] && printf 'Canonical: %s\n' "$dest"
    continue
  fi

  if [ "$example_kind" = "library" ]; then
    for s in $SYNC_EXAMPLE_SCRIPTS; do
      sync_one "$src" "$destdir" "$dest" "$s" x
    done
    for s in $SYNC_EXAMPLE_FILES; do
      sync_one "$src" "$destdir" "$dest" "$s" -
    done
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
        sync_one "$LIB_SRC" "$destdir" "$dest" "$s" x
      done ;;
  esac
done < "$REPOS_FILE"

# The scripts and setup repositories are workspace conductors rather than
# repos.txt entries. A scripts-only checkout (including GitHub Actions) does
# not contain the separate setup repository, so synchronize setup only when it
# is present in the surrounding workspace.
sync_policy_repository()
{
  policy_repo=$1
  policy_label=$(basename "$policy_repo")
  for s in .gitignore LICENSE; do
    sync_one "$C_SRC" "$policy_repo" "$policy_label" "$s" -
  done
}

sync_policy_repository "$SCRIPT_DIR"
if [ -d "$SCRIPT_DIR/../setup" ]; then
  sync_policy_repository "$SCRIPT_DIR/../setup"
fi

if [ "$failures" -gt 0 ]; then
  printf 'Repository policy check failed: %d problem(s).\n' "$failures" >&2
  exit 1
elif [ "$CHECK" -eq 1 ]; then
  printf 'PASS: repository policy files match and retired shims are absent.\n'
elif [ "$DRYRUN" -eq 1 ]; then
  printf '(dry-run) %d file(s) would change.\n' "$updated"
else
  printf 'Done: %d file(s) updated.\n' "$updated"
fi
