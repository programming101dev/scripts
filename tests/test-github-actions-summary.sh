#!/usr/bin/env bash
# Exercise GitHub summary publication without requiring a GitHub runner.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

cmp .github/workflows/p101-stack.yml github-actions/p101-stack.yml
grep -Fq 'workspace-candidate.json' github-actions/preflight.sh
grep -Fq -- '--acceptance-receipt' github-actions/preflight.sh
grep -Fq 'P101_STACK_REPOS_LOCK="$candidate_lock"' github-actions/preflight.sh
grep -Fq -- '--parallel "$acceptance_jobs"' github-actions/preflight.sh
grep -Fq 'P101_STACK_CONTRACT="$candidate_stack_contract"' \
  github-actions/preflight.sh
grep -Fq -- '-DP101_ACCEPTANCE_NO_CACHE=OFF' github-actions/preflight.sh
grep -Fq 'candidate_lock_sha256:' github-actions/p101-stack.yml
grep -Fq 'candidate_stack_contract_sha256:' github-actions/p101-stack.yml
grep -Fq 'qualification_ref:' github-actions/p101-stack.yml
grep -Fq 'platform-qualification' github-actions/p101-stack.yml
grep -Fq 'aggregate-qualification' github-actions/p101-stack.yml
grep -Fq 'name: workspace-qualification' github-actions/p101-stack.yml
grep -Fq 'candidate qualification must run all supported platforms' \
  github-actions/p101-stack.yml
grep -Fq 'git config --global --add safe.directory "$(pwd -P)"' \
  .github/workflows/p101-stack.yml
grep -Fq -- '--acceptance-output ci-output' .github/workflows/p101-stack.yml
grep -Fq -- '--matrix-output ci-output/compiler-matrix' \
  .github/workflows/p101-stack.yml
