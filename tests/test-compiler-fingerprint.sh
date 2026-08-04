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

export P101_TEST_VERSION="${version}"
export P101_TEST_TARGET="${target}"

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

printf 'PASS: compiler flag-cache fingerprint contract\n'
