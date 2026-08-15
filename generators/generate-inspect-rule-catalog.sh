#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
workspace=$(dirname -- "$root")
mode='write'

if [ "$#" -gt 1 ]; then
  printf 'Usage: %s [--check]\n' "$0" >&2
  exit 2
fi
if [ "$#" -eq 1 ]; then
  if [ "$1" != --check ]; then
    printf 'Usage: %s [--check]\n' "$0" >&2
    exit 2
  fi
  mode='check'
fi

# shellcheck source=../shared/bootstrap.sh
. "$root/shared/bootstrap.sh"
bootstrap=$(p101_bootstrap_build "$root" "${CC:-cc}")
exec "$bootstrap" inspect-rule-catalog \
  "$root/rules" "$workspace/programs/p101-inspect/src/rule_catalog.inc" "$mode"
