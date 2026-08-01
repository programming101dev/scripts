#!/usr/bin/env bash
# Verify that build-repo.sh retries only the failed phase in interactive mode.
set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "test-build-repo-interactive.sh — exercise interactive retry and abort behavior."
    exit 0
    ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-interactive-build.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT
mkdir -p "$sandbox/scripts" "$sandbox/repo"
cp ./build-repo.sh "$sandbox/scripts/build-repo.sh"
chmod +x "$sandbox/scripts/build-repo.sh"

mkdir -p "$sandbox/bin"
cat > "$sandbox/bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$P101_TEST_GIT_LOG"
if [[ -n "${P101_TEST_GIT_FAIL_ONCE_FILE:-}" && -f "$P101_TEST_GIT_FAIL_ONCE_FILE" ]]; then
  rm -f "$P101_TEST_GIT_FAIL_ONCE_FILE"
  exit 9
fi
EOF
chmod +x "$sandbox/bin/git"
export PATH="$sandbox/bin:$PATH"
export P101_TEST_GIT_LOG="$sandbox/git-invocations.txt"

cat > "$sandbox/scripts/repos.txt" <<EOF
https://example.invalid/test.git|$sandbox/repo|c
EOF

cat > "$sandbox/repo/change-compiler.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> configure-invocations.txt
EOF

cat > "$sandbox/repo/build.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
count=0
if [[ -f build-invocations.txt ]]; then
  count="$(wc -l < build-invocations.txt)"
fi
printf 'build\n' >> build-invocations.txt
if [[ -f always-fail ]] || [[ "$count" -eq 0 ]]; then
  exit 7
fi
EOF
chmod +x "$sandbox/repo/change-compiler.sh" "$sandbox/repo/build.sh"

tool="/usr/bin/true"
[[ -x "$tool" ]] || tool="$(command -v true)"

printf '\n' | "$sandbox/scripts/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  --interactive -I > "$sandbox/retry.stdout" 2> "$sandbox/retry.stderr"

[[ "$(wc -l < "$sandbox/repo/configure-invocations.txt")" -eq 1 ]]
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 2 ]]
grep -Fxq 'pull --ff-only --no-stat --no-edit' "$P101_TEST_GIT_LOG"
grep -Fq 'Pulling repository updates before retry' "$sandbox/retry.stderr"
grep -Fq 'Retrying: build' "$sandbox/retry.stderr"

: > "$sandbox/repo/build-invocations.txt"
: > "$P101_TEST_GIT_LOG"
touch "$sandbox/fail-pull-once"
export P101_TEST_GIT_FAIL_ONCE_FILE="$sandbox/fail-pull-once"
printf '\n\n' | "$sandbox/scripts/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  --interactive -I > "$sandbox/pull-retry.stdout" 2> "$sandbox/pull-retry.stderr"
unset P101_TEST_GIT_FAIL_ONCE_FILE
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 2 ]]
[[ "$(wc -l < "$P101_TEST_GIT_LOG")" -eq 2 ]]
grep -Fq 'Pull failed (exit 9); phase not retried' "$sandbox/pull-retry.stderr"

: > "$sandbox/repo/build-invocations.txt"
: > "$P101_TEST_GIT_LOG"
touch "$sandbox/repo/always-fail"
set +e
printf 'q\n' | "$sandbox/scripts/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  --interactive -I > "$sandbox/abort.stdout" 2> "$sandbox/abort.stderr"
status=$?
set -e
[[ "$status" -eq 7 ]]
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 1 ]]
[[ ! -s "$P101_TEST_GIT_LOG" ]]
grep -Fq 'Aborting at: build' "$sandbox/abort.stderr"

mkdir -p "$sandbox/matrix"
cp ./update-all.sh "$sandbox/matrix/update-all.sh"
cp ./pull.sh "$sandbox/matrix/pull.sh"
chmod +x "$sandbox/matrix/update-all.sh" "$sandbox/matrix/pull.sh"
cat > "$sandbox/matrix/driver.sh" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > driver-arguments.txt
EOF
chmod +x "$sandbox/matrix/driver.sh"
printf 'clang\n' > "$sandbox/matrix/c.txt"
printf 'clang++\n' > "$sandbox/matrix/cxx.txt"
# FreeBSD VM actions may copy the checkout's .git file while leaving its
# referenced Git directory behind. Ensure that unusable metadata is treated as
# a source snapshot rather than as a repository that can self-update.
printf 'gitdir: /definitely/missing/p101-scripts-git-dir\n' > "$sandbox/matrix/.git"
(
  cd "$sandbox/matrix"
  ./update-all.sh -u ./driver.sh -C c.txt -X cxx.txt \
    -f clang-format -t clang-tidy -k cppcheck -s address \
    --interactive --skip-install > update-all.stdout
)
grep -Fq 'source snapshot without usable Git metadata; skipping self-update' \
  "$sandbox/matrix/update-all.stdout"
grep -Fxq -- '--skip-self-update' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--interactive' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--skip-install' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- 'address' "$sandbox/matrix/driver-arguments.txt"

printf 'PASS: interactive repository phase retry and abort behavior\n'
