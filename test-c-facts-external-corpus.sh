#!/usr/bin/env bash

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

./check-c-facts-external-corpus.sh --validate-only

manifest="corpora/c-facts-external.tsv"
actual="$(awk -F '\t' 'NR > 1 { count[$1]++ } END {
  printf "good-c=%d good-cxx=%d poor-c=%d poor-cxx=%d ioccc=%d",
    count["good-c"], count["good-cxx"], count["poor-c"],
    count["poor-cxx"], count["ioccc"]
}' "$manifest")"
expected="good-c=10 good-cxx=10 poor-c=10 poor-cxx=10 ioccc=10"
[ "$actual" = "$expected" ] || {
  echo "unexpected cohort inventory: $actual" >&2
  exit 1
}

if awk -F '\t' 'NR > 1 { key = $4 "\t" $5; seen[key] = 1 } END {
  count = 0; for (key in seen) count++; exit count == 22 ? 0 : 1
}' "$manifest"; then
  :
else
  echo "expected 22 distinct pinned upstream trees" >&2
  exit 1
fi

echo "c-facts external corpus tests: PASS"
