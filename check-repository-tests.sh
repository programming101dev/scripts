#!/usr/bin/env bash
# Run each repository-owned unit suite and a bounded fuzz smoke where supported.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

out_dir=""
fuzz_secs=5
skip_fuzz=0
c_compiler=""
cxx_compiler=""

usage() {
  cat <<'USAGE'
Usage: ./check-repository-tests.sh [-c <cc>] [-x <cxx>] [-o <dir>] [--fuzz-secs <seconds>] [--skip-fuzz]

Runs every meaningful standalone test.sh named by repos.txt. Repositories with
a fuzz target also receive a bounded fuzz run when a fuzzer-capable compiler is
available. A missing test suite is reported as NO TEST rather than silently
treated as tested.
When -c/-x is supplied, fuzzing is attempted only with that compiler. This
keeps fuzz executables and sanitizer-instrumented p101 libraries on the same
runtime.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c) c_compiler="${2:?}"; shift 2 ;;
    -x) cxx_compiler="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    --fuzz-secs) fuzz_secs="${2:?}"; shift 2 ;;
    --skip-fuzz) skip_fuzz=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

resolve_compiler() {
  requested="$1"
  [ -n "$requested" ] || return 0
  if [ -f compiler_paths.txt ]; then
    resolved="$(awk -F= -v name="$requested" '$1 == name { print substr($0, index($0, "=") + 1); exit }' compiler_paths.txt)"
    if [ -n "$resolved" ]; then
      printf '%s\n' "$resolved"
      return 0
    fi
  fi
  command -v "$requested" 2>/dev/null || printf '%s\n' "$requested"
}

c_compiler="$(resolve_compiler "$c_compiler")"
cxx_compiler="$(resolve_compiler "$cxx_compiler")"

case "$fuzz_secs" in *[!0-9]*|'') echo "--fuzz-secs must be an unsigned integer" >&2; exit 2 ;; esac
[ -n "$out_dir" ] || out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-repository-tests.XXXXXX")"
out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
summary="$out_dir/summary.md"
printf '# p101 standalone repository tests\n\n| Repository | Unit tests | Fuzz smoke |\n| --- | --- | --- |\n' > "$summary"

failed=0
while IFS='|' read -r _url relative language || [ -n "${relative:-}" ]; do
  [ -n "${relative:-}" ] || continue
  repo="$(CDPATH='' cd "$relative" 2>/dev/null && pwd -P || true)"
  [ -n "$repo" ] || { printf '| %s | MISSING | MISSING |\n' "$relative" >> "$summary"; failed=1; continue; }
  name="$(basename "$repo")"
  unit="NO TEST"
  fuzz="NO FUZZ TARGET"

  if [ -x "$repo/test.sh" ] && { [ -f "$repo/test/CMakeLists.txt" ] || [ "$name" = "p101-wrapper-audit" ]; }; then
    if (CDPATH='' cd "$repo" && ./test.sh) > "$out_dir/$name-test.log" 2>&1; then
      unit="PASS"
    else
      unit="FAIL"
      failed=1
    fi
  fi

  if [ "$skip_fuzz" -eq 1 ]; then
    fuzz="SKIP"
  elif [ -x "$repo/fuzz.sh" ] && [ -f "$repo/fuzz/CMakeLists.txt" ]; then
    fuzz_compiler="$c_compiler"
    case "$language" in cxx|CXX|CPP) fuzz_compiler="$cxx_compiler" ;; esac
    fuzz_command=(./fuzz.sh)
    if [ -n "$fuzz_compiler" ]; then
      fuzz_command=(env "FUZZ_CC=$fuzz_compiler" ./fuzz.sh)
    fi
    if (CDPATH='' cd "$repo" && "${fuzz_command[@]}" --can-fuzz) >/dev/null 2>&1; then
      if (CDPATH='' cd "$repo" && "${fuzz_command[@]}" -t "$fuzz_secs") > "$out_dir/$name-fuzz.log" 2>&1; then
        fuzz="PASS"
      else
        fuzz="FAIL"
        failed=1
      fi
    else
      fuzz="UNAVAILABLE"
    fi
  fi

  printf '%-30s test=%-8s fuzz=%s\n' "$name" "$unit" "$fuzz"
  printf '| %s | %s | %s |\n' "$name" "$unit" "$fuzz" >> "$summary"
done < repos.txt

printf 'Repository test summary: %s\n' "$summary"
[ "$failed" -eq 0 ]
