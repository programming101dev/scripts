#!/bin/sh
set -eu

scripts_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
contract=${P101_STACK_CONTRACT:-$scripts_root/contracts/p101-stack-contract.json}
receipt=-
command=

usage()
{
  printf 'Usage: %s [--scripts-root DIR] [--contract FILE] refresh|verify [--receipt FILE]\n' "$0" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --scripts-root)
      scripts_root=${2:?--scripts-root requires a directory}
      shift 2
      ;;
    --contract)
      contract=${2:?--contract requires a path}
      shift 2
      ;;
    --receipt)
      receipt=${2:?--receipt requires a path}
      shift 2
      ;;
    refresh|verify)
      [ -z "$command" ] || { usage; exit 2; }
      command=$1
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

[ -n "$command" ] || { usage; exit 2; }
# shellcheck source=../shared/bootstrap.sh
. "$scripts_root/shared/bootstrap.sh"
bootstrap=$(p101_bootstrap_build "$scripts_root" "${CC:-cc}")

if [ "$command" = refresh ]; then
  exec "$bootstrap" stack-contract refresh "$scripts_root" "$contract"
fi
exec "$bootstrap" stack-contract verify "$scripts_root" "$contract" "$receipt"