[[ "$(grep -c './update-all.sh .*--level 3' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
grep -Fq 'ln -sf "/usr/local/llvm${required}/bin/clang-tidy" /usr/local/bin/clang-tidy' \
  .github/workflows/p101-stack.yml
[[ "$(grep -c 'Prepare .* once, then build compiler pairs' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c 'platform-sentinel.sh' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c './update-all.sh -C ci_c_compilers.txt' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c './update-all.sh .*--format-check' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c 'uses: actions/cache/restore@v5' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c 'uses: actions/cache/save@v5' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c 'uses: actions/download-artifact@v8' \
  .github/workflows/p101-stack.yml)" -eq 1 ]]
[[ "$(grep -c 'digest-mismatch: error' \
  .github/workflows/p101-stack.yml)" -eq 1 ]]
[[ "$(grep -c 'P101_REPOSITORY_BUILD_CACHE=' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c 'P101_TEST_BUILD_CACHE=' \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c 'target/check-evidence-cache' \
  .github/workflows/p101-stack.yml)" -eq 6 ]]
[[ "$(grep -c 'target/ci-build-cache' \
  .github/workflows/p101-stack.yml)" -eq 9 ]]
[[ "$(grep -c 'target/ci-test-cache' \
  .github/workflows/p101-stack.yml)" -eq 9 ]]
[[ "$(grep -c "steps.workspace-cache.outputs.cache-hit != 'true'" \
  .github/workflows/p101-stack.yml)" -eq 3 ]]
[[ "$(grep -c "steps.update.outcome == 'success'.*steps.workspace-cache.outputs.cache-hit" \
  .github/workflows/p101-stack.yml)" -eq 2 ]]
grep -Fq "steps.freebsd-cache.outputs.save == 'true'" \
  .github/workflows/p101-stack.yml
if [[ "${P101_SNAPSHOT_ONLY:-0}" != 1 ]]; then
  grep -Fq 'P101_TEST_BUILD_CACHE' ../templates/template-c/test.sh
  grep -Fq 'P101_TEST_BUILD_CACHE' ../templates/template-cxx/test.sh
  grep -Fq 'components/$component_name' ../programs/p101-audit/test-components.sh
  grep -Fq 'components/mutation' ../programs/p101-test/test-components.sh
fi
[[ "$(grep -c '^[[:space:]]\{12\}exit 0$' \
  .github/workflows/p101-stack.yml)" -ge 1 ]]
if awk '
  /key: .*steps\.workspace-cache\.outputs\.cache-primary-key/ {
    getline
    if ($0 ~ /^[[:space:]]+exit 0$/) found = 1
  }
  END { exit found ? 0 : 1 }
' .github/workflows/p101-stack.yml; then
  echo 'FreeBSD VM exit leaked into an action input map.' >&2
  exit 1
fi
if grep -Fq './distribution/clone-repos.sh' \
  .github/workflows/p101-stack.yml; then
  echo 'GitHub Actions clones repositories outside update-all preparation.' >&2
  exit 1
fi
if grep -Fq './workspace/check-compilers.sh' \
  .github/workflows/p101-stack.yml; then
  echo 'GitHub Actions discovers compilers outside update-all preparation.' >&2
  exit 1
fi
if grep -Fq -- '- name: Check after update-all' .github/workflows/p101-stack.yml; then
  echo 'GitHub Actions still runs the governed graph twice.' >&2
  exit 1
fi

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
mkdir -p "$sandbox/early/compiler-matrix"
cat > "$sandbox/early/update-all.log" <<'EOF'
Configuring repositories...
/workspace/tool.c:87:5: error: 'switch' missing 'default' label
make: stopped
EOF
printf 'index\tc_compiler\tcxx_compiler\tstatus\texit\telapsed_seconds\tlog\n' \
  > "$sandbox/early/compiler-matrix/summary.tsv"
printf '0001\tclang\tclang++\tPASS\t0\t12\t%s\n' \
  "$sandbox/early/compiler-matrix/0001-clang.log" \
  >> "$sandbox/early/compiler-matrix/summary.tsv"
printf '0002\tgcc\tg++\tFAIL\t2\t15\t%s\n' \
  "$sandbox/early/compiler-matrix/0002-gcc.log" \
  >> "$sandbox/early/compiler-matrix/summary.tsv"
cat > "$sandbox/early/compiler-matrix/summary.md" <<'EOF'
# p101 compiler matrix

| C compiler | C++ compiler | Result | Exit | Seconds | Log |
| --- | --- | --- | ---: | ---: | --- |
| clang | clang++ | PASS | 0 | 12 | `clang.log` |
| gcc | g++ | FAIL | 2 | 15 | `gcc.log` |
EOF
cat > "$sandbox/early/compiler-matrix/0002-gcc.log" <<'EOF'
/workspace/gcc-only.c:23:7: error: GCC pair failure
EOF
GITHUB_STEP_SUMMARY="$sandbox/early-summary.md" \
  ./github-actions/publish-ci-summary.sh \
  "$sandbox/early" FreeBSD failure skipped > "$sandbox/early-stdout"
grep -Fq '::error title=p101%3A gcc %3A g++::/workspace/gcc-only.c:23:7: error: GCC pair failure' \
  "$sandbox/early-stdout"
grep -Fq 'did not produce a summary' "$sandbox/early-summary.md"
grep -Fq '# p101 compiler matrix' "$sandbox/early-summary.md"
grep -Fq '<summary>Compiler pair: gcc : g++</summary>' "$sandbox/early-summary.md"
grep -Fq '/workspace/gcc-only.c:23:7: error: GCC pair failure' "$sandbox/early-summary.md"

mkdir -p "$sandbox/freebsd"
cat > "$sandbox/freebsd/clone.log" <<'EOF'
Cloning repositories...
fatal: repository 'missing' not found
EOF
printf 'clone\n' > "$sandbox/freebsd/freebsd-failed-phase"
printf '1\n' > "$sandbox/freebsd/freebsd-exit-code"
GITHUB_STEP_SUMMARY="$sandbox/freebsd-summary.md" \
  ./github-actions/publish-ci-summary.sh \
  "$sandbox/freebsd" FreeBSD not-run success > "$sandbox/freebsd-stdout"
grep -Fq '| Repository update/build | failure |' "$sandbox/freebsd-summary.md"
grep -Fq '| Governed acceptance graph | not-run |' "$sandbox/freebsd-summary.md"
grep -Fq '<summary>Repository clone failure</summary>' "$sandbox/freebsd-summary.md"
grep -Fq "fatal: repository 'missing' not found" "$sandbox/freebsd-summary.md"
grep -Fq '::error title=p101%3A repository update/build::Cloning repositories...' \
  "$sandbox/freebsd-stdout"

mkdir -p "$sandbox/freebsd-invalid"
printf 'check\n' > "$sandbox/freebsd-invalid/freebsd-failed-phase"
printf '1 (core dumped)\n' > "$sandbox/freebsd-invalid/freebsd-exit-code"
GITHUB_STEP_SUMMARY="$sandbox/freebsd-invalid-summary.md" \
  ./github-actions/publish-ci-summary.sh \
  "$sandbox/freebsd-invalid" FreeBSD success success \
  > "$sandbox/freebsd-invalid-stdout" \
  2> "$sandbox/freebsd-invalid-stderr"
grep -Fq 'Invalid FreeBSD exit-status receipt: 1 (core dumped)' \
  "$sandbox/freebsd-invalid-stderr"
grep -Fq '| Repository update/build | failure |' \
  "$sandbox/freebsd-invalid-summary.md"
grep -Fq '| Governed acceptance graph | failure |' \
  "$sandbox/freebsd-invalid-summary.md"

./tests/test-freebsd-result.sh

printf 'PASS: GitHub Actions failures produce annotations and an inline job summary.\n'
