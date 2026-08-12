#!/usr/bin/env bash
# Verify that build-repo.sh retries only the failed phase in interactive mode.
set -euo pipefail

report_failure() {
  local status="$1"
  local line="$2"
  local command="$3"

  # Several negative controls below intentionally disable errexit while they
  # capture a status.  Do not turn those admitted failures into test failures.
  case "$-" in
    *e*) ;;
    *) return 0 ;;
  esac
  printf 'FAIL: %s:%s: command exited %d: %s\n' \
    "${BASH_SOURCE[1]}" "$line" "$status" "$command" >&2
  exit "$status"
}
trap 'report_failure "$?" "$LINENO" "$BASH_COMMAND"' ERR

# This test constructs its own admitted-cache fixtures below.  A workspace
# acceptance run may itself use a repository build cache; inheriting that
# outer cache would make the first fixture exercise cache policy rather than
# interactive retry behavior.
unset P101_REPOSITORY_BUILD_CACHE

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
  *" --help "*|*" -h "*)
    printf '%s\n' "test-build-repo-interactive.sh — exercise interactive retry and abort behavior."
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

# Formatting has two deliberately different contracts. CI check mode must
# diagnose drift without touching the checkout; local apply mode must update
# the bytes and continue successfully so later cache identities see them.
format_fixture="$sandbox/format-fixture"
mkdir -p "$format_fixture/scripts/checks" "$format_fixture/bin"
cp ./checks/format-workspace.py \
  "$format_fixture/scripts/checks/format-workspace.py"
chmod +x "$format_fixture/scripts/checks/format-workspace.py"
: > "$format_fixture/scripts/repos.txt"
: > "$format_fixture/scripts/.clang-format"
printf '%s\n' 'int badly_formatted;' > "$format_fixture/scripts/sample.c"
git -C "$format_fixture/scripts" init -q
git -C "$format_fixture/scripts" add repos.txt .clang-format sample.c
git -C "$format_fixture/scripts" -c user.name=p101-test \
  -c user.email=p101-test@example.invalid commit -qm fixture
cat > "$format_fixture/bin/clang-format" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *" --version "*) printf '%s\n' 'clang-format version test'; exit 0 ;;
esac
check=false
case " $* " in *" --dry-run "*) check=true ;; esac
status=0
for argument in "$@"; do
  case "$argument" in
    *.c)
      if $check; then
        if grep -Fq badly_formatted "$argument"; then
          printf '%s:1:1: error: code should be clang-formatted\n' "$argument" >&2
          status=1
        fi
      else
        printf '%s\n' 'int well_formatted;' > "$argument"
      fi
      ;;
  esac
done
exit "$status"
EOF
chmod +x "$format_fixture/bin/clang-format"
format_check_status=0
"$format_fixture/scripts/checks/format-workspace.py" \
  --formatter "$format_fixture/bin/clang-format" \
  --receipt "$format_fixture/check.json" --check \
  > "$format_fixture/check.stdout" 2> "$format_fixture/check.stderr" \
  || format_check_status=$?
[[ "$format_check_status" -eq 1 ]]
grep -Fxq 'int badly_formatted;' "$format_fixture/scripts/sample.c"
grep -Fq '"mode": "check"' "$format_fixture/check.json"
grep -Fq '"passed": false' "$format_fixture/check.json"
"$format_fixture/scripts/checks/format-workspace.py" \
  --formatter "$format_fixture/bin/clang-format" \
  --receipt "$format_fixture/apply.json" \
  > "$format_fixture/apply.stdout"
grep -Fxq 'int well_formatted;' "$format_fixture/scripts/sample.c"
grep -Fq '"mode": "apply"' "$format_fixture/apply.json"
grep -Fq '"changed_count": 1' "$format_fixture/apply.json"
grep -Fq '"passed": true' "$format_fixture/apply.json"

# The repository driver may expose an exact build-cache lane through a
# repository-local symlink.  The canonical configurator must accept only links
# whose resolved target is inside the explicitly admitted cache, and only in
# incremental mode so it cannot unlink the cache entry before configuring it.
guard_repo="$sandbox/build-cache-guard"
guard_cache="$sandbox/admitted-build-cache"
guard_outside="$sandbox/outside-build-cache"
mkdir -p "$guard_repo/bin" "$guard_cache/repo/build-cached" \
  "$guard_outside/build-foreign"
cp ../templates/template-c/change-compiler.sh "$guard_repo/change-compiler.sh"
cat > "$guard_repo/bin/cmake" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$guard_repo/change-compiler.sh" "$guard_repo/bin/cmake"
ln -s "$guard_cache/repo/build-cached" "$guard_repo/build-cached"
(
  cd "$guard_repo"
  P101_REPOSITORY_BUILD_CACHE="$guard_cache" PATH="$guard_repo/bin:$PATH" \
    ./change-compiler.sh -R -c /usr/bin/true -f /usr/bin/true \
      -t /usr/bin/true -k /usr/bin/true -s "" -b build-cached >/dev/null
)
if (
  cd "$guard_repo"
  PATH="$guard_repo/bin:$PATH" ./change-compiler.sh -R -c /usr/bin/true \
    -f /usr/bin/true -t /usr/bin/true -k /usr/bin/true -s "" \
    -b build-cached >/dev/null 2>&1
); then
  echo 'cached build symlink was admitted without a cache root' >&2
  exit 1
fi
if (
  cd "$guard_repo"
  P101_REPOSITORY_BUILD_CACHE="$guard_cache" PATH="$guard_repo/bin:$PATH" \
    ./change-compiler.sh -c /usr/bin/true -f /usr/bin/true \
      -t /usr/bin/true -k /usr/bin/true -s "" -b build-cached \
      >/dev/null 2>&1
); then
  echo 'cached build symlink was admitted without incremental mode' >&2
  exit 1
