#!/usr/bin/env bash
# p101-check.sh — one-command student feedback workflow.

set -u
set -o pipefail

script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
workspace_dir="$(CDPATH='' cd -- "$script_dir/../.." && pwd)"
invoke_cwd="${P101_DISPATCH_CWD:-$(pwd)}"

out_dir=""
source_path="."
project_dir="$invoke_cwd"
fault_count=16
skip_wrapper=0
skip_quality=0
run_coverage=0
skip_html=0
skip_bundle=0

usage() {
  cat <<'USAGE'
Usage: p101 check [options] [<source-path>] -- <command> [args...]
       p101 check [options] <command> [args...]

Run the golden-path p101 teaching workflow:
  1. project quality gate via ./check.sh when present;
  2. p101-doctor source/module preflight;
  3. one capture and one shared-model runtime analysis;
  4. systematic error-path walking;
  5. optional coverage receipt;
  6. one top-level HTML report and bug bundle.

Options:
  -o <dir>          Output directory. Default: ./p101-check-<pid>
  -s <path>         Source path passed to p101-doctor. Default: .
  -p <dir>          Project directory for check.sh/coverage-report.sh. Default: caller cwd.
  -n <count>        Fault-injection cases for p101-error-path-walk. Default: 16
  -x                Skip static source-contract checks inside p101-doctor.
  --skip-quality    Do not run ./check.sh before doctor.
  --coverage        Also run ./coverage-report.sh --no-open when available.
  --skip-html       Do not render HTML reports.
  --skip-bundle     Do not create bug-bundle.tar.gz.
  -h, --help        Show this help.

Tool locations may be overridden with the usual P101_* environment variables.

Examples:
  p101 check -s src -- ./build-clang/my-program input.txt
  p101 check ./src -- ./build-clang/my-program
  p101 check --skip-quality -n 32 -- ./my-program
USAGE
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
    if [ -z "$candidate" ]; then
      continue
    fi
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
  repo="$1"
  tool="$2"
  last_build_file="$repo/.last-runtime-build-dir"
  if [ ! -f "$last_build_file" ]; then
    last_build_file="$repo/.last-build-dir"
  fi

  if [ -f "$last_build_file" ]; then
    build_dir="$(cat "$last_build_file")"
    if [ -n "$build_dir" ]; then
      printf '%s\n' "$repo/$build_dir/$tool"
    fi
  fi
}

quote_command() {
  for arg in "$@"; do
    printf '%q ' "$arg"
  done
  printf '\n'
}

