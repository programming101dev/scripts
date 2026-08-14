#!/usr/bin/env bash
# A scripts-only checkout must not require the separate setup repository.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-copy-scripts.XXXXXX")"
trap 'rm -rf "$sandbox"' EXIT

mkdir -p "$sandbox/scripts/distribution" \
  "$sandbox/templates/template-c" \
  "$sandbox/programs/example-tool" \
  "$sandbox/examples/c-examples"
cp distribution/copy-scripts.sh "$sandbox/scripts/distribution/copy-scripts.sh"
printf '|../programs/example-tool|c\n|../examples/c-examples|c\n' \
  > "$sandbox/scripts/repos.txt"
printf 'canonical ignore\n' > "$sandbox/templates/template-c/.gitignore"
printf 'canonical license\n' > "$sandbox/templates/template-c/LICENSE"
for script in check-compilers.sh check-env.sh doctor.sh; do
  printf '#!/bin/sh\nexit 0\n' > "$sandbox/templates/template-c/$script"
  chmod +x "$sandbox/templates/template-c/$script"
  cp "$sandbox/templates/template-c/$script" "$sandbox/examples/c-examples/$script"
done
for script in build-all.sh build.sh change-compiler.sh check.sh clean.sh \
  coverage-report.sh create-links.sh debug.sh fuzz.sh profile-report.sh \
  report.sh test-all.sh test.sh; do
  printf '#!/bin/sh\nexit 0\n' > "$sandbox/templates/template-c/$script"
  chmod +x "$sandbox/templates/template-c/$script"
  cp "$sandbox/templates/template-c/$script" \
    "$sandbox/programs/example-tool/$script"
done
cp "$sandbox/templates/template-c/check-compilers.sh" \
  "$sandbox/programs/example-tool/check-compilers.sh"
cp "$sandbox/templates/template-c/check-env.sh" \
  "$sandbox/programs/example-tool/check-env.sh"
cp "$sandbox/templates/template-c/doctor.sh" \
  "$sandbox/programs/example-tool/doctor.sh"
cp "$sandbox/templates/template-c/.gitignore" \
  "$sandbox/examples/c-examples/.gitignore"
cp "$sandbox/templates/template-c/LICENSE" "$sandbox/examples/c-examples/LICENSE"
for file in .gitignore LICENSE; do
  cp "$sandbox/templates/template-c/$file" \
    "$sandbox/programs/example-tool/$file"
done
for file in .clang-format coverage.txt profile.txt; do
  printf 'canonical %s\n' "$file" > "$sandbox/templates/template-c/$file"
  cp "$sandbox/templates/template-c/$file" \
    "$sandbox/programs/example-tool/$file"
done
cp "$sandbox/templates/template-c/.gitignore" "$sandbox/scripts/.gitignore"
cp "$sandbox/templates/template-c/LICENSE" "$sandbox/scripts/LICENSE"

(
  cd "$sandbox/scripts"
  ./distribution/copy-scripts.sh -c
) > "$sandbox/output"

grep -Fq 'PASS: all shared repo scripts match their canonical copies.' \
  "$sandbox/output"
grep -Fq 'canonical ignore' "$sandbox/examples/c-examples/.gitignore"
cmp "$sandbox/templates/template-c/test.sh" \
  "$sandbox/programs/example-tool/test.sh"
[[ ! -e "$sandbox/setup" ]]

# The canonical test harness must place reusable build state outside a cloned
# repository when CI supplies P101_TEST_BUILD_CACHE. Exercise the path choice
# with harmless CMake/CTest stand-ins; the repository test itself is unchanged.
mkdir -p "$sandbox/cache-repo/test" "$sandbox/cache-repo/build-clang" \
  "$sandbox/fake-bin"
cp ../templates/template-c/test.sh \
  "$sandbox/cache-repo/test.sh"
chmod +x "$sandbox/cache-repo/test.sh"
printf 'project(example C)\n' > "$sandbox/cache-repo/test/CMakeLists.txt"
printf 'build-clang\n' > "$sandbox/cache-repo/.last-build-dir"
cat > "$sandbox/cache-repo/build-clang/CMakeCache.txt" <<EOF
CMAKE_C_COMPILER:FILEPATH=$(command -v clang)
DETECTED_SANITIZERS:STRING=
P101_PUBLIC_INCLUDE_DIRS:STRING=
P101_PUBLIC_LINK_DIRS:STRING=
EOF
cat > "$sandbox/fake-bin/cmake" <<'EOF'
#!/bin/sh
set -eu
if [ "$1" = "-S" ]; then
  if [ -n "${P101_CAPTURE_ARGS:-}" ]; then
    printf '%s\n' "$@" > "$P101_CAPTURE_ARGS"
  fi
  source_dir=$2
  shift 2
  [ "$1" = "-B" ]
  build_dir=$2
  mkdir -p "$build_dir"
  printf 'CMAKE_HOME_DIRECTORY:INTERNAL=%s\n' \
    "$(CDPATH='' cd -- "$source_dir" && pwd)" > "$build_dir/CMakeCache.txt"
  printf 'CMAKE_C_COMPILER:FILEPATH=%s\n' "$(command -v clang)" \
    >> "$build_dir/CMakeCache.txt"
