#!/usr/bin/env bash
# Compatibility entry point for refreshing the scripts repository. The
# mechanism lives in refresh-repo.sh so every setup/update path uses the same
# explicit fetch and fast-forward contract.
set -uo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 2

allow_snapshot=false
case "${1:-}" in
    -h|--help)
        exec ./refresh-repo.sh --help
        ;;
    --allow-snapshot)
        allow_snapshot=true
        shift
        ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Error: unknown option: %s\n' "$1" >&2; exit 2; }

if $allow_snapshot; then
    exec ./refresh-repo.sh --allow-snapshot .
fi
exec ./refresh-repo.sh .
