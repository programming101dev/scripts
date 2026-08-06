#!/usr/bin/env bash
set -euo pipefail
script_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=../shared/compilers.sh
. "$script_root/shared/compilers.sh"

usage() {
    cat <<'P101_USAGE'
Usage:
  compiler-fingerprint.sh print <compiler>
  compiler-fingerprint.sh write <compiler> <output>
  compiler-fingerprint.sh check <compiler> <fingerprint>

Record or compare the compiler identity that owns a probed flag cache.
The identity includes the resolved executable, compiler version report, target
triple, and host kernel identity. A mismatch means the cache must be
regenerated. The host identity matters because SDK and sanitizer support can
change while a system compiler path and target triple stay unchanged.
P101_USAGE
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
esac

operation="${1:-}"
compiler="${2:-}"
output="${3:-}"

case "${operation}" in
    print)
        [[ "$#" -eq 2 ]] || { usage >&2; exit 2; }
        ;;
    write|check)
        [[ "$#" -eq 3 ]] || { usage >&2; exit 2; }
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

resolve_compiler() {
    p101_resolve_compiler "$1" "$script_root/compiler_paths.txt"
}

resolved="$(resolve_compiler "${compiler}")" || {
    printf 'Error: compiler is not executable: %s\n' "${compiler}" >&2
    exit 2
}

canonical_path="${resolved}"
if command -v realpath >/dev/null 2>&1; then
    canonical_path="$(realpath "${resolved}" 2>/dev/null || printf '%s' "${resolved}")"
fi

emit_fingerprint() {
    local host target

    target="$("${resolved}" -dumpmachine 2>/dev/null || true)"
    host="$(uname -srm 2>/dev/null || true)"
    printf 'schema=p101-compiler-fingerprint-v2\n'
    printf 'executable=%s\n' "${canonical_path}"
    printf 'target=%s\n' "${target}"
    printf 'host=%s\n' "${host}"
    printf '%s\n' 'version-begin'
    "${resolved}" --version 2>&1
    printf '%s\n' 'version-end'
}

case "${operation}" in
    print)
        emit_fingerprint
        ;;
    write)
        mkdir -p -- "$(dirname -- "${output}")"
        temporary="$(mktemp "${output}.tmp.XXXXXX")"
        trap 'rm -f -- "${temporary}"' EXIT
        emit_fingerprint > "${temporary}"
        mv -f -- "${temporary}" "${output}"
        trap - EXIT
        ;;
    check)
        [[ -f "${output}" ]] || exit 1
        temporary="$(mktemp "${TMPDIR:-/tmp}/p101-compiler-fingerprint.XXXXXX")"
        trap 'rm -f -- "${temporary}"' EXIT
        emit_fingerprint > "${temporary}"
        cmp -s -- "${temporary}" "${output}"
        ;;
esac
