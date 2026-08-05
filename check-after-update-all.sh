#!/usr/bin/env bash
# check-after-update-all.sh — post-update-all acceptance checks.
#
# Run this after ./update-all.sh has already rebuilt the repos/compiler matrix.
# It intentionally does NOT rebuild every repo again. Instead it runs the checks
# that catch integration/template/tool regressions after the heavy build pass:
#
#   1. exact repository-lock verification and receipt;
#   2. GitHub Actions starter workflow drift check;
#   3. shared CMakeLists distribution drift check;
#   4. shared per-repository script distribution drift check;
#   5. shared playground-track runner distribution drift check;
#   6. replay-analysis receipt/integrity regression tests;
#   7. shared CMakeLists regression harness;
#   8. p101 tool contract documentation checks;
#   9. finding-to-lesson curriculum completeness;
#  10. strict source/module audits over every p101 tool;
#  11. source-contract, instrumentation, and executable wrapper conformance;
#  12. model-based wrapper lifecycle/fault/replay laboratory;
#  13. closed-workspace public API candidate audit;
#  14. every repository-owned unit suite and bounded fuzz target;
#  15. fresh-template standalone instantiate/build/test;
#  16. p101-tool-playground tour over observe/resource/trace/report/fault-walk/doctor;
#  17. the external C/C++ facts corpus manifest contract;
#  18. the cross-tool behavior regression corpus.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

cc=""
cxx=""
formatter="clang-format"
c_list_file="supported_c_compilers.txt"
cxx_list_file="supported_cxx_compilers.txt"
compiler_was_selected=0
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
changed_paths=()
jobs=""
cache_dir=""
no_cache=0
resume=0
measure=0
check_graph="${P101_CHECK_GRAPH:-./checks/p101-check-graph.py}"

usage() {
  cat <<'USAGE'
Usage: ./check-after-update-all.sh [options]

Run acceptance checks that make sense after ./update-all.sh has already built
the workspace. The script does not run build-repo.sh.

Options:
  -c <cc>          Select one C compiler instead of the full compiler matrix.
  -x <cxx>         Select one C++ compiler instead of the full compiler matrix.
  -f <formatter>   clang-format executable. Default: clang-format.
  -C <file>        C compiler list. Default: supported_c_compilers.txt.
  -X <file>        C++ compiler list. Default: supported_cxx_compilers.txt.
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
  --resume              Reuse only exact clean records from the receipt under -o.
  --from <node>         Resume at the named node after validating omitted prerequisites.
  --only <node>         Run only the named graph node and its dependencies.
  --changed <path>      Run the conservative impact closure for a workspace-relative path.
  --jobs <count>        Maximum concurrent graph nodes. Default: graph policy.
  --cache-dir <dir>     Exact local evidence cache. Default: scripts/target.
  --no-cache            Execute every selected functional node.
  --measure             Run sequentially without cache for comparable timings.
  -h, --help            Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c) cc="${2:?}"; compiler_was_selected=1; shift 2 ;;
    -x) cxx="${2:?}"; compiler_was_selected=1; shift 2 ;;
    -f) formatter="${2:?}"; shift 2 ;;
    -C) c_list_file="${2:?}"; shift 2 ;;
    -X) cxx_list_file="${2:?}"; shift 2 ;;
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
    --resume) resume=1; shift ;;
    --from) from_node="${2:?}"; shift 2 ;;
    --only) only_node="${2:?}"; shift 2 ;;
    --changed) changed_paths+=("${2:?}"); shift 2 ;;
    --jobs) jobs="${2:?}"; shift 2 ;;
    --cache-dir) cache_dir="${2:?}"; shift 2 ;;
    --no-cache) no_cache=1; shift ;;
    --measure) measure=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

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
  list_file="${2:-$cxx_list_file}"

  if [ -n "$wanted" ] && [ -f "$list_file" ]; then
    awk -v want="$wanted" '
      NF && $0 !~ /^[[:space:]]*#/ {
        n = split($1, parts, "/")
        if (parts[n] == want) {
          print $1
          exit
        }
      }
    ' "$list_file"
  fi
}

