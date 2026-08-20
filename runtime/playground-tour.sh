#!/usr/bin/env bash
# playground-tour.sh — generate one self-contained playground artifact directory.
#
# The playground is meant to show the whole p101 toolchain in one place:
# strict build, tests, fuzzing, coverage, observation, resource tracking, call
# tracing, correlated reports, error-path walking, and the audit-doctor conductor.
set -u
set -o pipefail

scripts_root="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
workspace_root="$(CDPATH='' cd -- "${scripts_root}/.." && pwd -P)"
playground_root="${workspace_root}/playgrounds"
CDPATH='' cd -- "${playground_root}" || exit 1

out_dir=""
fuzz_secs=5
fault_count=8
cc="clang"
do_quality=1
do_coverage=1
do_fuzz=1

usage() {
  cat <<'USAGE'
Usage: ./runtime/playground-tour.sh [options]

Create one report directory that demonstrates the p101 tooling pipeline.

Options:
  -o <dir>         Output directory.
                   Default: /tmp/p101-tool-playground-tour-<timestamp>-<pid>
  -t <seconds>    Fuzz smoke budget. Default: 5.
  -n <count>      Fault-injection cases for test-faults. Default: 8.
  -c <cc>         C compiler used by quality/coverage builds. Default: clang.
  --skip-quality  Skip build/test/fuzz/coverage and only run runtime demos.
  --skip-coverage Skip coverage generation.
  --skip-fuzz     Skip fuzz smoke.
  -h, --help      Show this help.

Tool paths may be overridden with:
  P101_AUDIT_WRAPPERS, P101_AUDIT_ERRORS, P101_AUDIT_MODULES,
  P101_AUDIT_DOCTOR, P101_INSPECT_CAPTURE, P101_TEST_FAULTS,
  P101_TOOL_PLAYGROUND
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -t) fuzz_secs="${2:?}"; shift 2 ;;
    -n) fault_count="${2:?}"; shift 2 ;;
    -c) cc="${2:?}"; shift 2 ;;
    --skip-quality) do_quality=0; shift ;;
    --skip-coverage) do_coverage=0; shift ;;
    --skip-fuzz) do_fuzz=0; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

timestamp="$(date +%Y%m%d-%H%M%S)"
if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "/tmp/p101-tool-playground-tour-${timestamp}.XXXXXX")"
fi

mkdir -p "$out_dir"
out_dir="$(CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"

summary="$out_dir/summary.md"
: > "$summary"

pass_count=0
fail_count=0
skip_count=0

record() {
  status="$1"
  title="$2"
  detail="$3"

  case "$status" in
    PASS) pass_count=$((pass_count + 1)) ;;
    FAIL) fail_count=$((fail_count + 1)) ;;
    SKIP) skip_count=$((skip_count + 1)) ;;
  esac

  printf '| %s | %s | %s |\n' "$status" "$title" "$detail" >> "$summary"
}