fi
ln -s "$guard_outside/build-foreign" "$guard_repo/build-foreign"
if (
  cd "$guard_repo"
  P101_REPOSITORY_BUILD_CACHE="$guard_cache" PATH="$guard_repo/bin:$PATH" \
    ./change-compiler.sh -R -c /usr/bin/true -f /usr/bin/true \
      -t /usr/bin/true -k /usr/bin/true -s "" -b build-foreign \
      >/dev/null 2>&1
); then
  echo 'build symlink outside the admitted cache was accepted' >&2
  exit 1
fi

# Direct repository builds remain clean-first. Only an explicit option or the
# workspace driver's environment opts into dependency-tracked incremental use.
build_interface="$sandbox/build-interface"
mkdir -p "$build_interface/build" "$build_interface/bin"
cp ../templates/template-c/build.sh "$build_interface/build.sh"
touch "$build_interface/build/CMakeCache.txt"
cat > "$build_interface/bin/cmake" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" > "$P101_TEST_CMAKE_ARGUMENTS"
EOF
chmod +x "$build_interface/build.sh" "$build_interface/bin/cmake"
export P101_TEST_CMAKE_ARGUMENTS="$build_interface/cmake-arguments.txt"
(
  cd "$build_interface"
  PATH="$build_interface/bin:$PATH" ./build.sh -q -b build
)
grep -Fq -- '--clean-first' "$P101_TEST_CMAKE_ARGUMENTS"
(
  cd "$build_interface"
  PATH="$build_interface/bin:$PATH" ./build.sh -q -b build --incremental
)
if grep -Fq -- '--clean-first' "$P101_TEST_CMAKE_ARGUMENTS"; then
  echo 'explicit incremental build unexpectedly cleaned its CMake tree' >&2
  exit 1
fi
(
  cd "$build_interface"
  PATH="$build_interface/bin:$PATH" P101_INCREMENTAL_BUILD=1 \
    ./build.sh -q -b build
)
if grep -Fq -- '--clean-first' "$P101_TEST_CMAKE_ARGUMENTS"; then
  echo 'workspace incremental build unexpectedly cleaned its CMake tree' >&2
  exit 1
fi
unset P101_TEST_CMAKE_ARGUMENTS

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

mkdir -p "$sandbox/scripts/workspace" "$sandbox/scripts/distribution" \
  "$sandbox/scripts/shared" "$sandbox/repo"
cp ./workspace/build-repo.sh "$sandbox/scripts/workspace/build-repo.sh"
cp ./workspace/build-lane.sh "$sandbox/scripts/workspace/build-lane.sh"
cp ./workspace/gc-build-cache.sh \
  "$sandbox/scripts/workspace/gc-build-cache.sh"
cp ./workspace/compiler-fingerprint.sh \
  "$sandbox/scripts/workspace/compiler-fingerprint.sh"
cp ./shared/compilers.sh "$sandbox/scripts/shared/compilers.sh"
chmod +x "$sandbox/scripts/workspace/build-repo.sh" \
  "$sandbox/scripts/workspace/build-lane.sh" \
  "$sandbox/scripts/workspace/gc-build-cache.sh" \
  "$sandbox/scripts/workspace/compiler-fingerprint.sh"

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
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -b) shift 2 ;;
    -c|-f|-t|-k|-s) shift 2 ;;
    *) shift ;;
  esac
done
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
  -B test-quality -U test-runtime \
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
  -B test-quality -U test-runtime \
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
  -B test-quality -U test-runtime \
  --interactive -I > "$sandbox/abort.stdout" 2> "$sandbox/abort.stderr"
status=$?
set -e
[[ "$status" -eq 7 ]]
[[ "$(wc -l < "$sandbox/repo/build-invocations.txt")" -eq 1 ]]
[[ ! -s "$P101_TEST_REFRESH_LOG" ]]
grep -Fq 'Aborting at: build' "$sandbox/abort.stderr"

# An instrumented quality build must be followed by a distinct,
# instrumentation-free runtime build. The strict marker remains the quality build;
# the runtime marker and install argument identify the consumer-safe artifact.
runtime_repo="$sandbox/runtime-repo"
mkdir -p "$runtime_repo"
cat > "$sandbox/scripts/repos.txt" <<EOF
https://example.invalid/runtime.git|$runtime_repo|c
EOF
cat > "$runtime_repo/change-compiler.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> configure-raw-invocations.txt
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
: > "$build_dir/CMakeCache.txt"
printf '%s\n' "$build_dir" > .last-build-dir
if [[ -z "$sanitizers" ]]; then
  printf '%s\n' "$build_dir" > .last-runtime-build-dir
fi
EOF
cat > "$runtime_repo/build.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
build_dir=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in -b) build_dir="$2"; shift 2 ;; *) shift ;; esac
done
printf '%s incremental=%s\n' "$build_dir" \
  "${P101_INCREMENTAL_BUILD:-unset}" >> build-invocations.txt
[[ ! -f fail-build ]] || exit 7
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
  -B test-quality -U test-runtime \
  -s address > "$sandbox/runtime.stdout" 2> "$sandbox/runtime.stderr"
[[ "$(wc -l < "$runtime_repo/configure-invocations.txt")" -eq 2 ]]
[[ "$(wc -l < "$runtime_repo/build-invocations.txt")" -eq 2 ]]
grep -Fq 'build-test-quality incremental=1' \
  "$runtime_repo/build-invocations.txt"
grep -Fq 'build-test-runtime incremental=1' \
  "$runtime_repo/build-invocations.txt"
