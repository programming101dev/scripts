#!/usr/bin/env bash
# Fail quickly on snapshot-local host-shell and compiler-lane regressions.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

tests=(
  tests/test-compiler-fingerprint.sh
  tests/test-build-lane.sh
  tests/test-github-actions-summary.sh
)
output="$(mktemp -d "${TMPDIR:-/tmp}/p101-platform-sentinel.XXXXXX")"
trap 'rm -rf "$output"' EXIT

pids=()
for test_path in "${tests[@]}"; do
  test_name="$(basename -- "$test_path" .sh)"
  "$test_path" > "$output/$test_name.log" 2>&1 &
  pids+=("$!")
done

status=0
for index in "${!tests[@]}"; do
  test_path="${tests[$index]}"
  test_name="$(basename -- "$test_path" .sh)"
  if wait "${pids[$index]}"; then
    printf '[PASS] %s\n' "$test_name"
  else
    test_status=$?
    printf '[FAIL] %s (exit %d)\n' "$test_name" "$test_status" >&2
    sed 's/^/| /' "$output/$test_name.log" >&2
    status=1
  fi
done

if [[ "$status" -ne 0 ]]; then
  printf 'Platform sentinel failed before the expensive workspace build.\n' >&2
else
  printf 'Platform sentinel passed.\n'
fi
exit "$status"