else
  [ "$1" = "--build" ]
  [ -d "$2" ]
fi
EOF
cat > "$sandbox/fake-bin/ctest" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$sandbox/fake-bin/cmake" "$sandbox/fake-bin/ctest"
(
  cd "$sandbox/cache-repo"
  PATH="$sandbox/fake-bin:$PATH" \
    P101_TEST_BUILD_CACHE="$sandbox/test-cache" ./test.sh >/dev/null
  PATH="$sandbox/fake-bin:$PATH" \
    P101_TEST_BUILD_CACHE="$sandbox/test-cache" ./test.sh >/dev/null
)
[[ -f "$sandbox/test-cache/cache-repo/root/build-clang/CMakeCache.txt" ]]
[[ ! -e "$sandbox/cache-repo/test/build-clang" ]]

# An exact workspace lane must never be mixed with an arbitrary historical
# build directory. The old broad build-* scan could load a stale dylib whose
# ABI no longer matched the dependency selected for the current lane.
workspace="$sandbox/exact-workspace"
exact_key="clang__clang++__quality-level3"
repo="$workspace/libraries/example"
dependency="$workspace/libraries/dependency"
main_build="$repo/build-${exact_key}__source-identity"
mkdir -p "$workspace/scripts" "$repo/test" "$main_build" \
  "$dependency/build-$exact_key" "$dependency/build-stale"
: > "$workspace/scripts/repos.txt"
cp ../templates/template-c/test.sh "$repo/test.sh"
chmod +x "$repo/test.sh"
printf 'project(example C)\n' > "$repo/test/CMakeLists.txt"
printf '%s\n' "${main_build##*/}" > "$repo/.last-build-dir"
cat > "$main_build/CMakeCache.txt" <<EOF
CMAKE_C_COMPILER:FILEPATH=$(command -v clang)
DETECTED_SANITIZERS:STRING=
P101_BUILD_KEY:UNINITIALIZED=$exact_key
P101_PUBLIC_INCLUDE_DIRS:STRING=
P101_PUBLIC_LINK_DIRS:STRING=
EOF
(
  cd "$repo"
  PATH="$sandbox/fake-bin:$PATH" \
    P101_CAPTURE_ARGS="$sandbox/exact-cmake-args" ./test.sh >/dev/null
)
if ! grep -Fq "/libraries/dependency/build-$exact_key" "$sandbox/exact-cmake-args"; then
  echo "FAIL: exact dependency lane was not admitted" >&2
  sed 's/^/  /' "$sandbox/exact-cmake-args" >&2
  exit 1
fi
if ! grep -Fq "/libraries/example/${main_build##*/}" "$sandbox/exact-cmake-args"; then
  echo "FAIL: repository under test did not admit its active build directory" >&2
  sed 's/^/  /' "$sandbox/exact-cmake-args" >&2
  exit 1
fi
if grep -Fq "/libraries/dependency/build-stale" "$sandbox/exact-cmake-args"; then
  echo "FAIL: exact-lane test admitted a stale dependency lane" >&2
  exit 1
fi
if ! grep -Fxq -- '-U' "$sandbox/exact-cmake-args" ||
   ! grep -Fxq -- 'P101_*_LIBRARY' "$sandbox/exact-cmake-args"; then
  echo "FAIL: standalone test configure did not invalidate cached library paths" >&2
  exit 1
fi

# CMake-owned test targets identify their exact parent build. That identity
# takes precedence over stale .last-build-dir state for the same compiler.
alternate_build="$repo/build-alternate-source-identity"
mkdir -p "$alternate_build"
alternate_build="$(CDPATH='' cd -- "$alternate_build" && pwd -P)"
cat > "$alternate_build/CMakeCache.txt" <<EOF
CMAKE_C_COMPILER:FILEPATH=$(command -v clang)
DETECTED_SANITIZERS:STRING=
P101_BUILD_KEY:UNINITIALIZED=$exact_key
P101_PUBLIC_INCLUDE_DIRS:STRING=
P101_PUBLIC_LINK_DIRS:STRING=
EOF
(
  cd "$repo"
  PATH="$sandbox/fake-bin:$PATH" \
    P101_TEST_MAIN_BUILD="$alternate_build" \
    P101_CAPTURE_ARGS="$sandbox/explicit-cmake-args" ./test.sh >/dev/null
)
if ! grep -Fq -- "/libraries/example/${alternate_build##*/}" \
  "$sandbox/explicit-cmake-args"; then
  echo "FAIL: explicit parent build was not admitted to the test link lane" >&2
  sed 's/^/  /' "$sandbox/explicit-cmake-args" >&2
  exit 1
fi

printf 'PASS: scripts-only distribution does not require ../setup.\n'
