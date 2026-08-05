#!/usr/bin/env bash
# Verify that build-repo.sh retries only the failed phase in interactive mode.
set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "test-build-repo-interactive.sh — exercise interactive retry and abort behavior."
    exit 0
    ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-interactive-build.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

# Sanitizer capability is a link-time property. A target may accept a
# sanitizer option for compilation while lacking the corresponding runtime.
sanitizer_flags="$sandbox/sanitizer-flags"
mkdir -p "$sanitizer_flags"
printf '%s\n' '-fsanitize=address' > "$sanitizer_flags/address_sanitizer_flags.txt"
printf '%s\n' '-fsanitize=leak' > "$sanitizer_flags/leak_sanitizer_flags.txt"
printf '%s\n' '-fsanitize=undefined' > "$sanitizer_flags/undefined_sanitizer_flags.txt"
printf '%s\n' '-fsanitize=thread' > "$sanitizer_flags/thread_sanitizer_flags.txt"

cat > "$sandbox/fake-sanitizer-compiler" <<'EOF'
#!/bin/sh
args=" $* "
case "$args" in
  *" -fsanitize=leak "*) exit 1 ;;
esac
case "$args" in
  *" -fsanitize=address "*)
    case "$args" in
      *" -fsanitize=thread "*) exit 1 ;;
    esac
    ;;
esac
exit 0
EOF
chmod +x "$sandbox/fake-sanitizer-compiler"

filtered="$(
  ./workspace/filter-sanitizers.sh \
    "$sandbox/fake-sanitizer-compiler" "$sanitizer_flags" \
    address,leak,undefined
)"
[[ "$filtered" == "address,undefined" ]]

if ./workspace/filter-sanitizers.sh \
    "$sandbox/fake-sanitizer-compiler" "$sanitizer_flags" \
    address,thread >"$sandbox/conflict.out" 2>"$sandbox/conflict.err"; then
  echo "expected incompatible supported sanitizers to fail" >&2
  exit 1
fi
grep -q 'cannot be combined' "$sandbox/conflict.err"

mkdir -p "$sandbox/scripts/workspace" "$sandbox/scripts/distribution" "$sandbox/repo"
cp ./workspace/build-repo.sh "$sandbox/scripts/workspace/build-repo.sh"
chmod +x "$sandbox/scripts/workspace/build-repo.sh"

cat > "$sandbox/scripts/distribution/refresh-repo.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$P101_TEST_REFRESH_LOG"
if [[ -n "${P101_TEST_REFRESH_FAIL_ONCE_FILE:-}" && -f "$P101_TEST_REFRESH_FAIL_ONCE_FILE" ]]; then
  rm -f "$P101_TEST_REFRESH_FAIL_ONCE_FILE"
  exit 3
fi
exit 1
EOF
chmod +x "$sandbox/scripts/distribution/refresh-repo.sh"
export P101_TEST_REFRESH_LOG="$sandbox/refresh-invocations.txt"

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

printf '\n' | "$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  --interactive -I > "$sandbox/retry.stdout" 2> "$sandbox/retry.stderr"

[[ "$(wc -l < "$sandbox/repo/configure-invocations.txt")" -eq 1 ]]
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 2 ]]
grep -Fxq '.' "$P101_TEST_REFRESH_LOG"
grep -Fq 'Refreshing repository upstream before retry' "$sandbox/retry.stderr"
grep -Fq 'Retrying: build' "$sandbox/retry.stderr"

: > "$sandbox/repo/build-invocations.txt"
: > "$P101_TEST_REFRESH_LOG"
touch "$sandbox/fail-refresh-once"
export P101_TEST_REFRESH_FAIL_ONCE_FILE="$sandbox/fail-refresh-once"
printf '\n\n' | "$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  --interactive -I > "$sandbox/pull-retry.stdout" 2> "$sandbox/pull-retry.stderr"
unset P101_TEST_REFRESH_FAIL_ONCE_FILE
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 2 ]]
[[ "$(wc -l < "$P101_TEST_REFRESH_LOG")" -eq 2 ]]
grep -Fq 'Repository refresh failed (exit 3); still paused' "$sandbox/pull-retry.stderr"

