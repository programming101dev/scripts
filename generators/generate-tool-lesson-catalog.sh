#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
workspace=$(dirname -- "$root")
catalog=$workspace/playgrounds/lessons/manifest.json
header=$workspace/libraries/lib_tool_support/include/p101_tool_support/lesson_catalog.h
source=$workspace/libraries/lib_tool_support/src/lesson_catalog.c
mode='write'

while [ "$#" -gt 0 ]; do
  case "$1" in
    --catalog)
      [ "$#" -ge 2 ] || { printf '%s\n' 'missing value for --catalog' >&2; exit 2; }
      catalog=$2
      shift 2
      ;;
    --header)
      [ "$#" -ge 2 ] || { printf '%s\n' 'missing value for --header' >&2; exit 2; }
      header=$2
      shift 2
      ;;
    --source)
      [ "$#" -ge 2 ] || { printf '%s\n' 'missing value for --source' >&2; exit 2; }
      source=$2
      shift 2
      ;;
    --check)
      mode='check'
      shift
      ;;
    *)
      printf 'Usage: %s [--catalog PATH] [--header PATH] [--source PATH] [--check]\n' "$0" >&2
      exit 2
      ;;
  esac
done

# shellcheck source=../shared/bootstrap.sh
. "$root/shared/bootstrap.sh"
bootstrap=$(p101_bootstrap_build "$root" "${CC:-cc}")
exec "$bootstrap" tool-lesson-catalog \
  "$catalog" "$header" "$source" "$mode"
