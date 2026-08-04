#!/usr/bin/env bash
# A scripts-only checkout must not require the separate setup repository.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-copy-scripts.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

mkdir -p "$sandbox/scripts/distribution" "$sandbox/templates/template-c"
cp distribution/copy-scripts.sh "$sandbox/scripts/distribution/copy-scripts.sh"
: > "$sandbox/scripts/repos.txt"
printf 'canonical ignore\n' > "$sandbox/templates/template-c/.gitignore"
printf 'canonical license\n' > "$sandbox/templates/template-c/LICENSE"
cp "$sandbox/templates/template-c/.gitignore" "$sandbox/scripts/.gitignore"
cp "$sandbox/templates/template-c/LICENSE" "$sandbox/scripts/LICENSE"

(
  cd "$sandbox/scripts"
  ./distribution/copy-scripts.sh -c
) > "$sandbox/output"

grep -Fq 'PASS: all shared repo scripts match their canonical copies.' \
  "$sandbox/output"
[[ ! -e "$sandbox/setup" ]]

printf 'PASS: scripts-only distribution does not require ../setup.\n'