grep -Fq 'build=build-test-quality sanitizers=address' \
  "$runtime_repo/configure-invocations.txt"
grep -Fq 'build=build-test-runtime sanitizers= cmake=-DP101_RUNTIME_ONLY=ON' \
  "$runtime_repo/configure-invocations.txt"
[[ "$(cat "$runtime_repo/.last-build-dir")" == "build-test-quality" ]]
[[ "$(cat "$runtime_repo/.last-runtime-build-dir")" == "build-test-runtime" ]]
grep -Fxq 'lane=test-quality' \
  "$runtime_repo/build-test-quality/p101-build-lane.txt"
grep -Fxq 'kind=quality' \
  "$runtime_repo/build-test-quality/p101-build-lane.txt"
grep -Fxq 'lane=test-runtime' \
  "$runtime_repo/build-test-runtime/p101-build-lane.txt"
grep -Fxq 'kind=runtime' \
  "$runtime_repo/build-test-runtime/p101-build-lane.txt"

# An explicit empty sanitizer selection is meaningful. It must reach
# change-compiler.sh as -s "" rather than falling back to a stale repository
# sanitizers.txt file.
: > "$runtime_repo/configure-invocations.txt"
: > "$runtime_repo/build-invocations.txt"
"$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -B test-quality -U test-runtime \
  -s "" -I > "$sandbox/empty-sanitizer.stdout" 2> "$sandbox/empty-sanitizer.stderr"
[[ "$(wc -l < "$runtime_repo/configure-invocations.txt")" -eq 1 ]]
grep -Fq 'build=build-test-quality sanitizers= cmake=-DP101_BUILD_KEY=test-quality' \
  "$runtime_repo/configure-invocations.txt"
grep -Fxq -- '-b build-test-runtime' "$runtime_repo/install-arguments.txt"

# Exercise cache identity in a real Git repository. A build-* directory is
# conventionally ignored, but Git still reports a symlink with that name as an
# untracked file because it is not a directory. The workspace identity must
# ignore only an admitted cache link, otherwise finalization invalidates the
# artifact it is about to install.
cat > "$runtime_repo/.gitignore" <<'EOF'
build-*/
*-invocations.txt
install-arguments.txt
.last-build-dir
.last-runtime-build-dir
EOF
git -C "$runtime_repo" init -q
git -C "$runtime_repo" add .gitignore build.sh change-compiler.sh install.sh
git -C "$runtime_repo" -c user.name=p101-test \
  -c user.email=p101-test@example.invalid commit -qm 'fixture'

# Cross-run acceleration stores each exact compiler lane outside the checkout,
# but leaves the repository's conventional build path as a transparent link.
# A restored cache remains an input to CMake; it is not accepted as a verdict.
rm -rf "$runtime_repo/build-test-quality"
: > "$runtime_repo/configure-invocations.txt"
: > "$runtime_repo/build-invocations.txt"
P101_REPOSITORY_BUILD_CACHE="$sandbox/repository-build-cache" \
  "$sandbox/scripts/workspace/build-repo.sh" \
    -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
    -B test-quality -U test-runtime -s "" -I \
    > "$sandbox/cache-miss.stdout" 2> "$sandbox/cache-miss.stderr"
cached_quality="$(cat "$runtime_repo/.last-build-dir")"
[[ "$cached_quality" == build-test-quality__???????????????????????????????????????? ]]
[[ -L "$runtime_repo/$cached_quality" ]]
[[ -L "$runtime_repo/build-test-quality" ]]
[[ "$(readlink "$runtime_repo/build-test-quality")" == "$cached_quality" ]]
cached_runtime="$(cat "$runtime_repo/.last-runtime-build-dir")"
[[ "$cached_runtime" == build-test-runtime__???????????????????????????????????????? ]]
[[ -L "$runtime_repo/build-test-runtime" ]]
[[ "$(readlink "$runtime_repo/build-test-runtime")" == "$cached_runtime" ]]
tail -n 1 "$runtime_repo/configure-raw-invocations.txt" | grep -Eq '(^| )-R( |$)'
grep -Fq 'Build cache MISS' "$sandbox/cache-miss.stdout"
grep -Fxq 'cache_state=miss' \
  "$runtime_repo/$cached_quality/p101-build-lane.txt"
grep -Eq '^orchestrator_identity=policy:[0-9a-f]{40}$' \
  "$runtime_repo/$cached_quality/p101-build-lane.txt"
policy_identity_before="$(grep '^orchestrator_identity=' \
  "$runtime_repo/$cached_quality/p101-build-lane.txt")"
grep -Fq "$cached_quality incremental=1" \
  "$runtime_repo/build-invocations.txt"

: > "$runtime_repo/configure-invocations.txt"
: > "$runtime_repo/build-invocations.txt"
P101_REPOSITORY_BUILD_CACHE="$sandbox/repository-build-cache" \
  "$sandbox/scripts/workspace/build-repo.sh" \
    -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
    -B test-quality -U test-runtime -s "" -I \
    > "$sandbox/cache-hit.stdout" 2> "$sandbox/cache-hit.stderr"
tail -n 1 "$runtime_repo/configure-raw-invocations.txt" | grep -Eq '(^| )-R( |$)'
grep -Fq 'Build cache HIT' "$sandbox/cache-hit.stdout"
grep -Fxq 'cache_state=hit' \
  "$runtime_repo/$cached_quality/p101-build-lane.txt"
