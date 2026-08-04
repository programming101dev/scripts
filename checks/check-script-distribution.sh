#!/usr/bin/env bash
# Verify that generated per-repository helper scripts have not drifted.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' \
      "check-script-distribution.sh — verify canonical shared repo scripts."
    exit 0
    ;;
esac

if [[ "$#" -ne 0 ]]; then
  echo "Usage: ./check-script-distribution.sh" >&2
  exit 2
fi

./tests/test-copy-scripts-standalone.sh
./distribution/copy-scripts.sh -c