relpath() {
  path="$1"
  case "$path" in
    "$out_dir"/*) printf '%s\n' "${path#"$out_dir"/}" ;;
    *) printf '%s\n' "$path" ;;
  esac
}

run_logged() {
  title="$1"
  log="$2"
  shift 2

  printf '==> %s\n' "$title"
  {
    printf '$'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n\n'
  } > "$log"

  set +e
  "$@" >> "$log" 2>&1
  rc=$?
  set +e
  printf '    exit %s\n' "$rc"
  return "$rc"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -s) source_path="${2:?}"; shift 2 ;;
    -p) project_dir="${2:?}"; shift 2 ;;
    -n) fault_count="${2:?}"; shift 2 ;;
    -x) skip_wrapper=1; shift ;;
    --skip-quality) skip_quality=1; shift ;;
    --coverage) run_coverage=1; shift ;;
    --skip-html) skip_html=1; shift ;;
    --skip-bundle) skip_bundle=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if [ "$#" -gt 1 ] && [ -d "$1" ] && [ "$2" = "--" ]; then
  source_path="$1"
  shift 2
fi

if [ "$#" -eq 0 ]; then
  usage >&2
  exit 2
fi

project_dir="$(CDPATH='' cd -P "$project_dir" && pwd -P)" || {
  echo "p101 check: project directory does not exist: $project_dir" >&2
  exit 2
}
if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "$project_dir/p101-check.XXXXXX")"
fi
case "$out_dir" in
  /*) ;;
  *) out_dir="$project_dir/$out_dir" ;;
esac

if [ -e "$out_dir" ]; then
  echo "p101 check: output path already exists: $out_dir" >&2
  exit 2
fi

mkdir -p "$out_dir/logs"
out_dir="$(CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
summary="$out_dir/summary.md"

doctor_tool="$(find_tool P101_DOCTOR "$(last_build_tool "$workspace_dir/programs/p101-doctor" p101-doctor)" "$workspace_dir/programs/p101-doctor/build-clang-22/p101-doctor" "$workspace_dir/programs/p101-doctor/build-clang/p101-doctor" p101-doctor)" || { echo "p101 check: p101-doctor not found" >&2; exit 2; }
wrapper_tool="$(find_tool P101_WRAPPER_AUDIT "$workspace_dir/programs/p101-wrapper-audit/p101-wrapper-audit" p101-wrapper-audit)" || { echo "p101 check: p101-wrapper-audit not found" >&2; exit 2; }
error_contract_tool="$(find_tool P101_ERROR_CONTRACT "$workspace_dir/programs/p101-error-contract/build-clang-22/p101-error-contract" "$workspace_dir/programs/p101-error-contract/build-clang/p101-error-contract" "$(last_build_tool "$workspace_dir/programs/p101-error-contract" p101-error-contract)" p101-error-contract)" || { echo "p101 check: p101-error-contract not found" >&2; exit 2; }
module_tool="$(find_tool P101_MODULE_MAP "$workspace_dir/programs/p101-module-map/build-clang-22/p101-module-map" "$workspace_dir/programs/p101-module-map/build-clang/p101-module-map" "$(last_build_tool "$workspace_dir/programs/p101-module-map" p101-module-map)" p101-module-map)" || { echo "p101 check: p101-module-map not found" >&2; exit 2; }
observe_tool="$(find_tool P101_OBSERVE "$workspace_dir/programs/p101-observe/build-clang-22/p101-observe" "$workspace_dir/programs/p101-observe/build-clang/p101-observe" "$(last_build_tool "$workspace_dir/programs/p101-observe" p101-observe)" p101-observe)" || { echo "p101 check: p101-observe not found" >&2; exit 2; }
walk_tool="$(find_tool P101_ERROR_PATH_WALK "$workspace_dir/programs/p101-error-path-walk/build-clang-22/p101-error-path-walk" "$workspace_dir/programs/p101-error-path-walk/build-clang/p101-error-path-walk" "$(last_build_tool "$workspace_dir/programs/p101-error-path-walk" p101-error-path-walk)" p101-error-path-walk)" || { echo "p101 check: p101-error-path-walk not found" >&2; exit 2; }
model_tool="$(find_tool P101_EVENT_MODEL "$workspace_dir/libraries/lib_tool_event/build-clang-22/p101-event-model" "$workspace_dir/libraries/lib_tool_event/build-clang/p101-event-model" "$(last_build_tool "$workspace_dir/libraries/lib_tool_event" p101-event-model)" p101-event-model)" || { echo "p101 check: p101-event-model not found" >&2; exit 2; }

quality_status=0
quality_state="SKIP"
coverage_status=0
coverage_state="SKIP"
doctor_status=2
runtime_status=2
walk_status=2
lesson_status=2
html_status=0
html_state="PASS"
bundle_status=0
bundle_state="PASS"

printf 'p101 check output: %s\n' "$out_dir"

if [ "$skip_quality" -eq 0 ] && [ -x "$project_dir/check.sh" ]; then
  (cd "$project_dir" && run_logged "project quality gate" "$log_dir/quality-check.log" ./check.sh)
  quality_status=$?
  if [ "$quality_status" -eq 0 ]; then quality_state="PASS"; else quality_state="FAIL"; fi
elif [ "$skip_quality" -eq 0 ]; then
  quality_status=2
  quality_state="FAIL"
  printf '==> project quality gate\n    FAIL (no executable check.sh in %s)\n' "$project_dir"
  printf 'FAIL: no executable check.sh in %s\n' "$project_dir" > "$log_dir/quality-check.log"
else
  printf '==> project quality gate\n    SKIP\n'
  printf 'SKIP: --skip-quality\n' > "$log_dir/quality-check.log"
fi

doctor_args=(-o "$out_dir/doctor" -s "$source_path" -A "$wrapper_tool" -E "$error_contract_tool" -M "$module_tool")
if [ "$skip_wrapper" -eq 1 ]; then
  doctor_args=(-x "${doctor_args[@]}")
fi
doctor_args+=(-- "$@")

(cd "$project_dir" && run_logged "p101 doctor" "$log_dir/doctor.log" "$doctor_tool" "${doctor_args[@]}")
doctor_status=$?

(cd "$project_dir" && run_logged "p101 runtime capture and analysis" "$log_dir/runtime.log" "$script_dir/p101-run.py" -o "$out_dir/runtime" --observe-tool "$observe_tool" --model-tool "$model_tool" -- "$@")
runtime_status=$?

mkdir -p "$out_dir/fault-walk"
walk_args=(-n "$fault_count" -l "$out_dir/fault-walk/case" -U "$script_dir/p101-run.py" -O "$observe_tool" -Y "$script_dir/p101-analyze.py" -B "$model_tool" -- "$@")
(cd "$project_dir" && run_logged "p101 error-path walk" "$log_dir/error-path-walk.log" "$walk_tool" "${walk_args[@]}")
walk_status=$?

python3 "$script_dir/p101_lessons.py" --catalog "$workspace_dir/playgrounds/lessons/manifest.json" guide --markdown \
  "$out_dir/doctor" "$out_dir/runtime/analysis" "$out_dir/fault-walk" > "$out_dir/lesson-guide.md" 2> "$log_dir/lesson-guide.log"
lesson_status=$?

if [ "$run_coverage" -eq 1 ] && [ -x "$project_dir/coverage-report.sh" ]; then
  (cd "$project_dir" && run_logged "project coverage receipt" "$log_dir/coverage.log" ./coverage-report.sh --no-open -- "$@")
  coverage_status=$?
  if [ "$coverage_status" -eq 0 ]; then coverage_state="PASS"; else coverage_state="FAIL"; fi
elif [ "$run_coverage" -eq 1 ]; then
  coverage_status=2
  coverage_state="FAIL"
  printf '==> project coverage receipt\n    FAIL (no executable coverage-report.sh in %s)\n' "$project_dir"
  printf 'FAIL: no executable coverage-report.sh in %s\n' "$project_dir" > "$log_dir/coverage.log"
else
  printf '==> project coverage receipt\n    SKIP\n'
  printf 'SKIP: --coverage not requested\n' > "$log_dir/coverage.log"
fi

if [ "$skip_html" -eq 0 ]; then
  if [ -d "$out_dir/runtime/analysis" ]; then
    python3 "$script_dir/p101-html-report.py" "$out_dir/runtime/analysis" -o "$out_dir/runtime/analysis/index.html" > "$log_dir/observe-html.log" 2>&1
    html_status=$?
  else
    printf 'FAIL: no observe directory available\n' > "$log_dir/observe-html.log"
    html_status=1
  fi
else
  html_state="SKIP"
  printf 'SKIP: --skip-html\n' > "$log_dir/check-html.log"
fi

if [ "$skip_bundle" -eq 0 ] && [ -d "$out_dir/runtime" ]; then
  "$script_dir/p101-bug-bundle.sh" -o "$out_dir/bug-bundle.tar.gz" "$out_dir/runtime" > "$log_dir/bug-bundle.log" 2>&1
  bundle_status=$?
elif [ "$skip_bundle" -eq 1 ]; then
  bundle_state="SKIP"
  printf 'SKIP: --skip-bundle\n' > "$log_dir/bug-bundle.log"
else
  bundle_state="FAIL"
  bundle_status=1
  printf 'FAIL: no observe directory available\n' > "$log_dir/bug-bundle.log"
fi

cat > "$summary" <<EOF
# p101 check

Project: \`${project_dir}\`

Source path: \`${source_path}\`

Command: \`$(quote_command "$@")\`

## Results

| Step | Status | Artifact |
| --- | --- | --- |
| Project quality gate | ${quality_state} (${quality_status}) | [log](./$(relpath "$log_dir/quality-check.log")) |
| Source/module preflight | $([ "$doctor_status" -eq 0 ] && printf 'PASS' || { [ "$doctor_status" -eq 1 ] && printf 'FINDINGS' || printf 'TROUBLE'; }) (${doctor_status}) | [doctor](./doctor/) |
| Runtime analysis | $([ "$runtime_status" -eq 0 ] && printf 'PASS' || { [ "$runtime_status" -eq 1 ] && printf 'FINDINGS' || printf 'TROUBLE'; }) (${runtime_status}) | [runtime](./runtime/) |
| Error-path walk | $([ "$walk_status" -eq 0 ] && printf 'PASS' || { [ "$walk_status" -eq 1 ] && printf 'FINDINGS' || printf 'TROUBLE'; }) (${walk_status}) | [fault-walk](./fault-walk/) |
| Lesson mapping | $([ "$lesson_status" -eq 0 ] && printf 'PASS' || printf 'TROUBLE') (${lesson_status}) | [lesson guide](./lesson-guide.md) |
| Project coverage | ${coverage_state} (${coverage_status}) | [log](./$(relpath "$log_dir/coverage.log")) |
| HTML report | $([ "$html_state" = "SKIP" ] && printf 'SKIP' || { [ "$html_status" -eq 0 ] && printf 'PASS' || printf 'FAIL'; }) (${html_status}) | [index.html](./index.html) |
| Bug bundle | $([ "$bundle_state" = "SKIP" ] && printf 'SKIP' || { [ "$bundle_status" -eq 0 ] && printf 'PASS' || printf 'FAIL'; }) (${bundle_status}) | [bug-bundle.tar.gz](./bug-bundle.tar.gz) |

## Main artifacts

- Student report: [index.html](./index.html)
- Doctor summary: [doctor/summary.md](./doctor/summary.md)
- Module map: [doctor/module-map.md](./doctor/module-map.md)
- Runtime capture: [runtime/capture](./runtime/capture/)
- Runtime analysis: [runtime/analysis](./runtime/analysis/)
- Runtime HTML: [runtime/analysis/index.html](./runtime/analysis/index.html)
- Error-path walk cases: [fault-walk](./fault-walk/)
- Finding lessons and verification steps: [lesson-guide.md](./lesson-guide.md)
- Bug bundle: [bug-bundle.tar.gz](./bug-bundle.tar.gz)

## How to read this

\`p101 check\` is a teaching workflow, not an OS-level tracer. It can only grade
what the p101 wrappers, the selected run, and the available project scripts make
visible. Use wrapper-audit findings first: direct libc calls can hide resource
activity from the runtime tools.
EOF

# Re-render now that summary.md exists.
if [ "$skip_html" -eq 0 ]; then
  python3 "$script_dir/p101-check-report.py" "$out_dir" -o "$out_dir/index.html" > "$log_dir/check-html.log" 2>&1
  report_status=$?
  html_status=$((html_status + report_status))
fi

printf 'p101 check report: %s\n' "$out_dir/index.html"
printf 'p101 check summary: %s\n' "$summary"

if [ "$doctor_status" -eq 2 ] || [ "$runtime_status" -eq 2 ] || [ "$walk_status" -eq 2 ] || [ "$lesson_status" -ne 0 ] || [ "$html_status" -ne 0 ] || [ "$bundle_status" -ne 0 ]; then
  exit 2
fi

if [ "$quality_status" -ne 0 ] || [ "$doctor_status" -ne 0 ] || [ "$runtime_status" -ne 0 ] || [ "$walk_status" -ne 0 ] || [ "$coverage_status" -ne 0 ]; then
  exit 1
fi

exit 0
