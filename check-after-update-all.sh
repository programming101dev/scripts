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
#  16. the external C/C++ facts corpus manifest contract;
#  17. the cross-tool behavior regression corpus.

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
interactive=0
from_node=""
only_node=""

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
  --interactive         Pause at a failed graph node and retry that exact node.
  --from <node>         Resume at the named graph node and run its downstream nodes.
  --only <node>         Run only the named graph node and its dependencies.
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
    --interactive) interactive=1; shift ;;
    --from) from_node="${2:?}"; shift 2 ;;
    --only) only_node="${2:?}"; shift 2 ;;
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

template_no_tests_arg=""
playground_quality_arg=""
playground_coverage_arg=""
playground_fuzz_arg=""
[ "$template_no_tests" -eq 0 ] || template_no_tests_arg="--template-no-tests"
[ "$playground_quality" -eq 0 ] || playground_quality_arg="--playground-quality"
[ "$playground_coverage" -eq 0 ] || playground_coverage_arg="--playground-coverage"
[ "$playground_fuzz" -eq 0 ] || playground_fuzz_arg="--playground-fuzz"

graph_args=(
  run
  --out "$out_dir"
  --var "cc=$cc"
  --var "cxx=$cxx"
  --var "fuzz_secs=$fuzz_secs"
  --var "fault_count=$fault_count"
  --var "template_no_tests=$template_no_tests_arg"
  --var "playground_quality=$playground_quality_arg"
  --var "playground_coverage=$playground_coverage_arg"
  --var "playground_fuzz=$playground_fuzz_arg"
)

[ "$skip_cmake" -eq 0 ] || graph_args+=(--skip-group cmake)
[ "$skip_tool_contracts" -eq 0 ] || graph_args+=(--skip-group tool-contracts)
[ "$skip_tool_audit" -eq 0 ] || graph_args+=(--skip-group tool-audit)
[ "$skip_library_audit" -eq 0 ] || graph_args+=(--skip-group library-audit)
[ "$skip_stack" -eq 0 ] || graph_args+=(--skip-group stack)
[ "$skip_regression" -eq 0 ] || graph_args+=(--skip-group regression)
[ "$interactive" -eq 0 ] || graph_args+=(--interactive)
[ -z "$from_node" ] || graph_args+=(--from "$from_node")
[ -z "$only_node" ] || graph_args+=(--only "$only_node")

printf 'p101 post-update-all check output: %s\n' "$out_dir"
printf 'Host:         %s %s %s\n' "$host_os" "$host_release" "$host_machine"
printf 'C compiler:   %s\n' "$cc"
printf 'C++ compiler: %s\n' "$cxx"

./p101-check-graph.py "${graph_args[@]}"

printf 'p101 post-update-all checks passed: %s\n' "$out_dir"
printf 'Summary: %s\n' "$summary"
