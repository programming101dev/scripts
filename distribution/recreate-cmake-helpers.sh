#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'P101_USAGE'
Usage: recreate-cmake-helpers.sh [-o <dir>] [--force]

Materialize the canonical scripts/cmake helper directory into <dir>.

Defaults:
  - output directory: ./cmake.generated
  - existing output directories are refused unless --force is passed

This script intentionally copies from the live scripts/cmake/ directory instead
of carrying embedded helper text. That keeps scripts/cmake/ as the single source
of truth and avoids stale generated copies.
P101_USAGE
  exit "${1:-0}"
}

script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
src_dir="$script_dir/cmake"
out_dir="$(pwd)/cmake.generated"
force=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    -o|--output)
      [ "$#" -ge 2 ] || usage 2
      out_dir="$2"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    -h|--help)
      usage 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage 2
      ;;
  esac
done

[ -d "$src_dir" ] || { printf 'Error: canonical CMake helper dir not found: %s\n' "$src_dir" >&2; exit 1; }

mkdir -p "$(dirname -- "$out_dir")"
out_dir="$(CDPATH='' cd -- "$(dirname -- "$out_dir")" && printf '%s/%s\n' "$(pwd -P)" "$(basename -- "$out_dir")")"
case "$out_dir" in
  "$src_dir"|"$src_dir"/*)
    printf 'Error: refusing to overwrite canonical source dir: %s\n' "$src_dir" >&2
    exit 2
    ;;
esac

if [ -e "$out_dir" ]; then
  if [ "$force" -ne 1 ]; then
    printf 'Error: output already exists: %s\n' "$out_dir" >&2
    printf 'Pass --force to replace it, or choose another -o directory.\n' >&2
    exit 2
  fi
  if [ ! -f "$out_dir/.p101-generated-cmake-helpers" ]; then
    printf 'Error: refusing to replace unmarked directory: %s\n' "$out_dir" >&2
    printf 'Remove it explicitly, or choose another -o directory.\n' >&2
    exit 2
  fi
  rm -rf "$out_dir"
fi

mkdir -p "$out_dir"
cp -R "$src_dir/." "$out_dir/"
: > "$out_dir/.p101-generated-cmake-helpers"
printf 'Wrote CMake helpers: %s\n' "$out_dir"
