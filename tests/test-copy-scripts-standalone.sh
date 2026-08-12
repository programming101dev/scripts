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

printf 'PASS: scripts-only distribution does not require ../setup.\n'
