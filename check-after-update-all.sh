#!/usr/bin/env bash
# check-after-update-all.sh — post-update-all acceptance checks.
#
# Run this after ./update-all.sh has already rebuilt the repos/compiler matrix.
# It intentionally does NOT rebuild every repo again. Instead it runs the checks
# that catch integration/template/tool regressions after the heavy build pass:
#
#   1. GitHub Actions starter workflow drift check;
#   2. shared CMakeLists distribution drift check;
#   3. shared per-repository script distribution drift check;
#   4. shared playground-track runner distribution drift check;
#   5. replay-analysis receipt/integrity regression tests;
#   6. shared CMakeLists regression harness;
#   7. p101 tool contract documentation checks;
#   8. finding-to-lesson curriculum completeness;
#   9. strict source/module audits over every p101 tool;
#  10. source-contract, instrumentation, and executable wrapper conformance;
#  11. model-based wrapper lifecycle/fault/replay laboratory;
#  12. closed-workspace public API candidate audit;
#  13. every repository-owned unit suite and bounded fuzz target;
#  14. fresh-template standalone instantiate/build/test;
#  15. p101-tool-playground tour over observe/resource/trace/report/fault-walk/doctor;
#  16. the cross-tool behavior regression corpus.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

cc=""
cxx=""
out_dir=""
skip_cmake=0
skip_tool_contracts=0
skip_tool_audit=0
skip_library_audit=0
skip_stack=0
skip_regression=0
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
  --fuzz-secs <s>  Per-target repository fuzz budget and optional playground
                   fuzz budget. Default: 5.

  --skip-cmake        Skip the shared CMakeLists regression harness.
  --skip-tool-contracts
                      Skip p101 tool README contract checks.
  --skip-tool-audit   Skip strict wrapper/module audits over p101 tools.
  --skip-library-audit
                      Skip wrapper/error/module audits over lib_* repos.
  --skip-stack        Skip template/playground stack checks.
  --skip-regression   Skip the p101 regression corpus.
  --template-no-tests Build fresh template instances but skip their tests.

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
    --skip-tool-contracts) skip_tool_contracts=1; shift ;;
    --skip-tool-audit) skip_tool_audit=1; shift ;;
    --skip-library-audit) skip_library_audit=1; shift ;;
    --skip-stack) skip_stack=1; shift ;;
    --skip-regression) skip_regression=1; shift ;;
    --template-no-tests) template_no_tests=1; shift ;;
    --playground-quality) playground_quality=1; shift ;;
    --playground-coverage) playground_coverage=1; shift ;;
    --playground-fuzz) playground_fuzz=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-after-update-all-check.XXXXXX")"
fi

out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"
summary="$out_dir/summary.md"
host_os="$(uname -s)"
host_release="$(uname -r)"
host_machine="$(uname -m)"

trim_line() {
  awk 'NF && $0 !~ /^[[:space:]]*#/ { print $1; exit }' "$1"
}

resolve_compiler() {
  requested="$1"
  resolved=""
  if [ -f compiler_paths.txt ]; then
    resolved="$(awk -F= -v name="$requested" '$1 == name { print substr($0, index($0, "=") + 1); exit }' compiler_paths.txt)"
  fi
  if [ -n "$resolved" ]; then
    printf '%s\n' "$resolved"
  else
    command -v "$requested" 2>/dev/null || printf '%s\n' "$requested"
  fi
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

cc="$(resolve_compiler "$cc")"
cxx="$(resolve_compiler "$cxx")"

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
    say "    --- log tail ---"
    tail -n 120 "$log" || true
    say "    --- end log tail ---"
    printf '| FAIL | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
    exit 1
  fi
}

cat > "$summary" <<EOF
# p101 post-update-all checks

Compilers:

- C: ${cc}
- C++: ${cxx}

Host:

- OS: ${host_os}
- Release: ${host_release}
- Machine: ${host_machine}

| Status | Check | Artifact |
| --- | --- | --- |
EOF

say "p101 post-update-all check output: $out_dir"
say "Host:         $host_os $host_release $host_machine"
say "C compiler:   $cc"
say "C++ compiler: $cxx"

run_logged "workspace shell-script gate" "$log_dir/check-shell-scripts.log" ./check-shell-scripts.sh
run_logged "GitHub Actions starter workflow drift" "$log_dir/check-github-actions-template.log" ./check-github-actions-template.sh
run_logged "shared CMakeLists distribution drift" "$log_dir/check-cmake-distribution.log" ./check-cmake-distribution.sh
run_logged "shared repository script distribution drift" "$log_dir/check-script-distribution.log" ./check-script-distribution.sh
run_logged "shared playground-track runner distribution drift" "$log_dir/check-playground-track-scripts.log" ./copy-playground-track-scripts.sh -c
run_logged "shared workspace link distribution" "$log_dir/check-shared-links.log" ./check-shared-links.sh
run_logged "p101 replay-analysis receipt and integrity tests" "$log_dir/test-p101-analyze.log" ./test-p101-analyze.py
run_logged "p101 capture/analyze composition tests" "$log_dir/test-p101-run.log" ./test-p101-run.py
run_logged "p101 causal-model verify and compare tests" "$log_dir/test-p101-model.log" ./test-p101-model.py
run_logged "p101 shared runtime policy tests" "$log_dir/test-p101-runtime.log" ./test-p101-runtime.py
run_logged "p101 finding-to-lesson tests" "$log_dir/test-p101-lessons.log" ./test-p101-lessons.py
run_logged "p101 finding-to-lesson completeness" "$log_dir/check-p101-lessons.log" ./p101_lessons.py check
run_logged "p101 executable lesson acceptance" "$log_dir/check-p101-lesson-acceptance.log" \
  ./p101 lessons verify --full -o "$out_dir/lesson-acceptance"