run_checks() {
  local run_cc="$1"
  local run_cxx="$2"
  local run_out_dir="$3"
  local template_no_tests_arg=""
  local playground_quality_arg=""
  local playground_coverage_arg=""
  local playground_fuzz_arg=""
  local profile
  local summary
  local graph_status
  local -a graph_args

  run_out_dir="$(mkdir -p "$run_out_dir" && CDPATH='' cd -P "$run_out_dir" && pwd -P)"
  mkdir -p "$run_out_dir/logs"
  profile="$run_out_dir/profile.md"
  summary="$run_out_dir/summary.md"

  [ "$template_no_tests" -eq 0 ] || template_no_tests_arg="--template-no-tests"
  [ "$playground_quality" -eq 0 ] || playground_quality_arg="--playground-quality"
  [ "$playground_coverage" -eq 0 ] || playground_coverage_arg="--playground-coverage"
  [ "$playground_fuzz" -eq 0 ] || playground_fuzz_arg="--playground-fuzz"

  graph_args=(
    run
    --out "$run_out_dir"
    --var "cc=$run_cc"
    --var "cxx=$run_cxx"
    --var "formatter=$formatter"
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
  [ "$resume" -eq 0 ] || graph_args+=(--resume)
  [ -z "$from_node" ] || graph_args+=(--from "$from_node")
  [ -z "$only_node" ] || graph_args+=(--only "$only_node")
  if [ "${#changed_paths[@]}" -ne 0 ]; then
    for changed_path in "${changed_paths[@]}"; do
      graph_args+=(--changed "$changed_path")
    done
  fi
  [ -z "$jobs" ] || graph_args+=(--jobs "$jobs")
  [ -z "$cache_dir" ] || graph_args+=(--cache-dir "$cache_dir")
  [ "$no_cache" -eq 0 ] || graph_args+=(--no-cache)
  [ "$measure" -eq 0 ] || graph_args+=(--measure)

  printf 'p101 post-update-all check output: %s\n' "$run_out_dir"
  printf 'Host:         %s %s %s\n' "$host_os" "$host_release" "$host_machine"
  printf 'C compiler:   %s\n' "$run_cc"
  printf 'C++ compiler: %s\n' "$run_cxx"

  graph_status=0
  "$check_graph" "${graph_args[@]}" || graph_status=$?
  if [ "$graph_status" -ne 0 ]; then
    printf 'p101 post-update-all checks failed: %s\n' "$run_out_dir" >&2
    printf 'Summary: %s\n' "$summary" >&2
    printf 'Profile: %s\n' "$profile" >&2
    return "$graph_status"
  fi

  printf 'p101 post-update-all checks passed: %s\n' "$run_out_dir"
  printf 'Summary: %s\n' "$summary"
  printf 'Profile: %s\n' "$profile"
}

if [ "$compiler_was_selected" -eq 0 ]; then
  [ -f "$c_list_file" ] || { printf 'C compiler list not found: %s\n' "$c_list_file" >&2; exit 2; }
  [ -f "$cxx_list_file" ] || { printf 'C++ compiler list not found: %s\n' "$cxx_list_file" >&2; exit 2; }
  if [ -z "$out_dir" ]; then
    out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-after-update-all-check.XXXXXX")"
  fi
  out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
  matrix_summary="$out_dir/matrix-summary.md"
  printf '# p101 post-update-all compiler matrix\n\n| C compiler | C++ compiler | Result | Summary | Profile |\n| --- | --- | --- | --- | --- |\n' > "$matrix_summary"
  pairs_run=0
  pairs_failed=0
  pairs_skipped=0

  while IFS= read -r matrix_cc; do
    [ -n "$matrix_cc" ] || continue
    matrix_cxx="$(find_cxx_for_c "$matrix_cc" "$cxx_list_file")"
    if [ -z "$matrix_cxx" ]; then
      printf 'WARN: %s has no matching C++ compiler in %s; skipping.\n' "$matrix_cc" "$cxx_list_file" >&2
      pairs_skipped=$((pairs_skipped + 1))
      continue
    fi
    resolved_cc="$(resolve_compiler "$matrix_cc")"
    resolved_cxx="$(resolve_compiler "$matrix_cxx")"
    pair_name="$(printf '%s__%s' "$(basename "$matrix_cc")" "$(basename "$matrix_cxx")" | tr -c '[:alnum:]_.-' '_')"
    pair_out="$out_dir/$pair_name"
    printf '\n===============================================================================\n'
    printf 'Checking compiler pair: %s : %s\n' "$matrix_cc" "$matrix_cxx"
    printf '===============================================================================\n'
    if run_checks "$resolved_cc" "$resolved_cxx" "$pair_out"; then
      printf '| %s | %s | PASS | [%s](%s/summary.md) | [profile](%s/profile.md) |\n' "$matrix_cc" "$matrix_cxx" "$pair_name" "$pair_name" "$pair_name" >> "$matrix_summary"
    else
      printf '| %s | %s | FAIL | [%s](%s/summary.md) | [profile](%s/profile.md) |\n' "$matrix_cc" "$matrix_cxx" "$pair_name" "$pair_name" "$pair_name" >> "$matrix_summary"
      pairs_failed=$((pairs_failed + 1))
    fi
    pairs_run=$((pairs_run + 1))
  done < <(awk 'NF && $0 !~ /^[[:space:]]*#/ { print $1 }' "$c_list_file")

  [ "$pairs_run" -gt 0 ] || { printf 'No usable compiler pairs were found.\n' >&2; exit 3; }
  printf '\nCompiler matrix complete: %d passed, %d failed, %d skipped.\n' \
    "$((pairs_run - pairs_failed))" "$pairs_failed" "$pairs_skipped"
  printf 'Matrix summary: %s\n' "$matrix_summary"
  [ "$pairs_failed" -eq 0 ]
  exit
fi

if [ -z "$cc" ]; then
  cc="$(trim_line "$c_list_file")"
fi
if [ -z "$cxx" ]; then
  cxx="$(find_cxx_for_c "$cc" "$cxx_list_file")"
fi
if [ -z "$cxx" ]; then
  cxx="$(trim_line "$cxx_list_file")"
fi
if [ -z "$cc" ] || [ -z "$cxx" ]; then
  echo "Unable to choose compilers. Run ./p101-workspace compilers first or pass -c/-x." >&2
  exit 2
fi
cc="$(resolve_compiler "$cc")"
cxx="$(resolve_compiler "$cxx")"
if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-after-update-all-check.XXXXXX")"
fi
run_checks "$cc" "$cxx" "$out_dir"
