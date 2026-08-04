#!/usr/bin/env bash
# Verify that FreeBSD receipt enforcement prints actionable evidence.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-freebsd-result.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

mkdir -p "$sandbox/output"
printf '0\n' > "$sandbox/output/freebsd-exit-code"
./github-actions/enforce-freebsd-result.sh "$sandbox/output"

printf 'update\n' > "$sandbox/output/freebsd-failed-phase"
printf '7\n' > "$sandbox/output/freebsd-exit-code"
cat > "$sandbox/output/update-all.log" <<'EOF'
starting update
source.c:10:2: error: deliberate FreeBSD failure
EOF
cat > "$sandbox/output/github-step-summary.md" <<'EOF'
# p101 CI result — FreeBSD

The update phase failed.
EOF

set +e
./github-actions/enforce-freebsd-result.sh "$sandbox/output" \
  > "$sandbox/stdout" 2> "$sandbox/stderr"
status=$?
set -e

[[ "$status" -eq 7 ]]
grep -Fq 'FreeBSD update phase failed with exit 7.' "$sandbox/stderr"
grep -Fq '# p101 CI result — FreeBSD' "$sandbox/stderr"
grep -Fq 'source.c:10:2: error: deliberate FreeBSD failure' "$sandbox/stderr"

mkdir -p "$sandbox/missing"
set +e
./github-actions/enforce-freebsd-result.sh "$sandbox/missing" \
  > "$sandbox/missing-stdout" 2> "$sandbox/missing-stderr"
status=$?
set -e
[[ "$status" -eq 2 ]]
grep -Fq 'did not return an exit-status receipt' "$sandbox/missing-stderr"

printf 'PASS: FreeBSD receipt enforcement prints the failing phase and evidence.\n'