if [ "$skip_cmake" -eq 0 ]; then
  run_logged "shared CMakeLists regression harness" "$log_dir/test-cmake.log" ./test-cmake.sh -c "$cc" -x "$cxx" -k
else
  say "==> shared CMakeLists regression harness"
  say "    SKIP"
  printf '| SKIP | shared CMakeLists regression harness | --skip-cmake |\n' >> "$summary"
fi

if [ "$skip_tool_contracts" -eq 0 ]; then
  run_logged "p101 tool design contract checks" "$log_dir/check-p101-tool-contracts.log" ./check-p101-tool-contracts.sh
else
  say "==> p101 tool design contract checks"
  say "    SKIP"
  printf '| SKIP | p101 tool design contract checks | --skip-tool-contracts |\n' >> "$summary"
fi

if [ "$skip_tool_audit" -eq 0 ]; then
  run_logged "p101 tool source/module audit" "$log_dir/check-p101-tool-audit.log" ./check-p101-tool-audit.sh --skip-contracts --fail-module-notes -o "$out_dir/tool-audit"
else
  say "==> p101 tool source/module audit"
  say "    SKIP"
  printf '| SKIP | p101 tool source/module audit | --skip-tool-audit |\n' >> "$summary"
fi

if [ "$skip_library_audit" -eq 0 ]; then
  run_logged "functional wrapper-library ownership" "$log_dir/check-functional-library-split.log" ./check-functional-library-split.py
  run_logged "workspace public API unit-test coverage" "$log_dir/check-wrapper-unit-tests.log" ./check-wrapper-unit-tests.py
  run_logged "p101 library source-contract audit" "$log_dir/check-p101-library-audit.log" ./check-p101-library-audit.sh -o "$out_dir/library-audit"
  run_logged "p101 wrapper instrumentation coverage" "$log_dir/check-p101-instrumentation.log" ./check-p101-instrumentation.py --receipt "$out_dir/instrumentation-receipt.json"
  run_logged "p101 executable wrapper conformance" "$log_dir/check-wrapper-conformance.log" ./check-wrapper-conformance.py -o "$out_dir/wrapper-conformance"
  run_logged "p101 model-based wrapper lifecycles" "$log_dir/check-wrapper-lifecycles.log" ./check-wrapper-lifecycles.py -c "$cc" --cases 2 --max-steps 6 -o "$out_dir/wrapper-lifecycles"
  run_logged "workspace-wide public API audit" "$log_dir/check-workspace-public-api.log" ./check-workspace-public-api.sh -o "$out_dir/workspace-api"
else
  say "==> p101 library source-contract, instrumentation, and workspace API audits"
  say "    SKIP"
  printf '| SKIP | p101 library source-contract audit | --skip-library-audit |\n' >> "$summary"
  printf '| SKIP | functional wrapper-library ownership | --skip-library-audit |\n' >> "$summary"
  printf '| SKIP | workspace public API unit-test coverage | --skip-library-audit |\n' >> "$summary"
  printf '| SKIP | p101 wrapper instrumentation coverage | --skip-library-audit |\n' >> "$summary"
  printf '| SKIP | p101 executable wrapper conformance | --skip-library-audit |\n' >> "$summary"
  printf '| SKIP | p101 model-based wrapper lifecycles | --skip-library-audit |\n' >> "$summary"
  printf '| SKIP | workspace-wide public API audit | --skip-library-audit |\n' >> "$summary"
fi

run_logged "standalone repository unit and fuzz checks" "$log_dir/check-repository-tests.log" ./check-repository-tests.sh -c "$cc" -x "$cxx" -o "$out_dir/repository-tests" --fuzz-secs "$fuzz_secs"

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

if [ "$skip_regression" -eq 0 ]; then
  run_logged "p101 behavior regression corpus" "$log_dir/check-p101-regression-corpus.log" ./check-p101-regression-corpus.sh -o "$out_dir/regression-corpus"
else
  say "==> p101 behavior regression corpus"
  say "    SKIP"
  printf '| SKIP | p101 behavior regression corpus | --skip-regression |\n' >> "$summary"
fi

cat >> "$summary" <<EOF

## Details

- Stack output: [stack](./stack/)
- Regression corpus: [regression-corpus](./regression-corpus/)
- Tool audit: [tool-audit](./tool-audit/)
- Library audit: [library-audit](./library-audit/)
- Workspace API candidates: [workspace-api/public-api.md](./workspace-api/public-api.md)
- Standalone repository tests: [repository-tests/summary.md](./repository-tests/summary.md)
EOF

say "p101 post-update-all checks passed: $out_dir"
say "Summary: $summary"