relpath() {
  path="$1"
  case "$path" in
    "$out_dir"/*) printf '%s\n' "${path#"$out_dir"/}" ;;
    *) printf '%s\n' "$path" ;;
  esac
}

find_tool() {
  env_name="$1"
  shift

  configured="$(printenv "$env_name" 2>/dev/null || true)"
  if [ -n "$configured" ]; then
    if [ -x "$configured" ] || command -v "$configured" >/dev/null 2>&1; then
      printf '%s\n' "$configured"
      return 0
    fi
  fi

  for candidate in "$@"; do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi

    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  return 1
}

last_build_tool() {
  project_dir="$1"
  tool_name="$2"

  marker="$project_dir/.last-runtime-build-dir"
  if [ ! -f "$marker" ]; then
    marker="$project_dir/.last-build-dir"
  fi
  if [ -f "$marker" ]; then
    build_dir="$(cat "$marker")"
    candidate="$project_dir/$build_dir/$tool_name"
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  return 1
}

run_logged() {
  title="$1"
  log="$2"
  expected="$3"
  shift 3

  printf '\n==> %s\n' "$title"
  printf '$' > "$log"
  for arg in "$@"; do
    printf ' %s' "$arg" >> "$log"
  done
  printf '\n\n' >> "$log"

  set +e
  "$@" >> "$log" 2>&1
  rc=$?
  set +e

  for ok in $expected; do
    if [ "$rc" -eq "$ok" ]; then
      printf '    PASS (exit %s)\n' "$rc"
      record "PASS" "$title" "[log](./$(relpath "$log"))"
      return 0
    fi
  done

  printf '    FAIL (exit %s; see %s)\n' "$rc" "$log"
  record "FAIL" "$title" "[log](./$(relpath "$log"))"
  return 1
}

missing_tools() {
  missing=""

  while [ $# -gt 1 ]; do
    name="$1"
    value="$2"
    shift 2

    if [ -z "$value" ]; then
      if [ -z "$missing" ]; then
        missing="$name"
      else
        missing="${missing}, ${name}"
      fi
    fi
  done

  printf '%s\n' "$missing"
}

reset_child_dir() {
  child="$1"

  case "$child" in
    "$out_dir"/*)
      rm -rf "$child"
      ;;
    *)
      printf 'Refusing to remove path outside output directory: %s\n' "$child" >&2
      return 1
      ;;
  esac
}

write_summary_header() {
  cat > "$summary" <<EOF
# p101-tool-playground tour

Generated: ${timestamp}

This directory is a one-command tour of the p101 tooling stack: strict checks,
unit tests, fuzzing, coverage, runtime observation, resource tracking, call
tracing, correlated reports, fault-injected error-path walking, and the
audit-doctor conductor.

## Results

| Status | Step | Artifact |
| --- | --- | --- |
EOF
}

append_summary_footer() {
  cat >> "$summary" <<EOF

## Totals

- PASS: ${pass_count}
- FAIL: ${fail_count}
- SKIP: ${skip_count}

## Runtime report directories
EOF

  if [ -d "$out_dir/observed-tour" ]; then
    printf '\n- Full clean tour: [observed-tour](./observed-tour/)\n' >> "$summary"
  fi

  if [ -d "$out_dir/observed-fd-leak" ]; then
    printf -- '- Descriptor leak: [observed-fd-leak](./observed-fd-leak/)\n' >> "$summary"
  fi

  if [ -d "$out_dir/observed-alloc-leak" ]; then
    printf -- '- Allocation leak: [observed-alloc-leak](./observed-alloc-leak/)\n' >> "$summary"
  fi

  if [ -d "$out_dir/observed-double-close" ]; then
    printf -- '- Double close: [observed-double-close](./observed-double-close/)\n' >> "$summary"
  fi

  if [ -d "$out_dir/fault-walk" ]; then
    printf -- '- Fault walk: [fault-walk](./fault-walk/)\n' >> "$summary"
  fi

  if [ -d "$out_dir/audit-doctor" ]; then
    printf -- '- Doctor: [audit-doctor](./audit-doctor/)\n' >> "$summary"
  fi

  if [ -f "$out_dir/coverage/index.html" ]; then
    printf -- '- Coverage: [coverage/index.html](./coverage/index.html)\n' >> "$summary"
  fi

  cat >> "$summary" <<EOF

## Handy next reads
EOF

  if [ -f "$out_dir/observed-tour/summary.txt" ]; then
    printf '\n- [observed-tour/summary.txt](./observed-tour/summary.txt)\n' >> "$summary"
  fi

  if [ -f "$out_dir/observed-fd-leak/resource-report.txt" ]; then
    printf -- '- [observed-fd-leak/resource-report.txt](./observed-fd-leak/resource-report.txt)\n' >> "$summary"
  fi

  if [ -f "$out_dir/observed-alloc-leak/correlated-report.json" ]; then
    printf -- '- [observed-alloc-leak/correlated-report.json](./observed-alloc-leak/correlated-report.json)\n' >> "$summary"
  fi

  if [ -f "$log_dir/fault-walk.log" ]; then
    printf -- '- [fault-walk output](./logs/fault-walk.log)\n' >> "$summary"
  fi

  if [ -f "$out_dir/audit-doctor/summary.md" ]; then
    printf -- '- [audit-doctor/summary.md](./audit-doctor/summary.md)\n' >> "$summary"
  fi

  if [ -f "$out_dir/audit-doctor/audit-doctor.json" ]; then
    printf -- '- [audit-doctor/audit-doctor.json](./audit-doctor/audit-doctor.json)\n' >> "$summary"
  fi
}

write_summary_header

echo "p101-tool-playground tour output: $out_dir"

if [ "$do_quality" -eq 1 ]; then
  run_logged "configure quality build" "$log_dir/configure.log" "0" \
    cmake -S . -B build-tour -DCMAKE_C_COMPILER="$cc" -DP101_BUILD_LEVEL=3 || true
  run_logged "strict build and unit tests" "$log_dir/build.log" "0" \
    cmake --build build-tour || true

  fuzz_runner="../templates/template-c/fuzz.sh"
  if [ "$do_fuzz" -eq 1 ] && [ -x "$fuzz_runner" ] && P101_REPOSITORY_ROOT="$PWD" "$fuzz_runner" --can-fuzz >/dev/null 2>&1; then
    run_logged "fuzz smoke" "$log_dir/fuzz.log" "0" \
      env P101_REPOSITORY_ROOT="$PWD" "$fuzz_runner" -t "$fuzz_secs" || true
  elif [ "$do_fuzz" -eq 1 ]; then
    record "FAIL" "fuzz smoke" "no fuzzer-capable clang found"
  else
    record "SKIP" "fuzz smoke" "--skip-fuzz"
  fi

  if [ "$do_coverage" -eq 1 ] && command -v gcovr >/dev/null 2>&1; then
    run_logged "configure coverage build" "$log_dir/configure-coverage.log" "0" \
      cmake -S . -B build-tour-coverage -DCMAKE_C_COMPILER="$cc" \
        -DP101_BUILD_LEVEL=2 -DP101_COVERAGE_MODE=ON || true
    run_logged "coverage build and tests" "$log_dir/build-coverage.log" "0" \
      cmake --build build-tour-coverage || true
    mkdir -p "$out_dir/coverage"
    run_logged "coverage report" "$log_dir/coverage.log" "0" \
      gcovr -r . --html-details "$out_dir/coverage/index.html" --fail-under-line 1 || true
  elif [ "$do_coverage" -eq 1 ]; then
    record "FAIL" "coverage report" "gcovr not found"
  else
    record "SKIP" "coverage report" "--skip-coverage"
  fi
else
  record "SKIP" "quality pipeline" "--skip-quality"
fi

playground="$(find_tool P101_TOOL_PLAYGROUND ./build-tour/p101-tool-playground "$(last_build_tool . p101-tool-playground)" ./build-clang-22/p101-tool-playground ./build-clang/p101-tool-playground ./build-gcc-16/p101-tool-playground p101-tool-playground)"
run_analysis="$(find_tool P101_INSPECT "$(last_build_tool ../programs/p101-inspect p101-inspect)" ../programs/p101-inspect/build-clang-22/p101-inspect ../programs/p101-inspect/build-clang/p101-inspect ../programs/p101-inspect/build-gcc-16/p101-inspect p101-inspect)"
fault_runner="$(find_tool P101_TEST_FAULTS "$(last_build_tool ../programs/p101-test test-faults)" ../programs/p101-test/build-clang-22/test-faults ../programs/p101-test/build-clang/test-faults ../programs/p101-test/build-gcc-16/test-faults test-faults)"
capture_tool="$(find_tool P101_INSPECT_CAPTURE "$(last_build_tool ../programs/p101-inspect inspect-capture)" ../programs/p101-inspect/build-clang-22/inspect-capture ../programs/p101-inspect/build-clang/inspect-capture ../programs/p101-inspect/build-gcc-16/inspect-capture inspect-capture)"
wrapper_audit="$(find_tool P101_AUDIT_WRAPPERS ../programs/p101-audit/audit-wrappers audit-wrappers)"
error_contract="$(find_tool P101_AUDIT_ERRORS "$(last_build_tool ../programs/p101-audit audit-errors)" ../programs/p101-audit/build-clang-22/audit-errors ../programs/p101-audit/build-clang/audit-errors ../programs/p101-audit/build-gcc-16/audit-errors audit-errors)"
module_map="$(find_tool P101_AUDIT_MODULES "$(last_build_tool ../programs/p101-audit audit-modules)" ../programs/p101-audit/build-clang-22/audit-modules ../programs/p101-audit/build-clang/audit-modules ../programs/p101-audit/build-gcc-16/audit-modules audit-modules)"
doctor="$(find_tool P101_AUDIT_DOCTOR "$(last_build_tool ../programs/p101-audit audit-doctor)" ../programs/p101-audit/build-clang-22/audit-doctor ../programs/p101-audit/build-clang/audit-doctor ../programs/p101-audit/build-gcc-16/audit-doctor audit-doctor)"

if [ -z "$playground" ]; then
  record "FAIL" "locate playground binary" "configure and build the playground first"
elif [ -z "$run_analysis" ] || [ -z "$capture_tool" ]; then
  record "FAIL" "observed runtime demos" "capture or analysis engine not found"
else
  reset_child_dir "$out_dir/observed-tour"
  reset_child_dir "$out_dir/observed-fd-leak"
  reset_child_dir "$out_dir/observed-alloc-leak"
  reset_child_dir "$out_dir/observed-double-close"
  run_logged "observe full clean tour" "$log_dir/observe-tour.log" "0" "$run_analysis" run -o "$out_dir/observed-tour" --observe-tool "$capture_tool" -- "$playground" -s tour -o "$out_dir/tour-output.txt" || true
  run_logged "observe fd leak" "$log_dir/observe-fd-leak.log" "0 1" "$run_analysis" run -o "$out_dir/observed-fd-leak" --observe-tool "$capture_tool" -- "$playground" -s fd-leak -o "$out_dir/fd-leak-output.txt" || true
  run_logged "observe allocation leak" "$log_dir/observe-alloc-leak.log" "0 1" "$run_analysis" run -o "$out_dir/observed-alloc-leak" --observe-tool "$capture_tool" -- "$playground" -s alloc-leak -o "$out_dir/alloc-leak-output.txt" || true
  run_logged "observe double close" "$log_dir/observe-double-close.log" "0 1" "$run_analysis" run -o "$out_dir/observed-double-close" --observe-tool "$capture_tool" -- "$playground" -s double-close -o "$out_dir/double-close-output.txt" || true
fi

if [ -z "$playground" ] || [ -z "$run_analysis" ] || [ -z "$fault_runner" ] || [ -z "$capture_tool" ]; then
  record "FAIL" "fault walk" "missing fault, capture, model, or playground engine"
else
  reset_child_dir "$out_dir/fault-walk"
  mkdir -p "$out_dir/fault-walk"
  run_logged "fault walk" "$log_dir/fault-walk.log" "0 1" "$fault_runner" -U "$run_analysis" -O "$capture_tool" -n "$fault_count" -l "$out_dir/fault-walk/fault" -- "$playground" -s fault-lab -o "$out_dir/fault-lab-output.txt" || true
fi

if [ -z "$playground" ] || [ -z "$doctor" ] || [ -z "$wrapper_audit" ] || [ -z "$error_contract" ] || [ -z "$module_map" ]; then
  record "FAIL" "doctor source audit" "missing source-analysis engine"
else
  reset_child_dir "$out_dir/audit-doctor"
  run_logged "doctor source audit" "$log_dir/doctor.log" "0 1" "$doctor" -o "$out_dir/audit-doctor" -s src -A "$wrapper_audit" -E "$error_contract" -M "$module_map" -- "$playground" -s clean-file -o "$out_dir/audit-doctor-target-output.txt" || true
fi

append_summary_footer

echo
echo "Tour complete: $out_dir"
echo "Summary: $summary"

if [ "$fail_count" -gt 0 ]; then
  exit 1
fi

exit 0
