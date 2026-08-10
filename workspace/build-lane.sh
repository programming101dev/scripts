#!/usr/bin/env bash
# Produce the artifact identity shared by every repository in one compiler lane.

set -euo pipefail

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
COMPILER_FINGERPRINT_SH="$PWD/workspace/compiler-fingerprint.sh"

c_compiler=""
cxx_compiler=""
sanitizers=""
flags_profile="maximal"
coverage=0
profile=0
kind="quality"

usage() {
  cat <<'EOF' >&2
Usage: build-lane.sh -c <cc> -x <cxx> [-s <sanitizers>]
                     [-F maximal|standard|none] [-C 0|1] [-P 0|1]
                     [-K quality|runtime]

Print a stable, path-safe artifact identity for a complete compiler-pair
configuration. Runtime identities always disable sanitizers, coverage, and
profiling. The digest binds both compiler fingerprints and every admitted
configuration field.
EOF
  exit 2
}

while getopts ":c:x:s:F:C:P:K:h" option; do
  case "$option" in
    c) c_compiler=$OPTARG ;;
    x) cxx_compiler=$OPTARG ;;
    s) sanitizers=$OPTARG ;;
    F) flags_profile=$OPTARG ;;
    C) coverage=$OPTARG ;;
    P) profile=$OPTARG ;;
    K) kind=$OPTARG ;;
    h|*) usage ;;
  esac
done

[[ -n "$c_compiler" && -n "$cxx_compiler" ]] || usage
case "$flags_profile" in maximal|standard|none) ;; *) usage ;; esac
case "$coverage" in 0|1) ;; *) usage ;; esac
case "$profile" in 0|1) ;; *) usage ;; esac
case "$kind" in quality|runtime) ;; *) usage ;; esac

if [[ "$kind" == runtime ]]; then
  sanitizers=""
  coverage=0
  profile=0
  lane_cflags=""
  lane_cxxflags=""
  lane_cppflags=""
  lane_ldflags=""
else
  lane_cflags="${CFLAGS:-}"
  lane_cxxflags="${CXXFLAGS:-}"
  lane_cppflags="${CPPFLAGS:-}"
  lane_ldflags="${LDFLAGS:-}"
fi

canonical_sanitizers="$({
  printf '%s\n' "$sanitizers" |
    tr ',' '\n' |
    sed 's/^[[:space:]]*//;s/[[:space:]]*$//' |
    awk 'NF' |
    LC_ALL=C sort -u
} | paste -sd, -)"

path_component() {
  printf '%s' "$(basename -- "$1")" | tr -c 'A-Za-z0-9_.+-' '_'
}

hash_payload() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  elif command -v sha256 >/dev/null 2>&1; then
    sha256 -q
  else
    printf 'build-lane.sh: no SHA-256 implementation found\n' >&2
    exit 2
  fi
}

emit_flag_cache() {
  local compiler="$1"
  local cache_root cache_dir file relative file_digest

  case "$flags_profile" in
    maximal) cache_root="$PWD/../.flags" ;;
    standard) cache_root="$PWD/../.flags-standard" ;;
    none)
      printf 'disabled\n'
      return
      ;;
  esac
  cache_dir="$cache_root/$(basename -- "$compiler")"
  if [[ ! -d "$cache_dir" ]]; then
    printf 'absent\n'
    return
  fi
  while IFS= read -r file; do
    relative=${file#"$cache_dir"/}
    file_digest="$(hash_payload < "$file")"
    printf '%s=%s\n' "$relative" "$file_digest"
  done < <(find -L "$cache_dir" -type f -print | LC_ALL=C sort)
}

c_name="$(path_component "$c_compiler")"
cxx_name="$(path_component "$cxx_compiler")"
c_fingerprint="$($COMPILER_FINGERPRINT_SH print "$c_compiler")"
cxx_fingerprint="$($COMPILER_FINGERPRINT_SH print "$cxx_compiler")"

digest="$({
  printf 'schema=p101-build-lane-v1\n'
  printf 'kind=%s\n' "$kind"
  printf 'flags_profile=%s\n' "$flags_profile"
  printf 'coverage=%s\n' "$coverage"
  printf 'profile=%s\n' "$profile"
  printf 'sanitizers=%s\n' "$canonical_sanitizers"
  printf 'CFLAGS=%s\n' "$lane_cflags"
  printf 'CXXFLAGS=%s\n' "$lane_cxxflags"
  printf 'CPPFLAGS=%s\n' "$lane_cppflags"
  printf 'LDFLAGS=%s\n' "$lane_ldflags"
  printf 'CPATH=%s\n' "${CPATH:-}"
  printf 'C_INCLUDE_PATH=%s\n' "${C_INCLUDE_PATH:-}"
  printf 'CPLUS_INCLUDE_PATH=%s\n' "${CPLUS_INCLUDE_PATH:-}"
  printf 'LIBRARY_PATH=%s\n' "${LIBRARY_PATH:-}"
  printf 'PKG_CONFIG_PATH=%s\n' "${PKG_CONFIG_PATH:-}"
  printf 'SDKROOT=%s\n' "${SDKROOT:-}"
  printf 'MACOSX_DEPLOYMENT_TARGET=%s\n' "${MACOSX_DEPLOYMENT_TARGET:-}"
  printf 'c-fingerprint-begin\n%s\nc-fingerprint-end\n' "$c_fingerprint"
  printf 'cxx-fingerprint-begin\n%s\ncxx-fingerprint-end\n' "$cxx_fingerprint"
  printf 'c-flag-cache-begin\n'
  emit_flag_cache "$c_compiler"
  printf 'c-flag-cache-end\n'
  printf 'cxx-flag-cache-begin\n'
  emit_flag_cache "$cxx_compiler"
  printf 'cxx-flag-cache-end\n'
} | hash_payload)"

instrumentation="clean"
if [[ "$coverage" == 1 && "$profile" == 1 ]]; then
  instrumentation="coverage-profile"
elif [[ "$coverage" == 1 ]]; then
  instrumentation="coverage"
elif [[ "$profile" == 1 ]]; then
  instrumentation="profile"
fi
if [[ -n "$canonical_sanitizers" ]]; then
  instrumentation="${instrumentation}-san"
fi

printf '%s__%s__%s-%s-%s__%s\n' \
  "$c_name" "$cxx_name" "$kind" "$flags_profile" "$instrumentation" \
  "${digest:0:16}"
