#!/usr/bin/env bash
# Exercise GitHub summary publication without requiring a GitHub runner.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-github-summary.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/output/logs"

cat > "$sandbox/output/summary.md" <<'EOF'
# p101 governed check graph

| Status | Check | Time | Result | Artifact |
| --- | --- | ---: | --- | --- |
| CLEAN | passing check | 0.001s | PASS | [log](./logs/pass.log) |
| TOOL-ERROR | failing check | 0.002s | compiler failed | [log](./logs/fail.log) |
| BLOCKED | dependent check | 0.000s | blocked | [log](./logs/blocked.log) |
EOF
cat > "$sandbox/output/logs/fail.log" <<'EOF'
$ ./failing-command

source.c:12:4: error: deliberately broken
EOF
cat > "$sandbox/output/logs/blocked.log" <<'EOF'
blocked by failed dependencies: failing-check
EOF

GITHUB_STEP_SUMMARY="$sandbox/step-summary.md" \
  ./github-actions/publish-ci-summary.sh \
  "$sandbox/output" Linux success failure > "$sandbox/stdout"

grep -Fq '::error title=p101%3A failing check::source.c:12:4: error: deliberately broken' \
  "$sandbox/stdout"
grep -Fq '::error title=p101%3A dependent check::blocked by failed dependencies: failing-check' \
  "$sandbox/stdout"
grep -Fq '# p101 CI result — Linux' "$sandbox/step-summary.md"
grep -Fq '<summary>failing check</summary>' "$sandbox/step-summary.md"
grep -Fq 'source.c:12:4: error: deliberately broken' "$sandbox/step-summary.md"
cmp -s "$sandbox/output/github-step-summary.md" "$sandbox/step-summary.md"

mkdir -p "$sandbox/early"
cat > "$sandbox/early/update-all.log" <<'EOF'
Configuring repositories...
/workspace/tool.c:87:5: error: 'switch' missing 'default' label
make: stopped
EOF
GITHUB_STEP_SUMMARY="$sandbox/early-summary.md" \
  ./github-actions/publish-ci-summary.sh \
  "$sandbox/early" FreeBSD failure skipped > "$sandbox/early-stdout"
grep -Fq "::error title=p101%3A repository update/build::/workspace/tool.c:87:5: error: 'switch' missing 'default' label" \
  "$sandbox/early-stdout"
grep -Fq 'did not produce a summary' "$sandbox/early-summary.md"
grep -Fq '<summary>Repository update/build failure</summary>' "$sandbox/early-summary.md"
grep -Fq "/workspace/tool.c:87:5: error: 'switch' missing 'default' label" "$sandbox/early-summary.md"

printf 'PASS: GitHub Actions failures produce annotations and an inline job summary.\n'