[[ "$(cat "$runtime_repo/.last-build-dir")" == "$cached_quality" ]]
[[ "$(readlink "$runtime_repo/build-test-quality")" == "$cached_quality" ]]
[[ "$(wc -l < "$runtime_repo/configure-invocations.txt")" -eq 1 ]]
[[ "$(wc -l < "$runtime_repo/build-invocations.txt")" -eq 1 ]]
P101_REPOSITORY_BUILD_CACHE="$sandbox/repository-build-cache" \
  "$sandbox/scripts/workspace/build-repo.sh" \
    -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
    -B test-quality -U test-runtime -s "" -I --finalize-only \
    > "$sandbox/cache-finalize.stdout" 2> "$sandbox/cache-finalize.stderr"
grep -Fq 'Build cache HIT' "$sandbox/cache-finalize.stdout"
[[ "$(wc -l < "$runtime_repo/configure-invocations.txt")" -eq 1 ]]
[[ "$(wc -l < "$runtime_repo/build-invocations.txt")" -eq 1 ]]

printf '\n# Deliberate build-policy identity change.\n' \
  >> "$sandbox/scripts/workspace/build-lane.sh"
P101_REPOSITORY_BUILD_CACHE="$sandbox/repository-build-cache" \
  "$sandbox/scripts/workspace/build-repo.sh" \
    -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
    -B test-quality -U test-runtime -s "" -I \
    > "$sandbox/cache-invalidated.stdout" 2> "$sandbox/cache-invalidated.stderr"
grep -Fq 'Build cache MISS' "$sandbox/cache-invalidated.stdout"
invalidated_quality="$(cat "$runtime_repo/.last-build-dir")"
[[ "$invalidated_quality" == build-test-quality__???????????????????????????????????????? ]]
[[ "$invalidated_quality" != "$cached_quality" ]]
[[ "$(readlink "$runtime_repo/build-test-quality")" == "$invalidated_quality" ]]
policy_identity_after="$(grep '^orchestrator_identity=' \
  "$runtime_repo/$invalidated_quality/p101-build-lane.txt")"
if [[ "$policy_identity_before" == "$policy_identity_after" ]]; then
  echo 'build-policy change retained a stale lane receipt' >&2
  exit 1
fi
grep -Eq '^orchestrator_identity=policy:[0-9a-f]{40}$' \
  "$runtime_repo/$invalidated_quality/p101-build-lane.txt"

# A failed content-addressed build may leave an unreferenced partial lane, but
# it must restore the last qualified marker instead of stranding the caller on
# the configurator's eager marker write.
printf '\n# Force another exact source identity.\n' >> "$runtime_repo/change-compiler.sh"
: > "$runtime_repo/fail-build"
set +e
P101_REPOSITORY_BUILD_CACHE="$sandbox/repository-build-cache" \
  "$sandbox/scripts/workspace/build-repo.sh" \
    -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
    -B test-quality -U test-runtime -s "" -I \
    > "$sandbox/cache-failure.stdout" 2> "$sandbox/cache-failure.stderr"
cache_failure_status=$?
set -e
rm -f "$runtime_repo/fail-build"
[[ "$cache_failure_status" -eq 7 ]]
[[ "$(cat "$runtime_repo/.last-build-dir")" == "$invalidated_quality" ]]
[[ "$(readlink "$runtime_repo/build-test-quality")" == "$invalidated_quality" ]]
[[ ! -e "$runtime_repo/.last-runtime-build-dir" ]]

# A stale marker is not qualified state and must not be resurrected by a
# failed transaction. Treat it as absent even if the eager configurator wrote
# another value before its build failed.
printf '%s\n' build-does-not-exist > "$runtime_repo/.last-build-dir"
printf '\n# Force a source identity for stale-marker rollback.\n' \
  >> "$runtime_repo/change-compiler.sh"
: > "$runtime_repo/fail-build"
set +e
P101_REPOSITORY_BUILD_CACHE="$sandbox/repository-build-cache" \
  "$sandbox/scripts/workspace/build-repo.sh" \
    -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
    -B test-quality -U test-runtime -s "" -I \
    > "$sandbox/stale-marker-failure.stdout" \
    2> "$sandbox/stale-marker-failure.stderr"
stale_marker_failure_status=$?
set -e
rm -f "$runtime_repo/fail-build"
[[ "$stale_marker_failure_status" -eq 7 ]]
[[ ! -e "$runtime_repo/.last-build-dir" ]]
[[ ! -e "$runtime_repo/.last-runtime-build-dir" ]]
printf '%s\n' "$invalidated_quality" > "$runtime_repo/.last-build-dir"

# Collection removes obsolete repository aliases immediately and aged cache
# content separately. A marker-selected lane survives even with age zero.
"$sandbox/scripts/workspace/gc-build-cache.sh" \
  --cache "$sandbox/repository-build-cache" --max-age-days 0 \
  > "$sandbox/cache-gc.stdout"
[[ -e "$runtime_repo/$invalidated_quality" ]]
[[ "$(find "$runtime_repo" -maxdepth 1 -type l -name 'build-*__*' | wc -l | tr -d ' ')" -eq 1 ]]
[[ "$(find "$sandbox/repository-build-cache" -mindepth 2 -maxdepth 2 \
  -type d -name 'build-*__*' | wc -l | tr -d ' ')" -eq 1 ]]
grep -Eq 'Build-cache GC: [1-9][0-9]* alias\(es\), [1-9][0-9]* aged lane\(s\)' \
  "$sandbox/cache-gc.stdout"

# Matrix workers complete their exact lanes without publishing shared marker
# state. The deterministic host finalizer is the sole publisher.
P101_DEFER_BUILD_MARKERS=1 \
P101_REPOSITORY_BUILD_CACHE="$sandbox/repository-build-cache" \
  "$sandbox/scripts/workspace/build-repo.sh" \
    -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
    -B test-quality -U test-runtime -s "" -I \
    > "$sandbox/deferred-markers.stdout" \
    2> "$sandbox/deferred-markers.stderr"
