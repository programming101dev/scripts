#!/usr/bin/env bash
set -euo pipefail

scripts_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source_file="$scripts_root/cmake/p101_compile_db.c"
bootstrap_dir="$scripts_root/target/bootstrap"
bootstrap="$bootstrap_dir/p101-bootstrap"
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

mkdir -p "$bootstrap_dir"
if [[ ! -x "$bootstrap" || "$source_file" -nt "$bootstrap" ]]; then
    temporary="$bootstrap.tmp.$$"
    trap 'rm -f "$temporary"' EXIT
    "$compiler" -std=c17 -Wall -Wextra -Werror -pedantic \
        "$source_file" -o "$temporary"
    chmod +x "$temporary"
    mv "$temporary" "$bootstrap"
    trap - EXIT
fi

exec "$bootstrap" format-workspace \
    "$formatter" "$receipt" "$mode" "$scripts_root"
