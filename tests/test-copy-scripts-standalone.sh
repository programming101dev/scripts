#!/usr/bin/env bash
# A scripts-only checkout must not require the separate setup repository.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-copy-scripts.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

mkdir -p "$sandbox/scripts/distribution" \
  "$sandbox/templates/template-c" \
  "$sandbox/examples/c-examples"
cp distribution/copy-scripts.sh "$sandbox/scripts/distribution/copy-scripts.sh"
printf '|../examples/c-examples|c\n' > "$sandbox/scripts/repos.txt"
printf 'canonical ignore\n' > "$sandbox/templates/template-c/.gitignore"
printf 'canonical license\n' > "$sandbox/templates/template-c/LICENSE"
for script in check-compilers.sh doctor.sh; do
  printf '#!/bin/sh\nexit 0\n' > "$sandbox/templates/template-c/$script"
  chmod +x "$sandbox/templates/template-c/$script"
  cp "$sandbox/templates/template-c/$script" "$sandbox/examples/c-examples/$script"
done
cp "$sandbox/templates/template-c/.gitignore" \
  "$sandbox/examples/c-examples/.gitignore"
cp "$sandbox/templates/template-c/LICENSE" "$sandbox/examples/c-examples/LICENSE"
cp "$sandbox/templates/template-c/.gitignore" "$sandbox/scripts/.gitignore"
cp "$sandbox/templates/template-c/LICENSE" "$sandbox/scripts/LICENSE"

(
  cd "$sandbox/scripts"
  ./distribution/copy-scripts.sh -c
) > "$sandbox/output"

grep -Fq 'PASS: all shared repo scripts match their canonical copies.' \
  "$sandbox/output"
grep -Fq 'canonical ignore' "$sandbox/examples/c-examples/.gitignore"
[[ ! -e "$sandbox/setup" ]]

printf 'PASS: scripts-only distribution does not require ../setup.\n'
