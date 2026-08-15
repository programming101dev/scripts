#!/bin/sh
set -eu

root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
sandbox=$(mktemp -d "${TMPDIR:-/tmp}/p101-stack-contract.XXXXXX")
cleanup()
{
  rm -rf "$sandbox"
}
trap cleanup EXIT HUP INT TERM

workspace=$sandbox/workspace
scripts=$workspace/scripts
mkdir -p "$scripts/contracts" "$scripts/cmake" "$scripts/shared" "$scripts/workspace"
cp "$root/contracts/p101-stack-paths.txt" "$scripts/contracts/p101-stack-paths.txt"
cp "$root/cmake/p101_compile_db.c" "$scripts/cmake/p101_compile_db.c"
cp "$root/shared/bootstrap.sh" "$scripts/shared/bootstrap.sh"
cp "$root/workspace/stack-contract.sh" "$scripts/workspace/stack-contract.sh"
chmod +x "$scripts/workspace/stack-contract.sh"

while IFS= read -r relative || [ -n "$relative" ]; do
  path=$scripts/$relative
  [ -e "$path" ] && continue
  mkdir -p "$(dirname -- "$path")"
  printf '%s\n' "$relative" > "$path"
done < "$scripts/contracts/p101-stack-paths.txt"

contract=$scripts/contracts/p101-stack-contract.json
receipt=$sandbox/receipt.json
"$scripts/workspace/stack-contract.sh" --scripts-root "$scripts" \
  --contract "$contract" refresh >/dev/null
"$scripts/workspace/stack-contract.sh" --scripts-root "$scripts" \
  --contract "$contract" verify --receipt "$receipt" >/dev/null
grep -q '"passed":true' "$receipt"
grep -q '"receipt_digest":"sha256:' "$receipt"

printf 'changed\n' > "$scripts/contracts/p101-boundaries.json"
status=0
"$scripts/workspace/stack-contract.sh" --scripts-root "$scripts" \
  --contract "$contract" verify >/dev/null || status=$?
[ "$status" -eq 1 ]

candidate_lock=$sandbox/candidate.lock
candidate_contract=$sandbox/candidate-contract.json
printf 'candidate\n' > "$candidate_lock"
P101_STACK_REPOS_LOCK=$candidate_lock \
  "$scripts/workspace/stack-contract.sh" --scripts-root "$scripts" \
  --contract "$candidate_contract" refresh >/dev/null
printf 'unrelated\n' > "$scripts/repos.lock"
P101_STACK_REPOS_LOCK=$candidate_lock \
  "$scripts/workspace/stack-contract.sh" --scripts-root "$scripts" \
  --contract "$candidate_contract" verify >/dev/null

printf '{}\n' > "$contract"
status=0
"$scripts/workspace/stack-contract.sh" --scripts-root "$scripts" \
  --contract "$contract" verify >"$sandbox/invalid.out" \
  2>"$sandbox/invalid.err" || status=$?
[ "$status" -eq 2 ]
grep -q 'p101-stack-contract-refusal-v1' "$sandbox/invalid.err"

printf 'PASS: native stack contract refresh, verify, drift, override, and refusal\n'