: > "$sandbox/repo/build-invocations.txt"
: > "$P101_TEST_REFRESH_LOG"
touch "$sandbox/repo/always-fail"
set +e
printf 'q\n' | "$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  --interactive -I > "$sandbox/abort.stdout" 2> "$sandbox/abort.stderr"
status=$?
set -e
[[ "$status" -eq 7 ]]
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 1 ]]
[[ ! -s "$P101_TEST_REFRESH_LOG" ]]
grep -Fq 'Aborting at: build' "$sandbox/abort.stderr"

# An installable sanitizer build must be followed by a distinct,
# sanitizer-free runtime build. The strict marker remains the quality build;
# the runtime marker and install argument identify the consumer-safe artifact.
runtime_repo="$sandbox/runtime-repo"
mkdir -p "$runtime_repo"
cat > "$sandbox/scripts/repos.txt" <<EOF
https://example.invalid/runtime.git|$runtime_repo|c
EOF
cat > "$runtime_repo/change-compiler.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
build_dir="build-quality"
sanitizers="<omitted>"
cmake_arguments=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -b) build_dir="$2"; shift 2 ;;
    -s) sanitizers="$2"; shift 2 ;;
    --) shift; cmake_arguments="$*"; break ;;
    -c|-f|-t|-k) shift 2 ;;
    *) shift ;;
  esac
done
printf 'build=%s sanitizers=%s cmake=%s\n' \
  "$build_dir" "$sanitizers" "$cmake_arguments" >> configure-invocations.txt
mkdir -p "$build_dir"
printf '%s\n' "$build_dir" > .last-build-dir
if [[ -z "$sanitizers" ]]; then
  printf '%s\n' "$build_dir" > .last-runtime-build-dir
fi
EOF
cat > "$runtime_repo/build.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$(cat .last-build-dir)" >> build-invocations.txt
EOF
cat > "$runtime_repo/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" > install-arguments.txt
EOF
chmod +x "$runtime_repo/change-compiler.sh" "$runtime_repo/build.sh" \
  "$runtime_repo/install.sh"
"$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -s address > "$sandbox/runtime.stdout" 2> "$sandbox/runtime.stderr"
[[ "$(wc -l < "$runtime_repo/configure-invocations.txt")" -eq 2 ]]
[[ "$(wc -l < "$runtime_repo/build-invocations.txt")" -eq 2 ]]
grep -Fq 'build=build-quality sanitizers=address' \
  "$runtime_repo/configure-invocations.txt"
grep -Fq 'build=build-quality-runtime sanitizers= cmake=-DP101_RUNTIME_ONLY=ON' \
  "$runtime_repo/configure-invocations.txt"
[[ "$(cat "$runtime_repo/.last-build-dir")" == "build-quality" ]]
[[ "$(cat "$runtime_repo/.last-runtime-build-dir")" == "build-quality-runtime" ]]
grep -Fxq -- '-b build-quality-runtime' "$runtime_repo/install-arguments.txt"

install_repo="$sandbox/install-selection"
mkdir -p "$install_repo/build-quality" "$install_repo/build-runtime"
cp ./shared/library/install.sh "$install_repo/install.sh"
chmod +x "$install_repo/install.sh"
printf '%s\n' build-quality > "$install_repo/.last-build-dir"
printf '%s\n' build-runtime > "$install_repo/.last-runtime-build-dir"
(
  cd "$install_repo"
  ./install.sh -n -v
) > "$sandbox/install-selection.stdout"
grep -Eq '^Build dir[[:space:]]*: build-runtime$' \
  "$sandbox/install-selection.stdout"
grep -Fq 'cmake --install build-runtime' "$sandbox/install-selection.stdout"