[[ "$(cat "$runtime_repo/.last-build-dir")" == "$invalidated_quality" ]]
[[ ! -e "$runtime_repo/.last-runtime-build-dir" ]]

# Parallel matrix mode builds the host runtime without installing it, then a
# separate finalize pass installs without recompiling and restores stable host
# markers after all concurrent workers have stopped.
: > "$runtime_repo/configure-invocations.txt"
: > "$runtime_repo/build-invocations.txt"
rm -f "$runtime_repo/install-arguments.txt"
"$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -B test-quality -U test-runtime \
  -s address --defer-install > "$sandbox/defer.stdout" 2> "$sandbox/defer.stderr"
[[ "$(wc -l < "$runtime_repo/configure-invocations.txt")" -eq 2 ]]
[[ "$(wc -l < "$runtime_repo/build-invocations.txt")" -eq 2 ]]
[[ ! -e "$runtime_repo/install-arguments.txt" ]]
"$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -B test-quality -U test-runtime \
  -s address --finalize-only > "$sandbox/finalize.stdout" 2> "$sandbox/finalize.stderr"
[[ "$(wc -l < "$runtime_repo/configure-invocations.txt")" -eq 2 ]]
[[ "$(wc -l < "$runtime_repo/build-invocations.txt")" -eq 2 ]]
grep -Fxq -- '-b build-test-runtime' "$runtime_repo/install-arguments.txt"
[[ "$(cat "$runtime_repo/.last-build-dir")" == "build-test-quality" ]]
[[ "$(cat "$runtime_repo/.last-runtime-build-dir")" == "build-test-runtime" ]]

# A path name alone is not artifact ownership. Finalization must reject a
# stale or renamed directory whose embedded lane receipt does not match.
cp "$runtime_repo/build-test-runtime/p101-build-lane.txt" \
  "$sandbox/runtime-lane-receipt.saved"
awk '{ if ($0 == "lane=test-runtime") print "lane=wrong-runtime"; else print }' \
  "$sandbox/runtime-lane-receipt.saved" \
  > "$runtime_repo/build-test-runtime/p101-build-lane.txt"
set +e
"$sandbox/scripts/workspace/build-repo.sh" \
  -c "$tool" -x "$tool" -f "$tool" -t "$tool" -k "$tool" \
  -B test-quality -U test-runtime -s address --finalize-only \
  > "$sandbox/wrong-lane.stdout" 2> "$sandbox/wrong-lane.stderr"
wrong_lane_status=$?
set -e
[[ "$wrong_lane_status" -eq 1 ]]
grep -Fq 'host runtime artifact is missing' "$sandbox/wrong-lane.stdout"
cp "$sandbox/runtime-lane-receipt.saved" \
  "$runtime_repo/build-test-runtime/p101-build-lane.txt"

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
mkdir -p "$sandbox/matrix/distribution" "$sandbox/matrix/workspace" \
  "$sandbox/matrix/shared"
cp ./shared/compilers.sh "$sandbox/matrix/shared/compilers.sh"
cp ./distribution/refresh-repo.sh "$sandbox/matrix/distribution/refresh-repo.sh"
cp ./workspace/update.sh "$sandbox/matrix/workspace/update.sh"
cp ./workspace/gc-build-cache.sh "$sandbox/matrix/workspace/gc-build-cache.sh"
chmod +x "$sandbox/matrix/update-all.sh" \
  "$sandbox/matrix/distribution/refresh-repo.sh" "$sandbox/matrix/workspace/update.sh"
chmod +x "$sandbox/matrix/workspace/gc-build-cache.sh"
cat > "$sandbox/matrix/driver.sh" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" >> driver-arguments.txt
printf 'cache=%s defer=%s\n' \
  "${P101_REPOSITORY_BUILD_CACHE:-}" \
  "${P101_DEFER_BUILD_MARKERS:-0}" >> driver-environment.txt
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
    --interactive --skip-install --skip-acceptance > update-all.stdout
)
grep -Fq 'source snapshot without usable Git metadata; skipping refresh' \
  "$sandbox/matrix/update-all.stdout"
[[ "$(grep -c -- '--skip-self-update' "$sandbox/matrix/driver-arguments.txt")" -eq 3 ]]
[[ "$(grep -c -- '--prepare-only' "$sandbox/matrix/driver-arguments.txt")" -eq 1 ]]
[[ "$(grep -c -- '--build-only' "$sandbox/matrix/driver-arguments.txt")" -eq 1 ]]
[[ "$(grep -c -- '--finalize-only' "$sandbox/matrix/driver-arguments.txt")" -eq 1 ]]
[[ "$(grep -c -- '--format' "$sandbox/matrix/driver-arguments.txt")" -eq 1 ]]
grep -Fxq -- '--prepare-only' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--build-only' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--finalize-only' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--interactive' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- '--skip-install' "$sandbox/matrix/driver-arguments.txt"
grep -Fxq -- 'address' "$sandbox/matrix/driver-arguments.txt"
matrix_cache="$(CDPATH='' cd -- "$sandbox/matrix" && pwd -P)/target/repository-build-cache"
[[ "$(wc -l < "$sandbox/matrix/driver-environment.txt")" -eq 3 ]]
[[ "$(grep -Fxc "cache=$matrix_cache defer=0" \
  "$sandbox/matrix/driver-environment.txt")" -eq 2 ]]
[[ "$(grep -Fxc "cache=$matrix_cache defer=1" \
  "$sandbox/matrix/driver-environment.txt")" -eq 1 ]]

