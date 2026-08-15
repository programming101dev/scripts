#!/usr/bin/env bash
set -euo pipefail

scripts_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
compiler="${CC:-cc}"
formatter=""
receipt=""
mode="apply"

usage()
{
    printf 'Usage: %s --formatter PATH --receipt PATH [--check]\n' "$0" >&2
}

while (($# > 0)); do
    case "$1" in
        --formatter)
            formatter="${2:?--formatter requires a path}"
            shift 2
            ;;
        --receipt)
            receipt="${2:?--receipt requires a path}"
            shift 2
            ;;
        --check)
            mode="check"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [[ -z "$formatter" || -z "$receipt" ]]; then
    usage
    exit 2
fi

# shellcheck source=../shared/bootstrap.sh
. "$scripts_root/shared/bootstrap.sh"
bootstrap="$(p101_bootstrap_build "$scripts_root" "$compiler")"

exec "$bootstrap" format-workspace \
    "$formatter" "$receipt" "$mode" "$scripts_root"
