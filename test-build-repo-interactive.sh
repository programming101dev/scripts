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
grep -Fq 'Retrying: build' "$sandbox/retry.stderr"

: > "$sandbox/repo/build-invocations.txt"
touch "$sandbox/repo/always-fail"
set +e
printf 'q\n' | "$sandbox/scripts/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  --interactive -I > "$sandbox/abort.stdout" 2> "$sandbox/abort.stderr"
status=$?
set -e
[[ "$status" -eq 7 ]]
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 1 ]]
grep -Fq 'Aborting at: build' "$sandbox/abort.stderr"

mkdir -p "$sandbox/matrix"
cp ./update-all.sh "$sandbox/matrix/update-all.sh"
chmod +x "$sandbox/matrix/update-all.sh"
cat > "$sandbox/matrix/driver.sh" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > driver-arguments.txt
EOF
chmod +x "$sandbox/matrix/driver.sh"
printf 'clang\n' > "$sandbox/matrix/c.txt"
printf 'clang++\n' > "$sandbox/matrix/cxx.txt"
(
  cd "$sandbox/matrix"
  ./update-all.sh -u ./driver.sh -C c.txt -X cxx.txt \
    -f clang-format -t clang-tidy -k cppcheck -s address \
    --interactive --skip-install > update-all.stdout
)
grep -Fq 'source snapshot without Git metadata; skipping self-update' \
  "$sandbox/matrix/update-all.stdout"
grep -Fxq -- '--interactive' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--skip-install' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- 'address' "$sandbox/matrix/driver-arguments.txt"

printf 'PASS: interactive repository phase retry and abort behavior\n'