# Local formatting is the default preparation phase. CI can select a
# non-mutating check, and an explicit local escape hatch suppresses both.
(
  cd "$sandbox/matrix"
  ./update-all.sh -u ./driver.sh -C c.txt -X cxx.txt \
    -f clang-format -t clang-tidy -k cppcheck -s address \
    --format-check --skip-install --skip-acceptance > format-check.stdout
  ./update-all.sh -u ./driver.sh -C c.txt -X cxx.txt \
    -f clang-format -t clang-tidy -k cppcheck -s address \
    --no-format --skip-install --skip-acceptance > no-format.stdout
)
[[ "$(grep -c -- '--format' "$sandbox/matrix/driver-arguments.txt")" -eq 1 ]]
[[ "$(grep -c -- '--format-check' "$sandbox/matrix/driver-arguments.txt")" -eq 1 ]]
if (
  cd "$sandbox/matrix"
  ./update-all.sh -u ./driver.sh -C c.txt -X cxx.txt \
    --format --format-check --skip-install --skip-acceptance >/dev/null 2>&1
); then
  echo 'mutually exclusive formatting modes were accepted' >&2
  exit 1
fi

# The strict CMake acceptance phase is the default, and it must use the first
# compiler pair that actually completed rather than the loop variables left by
# the last pair (or by the final failed read).
acceptance_matrix="$sandbox/acceptance-matrix"
mkdir -p "$acceptance_matrix/distribution" "$acceptance_matrix/workspace" \
  "$acceptance_matrix/shared" "$acceptance_matrix/bin"
cp ./update-all.sh "$acceptance_matrix/update-all.sh"
cp ./shared/compilers.sh "$acceptance_matrix/shared/compilers.sh"
cp ./workspace/gc-build-cache.sh \
  "$acceptance_matrix/workspace/gc-build-cache.sh"
cat > "$acceptance_matrix/distribution/refresh-repo.sh" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$acceptance_matrix/driver.sh" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> driver-invocations.txt
EOF
cat > "$acceptance_matrix/bin/cmake" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$P101_TEST_CMAKE_LOG"
EOF
cat > "$acceptance_matrix/bin/compiler" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$acceptance_matrix/update-all.sh" \
  "$acceptance_matrix/workspace/gc-build-cache.sh" \
  "$acceptance_matrix/distribution/refresh-repo.sh" \
  "$acceptance_matrix/driver.sh" "$acceptance_matrix/bin/cmake" \
  "$acceptance_matrix/bin/compiler"
for compiler in clang-a clang++-a clang-b clang++-b; do
  ln -s compiler "$acceptance_matrix/bin/$compiler"
done
printf 'clang-a\nclang-b\n' > "$acceptance_matrix/c.txt"
printf 'clang++-a\nclang++-b\n' > "$acceptance_matrix/cxx.txt"
export P101_TEST_CMAKE_LOG="$acceptance_matrix/cmake-invocations.txt"
(
  cd "$acceptance_matrix"
  PATH="$acceptance_matrix/bin:$PATH" ./update-all.sh \
    -u ./driver.sh -C c.txt -X cxx.txt \
    -f clang-format -t clang-tidy -k cppcheck \
    --acceptance-output evidence --acceptance-no-cache > update-all.stdout
)
unset P101_TEST_CMAKE_LOG
[[ "$(wc -l < "$acceptance_matrix/driver-invocations.txt")" -eq 4 ]]
[[ "$(grep -c -- '--prepare-only' "$acceptance_matrix/driver-invocations.txt")" -eq 1 ]]
[[ "$(grep -c -- '--build-only' "$acceptance_matrix/driver-invocations.txt")" -eq 2 ]]
[[ "$(grep -c -- '--finalize-only' "$acceptance_matrix/driver-invocations.txt")" -eq 1 ]]
grep -Fq -- '--prepare-only' "$acceptance_matrix/driver-invocations.txt"
grep -Fq -- '--build-only' "$acceptance_matrix/driver-invocations.txt"
grep -Fq -- '--finalize-only' "$acceptance_matrix/driver-invocations.txt"
grep -Fq -- "-DCMAKE_C_COMPILER=$acceptance_matrix/bin/clang-a" \
  "$acceptance_matrix/cmake-invocations.txt"
grep -Fq -- "-DP101_ACCEPTANCE_CXX_COMPILER=$acceptance_matrix/bin/clang++-a" \
  "$acceptance_matrix/cmake-invocations.txt"
acceptance_output_abs="$(CDPATH='' cd -- "$acceptance_matrix" && pwd -P)/evidence"
grep -Fq -- "-DP101_ACCEPTANCE_OUTPUT_DIR=$acceptance_output_abs" \
  "$acceptance_matrix/cmake-invocations.txt"
grep -Fq -- '-DP101_ACCEPTANCE_NO_CACHE=1' \
  "$acceptance_matrix/cmake-invocations.txt"
grep -Eq -- '--target p101_acceptance --parallel [1-9][0-9]*' \
  "$acceptance_matrix/cmake-invocations.txt"

# Compiler-pair workers start concurrently, keep isolated logs, and report all
# failures in manifest order instead of losing whichever background job failed
# first.
parallel_matrix="$sandbox/parallel-matrix"
mkdir -p "$parallel_matrix/distribution" "$parallel_matrix/shared" \
  "$parallel_matrix/workspace" \
  "$parallel_matrix/bin" "$parallel_matrix/evidence"
cp ./update-all.sh "$parallel_matrix/update-all.sh"
cp ./shared/compilers.sh "$parallel_matrix/shared/compilers.sh"
cp ./workspace/gc-build-cache.sh \
  "$parallel_matrix/workspace/gc-build-cache.sh"
