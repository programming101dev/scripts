#!/usr/bin/env bash
# check-after-update-all.sh — post-update-all acceptance checks.
#
# Run this after ./update-all.sh has already rebuilt the repos/compiler matrix.
# It intentionally does NOT rebuild every repo again. Instead it runs the checks
# that catch integration/template/tool regressions after the heavy build pass:
#
#   1. shared CMakeLists regression harness;
#   2. copied-template standalone copy/build/test;
#   3. p101-tool-playground tour over observe/resource/trace/report/fault-walk/doctor.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

cc=""
cxx=""
out_dir=""
skip_cmake=0
skip_stack=0
template_no_tests=0
playground_quality=0
playground_coverage=0
playground_fuzz=0
fault_count=1
fuzz_secs=5

usage() {
  cat <<'USAGE'
Usage: ./check-after-update-all.sh [options]

Run acceptance checks that make sense after ./update-all.sh has already built
the workspace. The script does not run build-repo.sh.

Options:
  -c <cc>          C compiler for checks. Default: first supported C compiler.
  -x <cxx>         C++ compiler for checks. Default: matching/first supported C++ compiler.
  -o <dir>         Artifact directory. Default: /tmp/p101-after-update-all-check-<pid>.
  -n <count>       Fault-injection cases for playground tour. Default: 1.
  --fuzz-secs <s>  Fuzz smoke budget if playground fuzz is enabled. Default: 5.

  --skip-cmake        Skip the shared CMakeLists regression harness.
  --skip-stack        Skip template/playground stack checks.
  --template-no-tests Build copied templates but skip copied template tests.

  --playground-quality  Let tour.sh run build/test/fuzz/coverage quality steps.
  --playground-coverage Let tour.sh run coverage.
  --playground-fuzz     Let tour.sh run fuzz smoke.
  -h, --help            Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c) cc="${2:?}"; shift 2 ;;
    -x) cxx="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -n) fault_count="${2:?}"; shift 2 ;;
    --fuzz-secs) fuzz_secs="${2:?}"; shift 2 ;;
    --skip-cmake) skip_cmake=1; shift ;;
    --skip-stack) skip_stack=1; shift ;;
    --template-no-tests) template_no_tests=1; shift ;;
    --playground-quality) playground_quality=1; shift ;;
    --playground-coverage) playground_coverage=1; shift ;;
    --playground-fuzz) playground_fuzz=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="${TMPDIR:-/tmp}/p101-after-update-all-check-$$"
fi

out_dir="$(mkdir -p "$out_dir" && cd "$out_dir" && pwd)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"
summary="$out_dir/summary.md"

trim_line() {
  awk 'NF && $0 !~ /^[[:space:]]*#/ { print $1; exit }' "$1"
}

derive_cxx_name() {
  base="$(basename "$1")"
  case "$base" in
    gcc*) printf 'g++%s\n' "${base#gcc}" ;;
    clang*) printf 'clang++%s\n' "${base#clang}" ;;
    *) printf '\n' ;;
  esac
}

find_cxx_for_c() {
  wanted="$(derive_cxx_name "$1")"

  if [ -n "$wanted" ] && [ -f supported_cxx_compilers.txt ]; then
    awk -v want="$wanted" '
      NF && $0 !~ /^[[:space:]]*#/ {
        n = split($1, parts, "/")
        if (parts[n] == want) {
          print $1
          exit
        }
      }
    ' supported_cxx_compilers.txt
  fi
}

if [ -z "$cc" ]; then
  cc="$(trim_line supported_c_compilers.txt)"
fi

if [ -z "$cxx" ]; then
  cxx="$(find_cxx_for_c "$cc")"
fi

if [ -z "$cxx" ]; then
  cxx="$(trim_line supported_cxx_compilers.txt)"
fi

if [ -z "$cc" ] || [ -z "$cxx" ]; then
  echo "Unable to choose compilers. Run ./check-compilers.sh first or pass -c/-x." >&2
  exit 2
fi

say() {
  printf '%s\n' "$*"
}

run_logged() {
  title="$1"
  log="$2"
  shift 2

  say "==> $title"
  {
    printf '$'
    for arg in "$@"; do
      printf ' %s' "$arg"
    done
    printf '\n\n'
  } > "$log"

  if "$@" >> "$log" 2>&1; then
    say "    PASS"
    printf '| PASS | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
  else
    say "    FAIL (see $log)"
    printf '| FAIL | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
    exit 1
  fi
}

cat > "$summary" <<EOF
# p101 post-update-all checks

Compilers:

- C: ${cc}
- C++: ${cxx}

| Status | Check | Artifact |
| --- | --- | --- |
EOF

say "p101 post-update-all check output: $out_dir"
say "C compiler:   $cc"
say "C++ compiler: $cxx"

if [ "$skip_cmake" -eq 0 ]; then
  run_logged "shared CMakeLists regression harness" "$log_dir/test-cmake.log" ./test-cmake.sh -c "$cc" -x "$cxx" -k
else
  say "==> shared CMakeLists regression harness"
  say "    SKIP"
  printf '| SKIP | shared CMakeLists regression harness | --skip-cmake |\n' >> "$summary"
fi

if [ "$skip_stack" -eq 0 ]; then
  stack_args=(--skip-repo-build -c "$cc" -x "$cxx" -o "$out_dir/stack" -n "$fault_count" --fuzz-secs "$fuzz_secs")
  if [ "$template_no_tests" -eq 1 ]; then
    stack_args+=(--template-no-tests)
  fi
  if [ "$playground_quality" -eq 1 ]; then
    stack_args+=(--playground-quality)
  fi
  if [ "$playground_coverage" -eq 1 ]; then
    stack_args+=(--playground-coverage)
  fi
  if [ "$playground_fuzz" -eq 1 ]; then
    stack_args+=(--playground-fuzz)
  fi
  run_logged "template/playground stack checks" "$log_dir/check-p101-stack.log" ./check-p101-stack.sh "${stack_args[@]}"
else
  say "==> template/playground stack checks"
  say "    SKIP"
  printf '| SKIP | template/playground stack checks | --skip-stack |\n' >> "$summary"
fi

cat >> "$summary" <<EOF

## Details

- Stack output: [stack](./stack/)
EOF

say "p101 post-update-all checks passed: $out_dir"
say "Summary: $summary"
