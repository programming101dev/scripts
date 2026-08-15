#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
# shellcheck source=../shared/bootstrap.sh
. "$root/shared/bootstrap.sh"
bootstrap=$(p101_bootstrap_build "$root" "${CC:-cc}")
exec "$bootstrap" compare-performance "$@"
