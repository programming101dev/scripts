#!/usr/bin/env bash
# prune-flags.sh — remove stale per-compiler flag buckets from the shared
# .flags/ directories: any profile bucket whose compiler is no longer listed in
# supported_c_compilers.txt / supported_cxx_compilers.txt (e.g. an old gcc-15
# left behind after moving to gcc-16).
#
# This is a MAINTAINER tool and lives only in scripts/. .flags is SHARED by
# every repo in the workspace (they symlink to it), so a prune here affects all
# of them — that is intended: a stale bucket is stale everywhere. It never
# touches a bucket for a currently-supported compiler.
set -uo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

usage() {
  cat <<'USAGE'
Usage: ./prune-flags.sh [-n|--dry-run] [-y|--yes] [--maximal|--standard|--all]
  Removes compiler directories from the selected flag profile(s) when the
  compiler is NOT in
  supported_c_compilers.txt or supported_cxx_compilers.txt.

  (default)       list stale buckets and ask  Delete <cc>? [y/N]  for each.
  -n, --dry-run   show what would be removed; delete nothing.
  -y, --yes       remove every stale bucket without asking.
  --maximal       inspect only ../.flags (default).
  --standard      inspect only ../.flags-standard.
  --all           inspect both profiles.
  -h, --help      this help.

  Buckets for supported compilers are never touched. .flags is shared across
  the workspace, so this prunes it for every repo at once.
USAGE
}

dry=0; assume_yes=0; profile=maximal
for a in "$@"; do
  case "$a" in
    -h|--help)    usage; exit 0 ;;
    -n|--dry-run) dry=1 ;;
    -y|--yes)     assume_yes=1 ;;
    --maximal)    profile=maximal ;;
    --standard)   profile=standard ;;
    --all)        profile=all ;;
    *) echo "Unknown option: $a" >&2; usage; exit 2 ;;
  esac
done

case "$profile" in
  maximal) flag_roots=("../.flags") ;;
  standard) flag_roots=("../.flags-standard") ;;
  all) flag_roots=("../.flags" "../.flags-standard") ;;
esac

# supported compiler basenames (paths or bare names, '#' comments allowed).
# Normalise to a single space-delimited string so the " $cc " membership test
# below works (awk prints one name per line; those newlines must become spaces).
names_from() { [ -f "$1" ] && awk 'NF && $0 !~ /^[[:space:]]*#/ { n=split($0,a,"/"); print a[n] }' "$1"; }
supported=" $( { names_from supported_c_compilers.txt; names_from supported_cxx_compilers.txt; } | tr '\n' ' ') "

# SAFETY: if we cannot read any supported compiler, refuse — otherwise every
# bucket would look "unsupported" and we would delete the lot.
if [ "$(printf '%s' "$supported" | tr -d '[:space:]')" = "" ]; then
  echo "Refusing to prune: supported_c_compilers.txt / supported_cxx_compilers.txt" >&2
  echo "not found or empty in $(pwd). Run ./p101-workspace compilers first." >&2
  exit 1
fi

stale=()
existing_roots=0
for flags_root in "${flag_roots[@]}"; do
  [ -d "$flags_root" ] || continue
  existing_roots=$((existing_roots + 1))
  shopt -s nullglob
  for d in "$flags_root"/*/; do
    cc="$(basename "$d")"
    case "$cc" in
      ''|.|..|*[!A-Za-z0-9._+-]*)
        printf 'Refusing to prune: unsafe compiler bucket name: %s\n' "$cc" >&2
        exit 1 ;;
    esac
    case "$supported" in
      *" $cc "*) : ;;
      *) stale+=("$flags_root/$cc") ;;
    esac
  done
  shopt -u nullglob
done

if [ "$existing_roots" -eq 0 ]; then
  printf 'No selected shared flag profile exists (%s) — nothing to prune.\n' \
    "${flag_roots[*]}" >&2
  exit 0
fi

if [ ${#stale[@]} -eq 0 ]; then
  echo "No stale flag buckets. Selected profiles match the supported compilers."
  exit 0
fi

echo "Stale flag buckets (compiler not in supported lists):"
printf '  %s\n' "${stale[@]}"
echo

removed=0; kept=0
is_selected_root() {
  candidate_root=$1
  for configured_root in "${flag_roots[@]}"; do
    [ -d "$configured_root" ] || continue
    configured_root="$(CDPATH='' cd -- "$configured_root" && pwd -P)"
    [ "$candidate_root" != "$configured_root" ] || return 0
  done
  return 1
}

for target in "${stale[@]}"; do
  cc="$(basename "$target")"
  profile_dir="$(basename "$(dirname "$target")")"
  if [ "$dry" -eq 1 ]; then
    echo "  [dry-run] would remove: $profile_dir/$cc"
    continue
  fi
  ans="n"
  if [ "$assume_yes" -eq 1 ]; then
    ans="y"
  else
    printf 'Delete stale bucket %s/%s ? [y/N] ' "$profile_dir" "$cc"
    IFS= read -r ans </dev/tty || ans="n"
  fi
  case "$ans" in
    y|Y|yes|YES)
      target_parent="$(CDPATH='' cd -- "$(dirname -- "$target")" && pwd -P)"
      if ! is_selected_root "$target_parent"; then
        printf 'Refusing to remove bucket outside selected profiles: %s\n' \
          "$target" >&2
        exit 1
      fi
      if rm -rf -- "$target"; then echo "  removed: $profile_dir/$cc"; removed=$((removed+1)); fi ;;
    *) echo "  kept:    $profile_dir/$cc"; kept=$((kept+1)) ;;
  esac
done

if [ "$dry" -eq 1 ]; then
  echo "(dry-run — nothing deleted)"
else
  printf '\nDone: %d removed, %d kept.\n' "$removed" "$kept"
fi