mkdir -p "$sandbox/matrix"
cp ./update-all.sh "$sandbox/matrix/update-all.sh"
mkdir -p "$sandbox/matrix/distribution" "$sandbox/matrix/workspace"
cp ./distribution/pull.sh "$sandbox/matrix/distribution/pull.sh"
cp ./distribution/refresh-repo.sh "$sandbox/matrix/distribution/refresh-repo.sh"
cp ./workspace/update.sh "$sandbox/matrix/workspace/update.sh"
chmod +x "$sandbox/matrix/update-all.sh" "$sandbox/matrix/distribution/pull.sh" \
  "$sandbox/matrix/distribution/refresh-repo.sh" "$sandbox/matrix/workspace/update.sh"
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
grep -Fq 'source snapshot without usable Git metadata; skipping refresh' \
  "$sandbox/matrix/update-all.stdout"
grep -Fxq -- '--skip-self-update' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--interactive' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--skip-install' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- 'address' "$sandbox/matrix/driver-arguments.txt"

snapshot_root="$sandbox/update-snapshot"
snapshot_scripts="$snapshot_root/scripts"
mkdir -p "$snapshot_scripts/workspace" "$snapshot_scripts/distribution" \
  "$snapshot_scripts/generators" "$snapshot_root/.flags" "$snapshot_root/bin"
cp ./workspace/update.sh "$snapshot_scripts/workspace/update.sh"
chmod +x "$snapshot_scripts/workspace/update.sh"

cat > "$snapshot_root/bin/tool" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$snapshot_root/bin/tool"
for name in clang clang++ clang-format clang-tidy cppcheck; do
  ln -s tool "$snapshot_root/bin/$name"
done

cat > "$snapshot_root/bin/diff" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
touch "$P101_TEST_DIFF_READY"
while [[ ! -f "$P101_TEST_DIFF_CONTINUE" ]]; do
  sleep 0.01
done
exit 0
EOF
chmod +x "$snapshot_root/bin/diff"

cat > "$snapshot_scripts/helper" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$snapshot_scripts/helper"
for helper in \
  distribution/pull.sh \
  workspace/check-env.sh \
  distribution/clone-repos.sh \
  workspace/check-compilers.sh \
  workspace/compiler-fingerprint.sh \
  workspace/filter-sanitizers.sh \
  generators/generate-flags.sh \
  distribution/link-flags.sh \
  distribution/link-compilers.sh \
  distribution/link-cmake.sh \
  workspace/build-repo.sh \
  distribution/copy-scripts.sh \
  distribution/copy-playground-track-scripts.sh \
  distribution/remove-retired-repos.sh \
  distribution/copy-cmake.sh
do
  ln -s ../helper "$snapshot_scripts/$helper"
done
printf 'clang\n' > "$snapshot_scripts/supported_c_compilers.txt"
printf 'clang++\n' > "$snapshot_scripts/supported_cxx_compilers.txt"
printf '1\n' > "$snapshot_scripts/version.txt"
printf '1\n' > "$snapshot_root/.flags/version.txt"

export P101_TEST_DIFF_READY="$snapshot_root/diff-ready"
export P101_TEST_DIFF_CONTINUE="$snapshot_root/diff-continue"
PATH="$snapshot_root/bin:$PATH" \
  "$snapshot_scripts/workspace/update.sh" \
    -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck \
    --dry-run --skip-self-update \
    > "$snapshot_root/update.stdout" 2> "$snapshot_root/update.stderr" &
snapshot_pid=$!
for _attempt in $(seq 1 500); do
  [[ -f "$P101_TEST_DIFF_READY" ]] && break
  sleep 0.01
done
[[ -f "$P101_TEST_DIFF_READY" ]]
# Simulate editing or fast-forwarding update.sh while an interactive run is
# paused. The running process must finish from its immutable startup snapshot.
printf '"\n' > "$snapshot_scripts/workspace/update.sh"
touch "$P101_TEST_DIFF_CONTINUE"
wait "$snapshot_pid"
grep -Fxq 'All done.' "$snapshot_root/update.stdout"
unset P101_TEST_DIFF_READY P101_TEST_DIFF_CONTINUE

printf 'PASS: interactive repository phase retry and abort behavior\n'
