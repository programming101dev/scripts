#!/usr/bin/env bash
# Verify direct-CMake dispatch and interactive retry.
set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "test-build-repo-interactive.sh — exercise CMake dispatch and interactive retry."
    exit 0
    ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-interactive-build.XXXXXX")"
cleanup() {
  if [[ "${P101_KEEP_TEST_SANDBOX:-0}" == 1 ]]; then
    printf 'Sandbox kept: %s\n' "$sandbox"
  else
    rm -rf "$sandbox"
  fi
}
trap cleanup EXIT

mkdir -p "$sandbox/scripts/workspace" "$sandbox/scripts/distribution" \
  "$sandbox/scripts/shared" "$sandbox/bin" "$sandbox/libraries/cmake-repo" \
  "$sandbox/retry-repo"
cp ./workspace/build-repo.sh ./workspace/build-lane.sh \
  ./workspace/gc-build-cache.sh "$sandbox/scripts/workspace/"
cp ./shared/bootstrap.sh "$sandbox/scripts/shared/bootstrap.sh"

cat > "$sandbox/scripts/distribution/refresh-repo.sh" <<'EOF'
#!/bin/sh
printf '%s\n' "$PWD" >> "$P101_TEST_REFRESH_LOG"
exit 0
EOF
chmod +x "$sandbox/scripts/distribution/refresh-repo.sh"
export P101_TEST_REFRESH_LOG="$sandbox/refresh-invocations.txt"

cat > "$sandbox/bin/cmake" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$P101_TEST_CMAKE_LOG"
if [[ "${1:-}" == --build ]]; then
  if [[ -n "${P101_TEST_BUILD_LOG:-}" ]]; then
    count=0
    [[ ! -f "$P101_TEST_BUILD_LOG" ]] || count="$(wc -l < "$P101_TEST_BUILD_LOG")"
    printf 'build\n' >> "$P101_TEST_BUILD_LOG"
    if [[ -f "${P101_TEST_ALWAYS_FAIL:-/nonexistent}" || "$count" -eq 0 ]]; then
      exit 7
    fi
  fi
  exit 0
fi
if [[ "${1:-}" == --install ]]; then
  exit 0
fi
build_dir=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -B) build_dir="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[[ -n "$build_dir" ]] || exit 2
mkdir -p "$build_dir"
printf 'CMAKE_INSTALL_PREFIX:PATH=%s/install\n' "$P101_TEST_SANDBOX" \
  > "$build_dir/CMakeCache.txt"
printf '[]\n' > "$build_dir/compile_commands.json"
EOF
chmod +x "$sandbox/bin/cmake"
export P101_TEST_CMAKE_LOG="$sandbox/cmake-invocations.txt"
export P101_TEST_SANDBOX="$sandbox"

cat > "$sandbox/libraries/cmake-repo/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.20)
project(fixture C)
EOF
cat > "$sandbox/scripts/repos.txt" <<EOF
https://example.invalid/cmake.git|../libraries/cmake-repo|c
EOF

tool="/usr/bin/true"
[[ -x "$tool" ]] || tool="$(command -v true)"
PATH="$sandbox/bin:$PATH" "$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -B test-quality -U test-runtime -s "" --defer-install \
  > "$sandbox/cmake.stdout" 2> "$sandbox/cmake.stderr"
grep -Fq -- '-DP101_BUILD_LEVEL=1' "$P101_TEST_CMAKE_LOG"
grep -Fq -- '-DP101_RUNTIME_ONLY=ON' "$P101_TEST_CMAKE_LOG"
grep -Fq -- '-DP101_LINK_COMPILE_COMMANDS=OFF' "$P101_TEST_CMAKE_LOG"
grep -Fq -- '--build build-test-quality' "$P101_TEST_CMAKE_LOG"
grep -Fq -- '--build build-test-runtime' "$P101_TEST_CMAKE_LOG"
[[ -f "$sandbox/libraries/cmake-repo/.last-build-dir" ]]
[[ -f "$sandbox/libraries/cmake-repo/.last-runtime-build-dir" ]]
[[ -L "$sandbox/libraries/cmake-repo/compile_commands.json" ]]
[[ "$(readlink "$sandbox/libraries/cmake-repo/compile_commands.json")" == \
   "$sandbox/libraries/cmake-repo/build-test-quality/compile_commands.json" ]]
if find "$sandbox/libraries/cmake-repo" -maxdepth 1 -name '*.sh' -print -quit | grep -q .; then
  printf 'CMake repository unexpectedly required a local shell wrapper\n' >&2
  exit 1
fi

cat > "$sandbox/retry-repo/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.20)
project(retry_fixture C)
EOF
cat > "$sandbox/scripts/repos.txt" <<EOF
https://example.invalid/retry.git|$sandbox/retry-repo|c
EOF

export P101_TEST_BUILD_LOG="$sandbox/retry-build-invocations.txt"
export P101_TEST_ALWAYS_FAIL="$sandbox/retry-always-fail"
printf '\n' | PATH="$sandbox/bin:$PATH" "$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -B test-quality -U test-runtime -s "" --interactive -I \
  > "$sandbox/retry.stdout" 2> "$sandbox/retry.stderr"
[[ "$(wc -l < "$P101_TEST_BUILD_LOG")" -eq 2 ]]
[[ "$(wc -l < "$P101_TEST_REFRESH_LOG")" -eq 1 ]]
grep -Fq 'Retrying: build' "$sandbox/retry.stderr"
[[ -f "$sandbox/retry-repo/.last-build-dir" ]]

: > "$P101_TEST_BUILD_LOG"
: > "$P101_TEST_REFRESH_LOG"
touch "$P101_TEST_ALWAYS_FAIL"
set +e
printf 'q\n' | PATH="$sandbox/bin:$PATH" "$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -B test-quality -U test-runtime -s "" --interactive -I \
  > "$sandbox/abort.stdout" 2> "$sandbox/abort.stderr"
status=$?
set -e
[[ "$status" -eq 7 ]]
[[ "$(wc -l < "$P101_TEST_BUILD_LOG")" -eq 1 ]]
[[ ! -s "$P101_TEST_REFRESH_LOG" ]]
grep -Fq 'Aborting at: build' "$sandbox/abort.stderr"

printf 'build-repo CMake dispatch and interactive retry tests passed\n'
