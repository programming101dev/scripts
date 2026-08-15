#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
sandbox=$(mktemp -d "${TMPDIR:-/tmp}/p101-performance.XXXXXX")
cleanup()
{
  rm -rf "$sandbox"
}
trap cleanup EXIT HUP INT TERM

receipt()
{
  path=$1
  elapsed=$2
  identity=$3
  reused=$4
  outcome=clean
  [ "$reused" -eq 0 ] || outcome=reused
  printf '{"schema":"p101-check-graph-receipt-v2","outcome":"clean","mode":"measurement","cache":{"reused":%s},"elapsed_ns":%s,"records":[{"id":"one","outcome":"%s","input_identity":"%s","result":{"exit_code":0}}]}\n' \
    "$reused" "$elapsed" "$outcome" "$identity" > "$path"
}

set --
index=0
while [ "$index" -lt 5 ]; do
  baseline=$sandbox/b$index.json
  candidate=$sandbox/c$index.json
  receipt "$baseline" "$((100 + index))" same 0
  receipt "$candidate" "$((70 + index))" same 0
  set -- "$@" --baseline "$baseline" --candidate "$candidate"
  index=$((index + 1))
done
"$root/checks/compare-check-performance.sh" "$@" > "$sandbox/clean.out"
grep -q faster "$sandbox/clean.out"

receipt "$sandbox/c4.json" 70 different 0
result=0
"$root/checks/compare-check-performance.sh" "$@" > "$sandbox/drift.out" || result=$?
[ "$result" -eq 2 ]
grep -q 'identity differs' "$sandbox/drift.out"

index=0
while [ "$index" -lt 5 ]; do
  receipt "$sandbox/c$index.json" 1 same 1
  index=$((index + 1))
done
result=0
"$root/checks/compare-check-performance.sh" "$@" > "$sandbox/reused.out" || result=$?
[ "$result" -eq 2 ]
grep -q 'reused cached nodes' "$sandbox/reused.out"

printf 'PASS: native performance comparison identity and cache controls\n'
