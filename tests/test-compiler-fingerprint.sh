#!/usr/bin/env bash
set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

case " $* " in
    *" --help "*|*" -h "*)
        printf '%s\n' "test-compiler-fingerprint.sh — reject flag caches after compiler identity or target drift."
        exit 0
        ;;
esac
[[ "$#" -eq 0 ]] || { printf 'Usage: %s\n' "$0" >&2; exit 2; }

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/p101-compiler-fingerprint.XXXXXX")"
trap 'rm -rf -- "${sandbox}"' EXIT

compiler="${sandbox}/cc"
version="${sandbox}/version"
target="${sandbox}/target"
host_command="${sandbox}/uname"
fingerprint="${sandbox}/fingerprint"

printf '%s\n' 'p101 compiler 1.0' > "${version}"
printf '%s\n' 'test-platform-v1' > "${target}"
cat > "${compiler}" <<'P101_COMPILER'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
    --version) cat "${P101_TEST_VERSION}" ;;
    -dumpmachine) cat "${P101_TEST_TARGET}" ;;
    *) exit 2 ;;
esac
P101_COMPILER
chmod +x "${compiler}"
cat > "${host_command}" <<'P101_UNAME'
#!/usr/bin/env bash
set -euo pipefail
cat "${P101_TEST_HOST}"
P101_UNAME
chmod +x "${host_command}"

export P101_TEST_VERSION="${version}"
export P101_TEST_TARGET="${target}"
host="${sandbox}/host"
printf '%s\n' 'TestOS 1.0 test-arch' > "${host}"
export P101_TEST_HOST="${host}"
export PATH="${sandbox}:${PATH}"

# The shared resolver/pairing mechanism must be identical for every caller.
# shellcheck source=../shared/compilers.sh
. ./shared/compilers.sh
# shellcheck source=../shared/artifacts.sh
. ./shared/artifacts.sh
[[ "$(p101_derive_cxx_name gcc-16)" == "g++-16" ]]
[[ "$(p101_derive_cxx_name clang22)" == "clang++22" ]]
printf 'test-cc=%s\n' "$compiler" > "$sandbox/compiler_paths.txt"
[[ "$(p101_resolve_compiler test-cc "$sandbox/compiler_paths.txt")" == "$compiler" ]]
relative_compiler_dir="$sandbox/relative-compiler"
mkdir -p "$relative_compiler_dir"
ln -s "$compiler" "$relative_compiler_dir/test-cc"
relative_compiler="$(cd "$sandbox" && p101_resolve_compiler ./relative-compiler/test-cc "$sandbox/compiler_paths.txt")"
[[ "$relative_compiler" == "$relative_compiler_dir/test-cc" ]]
[[ "$relative_compiler" = /* ]]
if p101_resolve_compiler p101-definitely-missing "$sandbox/compiler_paths.txt" \
  >/dev/null 2>&1
then
    printf 'FAIL: shared resolver accepted a missing compiler.\n' >&2
    exit 1
fi
artifact_repo="$sandbox/artifact-repository"
mkdir -p "$artifact_repo/build-coverage" "$artifact_repo/build-clang"
printf 'build-coverage\n' > "$artifact_repo/.last-build-dir"
printf '#!/bin/sh\nexit 0\n' > "$artifact_repo/build-coverage/demo-tool"
printf '#!/bin/sh\nexit 0\n' > "$artifact_repo/build-clang/demo-tool"
chmod +x "$artifact_repo/build-coverage/demo-tool" \
  "$artifact_repo/build-clang/demo-tool"
printf '{}\n' > "$artifact_repo/build-coverage/compile_commands.json"
printf '[]\n' > "$artifact_repo/build-clang/compile_commands.json"
[[ "$(p101_find_built_tool "$artifact_repo" demo-tool)" == \
  "$artifact_repo/build-clang/demo-tool" ]]
[[ "$(p101_find_compile_database "$artifact_repo")" == \
  "$artifact_repo/build-clang/compile_commands.json" ]]

./workspace/compiler-fingerprint.sh write "${compiler}" "${fingerprint}"
./workspace/compiler-fingerprint.sh check "${compiler}" "${fingerprint}"

printf '%s\n' 'p101 compiler 2.0' > "${version}"
if ./workspace/compiler-fingerprint.sh check "${compiler}" "${fingerprint}"; then
    printf 'FAIL: compiler version drift reused the old fingerprint.\n' >&2
    exit 1
fi

./workspace/compiler-fingerprint.sh write "${compiler}" "${fingerprint}"
./workspace/compiler-fingerprint.sh check "${compiler}" "${fingerprint}"

printf '%s\n' 'test-platform-v2' > "${target}"
if ./workspace/compiler-fingerprint.sh check "${compiler}" "${fingerprint}"; then
    printf 'FAIL: compiler target drift reused the old fingerprint.\n' >&2
    exit 1
fi

./workspace/compiler-fingerprint.sh write "${compiler}" "${fingerprint}"
./workspace/compiler-fingerprint.sh check "${compiler}" "${fingerprint}"

printf '%s\n' 'TestOS 2.0 test-arch' > "${host}"
if ./workspace/compiler-fingerprint.sh check "${compiler}" "${fingerprint}"; then
    printf 'FAIL: host kernel drift reused the old fingerprint.\n' >&2
    exit 1
fi

printf 'PASS: compiler flag-cache fingerprint contract\n'