cat > "$parallel_matrix/distribution/refresh-repo.sh" <<'EOF'
#!/bin/sh
exit 0
EOF
cat > "$parallel_matrix/driver.sh" <<'EOF'
#!/bin/sh
phase=""
compiler=""
interactive=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    -c) compiler=$2; shift 2 ;;
    --prepare-only|--build-only|--finalize-only) phase=$1; shift ;;
    --interactive) interactive=1; shift ;;
    *) shift ;;
  esac
done
case "$phase" in
  --prepare-only|--finalize-only) exit 0 ;;
  --build-only)
    if [ "${CMAKE_BUILD_PARALLEL_LEVEL:-0}" -le 0 ]; then
      echo "compiler worker received no positive CMake build budget"
      exit 13
    fi
    : > "started-$compiler"
    echo "complete diagnostic for $compiler"
    attempts=0
    while :; do
      started_count=0
      for marker in started-*; do
        [ -f "$marker" ] || continue
        started_count=$((started_count + 1))
      done
      [ "$started_count" -lt 3 ] || break
      attempts=$((attempts + 1))
      [ "$attempts" -lt 10 ] || { echo "workers did not overlap"; exit 12; }
      sleep 1
    done
    if [ "$interactive" -eq 1 ]; then
      echo "interactive repair passed for $compiler"
      exit 0
    fi
    case "$compiler" in
      clang-b) exit 7 ;;
      clang-c) exit 9 ;;
    esac
    ;;
esac
EOF
cat > "$parallel_matrix/bin/compiler" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$parallel_matrix/update-all.sh" \
  "$parallel_matrix/workspace/gc-build-cache.sh" \
  "$parallel_matrix/distribution/refresh-repo.sh" \
  "$parallel_matrix/driver.sh" "$parallel_matrix/bin/compiler"
for compiler in clang-a clang++-a clang-b clang++-b clang-c clang++-c; do
  ln -s compiler "$parallel_matrix/bin/$compiler"
done
printf 'clang-a\nclang-b\nclang-c\n' > "$parallel_matrix/c.txt"
printf 'clang++-a\nclang++-b\nclang++-c\n' > "$parallel_matrix/cxx.txt"
printf 'stale evidence\n' > "$parallel_matrix/evidence/old.log"
printf '0\n' > "$parallel_matrix/evidence/old.status"
parallel_status=0
(
  cd "$parallel_matrix"
  PATH="$parallel_matrix/bin:$PATH" ./update-all.sh \
    -u ./driver.sh -C c.txt -X cxx.txt --skip-install --skip-acceptance \
    --matrix-output evidence > matrix.stdout 2> matrix.stderr
) || parallel_status=$?
[[ "$parallel_status" -eq 1 ]]
grep -Fq 'update-all:clang-b__clang__-b: error:' "$parallel_matrix/matrix.stderr"
grep -Fq 'update-all:clang-c__clang__-c: error:' "$parallel_matrix/matrix.stderr"
grep -Fq -- '--- failure log: clang-b : clang++-b ---' \
  "$parallel_matrix/matrix.stderr"
grep -Fq -- '--- failure log: clang-c : clang++-c ---' \
  "$parallel_matrix/matrix.stderr"
[[ ! -e "$parallel_matrix/evidence/old.log" ]]
[[ ! -e "$parallel_matrix/evidence/old.status" ]]
require_summary_row() {
  local expected="$1"

  if ! grep -Fq "$expected" "$parallel_matrix/evidence/summary.tsv"; then
    printf 'Expected compiler-matrix summary row was absent: %s\n' "$expected" >&2
    printf '%s\n' '--- actual compiler-matrix summary ---' >&2
    cat "$parallel_matrix/evidence/summary.tsv" >&2
    printf '%s\n' '--- end compiler-matrix summary ---' >&2
    return 1
  fi
}
require_summary_row $'0001\tclang-a\tclang++-a\tPASS\t0'
require_summary_row $'0002\tclang-b\tclang++-b\tFAIL\t7'
require_summary_row $'0003\tclang-c\tclang++-c\tFAIL\t9'

parallel_retry_status=0
(
  cd "$parallel_matrix"
  PATH="$parallel_matrix/bin:$PATH" ./update-all.sh \
    -u ./driver.sh -C c.txt -X cxx.txt --interactive \
    --skip-install --skip-acceptance --matrix-output evidence \
    > matrix-retry.stdout 2> matrix-retry.stderr
) || parallel_retry_status=$?
[[ "$parallel_retry_status" -eq 0 ]]
grep -Fq 'interactive repair passed for clang-b' \
  "$parallel_matrix/evidence/0002-clang-b__clang__-b.retry.log"
grep -Fq $'0002\tclang-b\tclang++-b\tPASS\t0' \
  "$parallel_matrix/evidence/summary.tsv"
grep -Fq '.retry.log' "$parallel_matrix/evidence/summary.tsv"

snapshot_root="$sandbox/update-snapshot"
snapshot_scripts="$snapshot_root/scripts"
mkdir -p "$snapshot_scripts/workspace" "$snapshot_scripts/distribution" \
  "$snapshot_scripts/shared" \
  "$snapshot_scripts/generators" "$snapshot_scripts/checks" \
  "$snapshot_root/.flags" "$snapshot_root/bin"
cp ./workspace/update.sh "$snapshot_scripts/workspace/update.sh"
cp ./shared/compilers.sh "$snapshot_scripts/shared/compilers.sh"
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
if [[ -n "${P101_TEST_HELPER_LOG:-}" ]]; then
  printf '%s\n' "${0##*/}" >> "$P101_TEST_HELPER_LOG"
fi
exit 0
EOF
chmod +x "$snapshot_scripts/helper"
for helper in \
  distribution/refresh-repo.sh \
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
  distribution/copy-cmake.sh \
  checks/format-workspace.py
do
  ln -s ../helper "$snapshot_scripts/$helper"
done
rm -f "$snapshot_scripts/workspace/check-env.sh"
cat > "$snapshot_scripts/workspace/check-env.sh" <<'EOF'
#!/usr/bin/env bash
if [[ -n "${P101_TEST_HELPER_LOG:-}" ]]; then
  printf 'check-env.sh' >> "$P101_TEST_HELPER_LOG"
  printf ' %s' "$@" >> "$P101_TEST_HELPER_LOG"
  printf '\n' >> "$P101_TEST_HELPER_LOG"
fi
exit 0
EOF
chmod +x "$snapshot_scripts/workspace/check-env.sh"
printf 'clang\n' > "$snapshot_scripts/supported_c_compilers.txt"
printf 'clang++\n' > "$snapshot_scripts/supported_cxx_compilers.txt"
printf '1\n' > "$snapshot_scripts/version.txt"
printf '1\n' > "$snapshot_root/.flags/version.txt"

export P101_TEST_HELPER_LOG="$snapshot_root/phase-helpers.log"
PATH="$snapshot_root/bin:$PATH" \
  "$snapshot_scripts/workspace/update.sh" \
    -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck \
    --no-flags --skip-self-update --prepare-only > /dev/null
grep -Fxq 'clone-repos.sh' "$P101_TEST_HELPER_LOG"
grep -Fxq 'copy-scripts.sh' "$P101_TEST_HELPER_LOG"
grep -Fxq 'format-workspace.py' "$P101_TEST_HELPER_LOG"
clone_line="$(grep -n -m 1 '^clone-repos.sh$' "$P101_TEST_HELPER_LOG" | cut -d: -f1)"
format_line="$(grep -n -m 1 '^format-workspace.py$' "$P101_TEST_HELPER_LOG" | cut -d: -f1)"
copy_line="$(grep -n -m 1 '^copy-scripts.sh$' "$P101_TEST_HELPER_LOG" | cut -d: -f1)"
[[ "$clone_line" -lt "$format_line" ]]
[[ "$format_line" -lt "$copy_line" ]]
grep -q '^check-env.sh ' "$P101_TEST_HELPER_LOG"
if grep -Fq -- '--compiler-only' "$P101_TEST_HELPER_LOG"; then
  echo 'prepare-only incorrectly used the pair-only environment check' >&2
  exit 1
fi
if grep -Fxq 'build-repo.sh' "$P101_TEST_HELPER_LOG"; then
  echo 'prepare-only unexpectedly built repositories' >&2
  exit 1
fi

: > "$P101_TEST_HELPER_LOG"
PATH="$snapshot_root/bin:$PATH" \
  "$snapshot_scripts/workspace/update.sh" \
    -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck \
    --no-flags --skip-self-update --skip-install --build-only > /dev/null
grep -q '^check-env.sh ' "$P101_TEST_HELPER_LOG"
grep -Fq -- '--compiler-only' "$P101_TEST_HELPER_LOG"
grep -Fxq 'build-repo.sh' "$P101_TEST_HELPER_LOG"
if grep -Eq 'clone-repos|copy-scripts|link-flags' "$P101_TEST_HELPER_LOG"; then
  echo 'build-only unexpectedly repeated workspace preparation' >&2
  exit 1
fi

# update.sh must filter the exact generated sanitizer cache consumed by CMake,
# not the broader source candidate file. Model an umbrella whose harvested
# cache contains one target-incompatible sub-flag.
rm -f "$snapshot_scripts/workspace/filter-sanitizers.sh" \
  "$snapshot_root/bin/clang"
cp ./workspace/filter-sanitizers.sh \
  "$snapshot_scripts/workspace/filter-sanitizers.sh"
chmod +x "$snapshot_scripts/workspace/filter-sanitizers.sh"
cat > "$snapshot_root/bin/clang" <<'EOF'
#!/usr/bin/env bash
case " $* " in
  *" -fsanitize=unsupported-subflag "*) exit 1 ;;
esac
exit 0
EOF
chmod +x "$snapshot_root/bin/clang"
mkdir -p "$snapshot_root/.flags/clang"
printf '%s\n' '-fsanitize=undefined -fsanitize=unsupported-subflag' \
  > "$snapshot_root/.flags/clang/undefined_sanitizer_flags.txt"
: > "$P101_TEST_HELPER_LOG"
PATH="$snapshot_root/bin:$PATH" \
  "$snapshot_scripts/workspace/update.sh" \
    -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck \
    -s undefined --skip-self-update --skip-install --build-only \
    > "$snapshot_root/sanitizer-cache.stdout" \
    2> "$snapshot_root/sanitizer-cache.stderr"
grep -Fq 'effective sanitizers = <none>' \
  "$snapshot_root/sanitizer-cache.stdout"
grep -Fq 'unsupported by' "$snapshot_root/sanitizer-cache.stderr"
grep -Fxq 'build-repo.sh' "$P101_TEST_HELPER_LOG"

: > "$P101_TEST_HELPER_LOG"
PATH="$snapshot_root/bin:$PATH" \
  "$snapshot_scripts/workspace/update.sh" \
    -c clang -x clang++ -f clang-format -t clang-tidy -k cppcheck \
    --no-flags --skip-self-update --skip-install --finalize-only > /dev/null
[[ "$(wc -l < "$P101_TEST_HELPER_LOG")" -eq 1 ]]
grep -Fxq 'build-repo.sh' "$P101_TEST_HELPER_LOG"
unset P101_TEST_HELPER_LOG

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
